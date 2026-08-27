"""
Puts a custom GLB onto a named NPC's body mesh, with skinning transferred from the NPC itself.

## Why this is not `inject_mesh.py`

`inject_mesh.py` writes positions and leaves the rest of the vertex stride zero. On a **stride 16**
mesh those trailing 8 bytes are the skinning - four weights then four bone indices - so zeroing them
gives every vertex no influence from any bone and the mesh collapses. That is fine for a rigid
helmet and fatal for a body.

`inject_scatterhorn.py` does transfer skinning, but it is hard-wired to the Warlock: it stitches
weights from three armour pieces because no single Guardian mesh covers the whole body, and it
splits hands onto the gauntlet draw to reach bones 20-71.

An NPC needs neither. Zavala's mesh 0 is **one complete humanoid donor** - 6,147 vertices, every one
weighted, weight sums all exactly 255, 54 bones spanning 1..63 - so a single nearest-neighbour
transfer from the mesh being replaced covers the whole character.

## Three things this reads from the target rather than assuming

1. **`w`.** The fourth int16 of a packed position is not a homogeneous coordinate. The Scatterhorn
   chest uses **18**; Zavala's body uses **32767**, on every one of its vertices. `inject_mesh.py`
   hardcodes 18, which would be wrong here, so this takes the donor's dominant value.
2. **Scale and translation**, from the model header, for the quantisation.
3. **The bone space.** Weights are copied from the *same mesh being replaced*, so whatever index
   space that mesh uses is carried over verbatim. Nothing has to be known about which skeleton it
   is - which matters, because armour and bodies do not agree (see `map_bone_space.py`).

## The texcoord buffer is not optional here

`has_room` in `inject_mesh.py` caps the vertex count at the target's only while the texcoord buffer
is **inherited**: a longer vertex list seeks past its end, and that hangs the Tower. A custom
character is far larger than an NPC body mesh (58,858 against Zavala's 6,147), so the UV buffer is
always resized here rather than being an opt-in flag.

The remaining hard ceiling is **16-bit indices** - above 65,535 vertices this would need to write
32-bit ones, and it does not.

Usage:
    python inject_npc_body.py 0x80C714B2 --dry-run
    python inject_npc_body.py 0x80C714B2 --glb <path.glb> --dry-run
    python inject_npc_body.py 0x80C714B2 --glb <path.glb> --stretch --dry-run
    python inject_npc_body.py 0x80C714B2 --glb <path.glb> --write
"""
from __future__ import annotations

import struct
import sys
from collections import Counter
from pathlib import Path

import numpy as np

# `parse_models` reads its model directory from `--dump` **at import time**, and it is imported
# transitively by `inject_mesh` below. Setting sys.argv inside main() is therefore too late: the
# module is already bound to its default `dump_models` folder, and every NPC model then reports as
# "not among the dumped models". Two directories are in play - `parse_models` enumerates models from
# this one, while `extract_mesh` reads buffers from `game_paths.dump_dir()` - and for NPC work both
# are the game's own dump.
if "--dump" not in sys.argv:
    sys.argv += ["--dump", r"C:\Sunrise\bin\x64\Sunrise\dump"]

from glb import load_mesh, mesh_names, read_glb
from inject_mesh import (PACKED_MAX, TRIANGLES, entry_index_of, package_of, pack_indices,
                         resize_per_vertex, rewrite_header, rewrite_model, to_destiny, write_all)
from inject_scatterhorn import nearest_index

DEFAULT_GLB = Path(r"C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb")
SKINNED_STRIDE = 16
MAX_16BIT = 0xFFFF


def option(name: str, fallback):
    return type(fallback)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


