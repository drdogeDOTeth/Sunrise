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
    python inject_mesh.py --all-helmets --rewrite-uvs
    python inject_mesh.py 0x80C714B2 --glb <path.glb> --mesh Body --silence-other-meshes
    python inject_mesh.py --request-uvs
    python inject_mesh.py --undo
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np

from glb import load_mesh
from parse_models import (
    INDEX,
    REQUEST_DIR,
    TAG_BASE,
    TAG_ENTRY_BITS,
    TAG_ENTRY_MASK,
    TAG_MAX,
    TAG_MIN,
    models,
    option,
)
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
PART_MATERIAL = 0x00
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


def resize_per_vertex(original: bytes, old_count: int, new_count: int) -> bytes:
    """@return `new_count` vertices of the same stride, tiled from `original` if needed.

    The game indexes this buffer by vertex. Tiling keeps every byte a copy of something it
    already accepted, so a longer custom mesh does not read off the end. UVs will not match
    the mask; the point is to keep the loader alive.
    """
    if old_count <= 0 or len(original) < old_count:
        raise SystemExit(f"cannot resize a {len(original)}-byte buffer of {old_count} vertices")
    stride = len(original) // old_count
    tiled = original if new_count <= old_count else original * ((new_count + old_count - 1) // old_count)
    return tiled[: new_count * stride]


def rewrite_model(model, mesh, index_count: int, silence_other_meshes: bool = False) -> bytes:
    """
    @param silence_other_meshes Zero every part of the model's *other* meshes as well.
    @return The model blob with part 0 covering the new triangles and every other part silenced.

    A character model is usually a body mesh plus separate armour meshes drawn over it. Replacing
    only the body leaves those plates floating around the custom mesh - the same failure as the
    stock gloves and bald race head that still overlay the custom Warlock. Zavala is the worked
    case: mesh 0 is a complete 6,147-vertex humanoid and mesh 2 is 12,367 vertices of disconnected
    armour plating, so a swap has to silence 2 to show 0.

    This stays size-preserving - it only zeroes index counts inside a record that already exists -
    so it carries none of the resize risk that a record whose declared length changes does.
    """
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
    if silence_other_meshes:
        for other in model.meshes:
            if other is mesh:
                continue
            for slot in range(other.part_count):
                struct.pack_into("<I", out, other.parts_at + slot * PART_STRIDE
                                 + PART_INDEX_COUNT, 0)
    return bytes(out)



def build_replacements(model, source: np.ndarray, faces: np.ndarray,
                       rewrite_uvs: bool = False,
                       silence_other_meshes: bool = False) -> dict[int, bytes]:
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
    replacements = {
        entry_index_of(mesh.position_buffer): positions,
        entry_index_of(mesh.positions): rewrite_header(vertex_header, len(positions), 0, "<I"),
        entry_index_of(mesh.index_buffer): indices,
        entry_index_of(mesh.indices): rewrite_header(index_header, len(indices), 8, "<q"),
        entry_index_of(model.tag): rewrite_model(model, mesh, faces.size, silence_other_meshes),
    }
    # UV rewrite is opt-in. Tiling that buffer across 40 models hung the tower; the 19-model
    # inject that left UVs alone loaded clean.
    if rewrite_uvs:
        uv_header = dumped(mesh.texcoords)
        uv_body = dumped(mesh.texcoord_buffer)
        if uv_header is None or not uv_body:
            raise SystemExit(f"0x{model.tag:08X}: --rewrite-uvs needs a dumped texcoord buffer")
        original = len(uv_body) // max(struct.unpack_from("<h", uv_header, 4)[0], 1)
        if original == 0:
            original = mesh.position_bytes // stride
        resized = resize_per_vertex(uv_body, original, len(source))
        replacements[entry_index_of(mesh.texcoord_buffer)] = resized
        replacements[entry_index_of(mesh.texcoords)] = rewrite_header(
            uv_header, len(resized), 0, "<I")
    return replacements


def blank_model(model) -> bytes:
    """@return The model blob with every part's index count zeroed, so it draws nothing.

    Hiding a mesh needs no new capability: a part with no indices issues no draw. That makes it a
    cheap probe. Blanking costs **one** entry per model - no buffers, no headers - so a whole
    package can be silenced in a single patch file, and whichever piece of armour disappears in game
    is the one whose model lives there.
    """
    out = bytearray(model.data)
    for mesh in model.meshes:
        for slot in range(mesh.part_count):
            struct.pack_into("<I", out, mesh.parts_at + slot * PART_STRIDE + PART_INDEX_COUNT, 0)
    return bytes(out)


def has_room(model, vertices: int, rewrite_uvs: bool = False) -> bool:
    """@return True when every per-vertex buffer we do *not* rewrite can still be indexed.

    Positions and indices are always replaced. The texcoord/normal buffer is inherited
    unless `--rewrite-uvs` is set. A 2,362-vertex mask on a 1,172-vertex helmet seeks
    past a stride-24 UV buffer and hangs the tower — that is why this check exists.
    """
    mesh = model.meshes[0]
    stride = vertex_stride(mesh.positions)
    if not stride:
        return False
    original = mesh.position_bytes // stride
    if original <= 0:
        return False
    if not rewrite_uvs:
        return vertices <= original and (mesh.texcoord_bytes == 0
                                         or vertices * (mesh.texcoord_bytes // original)
                                         <= mesh.texcoord_bytes)
    if mesh.weight_bytes == 0:
        return True
    return vertices * (mesh.weight_bytes // original) <= mesh.weight_bytes


def head_like(model) -> bool:
    """@return True for a hard-hat or a hood: head height, span up to a cowl."""
    return (1.55 <= model.height <= 2.05 and 0.15 <= model.span <= 0.80
            and model.vertices >= 300)


def injectable(model) -> bool:
    """@return True when mesh 0 of a head model has the buffers a write needs.

    Multi-mesh heads (antlers, visors) keep every mesh after 0 untouched.
    """
    if not head_like(model) or not model.meshes:
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
    if "--request-uvs" in sys.argv:
        lines = [
            "# Texcoord header + body for every single-mesh head model still missing them.",
            "# Also slot 4 of the investment root, the last 4301-row size match.",
            "",
            "# slot 4  class 0x808076F0  1,470,710 B",
            "tag 0x81327CF0",
            "",
        ]
        seen: set[int] = set()
        missing = 0
        for model in models:
            if not injectable(model):
                continue
            mesh = model.meshes[0]
            if dumped(mesh.texcoords) is not None and dumped(mesh.texcoord_buffer) is not None:
                continue
            missing += 1
            lines.append(f"# model 0x{model.tag:08X}  {model.vertices:,} verts")
            for tag in (mesh.texcoords, mesh.texcoord_buffer):
                if TAG_MIN <= tag <= TAG_MAX and tag not in seen:
                    seen.add(tag)
                    lines.append(f"tag 0x{tag:08X}")
        request = REQUEST_DIR / "request.txt"
        request.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
        print(f"wrote {len(seen) + 1} requests for {missing} models plus slot 4 to {request}")
        return
    tags = [argument for argument in sys.argv[1:] if argument.startswith("0x")]
    rewrite_uvs = "--rewrite-uvs" in sys.argv
    if "--blank-others" in sys.argv:
        # One launch, maximum information: the mask goes into every injectable helmet, and every
        # other dumped model in the same packages is silenced. Whatever the equipped helmet does -
        # turn into a mask, vanish, or stay put - names the package its model lives in.
        chosen = [model for model in models if injectable(model)]
        blanked = [model for model in models if model not in chosen]
        by_package: dict[Path, dict[int, bytes]] = {}
        positions, faces = load_mesh(option("--glb", str(DEFAULT_GLB)), option("--mesh", "GasMask"))
        source = to_destiny(np.asarray(positions, dtype=np.float64))
        for model in chosen:
            by_package.setdefault(package_of(model.tag), {}).update(
                build_replacements(model, source, faces, rewrite_uvs))
        for model in blanked:
            by_package.setdefault(package_of(model.tag), {})[entry_index_of(model.tag)] =                 blank_model(model)
        print(f"{len(chosen)} models get the mask, {len(blanked)} are blanked")
        for path, replacements in sorted(by_package.items()):
            print(f"  {path.name}: {len(replacements)} entries")
        if "--dry-run" in sys.argv:
            return
        write_all(by_package, option("--sandbox", ""))
        return

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

    cramped = [model for model in chosen if not has_room(model, len(source), rewrite_uvs)]
    chosen = [model for model in chosen if has_room(model, len(source), rewrite_uvs)]
    if cramped:
        print(f"skipping {len(cramped)} models with too few vertices to index safely:")
        for model in sorted(cramped, key=lambda m: m.vertices)[:6]:
            print(f"  0x{model.tag:08X}  {model.vertices:,} verts < {len(source):,}")

    # Grouped by package: only the newest file of a package is a legal base, so every model living
    # in the same package has to be redirected in one patch file rather than a chain of them.
    silence = "--silence-other-meshes" in sys.argv
    by_package: dict[Path, dict[int, bytes]] = {}
    for model in chosen:
        by_package.setdefault(package_of(model.tag), {}).update(
            build_replacements(model, source, faces, rewrite_uvs, silence))

    print(f"custom mesh {len(source):,} verts, {len(faces):,} tris")
    if silence:
        hidden = sum(len(m.meshes) - 1 for m in chosen)
        print(f"silencing {hidden} other mesh(es) so armour does not draw over the custom body")
    print(f"{len(chosen)} models across {len(by_package)} packages")
    for path, replacements in sorted(by_package.items()):
        print(f"  {path.name}: {len(replacements)} entries")

    if "--dry-run" in sys.argv:
        return

    write_all(by_package, option("--sandbox", ""))


def write_all(by_package: dict[Path, dict[int, bytes]], sandbox: str) -> None:
    """Writes one patch file per package and verifies every entry it redirected."""
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
