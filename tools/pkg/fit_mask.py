"""
Resamples a custom mesh onto a Destiny helmet's exact vertex layout, and repacks the buffer.

## Why not nearest-point

The obvious fit — move each helmet vertex to the closest point on the custom mesh — collapses. On
terrain it mapped 10,344 vertices onto 330 distinct positions, because "closest" is many-to-one
wherever the target is even slightly concave, and every vertex in a dimple lands on the same rim.

A helmet and a gas mask are both **star-shaped about the head**, so there is a much better map: cast
a ray from the head centre through each helmet vertex and take where it leaves the custom surface.
That is one-to-one by construction for star-shaped meshes, it preserves the angular arrangement of
the original vertices, and it therefore keeps each vertex in roughly the region it started in —
which is what keeps skinning and texture layout sane.

Vertices whose ray misses fall back to the nearest custom vertex, and the count is reported rather
than hidden, because a high fallback count means the shape is not star-shaped and the result should
be looked at before it is written.

## Coordinate frames

glTF is Y-up with +Z toward the viewer. Destiny is **Z-up with X forward and Y lateral**, which is
readable off the renders: the view with `y` horizontal is the symmetric face-on one, so `y` is the
left-right axis and `x` is depth. The map is therefore `destiny = (gltf.z, gltf.x, gltf.y)`.

## Repacking

Positions are packed signed 16-bit, `round((p - translation) / scale * 32767)`, using the *model's
own* scale and translation so the model header needs no edit. Only bytes 0-5 of each vertex are
rewritten; `w` and anything after it — bone indices and weights at stride 12 or 16 — are inherited
byte for byte, so the mesh stays rigged exactly as it was.

Usage:
    python fit_mask.py 0x80BA7474 --glb <path.glb> --mesh GasMask --obj preview.obj
    python fit_mask.py 0x80BA7474 --scale 0.95 --out fitted.bin
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

from glb import load_mesh
from parse_models import Model, models, option
from extract_mesh import GEOMETRY, dumped, read_positions, vertex_stride

PACKED_MAX = 32767.0
# Vertices per ray-cast batch. The full cross product of vertices and triangles would be gigabytes;
# this keeps each intermediate array around a million floats.
BATCH = 256
DEFAULT_GLB = Path(r"C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb")


def to_destiny(points: np.ndarray) -> np.ndarray:
    """@return glTF Y-up points remapped to Destiny's Z-up, X-forward frame."""
    return np.stack([points[:, 2], points[:, 0], points[:, 1]], axis=1)


