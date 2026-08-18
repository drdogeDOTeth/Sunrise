"""
Injects a custom mesh into a Destiny entity model, geometry and topology both.

## Why this and not a resample

`fit_mask.py` resamples a custom mesh onto the target's existing vertex layout. That keeps the
vertex count fixed, which was the safe first step, but it cannot work here: a helmet is a closed
shell and a gas mask is an open face plate, so ~9% of the helmet's vertices point in directions
where the custom mesh has no surface at all. No fill scale changes that - swept 1.0x to 1.9x with
the miss count constant.

The vertex count was never a hard limit, only a self-imposed one. We write the package, so the
custom mesh's own vertices and its own triangles can go in, and every entry involved gets *smaller*:

| entry | helmet 0x80BA7474 | gas mask |
|---|---|---|
| position buffer | 34,664 B | 18,896 B |
| index buffer | 28,568 B | 23,160 B |
| vertex header | 12 B | 12 B |
| index header | 24 B | 24 B |
| model | 1,120 B | 1,120 B |

## What has to change together

Five entries, in one patch file, because only the newest file of a package is a legal base:

1. **Position buffer** - packed int16 at the model's *existing* scale and translation. The custom
   mesh's half-extents are 0.086 x 0.082 x 0.112 against the model's 0.1796, so it fits the packed
   range with room to spare and the model header needs no edit at all.
2. **Vertex header** - the original 12 bytes with only `DataSize` changed. Stride stays 8.
3. **Index buffer** - 16-bit triangle indices.
4. **Index header** - the original 24 bytes with only `DataSize` changed.
5. **Model** - part 0 rewritten to cover the new triangles, every other part's index count zeroed.

That last one matters: the helmet's parts are **triangle strips** (primitive 5) and there are 24 of
them, 8 real parts repeated across 3 material stages. Part 0 becomes primitive 3 - a triangle list -
and the rest draw nothing.

`w` is inherited as the value the original overwhelmingly uses (18, on 4,273 of 4,333 vertices). It
is not a homogeneous coordinate, so writing 1.0 there would be wrong.

Usage:
    python inject_mesh.py 0x80BA7474 --dry-run
    python inject_mesh.py 0x80BA7474 --glb <path.glb> --mesh GasMask
    python inject_mesh.py --all-helmets
    python inject_mesh.py --undo
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np

from glb import load_mesh
from parse_models import INDEX, TAG_BASE, TAG_ENTRY_BITS, TAG_ENTRY_MASK, models, option
from extract_mesh import dumped, vertex_stride
from patch import write_patch_package_multi, verify_patch
from tigerpkg import Package

# Every file this tool adds is recorded here so undoing is one command. Nothing shipped is ever
# modified, moved or truncated - a patch file is only ever *added* - so deleting what the receipt
# names puts the install back byte for byte.
RECEIPT = Path(__file__).with_name("inject_receipt.json")

PACKED_MAX = 32767.0
TRIANGLES = 3
DEFAULT_GLB = Path(r"C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb")
# The value the target uses for the fourth position component on almost every vertex. Not a
# homogeneous w - it is small and near-constant - so it is copied rather than computed.
INHERITED_W = 18
PART_STRIDE = 0x20
# Offsets inside one part record.
PART_PRIMITIVE = 0x06
PART_INDEX_OFFSET = 0x08
PART_INDEX_COUNT = 0x0C
PART_LOD = 0x1B


def entry_index_of(tag: int) -> int:
    return (tag - TAG_BASE) & TAG_ENTRY_MASK


def package_of(tag: int) -> Path:
    package_id = (tag - TAG_BASE) >> TAG_ENTRY_BITS
    path = INDEX.get(package_id)
    if path is None:
        raise SystemExit(f"no package for id 0x{package_id:04X}")
    return path


def to_destiny(points: np.ndarray) -> np.ndarray:
    """@return glTF Y-up points remapped to Destiny's Z-up, X-forward frame."""
    return np.stack([points[:, 2], points[:, 0], points[:, 1]], axis=1)


def pack_positions(points: np.ndarray, stride: int, scale: float, translation) -> bytes:
    """@return A position buffer of `len(points)` vertices, packed at the model's own quantisation."""
    packed = np.clip(np.round((points - np.asarray(translation)) / scale * PACKED_MAX),
                     -32768, 32767).astype(np.int16)
    out = bytearray(len(points) * stride)
    for index, (x, y, z) in enumerate(packed):
        struct.pack_into("<4h", out, index * stride, int(x), int(y), int(z), INHERITED_W)
    return bytes(out)


def pack_indices(faces: np.ndarray) -> bytes:
    out = bytearray(faces.size * 2)
    for index, value in enumerate(faces.reshape(-1)):
        struct.pack_into("<H", out, index * 2, int(value))
    return bytes(out)


def rewrite_header(original: bytes, data_size: int, at: int, width: str) -> bytes:
    """@return The header with only its `DataSize` field replaced, everything else inherited."""
    out = bytearray(original)
    struct.pack_into(width, out, at, data_size)
    return bytes(out)


def rewrite_model(model, mesh, index_count: int) -> bytes:
    """@return The model blob with part 0 covering the new triangles and every other part silenced."""
    out = bytearray(model.data)
    for slot in range(mesh.part_count):
        at = mesh.parts_at + slot * PART_STRIDE
        if slot == 0:
            struct.pack_into("<h", out, at + PART_PRIMITIVE, TRIANGLES)
            struct.pack_into("<I", out, at + PART_INDEX_OFFSET, 0)
            struct.pack_into("<I", out, at + PART_INDEX_COUNT, index_count)
            out[at + PART_LOD] = 0
        else:
            struct.pack_into("<I", out, at + PART_INDEX_COUNT, 0)
    return bytes(out)



