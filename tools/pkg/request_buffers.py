"""
Requests every vertex buffer of one package, so a mesh edit can preserve what it does not understand.

The first distortion attempt synthesised all 16 bytes of each vertex, zeroing the normal, tangent
and index fields along with the positions. The destination then hung in geometry precaching. That is
the same mistake the from-scratch package writer made and the patch-file writer avoided: regenerate
only what is understood, and inherit the rest verbatim.

Doing that needs the original bytes, which needs a dump — the buffers are encrypted like everything
else holding art.

Usage: python request_buffers.py [--package mercury_destination_03a7] [--limit 250]
"""
from __future__ import annotations

import sys
from pathlib import Path

from tigerpkg import Package, PackageError

PACKAGES = Path(r"C:\Sunrise\packages")
REQUEST = Path(r"C:\Sunrise\bin\x64\Sunrise\dump\request.txt")
VERTEX_BUFFER_CLASS = 0x80807173
HEADER_SIZE = 48
STRIDE = 16


def option(name: str, fallback):
    return type(fallback)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


wanted = option("--package", "mercury_destination_03a7")
limit = option("--limit", 250)


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


# A destination is split across several packages - Mercury's is four, holding 17, 65, 97 and 9
# buffers - and which one draws the ground in front of the player is not knowable from the tables.
# Patching one of four changed 22% of Mercury's vertices and was invisible in game, so the whole
# family is requested at once. 188 buffers still fit under the 256-request limit in package_dump.cpp.
newest: dict[str, Path] = {}
for path in PACKAGES.glob("*.pkg"):
    if wanted.lower() not in path.name.lower():
        continue
    stem = path.stem.rsplit("_", 1)[0]
    if stem not in newest or patch_index(path) > patch_index(newest[stem]):
        newest[stem] = path
if not newest:
    raise SystemExit(f"no package matching {wanted!r}")

lines = [f"# Every vertex buffer of packages matching {wanted!r}, for a positions-only edit.", ""]
requested = 0
vertices = 0
measured = 0
for stem, source in sorted(newest.items()):
    try:
        pkg = Package(source)
    except PackageError as exc:
        print(f"  {source.name}: {exc}")
        continue
    buffers = [
        e for e in pkg.entries
        if e.reference == VERTEX_BUFFER_CLASS
        and e.size > HEADER_SIZE
        and (e.size - HEADER_SIZE) % STRIDE == 0
        and e.start_block < pkg.header.block_count
    ]
    if not buffers:
        continue
    buffers = buffers[:max(0, limit - requested)]
    if not buffers:
        print(f"  request limit {limit} reached; {source.name} and later were left out")
        break
    lines.append(f"# {source.name}: {len(buffers)} buffers")
    for entry in buffers:
        count = (entry.size - HEADER_SIZE) // STRIDE
        lines.append(
            f"tag 0x{pkg.tag_for(entry.index):08X}   # entry {entry.index}, {count:,} vertices")
    print(f"  {source.name}: patch {pkg.header.patch_id}, {len(buffers)} buffers, "
          f"{sum((e.size - HEADER_SIZE) // STRIDE for e in buffers):,} vertices")
    requested += len(buffers)
    vertices += sum((e.size - HEADER_SIZE) // STRIDE for e in buffers)
    measured += sum(e.size for e in buffers)

if requested == 0:
    raise SystemExit(f"nothing matching {wanted!r} carries decodable vertex buffers")

REQUEST.parent.mkdir(parents=True, exist_ok=True)
REQUEST.write_text("\r\n".join(lines), encoding="utf-8")
print(f"\n{requested} buffers, {vertices:,} vertices, {measured:,} bytes")
print(f"  wrote {requested} requests to {REQUEST}")
