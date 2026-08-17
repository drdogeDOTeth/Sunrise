"""
Rescales the positions of dumped vertex buffers, leaving every other byte exactly as shipped.

## Why this exists rather than `distort.py`

`distort.py` synthesised all 16 bytes of each vertex, zeroing the normal, tangent and index fields
along with the positions. Mercury then hung in geometry precaching and never finished loading, while
the same destination on a clean install loaded normally — so the synthetic fields were the cause,
not the write pipeline.

That is the same mistake the from-scratch package writer made. The patch-file writer only works
because it inherits every header field it does not understand; this does the same for vertices.
Bytes 0-5 are the `int16` position and are rewritten. Bytes 6-15 are copied verbatim.

Positions are normalised `int16`, so scaling **down** cannot overflow the range. Scaling up could
wrap and produce exactly the kind of degenerate geometry that hung the loader, which is why the
factor is clamped to at most 1.0.

Undo with `python gametest.py --undo`.

Usage:
    python reshape.py [--package mercury_destination_03a7] [--scale 0.5]
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from patch import write_patch_package_multi
from tigerpkg import TAG_BASE, TAG_ENTRY_MASK, Package, PackageError

PACKAGES = Path(r"C:\Sunrise\packages")
DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
RECEIPT = Path(__file__).with_name("gametest_receipt.json")
VERTEX_BUFFER_CLASS = 0x80807173
HEADER_SIZE = 48
STRIDE = 16
CACHE_GLOBS = (
    (Path(r"C:\Sunrise"), "cache_phr_*.dat"),
    (Path(r"C:\Sunrise\bin\x64\Sunrise\cache"), "content_manifest.bin"),
)


def option(name: str, fallback):
    return type(fallback)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


wanted = option("--package", "mercury_destination_03a7")
scale = option("--scale", 0.5)
if not 0.0 < scale <= 1.0:
    raise SystemExit("--scale must be within (0, 1]; scaling up can wrap the int16 range")


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


matches = [p for p in PACKAGES.glob("*.pkg") if wanted.lower() in p.name.lower()]
if not matches:
    raise SystemExit(f"no package matching {wanted!r}")
source = max(matches, key=patch_index)
pkg = Package(source)

if RECEIPT.is_file():
    raise SystemExit(f"{RECEIPT.name} exists; run 'python gametest.py --undo' first")

replacements: dict[int, bytes] = {}
vertices = 0
missing = 0
for entry in pkg.entries:
    if entry.reference != VERTEX_BUFFER_CLASS or entry.size <= HEADER_SIZE:
        continue
    if entry.start_block >= pkg.header.block_count:
        continue
    # A dump taken while one of our own patch files was installed reads back *through* it, so the
    # "original" on disk is already rescaled and scaling it again compounds the previous edit. In a
    # destination package every shipped block is encrypted and ours are the only plain ones, so a
    # plain block here means precisely that. Refusing beats silently producing a 0.25x mesh.
    if not pkg.blocks[entry.start_block].encrypted:
        raise SystemExit(
            f"entry {entry.index} resolves to a plain block, so {source.name} is one of ours and\n"
            "any dump taken against it is already modified. Run 'python gametest.py --undo',\n"
            "launch once to re-dump clean originals, then run this again."
        )
    tag = pkg.tag_for(entry.index)
    dumped = DUMP / f"tag_{tag:08X}.bin"
    if not dumped.is_file():
        missing += 1
        continue

    data = bytearray(dumped.read_bytes())
    if len(data) != entry.size:
        raise SystemExit(f"tag 0x{tag:08X}: dumped {len(data):,} bytes, entry declares {entry.size:,}")
    size, count, stride = struct.unpack_from("<QQQ", data, 0)
    if size != len(data) or stride != STRIDE or HEADER_SIZE + count * stride != len(data):
        raise SystemExit(f"tag 0x{tag:08X}: header does not describe a {STRIDE}-byte buffer")

    for i in range(count):
        at = HEADER_SIZE + i * STRIDE
        x, y, z = struct.unpack_from("<3h", data, at)
        struct.pack_into("<3h", data, at, int(x * scale), int(y * scale), int(z * scale))
    replacements[entry.index] = bytes(data)
    vertices += count

if not replacements:
    raise SystemExit(f"no dumped buffers found under {DUMP}; run request_buffers.py and launch first")

print(f"{source.name}: patch {pkg.header.patch_id}")
print(f"  {len(replacements)} buffers rescaled to {scale:g}x, {vertices:,} vertices")
if missing:
    print(f"  {missing} buffers left untouched - not dumped")

plans = write_patch_package_multi(source, replacements)
written = source.with_name(f"{pkg.stem}_{pkg.header.patch_id + 1}.pkg")
blocks = sum(len(p.spans) for p in plans.values())
print(f"  {written.name}: {written.stat().st_size:,} bytes, {blocks} block records")

check = Package(written)
introduced = set(check.check()) - set(pkg.check())
if introduced:
    written.unlink()
    for complaint in sorted(introduced)[:3]:
        print(f"  {complaint}")
    raise SystemExit("structural problems; removed, nothing installed")
for index, expected in replacements.items():
    if check.read_entry(index) != expected:
        written.unlink()
        raise SystemExit(f"entry {index} did not read back; removed")
print("  verified byte-exact")

spare = Path(__file__).parent / "cache_backup"
spare.mkdir(exist_ok=True)
moved = 0
for directory, pattern in CACHE_GLOBS:
    for cache in directory.glob(pattern):
        cache.replace(spare / cache.name)
        moved += 1
print(f"  cleared {moved} validation cache file(s)")

RECEIPT.write_text(json.dumps({"written": [written.name], "mode": "reshape"}, indent=2))
print(f"\nInstalled {written.name}. Travel to Mercury.")
print("Undo with: python gametest.py --undo")