def build_replacements(model, source: np.ndarray, faces: np.ndarray) -> dict[int, bytes]:
    """@return Entry index -> new bytes, for the five entries one model needs.

    @param source Custom mesh vertices, already in Destiny's frame and centred on nothing in
                  particular; this recentres them on the model's own box so the mask lands on the
                  head rather than wherever the donor scene put it.
    """
    mesh = model.meshes[0]
    stride = vertex_stride(mesh.positions)
    vertex_header = dumped(mesh.positions)
    index_header = dumped(mesh.indices)
    if not stride or vertex_header is None or index_header is None:
        raise SystemExit(f"0x{model.tag:08X}: buffer headers not dumped")

    scale = model.scale[0]
    translation = np.asarray(model.translation[:3])
    low, high = source.min(axis=0), source.max(axis=0)
    placed = source - (low + high) / 2 + translation
    half = (high - low) / 2
    if (half > scale).any():
        raise SystemExit(f"0x{model.tag:08X}: half-extents {np.round(half, 4)} exceed scale "
                         f"{scale:.4f}; the model header would need rewriting too")
    if len(source) > 0xFFFF:
        raise SystemExit(f"{len(source):,} vertices needs 32-bit indices, which this does not write")

    positions = pack_positions(placed, stride, scale, translation)
    indices = pack_indices(faces)
    return {
        entry_index_of(mesh.position_buffer): positions,
        entry_index_of(mesh.positions): rewrite_header(vertex_header, len(positions), 0, "<I"),
        entry_index_of(mesh.index_buffer): indices,
        entry_index_of(mesh.indices): rewrite_header(index_header, len(indices), 8, "<q"),
        entry_index_of(model.tag): rewrite_model(model, mesh, faces.size),
    }


def injectable(model) -> bool:
    """@return True when a model is a single-mesh helmet whose buffers were dumped."""
    if not model.helmet_like or len(model.meshes) != 1:
        return False
    mesh = model.meshes[0]
    return bool(vertex_stride(mesh.positions)) and dumped(mesh.indices) is not None


def undo() -> None:
    """Removes every patch file this tool added, and clears the validation caches."""
    if not RECEIPT.is_file():
        print("no receipt found; nothing this tool wrote is recorded")
        return
    for name in json.loads(RECEIPT.read_text())["written"]:
        written = Path(name)
        if written.is_file():
            written.unlink()
            print(f"removed {written.name}")
        else:
            print(f"{written.name} was already gone")
    RECEIPT.unlink()
    from gametest import clear_caches
    print("install is back to its shipped state; clearing validation caches:")
    clear_caches()


def main() -> None:
    if "--undo" in sys.argv:
        undo()
        return
    tags = [argument for argument in sys.argv[1:] if argument.startswith("0x")]
    if "--all-helmets" in sys.argv:
        chosen = [model for model in models if injectable(model)]
    elif tags:
        keep = {int(argument, 0) for argument in tags}
        chosen = [model for model in models if model.tag in keep]
        if not chosen:
            raise SystemExit(f"none of {tags} are among the {len(models)} dumped models")
    else:
        raise SystemExit(__doc__)

    positions, faces = load_mesh(option("--glb", str(DEFAULT_GLB)), option("--mesh", "GasMask"))
    source = to_destiny(np.asarray(positions, dtype=np.float64))
    if faces.max() >= len(source):
        raise SystemExit(f"index {faces.max()} exceeds {len(source)} vertices")

    # Grouped by package: only the newest file of a package is a legal base, so every model living
    # in the same package has to be redirected in one patch file rather than a chain of them.
    by_package: dict[Path, dict[int, bytes]] = {}
    for model in chosen:
        if len(model.meshes) != 1:
            print(f"  skipping 0x{model.tag:08X}: {len(model.meshes)} meshes")
            continue
        by_package.setdefault(package_of(model.tag), {}).update(
            build_replacements(model, source, faces))

    print(f"custom mesh {len(source):,} verts, {len(faces):,} tris")
    print(f"{len(chosen)} models across {len(by_package)} packages")
    for path, replacements in sorted(by_package.items()):
        print(f"  {path.name}: {len(replacements)} entries")

    if "--dry-run" in sys.argv:
        return

    sandbox = option("--sandbox", "")
    for path, replacements in sorted(by_package.items()):
        target = path
        if sandbox:
            # Write into a copy first. Anything we write is plain, so it reads back offline without
            # keys, and a bad patch never reaches the install.
            from patch import copy_family
            target = copy_family(path, sandbox)
        write_patch_package_multi(target, replacements)
        opened = Package(target)
        written = target.with_name(f"{opened.stem}_{opened.header.patch_id + 1}.pkg")
        for entry, data in replacements.items():
            verify_patch(written, entry, data)
        print(f"  wrote and verified {len(replacements)} entries -> {written}")
        if not sandbox:
            done = json.loads(RECEIPT.read_text())["written"] if RECEIPT.is_file() else []
            RECEIPT.write_text(json.dumps({"written": sorted(set(done) | {str(written)})}, indent=2))


if __name__ == "__main__":
    main()