def load_all_meshes(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """
    @param path The `.glb` to read.
    @return Every mesh in the file merged into one `(vertices, triangles)` pair.

    A character is authored as several meshes - body, clothing, accessories - and the live Warlock
    inject carries all of them as one buffer with per-part index ranges. Loading only the named mesh
    would drop most of the character.
    """
    document, _ = read_glb(path)
    points: list[np.ndarray] = []
    faces: list[np.ndarray] = []
    base = 0
    for name in mesh_names(document):
        vertices, triangles = load_mesh(path, name)
        points.append(np.asarray(vertices, dtype=np.float64))
        faces.append(np.asarray(triangles, dtype=np.int64) + base)
        base += len(vertices)
    return np.vstack(points), np.vstack(faces)


def donor_skin(body: bytes, stride: int) -> list[bytes]:
    """@return The 8-byte weight/bone tail of every donor vertex, in order."""
    return [bytes(body[at + 8:at + 16]) for at in range(0, len(body) - stride + 1, stride)]


def dominant_w(body: bytes, stride: int) -> int:
    """
    @return The `w` the target overwhelmingly uses in its packed positions.

    Read rather than assumed: the Scatterhorn chest uses 18 and Zavala's body uses 32767. Writing
    the wrong one is not a rounding error, it is a different field entirely.
    """
    counts = Counter(struct.unpack_from("<h", body, at + 6)[0]
                     for at in range(0, len(body) - stride + 1, stride))
    return counts.most_common(1)[0][0]


#: Destiny's z is up, so index 2 is height. Scaling a body by anything else is wrong - see below.
UP = 2


def place(source: np.ndarray, target: np.ndarray, stretch: bool) -> np.ndarray:
    """
    Moves the custom mesh onto the target body's box.

    **Scale by height, and stand it on the ground.** Fitting a bounding box the obvious way - uniform
    scale by the smallest axis ratio, centre on centre - collapses a body. A character authored with
    its arms out spans 1.79 m across where Zavala's arms span 0.90, so the smallest ratio is the arm
    axis and it halves the character's height: 1.77 m becomes 0.89. Measured consequence, before this
    was fixed: of 6,147 donor vertices only **190** were ever the nearest to anything, because the
    whole source had shrunk into the bottom of the target's box.

    Height is the axis that must match, and the feet must meet the floor rather than the boxes'
    centres meeting. Arms are then free to reach wider than the target's box, which is correct - the
    character's arms really are further apart.

    @param stretch Fit each axis independently instead. Distorts anything whose build differs from
                   the NPC's, so it is opt-in: a source that is not human-proportioned cannot both
                   keep its shape and fill the box, and choosing distortion silently is wrong.
    """
    low, high = source.min(axis=0), source.max(axis=0)
    target_low, target_high = target.min(axis=0), target.max(axis=0)
    span = np.maximum(high - low, 1e-9)
    target_span = target_high - target_low

    if stretch:
        return (source - low) * (target_span / span) + target_low

    scaled = (source - low) * (target_span[UP] / span[UP])
    # Centre the two horizontal axes, and sit the lowest vertex on the target's lowest.
    placed = scaled + target_low
    centre_shift = (target_low + target_high) / 2 - (placed.min(axis=0) + placed.max(axis=0)) / 2
    centre_shift[UP] = 0.0
    return placed + centre_shift


def build(model, mesh_index: int, source: np.ndarray, faces: np.ndarray,
          stretch: bool) -> tuple[dict[int, bytes], list[str]]:
    """
    @return `(entry index -> new bytes, notes)` for one NPC body swap.
    """
    import extract_mesh as em

    mesh = model.meshes[mesh_index]
    stride = em.vertex_stride(mesh.positions)
    body = em.dumped(mesh.position_buffer)
    vertex_header = em.dumped(mesh.positions)
    index_header = em.dumped(mesh.indices)
    if stride != SKINNED_STRIDE:
        raise SystemExit(f"mesh {mesh_index} is stride {stride}; this writes skinned stride 16 only")
    for label, blob in (("position buffer", body), ("vertex header", vertex_header),
                        ("index header", index_header)):
        if blob is None:
            raise SystemExit(f"0x{model.tag:08X} mesh {mesh_index}: {label} not dumped")
    if len(source) > MAX_16BIT:
        raise SystemExit(f"{len(source):,} vertices needs 32-bit indices, which this does not write")

    scale = model.scale[0]
    translation = np.asarray(model.translation[:3])
    donor_points = np.asarray(em.read_positions(body, stride, scale, translation))
    skins = donor_skin(body, stride)
    w = dominant_w(body, stride)

    placed = place(source, donor_points, stretch)
    half = (placed.max(axis=0) - placed.min(axis=0)) / 2
    if (half > scale).any():
        raise SystemExit(f"half-extents {np.round(half, 4)} exceed model scale {scale:.4f}; "
                         "the model header would need rewriting too")

    # Skinning is copied from the mesh being replaced, so the bone index space carries over verbatim
    # and nothing has to be known about which skeleton it is.
    nearest = nearest_index(placed, donor_points)

    packed = np.clip(np.round((placed - translation) / scale * PACKED_MAX), -32768, 32767)
    out = bytearray(len(placed) * stride)
    for index, (x, y, z) in enumerate(packed.astype(np.int64)):
        at = index * stride
        struct.pack_into("<4h", out, at, int(x), int(y), int(z), w)
        out[at + 8:at + 16] = skins[nearest[index]]
    positions = bytes(out)
    indices = pack_indices(faces)

    replacements = {
        entry_index_of(mesh.position_buffer): positions,
        entry_index_of(mesh.positions): rewrite_header(vertex_header, len(positions), 0, "<I"),
        entry_index_of(mesh.index_buffer): indices,
        entry_index_of(mesh.indices): rewrite_header(index_header, len(indices), 8, "<q"),
        entry_index_of(model.tag): rewrite_model(model, mesh, faces.size,
                                                 silence_other_meshes=True),
    }

    # Always, not opt-in: an inherited texcoord buffer shorter than the vertex list is read past its
    # end, and that hangs the Tower.
    uv_header = em.dumped(mesh.texcoords)
    uv_body = em.dumped(mesh.texcoord_buffer)
    # Transfer quality, stated rather than assumed. Nearest-neighbour skinning is only as good as
    # the pose agreement between source and donor: a character authored with its arms out, matched
    # against a donor standing differently, sends arm vertices to torso bones and the result is a
    # body that folds when it animates. Coverage and match distance are the two numbers that show
    # it, and neither is visible from looking at the geometry afterwards.
    gaps = np.linalg.norm(placed - donor_points[nearest], axis=1)
    coverage = len(set(nearest)) / len(donor_points)
    notes = [f"donor {len(donor_points):,} verts, {len(set(nearest)):,} used "
             f"({100 * coverage:.0f}% coverage)",
             f"match distance: median {np.median(gaps) * 100:.1f} cm, "
             f"90th pct {np.percentile(gaps, 90) * 100:.1f} cm, max {gaps.max() * 100:.1f} cm",
             f"w={w} taken from the target", f"bones carried over from mesh {mesh_index}"]
    if coverage < 0.5 or np.median(gaps) > 0.05:
        notes.append("POSE MISMATCH - the source and the donor are not standing the same way, so "
                     "these weights will deform badly. Retarget the source onto the rig first "
                     "(rig_true_human.json; this donor's bone indices are its row order).")
    if uv_header is None or not uv_body:
        notes.append("TEXCOORD BUFFER NOT DUMPED - inherited, and it is too short. Dump "
                     f"0x{mesh.texcoords:08X} and 0x{mesh.texcoord_buffer:08X} before writing.")
    else:
        uv_stride = max(struct.unpack_from("<h", uv_header, 4)[0], 1)
        original = len(uv_body) // uv_stride
        resized = resize_per_vertex(uv_body, original, len(placed))
        replacements[entry_index_of(mesh.texcoord_buffer)] = resized
        replacements[entry_index_of(mesh.texcoords)] = rewrite_header(uv_header, len(resized),
                                                                      0, "<I")
        notes.append(f"texcoords {len(uv_body):,} -> {len(resized):,} B at stride {uv_stride}")
    return replacements, notes


def main() -> int:
    tags = [a for a in sys.argv[1:] if a.startswith("0x")]
    if not tags:
        print(__doc__)
        return 2
    glb = Path(option("--glb", str(DEFAULT_GLB)))
    mesh_index = option("--mesh-index", 0)
    stretch = "--stretch" in sys.argv

    from parse_models import models

    by_tag = {m.tag: m for m in models}
    target = int(tags[0], 0)
    model = by_tag.get(target)
    if model is None:
        print(f"0x{target:08X} is not among the {len(models)} dumped models")
        return 1

    points, faces = load_all_meshes(glb)
    source = to_destiny(points)
    print(f"custom mesh   {len(source):,} verts, {len(faces):,} tris   from {glb.name}")
    print(f"target        0x{target:08X} mesh {mesh_index} of {len(model.meshes)}, "
          f"scale {model.scale[0]:.4f}")

    replacements, notes = build(model, mesh_index, source, faces, stretch)
    for note in notes:
        print(f"  {note}")
    # An entry belongs to whichever package its own tag names, and for one NPC body those are
    # several different packages - Zavala's model is in globals_0238 while his buffers are in
    # globals_01fe and globals_03ab. Grouping everything under the model's package would write the
    # buffers into a file that does not own them.
    by_package: dict[Path, dict[int, bytes]] = {}
    mesh = model.meshes[mesh_index]
    owners = {
        entry_index_of(mesh.position_buffer): mesh.position_buffer,
        entry_index_of(mesh.positions): mesh.positions,
        entry_index_of(mesh.index_buffer): mesh.index_buffer,
        entry_index_of(mesh.indices): mesh.indices,
        entry_index_of(model.tag): model.tag,
        entry_index_of(mesh.texcoord_buffer): mesh.texcoord_buffer,
        entry_index_of(mesh.texcoords): mesh.texcoords,
    }
    for index, blob in replacements.items():
        by_package.setdefault(package_of(owners[index]), {})[index] = blob

    print(f"\n{len(replacements)} entries across {len(by_package)} packages:")
    for path, entries in sorted(by_package.items()):
        print(f"  {path.name:26} {len(entries)} entries")

    if "--write" not in sys.argv:
        print("\ndry run; pass --write to install")
        return 0
    write_all(by_package, option("--sandbox", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
