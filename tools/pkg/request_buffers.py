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


matches = [p for p in PACKAGES.glob("*.pkg") if wanted.lower() in p.name.lower()]
if not matches:
    raise SystemExit(f"no package matching {wanted!r}")
source = max(matches, key=patch_index)

try:
    pkg = Package(source)
except PackageError as exc:
    raise SystemExit(f"{source.name}: {exc}")

buffers = [
    e for e in pkg.entries
    if e.reference == VERTEX_BUFFER_CLASS
    and e.size > HEADER_SIZE
    and (e.size - HEADER_SIZE) % STRIDE == 0
    and e.start_block < pkg.header.block_count
]
if not buffers:
    raise SystemExit(f"{source.name} carries no decodable vertex buffers")

buffers = buffers[:limit]
lines = [
    f"# Every vertex buffer of {source.name}, for a positions-only edit.",
    f"# {len(buffers)} buffers, "
    f"{sum((e.size - HEADER_SIZE) // STRIDE for e in buffers):,} vertices.",
    "",
]
for entry in buffers:
    count = (entry.size - HEADER_SIZE) // STRIDE
    lines.append(f"tag 0x{pkg.tag_for(entry.index):08X}   # entry {entry.index}, {count:,} vertices")

REQUEST.parent.mkdir(parents=True, exist_ok=True)
REQUEST.write_text("\r\n".join(lines), encoding="utf-8")

print(f"{source.name}: patch {pkg.header.patch_id}, {len(buffers)} vertex buffers")
print(f"  {sum((e.size - HEADER_SIZE) // STRIDE for e in buffers):,} vertices, "
      f"{sum(e.size for e in buffers):,} bytes")
print(f"  wrote {len(buffers)} requests to {REQUEST}")
