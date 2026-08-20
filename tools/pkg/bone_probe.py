"""Ask the shipped armour whether a bone palette is per-mesh, per-part, or global.

The whole `_18` failure rests on one belief: that a draw can only pose the bone indices its
original vertices already named, so a custom body has to be split across the chest, legs and
gauntlet slots. That belief was inferred from multi-variable failures, never tested.

The shipped data can answer it offline. If bone indices are a *global* skeleton index space,
the same index means the same joint wherever it appears and pieces overlap on shared joints.
If they are a *per-part palette*, parts of one mesh use disjoint index sets and some undecoded
field in the 0x20-byte part record selects the palette.

Prints, per part: the raw record, the twenty bytes `parse_models` does not decode, and the
exact bone set the vertices in that part's index range use.

Standalone on purpose - importing `parse_models` runs its 351-model census, which resolves four
buffer tags per mesh through the package entry tables and takes ~90s.

Usage:
    python bone_probe.py
    python bone_probe.py 0x80EFA1CA
"""
from __future__ import annotations

import collections
import struct
import sys
from pathlib import Path

from tigerpkg import HEADER_SIZE, TAG_BASE, TAG_ENTRY_BITS, TAG_ENTRY_MASK, Package, PackageError

DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
PACKAGES = Path(r"C:\Sunrise\packages")

PLAYABLE = {
    "chest_a": 0x80EFA1CA,
    "legs_a": 0x80EFA93B,
    "gauntlets_a": 0x80EF981E,
    "hood_a": 0x80EFA859,
    "class_a": 0x80EFA528,
}
FILLER = {254, 255}
MESH_ARRAY, MESH_STRIDE = 0x10, 0x88
PART_ARRAY, PART_STRIDE = 0x18, 0x20
ARRAY_HEADER = 0x10


def newest_packages() -> dict[int, Path]:
    newest: dict[str, Path] = {}
    for path in PACKAGES.glob("*.pkg"):
        stem, tail = path.stem.rsplit("_", 1)
        index = int(tail) if tail.isdigit() else -1
        best = newest.get(stem)
        if best is None or index > int(best.stem.rsplit("_", 1)[1]):
            newest[stem] = path
    index: dict[int, Path] = {}
    for path in newest.values():
        with open(path, "rb") as fh:
            head = fh.read(HEADER_SIZE)
        if len(head) >= HEADER_SIZE and struct.unpack_from("<H", head, 0)[0] == 38:
            index[struct.unpack_from("<H", head, 0x04)[0]] = path
    return index


INDEX = newest_packages()
_opened: dict[int, Package | None] = {}


def entry_of(tag: int):
    handle = tag - TAG_BASE
    if handle < 0:
        return None
    package_id, entry_index = handle >> TAG_ENTRY_BITS, handle & TAG_ENTRY_MASK
    if package_id not in _opened:
        path = INDEX.get(package_id)
        try:
            _opened[package_id] = Package(path) if path else None
        except PackageError:
            _opened[package_id] = None
    pkg = _opened[package_id]
    if pkg is None or entry_index >= len(pkg.entries):
        return None
    return pkg.entries[entry_index]


def buffer_of(header_tag: int) -> int:
    """@return Tag of the raw buffer a 12/24-byte buffer header points at, or 0."""
    header = entry_of(header_tag)
    return header.reference if header else 0


def dumped(tag: int) -> bytes | None:
    path = DUMP / f"tag_{tag:08X}.bin"
    return path.read_bytes() if path.is_file() else None


def stride_of(header_tag: int) -> int:
    data = dumped(header_tag)
    if data is None or len(data) < 12:
        return 0
    return struct.unpack_from("<h", data, 4)[0]


def array_at(data: bytes, at: int) -> tuple[int, int]:
    if at + 16 > len(data):
        return 0, 0
    count, relative = struct.unpack_from("<qq", data, at)
    start = at + 8 + relative + ARRAY_HEADER
    if count <= 0 or start <= 0 or start > len(data):
        return 0, 0
    return count, start