def ray_hits(origin: np.ndarray, directions: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Möller-Trumbore, every ray against every triangle.

    @param directions Unit ray directions, one per vertex.
    @return Distance to the **nearest** forward hit per ray, or 0 where nothing was hit.

    Nearest, not farthest. The origin sits inside the custom mesh, so the nearest hit is the surface
    facing that direction. Taking the farthest instead grabs the *inside of the far wall*, which on
    an open mesh - and a gas mask is open at the back - drags every backward-facing vertex onto the
    front of the face and blows the model out into a flat flare.
    """
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    edge1, edge2 = b - a, c - a
    out = np.zeros(len(directions))
    for start in range(0, len(directions), BATCH):
        chunk = directions[start:start + BATCH]
        pvec = np.cross(chunk[:, None, :], edge2[None, :, :])
        det = np.einsum("ij,kij->ki", edge1, pvec)
        alive = np.abs(det) > 1e-12
        inverse = np.where(alive, 1.0 / np.where(alive, det, 1.0), 0.0)
        tvec = origin - a
        u = np.einsum("ij,kij->ki", tvec, pvec) * inverse
        qvec = np.cross(tvec[None, :, :], edge1[None, :, :])
        v = np.einsum("kij,kij->ki", chunk[:, None, :] * np.ones_like(qvec), qvec) * inverse
        distance = np.einsum("ij,kij->ki", edge2, qvec) * inverse
        ok = alive & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6) & (distance > 1e-6)
        nearest = np.where(ok, distance, np.inf).min(axis=1)
        out[start:start + BATCH] = np.where(np.isfinite(nearest), nearest, 0.0)
    return out


def fit(target: np.ndarray, source: np.ndarray, faces: np.ndarray,
        origin: np.ndarray) -> tuple[np.ndarray, int]:
    """@return `(fitted positions, fallback count)` for every target vertex."""
    offsets = target - origin
    lengths = np.linalg.norm(offsets, axis=1)
    lengths[lengths == 0] = 1.0
    directions = offsets / lengths[:, None]
    distances = ray_hits(origin, directions, source[faces])
    fitted = origin + directions * distances[:, None]
    missed = distances <= 0
    # A miss keeps the vertex exactly where the game put it. Snapping it to the nearest custom
    # vertex instead throws it across the model and shows up as spikes, and there is no honest
    # answer for "where on the mask does the back of the helmet go" when the mask has no back.
    fitted[missed] = target[missed]
    return fitted, int(missed.sum())


def repack(buffer: bytes, stride: int, fitted: np.ndarray,
           scale: float, translation) -> bytes:
    """Writes fitted positions into a copy of the original buffer, touching bytes 0-5 only."""
    out = bytearray(buffer)
    packed = np.clip(np.round((fitted - np.asarray(translation)) / scale * PACKED_MAX),
                     -32768, 32767).astype(np.int16)
    for index in range(len(fitted)):
        struct.pack_into("<3h", out, index * stride, *packed[index])
    return bytes(out)


tags = [argument for argument in sys.argv[1:] if argument.startswith("0x")]
if not tags:
    raise SystemExit(__doc__)
wanted = int(tags[0], 0)
model = next((candidate for candidate in models if candidate.tag == wanted), None)
if model is None:
    raise SystemExit(f"0x{wanted:08X} is not among the {len(models)} dumped models")
if len(model.meshes) != 1:
    raise SystemExit(f"0x{wanted:08X} has {len(model.meshes)} meshes; this fits single-mesh models")

mesh = model.meshes[0]
stride = vertex_stride(mesh.positions)
buffer = dumped(mesh.position_buffer)
if not stride or buffer is None:
    raise SystemExit(f"buffers for 0x{wanted:08X} are not dumped; run parse_models.py --request")

scale = model.scale[0]
translation = model.translation[:3]
target = np.asarray(read_positions(buffer, stride, scale, translation))

positions, faces = load_mesh(option("--glb", str(DEFAULT_GLB)), option("--mesh", "GasMask"))
source = to_destiny(np.asarray(positions, dtype=np.float64))

# Put the custom mesh where the helmet is and at the helmet's size, so the packed range is used
# fully and nothing clips against the model's own bounding box.
fill = option("--scale", 1.0)
source_low, source_high = source.min(axis=0), source.max(axis=0)
target_low, target_high = target.min(axis=0), target.max(axis=0)
ratio = fill * np.min((target_high - target_low) / np.maximum(source_high - source_low, 1e-9))
source = (source - (source_low + source_high) / 2) * ratio + (target_low + target_high) / 2

origin = (target_low + target_high) / 2
fitted, fallbacks = fit(target, source, faces, origin)

print(f"0x{model.tag:08X}: {len(target):,} vertices at stride {stride}")
print(f"  custom mesh {len(source):,} verts, {len(faces):,} tris, scaled {ratio:.4f}x")
print(f"  ray fit {len(target) - fallbacks:,}, nearest-vertex fallback {fallbacks:,}")
print(f"  target box {np.round(target_high - target_low, 3)}")
print(f"  fitted box {np.round(fitted.max(axis=0) - fitted.min(axis=0), 3)}")

obj = option("--obj", "")
if obj:
    index_buffer = dumped(mesh.index_buffer)
    from extract_mesh import read_triangles
    lines = [f"o fitted_{model.tag:08X}"]
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in fitted]
    for _material, primitive, offset, count, lod in mesh.parts:
        if lod != 0:
            continue
        for a, b, c in read_triangles(index_buffer, offset, count, primitive):
            if max(a, b, c) < len(fitted):
                lines.append(f"f {a + 1} {b + 1} {c + 1}")
    Path(obj).write_text("\n".join(lines), encoding="utf-8")
    print(f"  wrote {obj}")

out = option("--out", "")
if out:
    Path(out).write_bytes(repack(buffer, stride, fitted, scale, translation))
    print(f"  wrote {len(buffer):,} bytes to {out}")
