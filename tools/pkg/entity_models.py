"""
Censuses entity models and their buffer headers offline, and requests models for dumping.

## The Shadowkeep schema, from Charm

Charm carries a per-version schema and its `DESTINY2_SHADOWKEEP_2601` strategy is exactly our build.
That matters because **class ids move between Destiny versions**: MontevenDynamicExtractor targets
Beyond Light, where the entity class is `0x80809AD8`, and that id appears **zero** times in this
install. The Shadowkeep ids are:

| class | struct | size | what |
|---|---|---|---|
| `0x80809C0F` | `SEntity` | 0xA0 | entity root, holds an entity-resource array at 0x10 |
| `0x80809C36` | entity resource | 0xA0 | one resource; a model resource names the model |
| `0x808073A5` | `SEntityModel` | 0xA0 | mesh array at 0x10, model scale/translation at 0x50 |
| `0x80807378` | `SEntityModelMesh` | 0x88 | one mesh: buffer tags then a part table |

`SEntityModelMesh` is the payload, 0x88 bytes per record:

    0x00  positions vertex buffer header tag
    0x04  texcoord/normal vertex buffer header tag
    0x08  old-weights vertex buffer header tag
    0x0C  unused, always 0xFFFFFFFF
    0x10  index buffer header tag
    0x18  part array: count at 0x18, self-relative offset at 0x20
    0x28  48 shorts of stage part offsets

That last field is what pins the record size: `0x28 + 48*2 == 0x88`, exactly Charm's declared size.

## Why the buffers cannot be found by shape alone

A **vertex buffer header** is a 12-byte entry, `{DataSize, Stride, Type, Deadbeef}`, whose
`reference` field in the entry table is the tag of the raw buffer. An **index buffer header** is 24
bytes. The buffers themselves are untyped byte arrays, so nothing distinguishes a position buffer
from a weight buffer except the header that points at it - and the stride lives in the header's
*body*, which is encrypted.

An earlier version of this tool tried to pair buffers by size arithmetic alone, looking for entries
sized `16n` and `8n`. Across a whole install that matches 41,488 times, almost all of it noise:
textures are divisible by 16 too. Shape is not enough; the mesh table has to be read. So the census
below is honest about what it can and cannot settle without a dump.

Usage:
    python entity_models.py                              # census every package
    python entity_models.py --packages investment,globals
    python entity_models.py --request investment_0361,investment_01d3
"""
from __future__ import annotations

import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

from tigerpkg import HEADER_SIZE, TAG_BASE, TAG_ENTRY_BITS, TAG_ENTRY_MASK, Package, PackageError

PACKAGES = Path(r"C:\Sunrise\packages")
REQUEST = Path(r"C:\Sunrise\bin\x64\Sunrise\dump\request.txt")
# package_dump.cpp bounds a run at this many requests; raised from 256 so both gear packages fit in
# one launch, since every extra pass costs the user a whole game start.
REQUEST_LIMIT = 1024

ENTITY = 0x80809C0F
ENTITY_RESOURCE = 0x80809C36
ENTITY_MODEL = 0x808073A5

VERTEX_HEADER_SIZE = 12
INDEX_HEADER_SIZE = 24

TAG_MIN, TAG_MAX = 0x80800000, 0x80FFFFFF
# Class ids cluster below this; a reference above it in a stub entry is a pointer to real data.
POINTER_MIN = 0x80801000


def option(name: str, fallback):
    return type(fallback)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


def newest_packages() -> dict[str, Path]:
    """@return Package stem -> newest patch file, which owns the authoritative tables."""
    newest: dict[str, Path] = {}
    for path in PACKAGES.glob("*.pkg"):
        stem = path.stem.rsplit("_", 1)[0]
        if stem not in newest or patch_index(path) > patch_index(newest[stem]):
            newest[stem] = path
    return newest


def build_index(newest: dict[str, Path]) -> dict[int, Path]:
    """@return Package id -> file. Buffer tags cross package boundaries, so index everything."""
    index: dict[int, Path] = {}
    for path in newest.values():
        try:
            with open(path, "rb") as fh:
                head = fh.read(HEADER_SIZE)
        except OSError:
            continue
        if len(head) < HEADER_SIZE or struct.unpack_from("<H", head, 0)[0] != 38:
            continue
        index[struct.unpack_from("<H", head, 0x04)[0]] = path
    return index


def decode(tag: int) -> tuple[int, int]:
    """@return `(package id, entry index)` for a tag handle."""
    handle = tag - TAG_BASE
    return handle >> TAG_ENTRY_BITS, handle & TAG_ENTRY_MASK


