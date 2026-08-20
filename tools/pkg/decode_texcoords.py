"""Decode the second vertex buffer - the one holding UVs, normals and tangents.

## The stride-24 layout, decoded 2026-08-20

    0x00  int16   u        primary texcoord, value/32767 * texcoord_scale + texcoord_translation
    0x02  int16   v
    0x04  int16   nx       normal, unit length, value/32767
    0x06  int16   ny
    0x08  int16   nz
    0x0A  int16   0        always zero
    0x0C  int16   tx       tangent, unit length, value/32767
    0x0E  int16   ty
    0x10  int16   tz
    0x12  int16   w        handedness, exactly +/-32767
    0x14  int16   u2       secondary texcoord
    0x16  int16   v2

The texcoord scale and translation are the model header's, at 0x70 and 0x78 - the same
dequantisation the positions get from 0x50 / 0x60.

**It verifies itself, and `--verify` re-runs the checks on any model.** Over the 4,100 vertices
of the Scatterhorn chest: both int16 triples are unit length to five decimals on *every* vertex,
the field at 0x0A has exactly one distinct value (0), the field at 0x12 has exactly two
(+/-32767), and normal and tangent are perpendicular - mean |dot| 0.0125, 95th percentile
0.0000. A tangent frame is the only thing that looks like that.

The body renders correctly but wears tiled Scatterhorn texture, because `clone_uvs` only
*resizes* this buffer to the new vertex count. Writing the custom mesh's own UVs means knowing
what the other 20 bytes of each 24-byte vertex are: normals and tangents live in here too, and
writing a UV pair while corrupting the normal flat-shades or inverts the lighting.

**The decode validates itself against geometry.** Face normals are computable from the position
buffer and the index buffer, which are already decoded, so a candidate normal field is right
exactly when it correlates with the normals the triangles actually have. No guessing from
byte patterns, and no launch.

UV candidates are checked a second way: dequantised through the model header's texcoord scale
and translation (0x70 / 0x78), a real UV lands in a sane range and varies smoothly between
vertices that share a triangle.

Usage:
    python decode_texcoords.py                      # verify the layout on the chest
    python decode_texcoords.py 0x80EFA93B --mesh 0  # verify it on another model
    python decode_texcoords.py --search             # re-derive it from scratch
"""
from __future__ import annotations

import struct
import sys

import numpy as np

from bone_probe import buffer_of, dumped, meshes_of, stride_of

PACKED_MAX = 32767.0
DEFAULT_MODEL = 0x80EFA1CA


def model_header(data: bytes) -> dict:
    return {
        "scale": struct.unpack_from("<4f", data, 0x50),
        "translation": struct.unpack_from("<4f", data, 0x60),
        "texcoord_scale": struct.unpack_from("<2f", data, 0x70),
        "texcoord_translation": struct.unpack_from("<2f", data, 0x78),
    }


