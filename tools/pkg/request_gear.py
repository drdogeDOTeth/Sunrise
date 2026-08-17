"""
Samples entries from the biggest classes in gear and npc packages, to find skinned mesh geometry.

## Why sampling rather than guessing

`0x80807173` is the one decoded geometry class: a 48-byte `{size, count, stride}` header followed by
`count` packed vertices of `stride` bytes. It appears in 79 world families and **zero** gear
packages, because it is static geometry — armour is skinned and needs bone indices and weights, so
it cannot share a 16-byte stride.

Filtering for classes exclusive to gear and npcs only surfaces textures: 2-entry classes whose sizes
halve in a mip pyramid, 11,184,824 -> 5,592,424 -> 2,796,216. So the mesh class is shared with world
packages and cannot be found by which packages carry it.

What it *can* be found by is shape. If skinned meshes reuse the same container - and the header is
generic enough that they should - then any class whose entries begin with a plausible
`{size, count, stride}` triple is a buffer, whatever its stride. That test needs the decrypted
bytes, which needs one dump.

Entries are sampled across many classes rather than exhausting one, because the point is to identify
which class to pursue, not to extract it yet.

Usage:
    python request_gear.py [--per-class 6] [--classes 30]
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

from tigerpkg import Package, PackageError

PACKAGES = Path(r"C:\Sunrise\packages")
REQUEST = Path(r"C:\Sunrise\bin\x64\Sunrise\dump\request.txt")
# package_dump.cpp bounds a run at 256 requests.
REQUEST_LIMIT = 256
# Below this an entry is a descriptor, not a payload: too small to hold a mesh worth finding.
MIN_PAYLOAD = 512


def option(name: str, fallback):
    return type(fallback)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


per_class = option("--per-class", 6)
class_limit = option("--classes", 30)
wanted = option("--packages", "gear,npcs")
families = [part.strip().lower() for part in wanted.split(",") if part.strip()]

newest: dict[str, Path] = {}
for path in PACKAGES.glob("*.pkg"):
    stem = path.stem.rsplit("_", 1)[0]
    if not any(family in stem.lower() for family in families):
        continue
    if stem not in newest or patch_index(path) > patch_index(newest[stem]):
        newest[stem] = path
if not newest:
    raise SystemExit(f"no packages matching {wanted!r}")

# tag -> (class, size), grouped by class. Ranked by total bytes rather than entry count: ranking by
# count surfaces small schema records, and two earlier dump rounds found nothing but descriptors.
by_class: dict[int, list[tuple[int, int]]] = defaultdict(list)
for stem, source in sorted(newest.items()):
    try:
        pkg = Package(source)
    except PackageError:
        continue
    for entry in pkg.entries:
        if entry.size >= MIN_PAYLOAD and entry.reference >= 0x80800000:
            if entry.start_block < pkg.header.block_count:
                by_class[entry.reference].append((pkg.tag_for(entry.index), entry.size))

ranked = sorted(by_class, key=lambda c: -sum(size for _, size in by_class[c]))[:class_limit]

lines = [
    "# Sampled entries from the largest classes in gear and npc packages.",
    "# Looking for a 48-byte {size, count, stride} header - the vertex buffer container.",
    "",
]
requested = 0
for class_id in ranked:
    members = sorted(by_class[class_id], key=lambda pair: -pair[1])
    # Largest and a few mid-sized ones: a skinned mesh should be big, but an all-largest sample can
    # land entirely on one outlier object.
    picked = members[: max(1, per_class // 2)] + members[len(members) // 2:][: per_class // 2]
    seen: set[int] = set()
    for tag, size in picked:
        if tag in seen or requested >= REQUEST_LIMIT:
            continue
        seen.add(tag)
        lines.append(f"tag 0x{tag:08X}   # class 0x{class_id:08X}, {size:,} bytes")
        requested += 1
    if requested >= REQUEST_LIMIT:
        break

REQUEST.parent.mkdir(parents=True, exist_ok=True)
REQUEST.write_text("\r\n".join(lines), encoding="utf-8")

print(f"{len(newest)} packages, {len(by_class)} classes with payload entries")
print(f"{'class':>12} {'entries':>8} {'total MB':>9} {'largest':>12}")
for class_id in ranked[:12]:
    members = by_class[class_id]
    print(f"  0x{class_id:08X} {len(members):8,} {sum(s for _, s in members)/1e6:9.1f} "
          f"{max(s for _, s in members):12,}")
print(f"\nwrote {requested} requests across {min(len(ranked), class_limit)} classes to {REQUEST}")
