"""
Replaces the confirmed in-world Guardian body with the custom character's own topology.

## Wrap versus inject

`wrap_player_body.py` moves Destiny's own vertices onto the character's surface. It keeps every
buffer's length, so nothing else has to change - but the result is still Destiny's mesh wearing
someone else's proportions. This writes the custom mesh itself: its vertices, its triangles, its
part table.

## The budget that shapes the whole design

Mesh 2 carries the body: 31,480 vertices at **stride 8**, which is position only. Its normals, UVs
and skinning live in the second vertex buffer - 629,600 B over 31,480 vertices, exactly 20 bytes
each - and that buffer is **not dumped**. The game indexes it per vertex, so a mesh with more
vertices than the original would read off its end and hang the loader.

Joining and welding the GLB collapses 61,908 vertices to **23,512** (the seams were duplicated), so
the custom character fits under that budget with room to spare and the second buffer can be
inherited untouched instead of dumped and rebuilt. That is the difference between this working now
and needing another game launch.

## Inheriting skinning sensibly

Vertex `k` of the new mesh inherits record `k` of the second buffer, including its bone indices and
weights. Writing vertices in arbitrary order would therefore give a hand vertex the skinning of a
foot. Both sets are sorted by position (height-dominant) and paired in that order, so each new
vertex inherits from an original vertex at roughly the same place on the body. Skinning will not be
exact, but it will be locally sane rather than random.

Meshes 0, 1 and 3 are silenced, otherwise the old body still draws through the new one.

Usage:
    python inject_player_body.py --dry-run
    python inject_player_body.py --obj placed.obj      # verify offline first
    python inject_player_body.py
    python inject_mesh.py --undo
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

from extract_mesh import vertex_stride
from inject_mesh import (INHERITED_W, PART_INDEX_COUNT, PART_INDEX_OFFSET, PART_LOD,
                         PART_PRIMITIVE, PART_STRIDE, TRIANGLES, entry_index_of, package_of,
                         rewrite_header, write_all)
from parse_models import Model, TAG_MAX, TAG_MIN
from wrap_player_body import DEFAULT_SWING, PRISTINE, original_buffer, swing_arms

DUMPS = [Path(r"C:\Sunrise\bin\x64\Sunrise\dump"),
         Path(r"C:\Sunrise\bin\x64\Sunrise\dump_models")]
PACKED_MAX = 32767.0
MESH_INDEX = 2
# All three model entries that reference the body's buffers, verified by scanning every dumped
# model for references to them.
#
# `0x80C23B5D` shares `0x80B9F855`'s buffers, and leaving it out was a real bug: sharing buffers
# means a *wrap* only has to run once, because a wrap changes vertex positions and nothing else.
# A topology swap also changes the index buffer's layout, and each model carries its **own part
# table** naming index offsets and counts. An un-rewritten entry therefore draws the old layout over
# the new buffers. The blank probe worked precisely because it hit all three.
TARGETS = [0x80B9F855, 0x80C23B5D, 0x80FA2308]


def option(name: str, fallback):
    return type(fallback)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


def load_model(tag: int) -> Model:
    for folder in DUMPS:
        path = folder / f"tag_{tag:08X}.bin"
        if path.is_file():
            return Model(tag, path.read_bytes())
    raise SystemExit(f"0x{tag:08X} header not dumped")


def read_obj(path: Path) -> tuple[np.ndarray, np.ndarray]:
    points: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if line.startswith("v "):
            _, x, y, z = line.split()[:4]
            points.append((float(x), float(y), float(z)))
        elif line.startswith("f "):
            tokens = line.split()[1:]
            if len(tokens) >= 3:
                faces.append(tuple(int(t.split("/")[0]) - 1 for t in tokens[:3]))
    return np.asarray(points, dtype=np.float64), np.asarray(faces, dtype=np.int64)


def spatial_order(points: np.ndarray) -> np.ndarray:
    """@return Indices ordering points by height, then side, then depth.

    Height dominates deliberately: skinning varies far more along the body's length than across it,
    so a height-dominant pairing keeps inherited bone weights closest to sane.
    """
    return np.lexsort((points[:, 0], points[:, 1], points[:, 2]))


def pack_positions(points: np.ndarray, stride: int, scale: float, translation) -> bytes:
    packed = np.clip(np.round((points - np.asarray(translation)) / scale * PACKED_MAX),
                     -32768, 32767).astype(np.int64)
    out = bytearray(len(points) * stride)
    for index, (x, y, z) in enumerate(packed):
        struct.pack_into("<4h", out, index * stride, int(x), int(y), int(z), INHERITED_W)
    return bytes(out)


def pack_indices(faces: np.ndarray) -> bytes:
    flat = faces.reshape(-1).astype(np.uint16)
    return flat.tobytes()


def carrier_slots(mesh) -> list[int]:
    """@return The LOD-0 part slots that actually draw the body.

    **Not part 0.** On the real Guardian body, slot 0 is a 66-index decal carrying material
    `0x80B9F85A` - 0.08% of LOD 0 - while the body itself is slot 31 and its twin 182 at 19,301
    indices each. Routing the whole custom mesh through slot 0, as the first attempt did, draws the
    entire character with a decal's material, which in game was indistinguishable from no change at
    all.

    The largest part is chosen instead, together with any part sharing its exact index range. Those
    twins are alternate material assignments over the same geometry, and which one the renderer
    picks is not knowable from here - so both are pointed at the new triangles.
    """
    lod0 = [(slot, part) for slot, part in enumerate(mesh.parts) if part[4] == 0]
    if not lod0:
        return [0]
    biggest = max(lod0, key=lambda item: item[1][3])[1]
    return [slot for slot, part in lod0 if part[2] == biggest[2] and part[3] == biggest[3]]


def rewrite_model(model: Model, carrier: int, index_count: int) -> tuple[bytes, list[int]]:
    """@return `(model blob drawing only the custom triangles, the part slots used)`."""
    out = bytearray(model.data)
    chosen: list[int] = []
    for number, mesh in enumerate(model.meshes):
        slots = set(carrier_slots(mesh)) if number == carrier else set()
        if number == carrier:
            chosen = sorted(slots)
        for slot in range(mesh.part_count):
            at = mesh.parts_at + slot * PART_STRIDE
            if at + PART_STRIDE > len(out):
                break
            if slot in slots:
                struct.pack_into("<h", out, at + PART_PRIMITIVE, TRIANGLES)
                struct.pack_into("<I", out, at + PART_INDEX_OFFSET, 0)
                struct.pack_into("<I", out, at + PART_INDEX_COUNT, index_count)
                out[at + PART_LOD] = 0
            else:
                struct.pack_into("<I", out, at + PART_INDEX_COUNT, 0)
    return bytes(out), chosen


def main() -> None:
    source_path = Path(option("--mesh", str(Path(__file__).with_name("character_solid.obj"))))
    if not source_path.is_file():
        raise SystemExit(f"custom mesh not found: {source_path}\n"
                         "Build it with tools/pkg/prepare_mesh.py through Blender.")
    points, faces = read_obj(source_path)
    points = swing_arms(points, option("--arm-swing", DEFAULT_SWING))
    print(f"custom mesh: {len(points):,} verts, {len(faces):,} tris  from {source_path.name}")
    if len(points) > 0xFFFF:
        raise SystemExit(f"{len(points):,} vertices needs 32-bit indices")

    by_package: dict[Path, dict[int, bytes]] = {}
    for tag in TARGETS:
        model = load_model(tag)
        if MESH_INDEX >= len(model.meshes):
            print(f"  0x{tag:08X}: no mesh {MESH_INDEX}, skipping")
            continue
        mesh = model.meshes[MESH_INDEX]
        stride = vertex_stride(mesh.positions)
        original = original_buffer(mesh.position_buffer)
        header = original_buffer(mesh.positions)
        index_header = original_buffer(mesh.indices)
        if not stride or original is None or header is None or index_header is None:
            print(f"  0x{tag:08X}: buffers not dumped, skipping")
            continue
        budget = len(original) // stride
        if len(points) > budget:
            raise SystemExit(
                f"0x{tag:08X}: {len(points):,} verts exceeds mesh {MESH_INDEX}'s {budget:,}; the "
                "second vertex buffer would be indexed off its end. Decimate first.")

        scale = model.scale[0]
        translation = np.asarray(model.translation[:3], dtype=np.float64)
        low, high = points.min(axis=0), points.max(axis=0)
        half = (high - low) / 2
        if (half > scale).any():
            raise SystemExit(f"0x{tag:08X}: half-extents {np.round(half, 4)} exceed scale {scale:.4f}")
        placed = points - (low + high) / 2 + translation

        # Pair new vertices to original slots by position, so inherited normals and skinning come
        # from a vertex in roughly the same place rather than an arbitrary one.
        originals = np.frombuffer(original, dtype=np.int16).reshape(-1, stride // 2)[:, :3]
        originals = originals.astype(np.float64) / PACKED_MAX * scale + translation
        slots = spatial_order(originals[:len(points)])
        order = spatial_order(placed)
        remap = np.empty(len(points), dtype=np.int64)
        remap[slots] = order
        reordered = placed[remap]
        inverse = np.empty(len(points), dtype=np.int64)
        inverse[remap] = np.arange(len(points))
        remapped_faces = inverse[faces]

        positions = pack_positions(reordered, stride, scale, translation)
        indices = pack_indices(remapped_faces)
        model_blob, chosen = rewrite_model(model, MESH_INDEX, remapped_faces.size)
        replacements = {
            entry_index_of(mesh.position_buffer): positions,
            entry_index_of(mesh.positions): rewrite_header(header, len(positions), 0, "<I"),
            entry_index_of(mesh.index_buffer): indices,
            entry_index_of(mesh.indices): rewrite_header(index_header, len(indices), 8, "<q"),
            entry_index_of(model.tag): model_blob,
        }
        print(f"\n0x{tag:08X}: mesh {MESH_INDEX} budget {budget:,} verts, stride {stride}")
        print(f"  positions {len(original):,} -> {len(positions):,} B")
        print(f"  indices   {len(original_buffer(mesh.index_buffer) or b''):,} -> {len(indices):,} B")
        carried = ", ".join(f"slot {slot} (material 0x{mesh.parts[slot][0]:08X}, "
                            f"was {mesh.parts[slot][3]:,} indices)" for slot in chosen)
        print(f"  carrier parts: {carried}")
        print(f"  each set to {remapped_faces.size:,} indices; all other parts zeroed")
        # Buffers and the model entry can live in different packages - 0x80FA2308's entry is in
        # globals_03d1 while its vertex data is in globals_0238 - so each entry is routed by its
        # own tag rather than by the model's.
        for tag_of_entry, blob in ((mesh.position_buffer, positions),
                                   (mesh.positions, replacements[entry_index_of(mesh.positions)]),
                                   (mesh.index_buffer, indices),
                                   (mesh.indices, replacements[entry_index_of(mesh.indices)]),
                                   (model.tag, replacements[entry_index_of(model.tag)])):
            by_package.setdefault(package_of(tag_of_entry), {})[entry_index_of(tag_of_entry)] = blob

        if option("--obj", ""):
            with open(option("--obj", ""), "w", encoding="ascii") as fh:
                for x, y, z in reordered:
                    fh.write(f"v {x:.5f} {y:.5f} {z:.5f}\n")
                for a, b, c in remapped_faces:
                    fh.write(f"f {a + 1} {b + 1} {c + 1}\n")

    total = sum(len(v) for v in by_package.values())
    print(f"\n{total} entries across {len(by_package)} packages")
    for path, replacements in sorted(by_package.items()):
        print(f"  {path.name}: {len(replacements)} entries")
    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return
    write_all(by_package, "")
    print("\nLaunch and look in world. Undo: python inject_mesh.py --undo")


if __name__ == "__main__":
    main()
