"""
Shrink-wraps the equipped Scatterhorn set onto the custom character.

This is the inspect-screen path. `wrap_player_body.py` targets Tower frames
(`0x80B9F855` / `0x80FA2308`) which are **not** the playable Guardian — do not run it
for this.

Only bytes 0-5 of each vertex are rewritten, so bone weights stay put and buffer length
does not change. Chest mesh 0 is ~4,100 skinned verts: this is a silhouette wrap, not a
full-topology swap. A raw nearest-point snap collapses triangles (measured 40-50% degenerate
on the full-body wrap); displacement is Laplacian-smoothed so topology survives.

Fit is **one AABB over every loaded Scatterhorn piece**, never per-mesh or per-slot. A
per-hood fit would crush the whole custom character into the helmet box. Hood / chest /
gauntlet vertices then land on the matching region of the custom surface.

Packages are resolved from the **position buffer** tag, not the model tag. Chest mesh 0
lives in `sandbox_037d`; hanging-panel meshes live in `sandbox_0698`. Writing to the
model's package would corrupt an unrelated entry.

Judge on the character / inspect / APPEARANCE screen. The in-world Guardian is a dissolve
shell and does not count. Helmet-hidden inspect still shows the Awoken head — the wrapped
hood is only visible with the helmet shown. Legs (`0x80EFA93B` / `0x80EFA92E`) are not
dumped yet and stay stock Scatterhorn.

Usage:
    python wrap_body.py --dry-run
    python wrap_body.py
    python inject_mesh.py --undo
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from extract_mesh import dumped, read_positions, vertex_stride
from inject_mesh import entry_index_of, package_of, to_destiny, write_all
from parse_models import Model, TAG_MAX, TAG_MIN
from wrap_player_body import (
    DEFAULT_SMOOTHING,
    DEFAULT_SWING,
    SURFACE_SAMPLES,
    adjacency,
    load_character,
    mesh_triangles,
    nearest,
    option,
    sample_surface,
    smooth_displacement,
    swing_arms,
    wrap_buffer,
)

DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")

# Both gender/race variants. Gender 0 mapping A vs B is unproven, so wrap both.
BODIES = [
    0x80EFA1CA, 0x80EFA1A9,  # Scatterhorn Robe
    0x80EFA859, 0x80EFA850,  # Scatterhorn Hood
    0x80EF981E, 0x80EF9809,  # Scatterhorn gauntlets
]
# Named, not dumped. wrap_body includes them automatically once the headers land.
LEGS = [0x80EFA93B, 0x80EFA92E]


def load_dumped_model(tag: int) -> Model | None:
    path = DUMP / f"tag_{tag:08X}.bin"
    if not path.is_file():
        return None
    return Model(tag, path.read_bytes())


def main() -> None:
    source, faces = load_character()
    source = to_destiny(np.asarray(source, dtype=np.float64))
    degrees = option("--arm-swing", DEFAULT_SWING)
    source = swing_arms(source, degrees)
    cloud = sample_surface(source, faces, SURFACE_SAMPLES)
    print(
        f"character {len(source):,} verts, {len(faces):,} tris, "
        f"{len(cloud):,} surface samples, arms swung {degrees:g} deg"
    )

    targets = list(BODIES)
    for tag in LEGS:
        if (DUMP / f"tag_{tag:08X}.bin").is_file():
            targets.append(tag)
        else:
            print(f"  legs 0x{tag:08X} not dumped - leaving stock")

    loaded: list[tuple] = []
    all_pts: list[np.ndarray] = []
    for tag in targets:
        model = load_dumped_model(tag)
        if model is None or model.scale is None or model.translation is None:
            print(f"  skip 0x{tag:08X}: header not dumped")
            continue
        translation = np.asarray(model.translation[:3], dtype=np.float64)
        scale = model.scale[0]
        meshes = []
        for index, mesh in enumerate(model.meshes):
            if not (TAG_MIN <= mesh.position_buffer <= TAG_MAX):
                continue
            stride = vertex_stride(mesh.positions)
            raw = dumped(mesh.position_buffer)
            if not stride or raw is None:
                print(f"  skip 0x{tag:08X} mesh {index}: buffers not dumped")
                continue
            points = np.asarray(
                read_positions(raw, stride, scale, tuple(translation)), dtype=np.float64)
            meshes.append((index, mesh, stride, raw, points))
        if not meshes:
            continue
        piece = np.concatenate([row[4] for row in meshes])
        loaded.append((tag, model, scale, translation, meshes))
        all_pts.append(piece)
        z0, z1 = piece[:, 2].min(), piece[:, 2].max()
        print(f"  loaded 0x{tag:08X}  {len(meshes)} meshes  {len(piece):,} verts  "
              f"z {z0:.3f}..{z1:.3f} m")

    if not all_pts:
        raise SystemExit("nothing loadable")

    every = np.concatenate(all_pts)
    dst_min, dst_max = every.min(0), every.max(0)
    src_min, src_max = source.min(0), source.max(0)
    src_size = np.maximum(src_max - src_min, 1e-9)
    dst_size = dst_max - dst_min
    placed_cloud = (cloud - src_min) / src_size * dst_size + dst_min
    print(
        f"\nshared fit  {len(every):,} dest verts  "
        f"box z {dst_min[2]:.3f}..{dst_max[2]:.3f} m  "
        f"span {dst_size.max():.3f} m"
    )

    by_package: dict[Path, dict[int, bytes]] = {}
    smoothing = option("--smooth", DEFAULT_SMOOTHING)
    for tag, model, scale, translation, meshes in loaded:
        print(f"\n0x{tag:08X}")
        for index, mesh, stride, raw, points in meshes:
            displacement = nearest(points, placed_cloud) - points
            triangles = mesh_triangles(mesh, len(points))
            if triangles is None:
                print(f"  mesh {index}: no index buffer, unsmoothed")
            elif smoothing > 0:
                edges, degree = adjacency(triangles, len(points))
                displacement = smooth_displacement(displacement, edges, degree, smoothing)
            wrapped = points + displacement
            moved = float(np.linalg.norm(wrapped - points, axis=1).mean())
            new_bytes = wrap_buffer(raw, wrapped, stride, scale, translation)
            if len(new_bytes) != len(raw):
                raise SystemExit(f"0x{tag:08X} mesh {index}: wrap changed buffer size")
            package = package_of(mesh.position_buffer)
            by_package.setdefault(package, {})[entry_index_of(mesh.position_buffer)] = new_bytes
            print(
                f"  mesh {index}: {len(points):>6,} verts  stride {stride}  "
                f"moved {moved:.3f} m avg  -> {package.name}"
            )

    total = sum(len(v) for v in by_package.values())
    print(f"\n{total} buffers across {len(by_package)} packages")
    for path, replacements in sorted(by_package.items()):
        print(f"  {path.name}: {len(replacements)} entries")
    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return
    write_all(by_package, "")
    print("\nLaunch and judge on inspect / APPEARANCE. Undo: python inject_mesh.py --undo")


if __name__ == "__main__":
    main()
