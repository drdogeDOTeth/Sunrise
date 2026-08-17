"""
Rescales the positions of dumped vertex buffers, leaving every other byte exactly as shipped.

## What is rewritten

Bytes 0-5 of each 16-byte vertex are the packed `int16` position and are rescaled. Bytes 6-15 hold
normals, tangents and an index whose layout is not decoded, and are copied verbatim — the same rule
that governs the package writer: inherit whatever you do not understand.

Positions are normalised `int16`, so scaling **down** cannot overflow the range. Scaling up could
wrap, so the factor is clamped to at most 1.0.

## Why the whole family is patched

A destination is split across several packages — Mercury's is four, holding 17, 65, 97 and 9 buffers
— and nothing in the tables says which one draws the ground in front of the player. Patching only
`03a7` changed 22% of Mercury's vertices and was completely invisible in game. Every package
matching the pattern is patched in one run, sharing one receipt so undo stays a single command.

Undo with `python gametest.py --undo`.

Usage:
    python reshape.py [--package mercury_destination] [--scale 0.5]
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from patch import write_patch_package_multi
from tigerpkg import BLOCK_SIZE, Package

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


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


wanted = option("--package", "mercury_destination")
scale = option("--scale", 0.5)
if not 0.0 < scale <= 1.0:
    raise SystemExit("--scale must be within (0, 1]; scaling up can wrap the int16 range")

# Bodies larger than one block are split across consecutive records, which round-trips through our
# own reader but has never been accepted by the game. The only package that ever loaded, 03a7, is
# also the only one whose buffers all fit a single block - it wrote 97 records for 97 buffers, while
# 0356 wrote 28 for 17. Patching every package but skipping oversized buffers keeps the coverage and
# removes multi-block as a variable.
single_block = "--single-block" in sys.argv

newest: dict[str, Path] = {}
for path in PACKAGES.glob("*.pkg"):
    if wanted.lower() not in path.name.lower():
        continue
    stem = path.stem.rsplit("_", 1)[0]
    if stem not in newest or patch_index(path) > patch_index(newest[stem]):
        newest[stem] = path
if not newest:
    raise SystemExit(f"no package matching {wanted!r}")

if RECEIPT.is_file():
    raise SystemExit(f"{RECEIPT.name} exists; run 'python gametest.py --undo' first")

written_names: list[str] = []
total_buffers = 0
total_vertices = 0

for stem, source in sorted(newest.items()):
    pkg = Package(source)
    replacements: dict[int, bytes] = {}
    vertices = 0
    missing = 0
    oversized = 0

    for entry in pkg.entries:
        if entry.reference != VERTEX_BUFFER_CLASS or entry.size <= HEADER_SIZE:
            continue
        if entry.start_block >= pkg.header.block_count:
            continue
        if single_block and entry.size > BLOCK_SIZE:
            oversized += 1
            continue
        # A dump taken while one of our own patch files was installed reads back *through* it, so
        # the "original" on disk is already rescaled and scaling it again compounds the edit. In a
        # destination package every shipped block is encrypted and ours are the only plain ones, so
        # a plain block here means precisely that. Refusing beats silently producing a 0.25x mesh.
        if not pkg.blocks[entry.start_block].encrypted:
            raise SystemExit(
                f"entry {entry.index} resolves to a plain block, so {source.name} is one of ours.\n"
                "Run 'python gametest.py --undo' first: a patch has to be based on a shipped file,\n"
                "and a dump taken while ours was installed would already be modified."
            )
        tag = pkg.tag_for(entry.index)
        dumped = DUMP / f"tag_{tag:08X}.bin"
        if not dumped.is_file():
            missing += 1
            continue

        data = bytearray(dumped.read_bytes())
        if len(data) != entry.size:
            raise SystemExit(
                f"tag 0x{tag:08X}: dumped {len(data):,} bytes, entry declares {entry.size:,}")
        size, count, stride = struct.unpack_from("<QQQ", data, 0)
        if size != len(data) or stride != STRIDE or HEADER_SIZE + count * stride != len(data):
            raise SystemExit(f"tag 0x{tag:08X}: header does not describe a {STRIDE}-byte buffer")

        for index in range(count):
            at = HEADER_SIZE + index * STRIDE
            x, y, z = struct.unpack_from("<3h", data, at)
            struct.pack_into("<3h", data, at, int(x * scale), int(y * scale), int(z * scale))
        replacements[entry.index] = bytes(data)
        vertices += count

    if not replacements:
        print(f"{source.name}: no dumped buffers, skipped")
        continue

    print(f"{source.name}: patch {pkg.header.patch_id}")
    print(f"  {len(replacements)} buffers rescaled to {scale:g}x, {vertices:,} vertices"
          + (f", {missing} not dumped" if missing else "")
          + (f", {oversized} skipped as multi-block" if oversized else ""))

    plans = write_patch_package_multi(source, replacements)
    written = source.with_name(f"{pkg.stem}_{pkg.header.patch_id + 1}.pkg")
    blocks = sum(len(plan.spans) for plan in plans.values())
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

    written_names.append(written.name)
    total_buffers += len(replacements)
    total_vertices += vertices

if not written_names:
    raise SystemExit(f"no dumped buffers found under {DUMP}; run request_buffers.py and launch first")

spare = Path(__file__).parent / "cache_backup"
spare.mkdir(exist_ok=True)
cleared = 0
for directory, pattern in CACHE_GLOBS:
    for cache in directory.glob(pattern):
        cache.replace(spare / cache.name)
        cleared += 1

RECEIPT.write_text(json.dumps({"written": written_names, "mode": "reshape"}, indent=2))
print(f"\n{len(written_names)} package(s), {total_buffers} buffers, {total_vertices:,} vertices "
      f"at {scale:g}x; cleared {cleared} cache file(s)")
for name in written_names:
    print(f"  {name}")
print("Travel to Mercury. Undo with: python gametest.py --undo")