ALL = newest_packages()
INDEX = build_index(ALL)
_opened: dict[int, Package | None] = {}


def package_by_id(package_id: int) -> Package | None:
    if package_id not in _opened:
        path = INDEX.get(package_id)
        try:
            _opened[package_id] = Package(path) if path else None
        except PackageError:
            _opened[package_id] = None
    return _opened[package_id]


def buffer_size(header_tag: int) -> int:
    """@return Size of the raw buffer a header points at, or 0 when it does not resolve."""
    if not POINTER_MIN <= header_tag <= TAG_MAX:
        return 0
    package_id, entry_index = decode(header_tag)
    target = package_by_id(package_id)
    if target is None or entry_index >= len(target.entries):
        return 0
    return target.entries[entry_index].size


def request_models(stems: list[str]) -> None:
    """Writes a dump request for every entity model in the named packages.

    Model headers are small, so a run comfortably covers hundreds of them, and each one yields the
    buffer tags of all its meshes. That is the step that turns the offline census into real geometry.
    """
    lines = [
        "# Entity models (class 0x808073A5) - Shadowkeep SEntityModel, 0xA0 header.",
        "# Mesh array at 0x10; each 0x88 record names its vertex and index buffer headers.",
        "",
    ]
    requested = 0
    for stem in stems:
        source = ALL.get(stem)
        if source is None:
            matches = [name for name in ALL if stem in name]
            if len(matches) != 1:
                raise SystemExit(f"{stem!r} matches {len(matches)} packages; name one exactly")
            source = ALL[matches[0]]
        pkg = Package(source)
        for entry in pkg.entries:
            if entry.reference != ENTITY_MODEL or requested >= REQUEST_LIMIT:
                continue
            lines.append(f"tag 0x{pkg.tag_for(entry.index):08X}   "
                         f"# {source.stem}, model, {entry.size:,} bytes")
            requested += 1
    REQUEST.parent.mkdir(parents=True, exist_ok=True)
    REQUEST.write_text("\r\n".join(lines), encoding="utf-8")
    print(f"wrote {requested} model requests to {REQUEST}")
    if requested >= REQUEST_LIMIT:
        print(f"capped at the {REQUEST_LIMIT}-request limit; rerun with fewer packages for the rest")


if "--request" in sys.argv:
    request_models([part.strip() for part in option("--request", "").split(",") if part.strip()])
    raise SystemExit(0)

wanted = option("--packages", "")
families = [part.strip().lower() for part in wanted.split(",") if part.strip()]

rows = []
strays = Counter()
for stem, source in sorted(ALL.items()):
    if families and not any(family in stem.lower() for family in families):
        continue
    try:
        pkg = Package(source)
    except PackageError:
        continue
    counts = Counter(entry.reference for entry in pkg.entries)
    models = counts.get(ENTITY_MODEL, 0)
    vertex_headers = 0
    index_headers = 0
    vertex_bytes = 0
    for entry in pkg.entries:
        if not POINTER_MIN <= entry.reference <= TAG_MAX:
            continue
        if entry.size == VERTEX_HEADER_SIZE:
            vertex_headers += 1
            vertex_bytes += buffer_size(entry.reference)
        elif entry.size == INDEX_HEADER_SIZE:
            index_headers += 1
        else:
            strays[entry.size] += 1
    if models or vertex_headers:
        rows.append((models, stem, counts.get(ENTITY, 0), counts.get(ENTITY_RESOURCE, 0),
                     vertex_headers, index_headers, vertex_bytes))

rows.sort(reverse=True)
print(f"{'models':>7} {'package':<32} {'entities':>9} {'resources':>10} "
      f"{'vtx hdr':>8} {'idx hdr':>8} {'vtx MB':>8}")
for models, stem, entities, resources, vertex_headers, index_headers, vertex_bytes in rows[:30]:
    print(f"{models:7,} {stem:<32} {entities:9,} {resources:10,} "
          f"{vertex_headers:8,} {index_headers:8,} {vertex_bytes / 1e6:8.1f}")

print(f"\n{len(rows)} packages carry models or buffer headers")
print("other pointer-stub sizes seen (not buffer headers): "
      + ", ".join(f"{size}B x{count:,}" for size, count in strays.most_common(6)))
print("\nBuffer pairing needs the mesh table, which is encrypted. Dump models next:")
print("  python entity_models.py --request investment_0361,investment_01d3")
