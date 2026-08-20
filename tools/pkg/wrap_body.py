"""
Shrink-wraps Destiny body meshes onto the custom character, keeping vertex count and skinning.

Helmet inject never hit the player because the inspect screen draws the body/head, not a helmet
item. These person-sized investment models (span ~1.6-1.8 m) are the full-body cluster. Only
bytes 0-5 of each stride-16 vertex are rewritten so bone weights stay put.

Usage:
    python wrap_body.py --dry-run
    python wrap_body.py
    python inject_mesh.py --undo
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np

from extract_mesh import dumped, read_positions, vertex_stride
from glb import load_mesh, mesh_names, read_glb
from inject_mesh import entry_index_of, package_of, to_destiny, write_all
from parse_models import models


def sample_surface(points: np.ndarray, triangles: np.ndarray, count: int) -> np.ndarray:
    a, b, c = points[triangles[:, 0]], points[triangles[:, 1]], points[triangles[:, 2]]
    areas = np.linalg.norm(np.cross(b - a, c - a), axis=1) * 0.5
    total = areas.sum()
    if total <= 0:
        raise SystemExit("model has zero surface area")
    rng = np.random.default_rng(0xD2)
    picked = rng.choice(len(triangles), size=count, p=areas / total)
    u = rng.random((count, 1))
    v = rng.random((count, 1))
    over = (u + v) > 1.0
    u[over] = 1.0 - u[over]
    v[over] = 1.0 - v[over]
    a, b, c = a[picked], b[picked], c[picked]
    return (a + u * (b - a) + v * (c - a)).astype(np.float32)


def nearest(queries: np.ndarray, cloud: np.ndarray, batch: int = 256) -> np.ndarray:
    out = np.empty_like(queries)
    squared = (cloud * cloud).sum(1)
    for at in range(0, len(queries), batch):
        chunk = queries[at:at + batch]
        distance = squared[None, :] - 2.0 * (chunk @ cloud.T)
        out[at:at + batch] = cloud[np.argmin(distance, axis=1)]
    return out

GLB = Path(r"C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb")
PACKED_MAX = 32767.0
SURFACE_SAMPLES = 80_000
# Person-sized investment bodies (stride 16, head verts at ~1.7 m). They did not show under
# Scatterhorn; armour is now null so this is the undersuit the inspect screen actually draws.
BODIES = {
    0x80BA75A7, 0x80EC20BE, 0x80BA769C, 0x80EC2AB4,
    0x80BA7661, 0x80BA75E3, 0x80EC342B, 0x80EC3824,
}


def load_character(path: Path) -> tuple[np.ndarray, np.ndarray]:
    document, _ = read_glb(path)
    chunks, faces, base = [], [], 0
    for name in mesh_names(document):
        points, tris = load_mesh(path, name)
        chunks.append(points)
        faces.append(tris + base)
        base += len(points)
    return np.concatenate(chunks), np.concatenate(faces)


def wrap_buffer(original: bytes, dest_points: np.ndarray, stride: int,
                scale: float, translation) -> bytes:
    """@return The original buffer with only packed xyz replaced."""
    packed = np.clip(
        np.round((dest_points - np.asarray(translation)) / scale * PACKED_MAX),
        -32768, 32767,
    ).astype(np.int16)
    out = bytearray(original)
    for index, (x, y, z) in enumerate(packed):
        struct.pack_into("<3h", out, index * stride, int(x), int(y), int(z))
    return bytes(out)


def main() -> None:
    source, faces = load_character(GLB)
    source = to_destiny(np.asarray(source, dtype=np.float64))
    cloud = sample_surface(source, faces, SURFACE_SAMPLES)
    print(f"character {len(source):,} verts, {len(faces):,} tris, {len(cloud):,} surface samples")

    by_package: dict[Path, dict[int, bytes]] = {}
    chosen = [model for model in models if model.tag in BODIES]
    if not chosen:
        raise SystemExit("none of the body tags are in the parsed dump_models set")

    for model in chosen:
        mesh = model.meshes[0]
        stride = vertex_stride(mesh.positions)
        raw = dumped(mesh.position_buffer)
        if not stride or raw is None:
            print(f"  skip 0x{model.tag:08X}: buffers not dumped")
            continue
        translation = np.asarray(model.translation[:3])
        scale = model.scale[0]
        original_pts = np.asarray(
            read_positions(raw, stride, scale, tuple(translation)), dtype=np.float64)
        # Fit the custom character's AABB onto this body's AABB so the wrap is in model space.
        src_min, src_max = source.min(0), source.max(0)
        dst_min, dst_max = original_pts.min(0), original_pts.max(0)
        src_size = np.maximum(src_max - src_min, 1e-6)
        dst_size = dst_max - dst_min
        placed = (source - src_min) / src_size * dst_size + dst_min
        placed_cloud = (cloud - src_min) / src_size * dst_size + dst_min
        wrapped = nearest(original_pts, placed_cloud)
        new_bytes = wrap_buffer(raw, wrapped, stride, scale, translation)
        if len(new_bytes) != len(raw):
            raise SystemExit(f"0x{model.tag:08X}: wrap changed buffer size")
        by_package.setdefault(package_of(model.tag), {})[
            entry_index_of(mesh.position_buffer)] = new_bytes
        print(
            f"  0x{model.tag:08X}  mesh0 {len(original_pts):,} verts  "
            f"span {model.span:.3f}  stride {stride}"
        )

    print(f"{sum(len(v) for v in by_package.values())} buffers across {len(by_package)} packages")
    if "--dry-run" in sys.argv:
        return
    write_all(by_package, "")


if __name__ == "__main__":
    main()
