"""
Puts the custom character on Destiny player-shaped bodies, skinned to Destiny's bones.

The GLB already has a Mixamo-style armature. Destiny will not play those bones; it plays its own.
Each custom vertex takes the bone indices/weights of the nearest Destiny vertex, so the silhouette
is yours and the runtime skeleton stays the game's.

The 72,409-vertex globals body (0x80B9F962) is large enough to take the custom topology. The
smaller skinned bodies keep their own topology and only have xyz rewritten.

Armor hid every previous body wrap. The Warlock's armour slots are set to null separately.

Usage:
    python playable_character.py --dry-run
    python playable_character.py
    python inject_mesh.py --undo
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

from extract_mesh import dumped, read_positions, vertex_stride
from glb import load_mesh, mesh_names, read_glb
from inject_mesh import (
    entry_index_of,
    pack_indices,
    package_of,
    rewrite_header,
    rewrite_model,
    to_destiny,
    write_all,
)
from parse_models import models
from wrap_body import GLB, nearest, sample_surface, wrap_buffer

PACKED_MAX = 32767.0
SURFACE_SAMPLES = 40_000
# Full-body, stride-16, includes a head (verts above 1.7 m). Custom topology fits.
RETARGET = 0x80B9F962
# Same class of mesh, too few verts for the custom topology — xyz wrap only.
WRAP = {0x80B9E810, 0x80C717CA, 0x80F56B13}


def load_character(path: Path) -> tuple[np.ndarray, np.ndarray]:
    document, _ = read_glb(path)
    chunks, faces, base = [], [], 0
    for name in mesh_names(document):
        points, tris = load_mesh(path, name)
        chunks.append(points)
        faces.append(tris + base)
        base += len(points)
    return np.concatenate(chunks), np.concatenate(faces)


def nearest_index(queries: np.ndarray, cloud: np.ndarray, batch: int = 128) -> np.ndarray:
    """@return For each query, the index of the nearest point in `cloud`."""
    out = np.empty(len(queries), dtype=np.int32)
    squared = (cloud * cloud).sum(1)
    for at in range(0, len(queries), batch):
        chunk = queries[at:at + batch]
        distance = squared[None, :] - 2.0 * (chunk @ cloud.T)
        out[at:at + batch] = np.argmin(distance, axis=1)
    return out


def pack_skinned(custom: np.ndarray, donor: bytes, donor_points: np.ndarray,
                 stride: int, scale: float, translation) -> bytes:
    """Custom positions, Destiny bone weights copied from the nearest donor vertex."""
    packed = np.clip(
        np.round((custom - np.asarray(translation)) / scale * PACKED_MAX),
        -32768, 32767,
    ).astype(np.int16)
    donor_at = nearest_index(custom, donor_points)
    out = bytearray(len(custom) * stride)
    for index, (x, y, z) in enumerate(packed):
        src = int(donor_at[index]) * stride
        out[index * stride:(index + 1) * stride] = donor[src:src + stride]
        struct.pack_into("<3h", out, index * stride, int(x), int(y), int(z))
    return bytes(out)


def fit(source: np.ndarray, dest: np.ndarray) -> np.ndarray:
    src_min, src_max = source.min(0), source.max(0)
    dst_min, dst_max = dest.min(0), dest.max(0)
    src_size = np.maximum(src_max - src_min, 1e-6)
    return (source - src_min) / src_size * (dst_max - dst_min) + dst_min


def retarget(model, source: np.ndarray, faces: np.ndarray) -> dict[int, bytes]:
    mesh = model.meshes[0]
    stride = vertex_stride(mesh.positions)
    raw = dumped(mesh.position_buffer)
    vertex_header = dumped(mesh.positions)
    index_header = dumped(mesh.indices)
    if not stride or raw is None or vertex_header is None or index_header is None:
        raise SystemExit(f"0x{model.tag:08X}: headers or positions not dumped")
    if len(source) > 0xFFFF:
        raise SystemExit(f"{len(source):,} vertices need 32-bit indices")
    translation = np.asarray(model.translation[:3])
    scale = model.scale[0]
    donor_points = np.asarray(
        read_positions(raw, stride, scale, tuple(translation)), dtype=np.float64)
    placed = fit(source, donor_points)
    positions = pack_skinned(placed, raw, donor_points, stride, scale, translation)
    indices = pack_indices(faces)
    print(
        f"  retarget 0x{model.tag:08X}  {len(donor_points):,} dest verts -> "
        f"{len(source):,} custom verts, stride {stride}"
    )
    return {
        entry_index_of(mesh.position_buffer): positions,
        entry_index_of(mesh.positions): rewrite_header(vertex_header, len(positions), 0, "<I"),
        entry_index_of(mesh.index_buffer): indices,
        entry_index_of(mesh.indices): rewrite_header(index_header, len(indices), 8, "<q"),
        entry_index_of(model.tag): rewrite_model(model, mesh, faces.size),
    }


def wrap(model, source: np.ndarray, cloud: np.ndarray) -> dict[int, bytes]:
    mesh = model.meshes[0]
    stride = vertex_stride(mesh.positions)
    raw = dumped(mesh.position_buffer)
    if not stride or raw is None:
        print(f"  skip wrap 0x{model.tag:08X}: buffers not dumped")
        return {}
    translation = np.asarray(model.translation[:3])
    scale = model.scale[0]
    original = np.asarray(
        read_positions(raw, stride, scale, tuple(translation)), dtype=np.float64)
    placed_cloud = fit(cloud, original)
    wrapped = nearest(original, placed_cloud)
    print(
        f"  wrap 0x{model.tag:08X}  {len(original):,} verts  span {model.span:.3f}  "
        f"stride {stride}"
    )
    return {entry_index_of(mesh.position_buffer): wrap_buffer(
        raw, wrapped, stride, scale, translation)}


def main() -> None:
    source, faces = load_character(GLB)
    source = to_destiny(np.asarray(source, dtype=np.float64))
    cloud = sample_surface(source, faces, SURFACE_SAMPLES)
    print(f"character {len(source):,} verts, {len(faces):,} tris, {len(cloud):,} samples")

    by_package: dict[Path, dict[int, bytes]] = {}
    found = {model.tag: model for model in models}

    target = found.get(RETARGET)
    if target is None:
        raise SystemExit(f"0x{RETARGET:08X} is not in dump_models")
    by_package.setdefault(package_of(target.tag), {}).update(retarget(target, source, faces))

    for tag in sorted(WRAP):
        model = found.get(tag)
        if model is None:
            print(f"  missing 0x{tag:08X}")
            continue
        by_package.setdefault(package_of(model.tag), {}).update(wrap(model, source, cloud))

    print(f"{sum(len(v) for v in by_package.values())} entries across {len(by_package)} packages")
    if "--dry-run" in sys.argv:
        return
    write_all(by_package, "")
    request = Path(r"C:\Sunrise\bin\x64\Sunrise\dump\request.txt")
    request.write_text("# blanked so a launch cannot re-dump through an installed patch\r\n",
                       encoding="utf-8")
    print(f"blanked {request}")


if __name__ == "__main__":
    main()