def meshes_of(data: bytes) -> list[dict]:
    out: list[dict] = []
    count, start = array_at(data, MESH_ARRAY)
    for index in range(min(count, 64)):
        at = start + index * MESH_STRIDE
        if at + MESH_STRIDE > len(data):
            break
        positions, _texcoords, _weights = struct.unpack_from("<3I", data, at)
        (indices,) = struct.unpack_from("<I", data, at + 0x10)
        part_count, parts_at = array_at(data, at + PART_ARRAY)
        parts = []
        for part_index in range(min(part_count, 256)):
            part = parts_at + part_index * PART_STRIDE
            if part + PART_STRIDE > len(data):
                break
            (primitive,) = struct.unpack_from("<h", data, part + 0x06)
            offset, indices_count = struct.unpack_from("<II", data, part + 0x08)
            parts.append((part, primitive, offset, indices_count, data[part + 0x1B]))
        out.append({"positions": positions, "indices": indices, "parts": parts})
    return out


def bones_of_vertex(body: bytes, at: int, stride: int) -> list[int]:
    """@return Bone indices carrying nonzero weight on one vertex."""
    if stride == 16:
        return [body[at + 12 + s] for s in range(4)
                if body[at + 8 + s] and body[at + 12 + s] not in FILLER]
    if stride == 12:
        out = []
        if body[at + 10] and body[at + 8] not in FILLER:
            out.append(body[at + 8])
        if body[at + 11] and body[at + 9] not in FILLER:
            out.append(body[at + 9])
        return out
    return []


def part_vertices(index_bytes: bytes, offset: int, count: int) -> set[int]:
    total = len(index_bytes) // 2
    stop = min(offset + count, total)
    if offset >= stop:
        return set()
    return {struct.unpack_from("<H", index_bytes, at * 2)[0] for at in range(offset, stop)}


def probe(name: str, tag: int) -> None:
    data = dumped(tag)
    if data is None:
        print(f"{name} 0x{tag:08X}: not dumped")
        return
    meshes = meshes_of(data)
    print(f"\n=== {name}  0x{tag:08X}  {len(meshes)} meshes ===")
    for mesh_index, mesh in enumerate(meshes):
        stride = stride_of(mesh["positions"])
        position_buffer = buffer_of(mesh["positions"])
        index_buffer = buffer_of(mesh["indices"])
        body = dumped(position_buffer)
        index_bytes = dumped(index_buffer)
        verts = len(body) // stride if body and stride else 0
        print(f"\n  mesh {mesh_index}: stride {stride}  {verts:,} verts  "
              f"{len(mesh['parts'])} parts  pos 0x{position_buffer:08X} idx 0x{index_buffer:08X}"
              f"{'' if index_bytes else '  [index buffer not dumped]'}")
        if not body or not stride:
            continue
        whole: collections.Counter[int] = collections.Counter()
        for at in range(0, len(body) - stride + 1, stride):
            whole.update(bones_of_vertex(body, at, stride))
        print(f"    mesh bone set: {sorted(whole)}")
        if index_bytes is None:
            continue
        for part_index, (at, primitive, offset, count, lod) in enumerate(mesh["parts"]):
            raw = data[at:at + PART_STRIDE]
            bones: collections.Counter[int] = collections.Counter()
            for vertex in part_vertices(index_bytes, offset, count):
                spot = vertex * stride
                if spot + stride <= len(body):
                    bones.update(bones_of_vertex(body, spot, stride))
            print(f"    part {part_index:>2} lod {lod:>2} prim {primitive} "
                  f"idx {offset:>6}+{count:<6}  bones {sorted(bones)}")
            print(f"        raw {raw.hex()}")


def main() -> None:
    wanted = [a for a in sys.argv[1:] if a.startswith("0x")]
    if wanted:
        for text in wanted:
            probe(text, int(text, 0))
        return
    for name, tag in PLAYABLE.items():
        probe(name, tag)


if __name__ == "__main__":
    main()