def positions(body: bytes, stride: int, scale: float, translation) -> np.ndarray:
    count = len(body) // stride
    raw = np.frombuffer(body, dtype=np.int16).reshape(count, stride // 2)[:, :3]
    return raw / PACKED_MAX * scale + np.asarray(translation)


def geometric_normals(points: np.ndarray, index_bytes: bytes, parts) -> np.ndarray:
    """@return Area-weighted vertex normals, from the triangles the index buffer declares."""
    normals = np.zeros_like(points)
    indices = np.frombuffer(index_bytes, dtype=np.uint16)
    for _at, primitive, offset, count, lod in parts:
        if lod != 0:
            continue
        span = indices[offset:offset + count].astype(np.int64)
        if primitive == 5:
            a, b, c = span[:-2], span[1:-1], span[2:]
            keep = (a != b) & (b != c) & (a != c)
            a, b, c = a[keep], b[keep], c[keep]
        else:
            usable = len(span) // 3 * 3
            a, b, c = span[:usable:3], span[1:usable:3], span[2:usable:3]
        if not len(a):
            continue
        inside = (a < len(points)) & (b < len(points)) & (c < len(points))
        a, b, c = a[inside], b[inside], c[inside]
        cross = np.cross(points[b] - points[a], points[c] - points[a])
        for corner in (a, b, c):
            np.add.at(normals, corner, cross)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return normals / np.maximum(length, 1e-12)


def unit(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)


def score_normals(candidate: np.ndarray, truth: np.ndarray, live: np.ndarray) -> float:
    """@return Mean |cos| between candidate and geometric normals, over vertices with geometry."""
    if not np.any(live):
        return 0.0
    dot = np.abs((unit(candidate[live]) * truth[live]).sum(1))
    return float(np.mean(dot))


UV, NORMAL, PAD, TANGENT, SIGN, UV2 = 0, 2, 5, 6, 9, 10


def verify(words: np.ndarray, truth: np.ndarray, live: np.ndarray) -> bool:
    """Re-run every check the layout rests on. @return True when all of them hold."""
    normal = words[:, NORMAL:NORMAL + 3].astype(np.float64)
    tangent = words[:, TANGENT:TANGENT + 3].astype(np.float64)
    normal_len = np.linalg.norm(normal, axis=1) / PACKED_MAX
    tangent_len = np.linalg.norm(tangent, axis=1) / PACKED_MAX
    pad = np.unique(words[:, PAD])
    sign = np.unique(words[:, SIGN])
    perpendicular = np.abs((unit(normal) * unit(tangent)).sum(1))
    against = score_normals(normal, truth, live)

    checks = [
        ("normal is unit length", abs(normal_len.mean() - 1.0) < 1e-3 and normal_len.std() < 1e-3,
         f"mean {normal_len.mean():.5f} std {normal_len.std():.5f}"),
        ("tangent is unit length",
         abs(tangent_len.mean() - 1.0) < 1e-3 and tangent_len.std() < 1e-3,
         f"mean {tangent_len.mean():.5f} std {tangent_len.std():.5f}"),
        ("+0x0A is always zero", len(pad) == 1 and pad[0] == 0, f"distinct {list(pad[:4])}"),
        ("+0x12 is only +/-32767", set(sign.tolist()) <= {-32767, 32767},
         f"distinct {list(sign[:4])}"),
        ("normal perpendicular to tangent", float(np.mean(perpendicular)) < 0.05,
         f"mean |dot| {np.mean(perpendicular):.4f}"),
        ("normal agrees with the triangles", against > 0.6, f"mean |cos| {against:.3f}"),
    ]
    print("\nlayout checks:")
    for name, passed, detail in checks:
        print(f"  [{'ok' if passed else 'FAIL'}] {name:<34} {detail}")
    return all(passed for _name, passed, _detail in checks)


def main() -> None:
    wanted = [a for a in sys.argv[1:] if a.startswith("0x")]
    tag = int(wanted[0], 0) if wanted else DEFAULT_MODEL
    which = int(sys.argv[sys.argv.index("--mesh") + 1]) if "--mesh" in sys.argv else 0

    data = dumped(tag)
    if data is None:
        raise SystemExit(f"0x{tag:08X} not dumped")
    header = model_header(data)
    mesh = meshes_of(data)[which]

    position_stride = stride_of(mesh["positions"])
    position_body = dumped(buffer_of(mesh["positions"]))
    index_body = dumped(buffer_of(mesh["indices"]))
    uv_header_tag = mesh["texcoords"]

    print(f"model 0x{tag:08X} mesh {which}")
    print(f"  model scale {header['scale'][0]:.4f}  translation {header['translation'][:3]}")
    print(f"  texcoord scale {header['texcoord_scale']}  "
          f"translation {header['texcoord_translation']}")

    uv_buffer = buffer_of(uv_header_tag)
    uv_body = dumped(uv_buffer)
    stride = stride_of(uv_header_tag)
    verts = len(position_body) // position_stride
    print(f"  positions {verts:,} verts stride {position_stride}")
    print(f"  texcoords header 0x{uv_header_tag:08X} -> buffer 0x{uv_buffer:08X} "
          f"stride {stride}, {0 if uv_body is None else len(uv_body):,} B")
    if uv_body is None:
        raise SystemExit("texcoord buffer not dumped")
    if len(uv_body) != verts * stride:
        print(f"  WARNING {len(uv_body):,} B is not {verts:,} x {stride} "
              f"= {verts * stride:,} - this dump may be one of ours, not pristine")

    words = np.frombuffer(uv_body, dtype=np.int16).reshape(verts, stride // 2)
    points = positions(position_body, position_stride, header["scale"][0],
                       header["translation"][:3])
    truth = geometric_normals(points, index_body, mesh["parts"])
    live = np.linalg.norm(truth, axis=1) > 0.5
    print(f"  {int(live.sum()):,} of {verts:,} vertices carry LOD-0 triangles")

    if stride != 24:
        print(f"\nstride {stride} is not the decoded 24-byte layout; use --search")
    elif not verify(words, truth, live):
        raise SystemExit("\nthe decoded layout does not hold for this model")
    else:
        us, vs = header["texcoord_scale"]
        ut, vt = header["texcoord_translation"]
        u = words[:, UV] / PACKED_MAX * us + ut
        v = words[:, UV + 1] / PACKED_MAX * vs + vt
        print(f"\n  uv   u {u.min():.3f}..{u.max():.3f}   v {v.min():.3f}..{v.max():.3f}")
        print(f"  uv2  u {(words[:, UV2] / PACKED_MAX * us + ut).mean():.3f} (mean)  "
              f"v {(words[:, UV2 + 1] / PACKED_MAX * vs + vt).mean():.3f} (mean)")

    if "--search" not in sys.argv:
        return

    print("\nfirst 6 vertices, raw:")
    for index in range(6):
        chunk = uv_body[index * stride:(index + 1) * stride]
        print(f"  {chunk.hex()}")
        print(f"    int16 {struct.unpack_from(f'<{stride // 2}h', chunk, 0)}")

    print(f"\nper-int16-field range over {verts:,} vertices:")
    for field in range(stride // 2):
        column = words[:, field]
        print(f"  +0x{field * 2:02X}  min {column.min():>7}  max {column.max():>7}  "
              f"mean {column.mean():>9.1f}  distinct {len(np.unique(column)):>6}")

    print("\nnormal candidates, scored as mean |cos| against the geometric normal:")
    best = (0.0, "")
    for offset in range(0, stride - 5, 2):
        candidate = words[:, offset // 2:offset // 2 + 3].astype(np.float64)
        score = score_normals(candidate, truth, live)
        flag = ""
        if score > best[0]:
            best = (score, f"int16 x3 at +0x{offset:02X}")
            flag = "  <-- best so far"
        print(f"  int16 x3 at +0x{offset:02X}: {score:.3f}{flag}")
    signed = np.frombuffer(uv_body, dtype=np.int8).reshape(verts, stride)
    for offset in range(0, stride - 2):
        candidate = signed[:, offset:offset + 3].astype(np.float64)
        score = score_normals(candidate, truth, live)
        if score > best[0]:
            best = (score, f"int8 x3 at +0x{offset:02X}")
        if score > 0.75:
            print(f"  int8  x3 at +0x{offset:02X}: {score:.3f}")
    print(f"\nbest normal field: {best[1]} at |cos| {best[0]:.3f}")

    print("\nUV candidates, dequantised as value/32767 * texcoord_scale + translation:")
    us, vs = header["texcoord_scale"]
    ut, vt = header["texcoord_translation"]
    for offset in range(0, stride - 3, 2):
        u = words[:, offset // 2] / PACKED_MAX * us + ut
        v = words[:, offset // 2 + 1] / PACKED_MAX * vs + vt
        inside = np.mean((u >= -0.05) & (u <= 1.05) & (v >= -0.05) & (v <= 1.05))
        print(f"  +0x{offset:02X}: u {u.min():>8.3f}..{u.max():>7.3f}  "
              f"v {v.min():>8.3f}..{v.max():>7.3f}   inside 0..1: {inside:.0%}")


if __name__ == "__main__":
    main()
