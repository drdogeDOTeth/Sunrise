"""
Writes the smallest possible change to a destination package, to find where the loader stops.

## Why this exists

Two Mercury patches hung in `activity:initial_slice_set_loading` while registration passed. Each
time a plausible cause was fixed and the hang came back unchanged: first the synthesised normals and
tangents, then the null SHA-1 in every block record. Both were real defects; neither was the cause.

The third attempt is a bisection instead of a theory, because that is what settled `-86`. Every
variable that separates the working audio test from the failing Mercury test is removed at once:

| variable          | audio test | Mercury attempts | here      |
|-------------------|------------|------------------|-----------|
| entries rewritten | 1          | 97               | 1         |
| body spans blocks | no         | yes              | no        |
| content changed   | yes        | yes              | **no**    |
| package family    | audio      | destination      | destination |

Writing an entry's own bytes back means a pass and a fail differ only in whether the loader accepts
a **plain block in a destination package** — which no shipped patch file has ever contained: across
destination families every one of 26,875 blocks introduced by a real patch level is compressed and
encrypted.

- **Loads** — plain blocks are fine here, and the cause is entry count, body size or the edit itself.
  Escalate with `--entries`.
- **Hangs** — the block must match what the family uses. Compression is available offline through
  the game's own Oodle; encryption is not, and would have to happen in-process where the keys are.

Undo with `python gametest.py --undo`.

Usage:
    python bisect_one.py                 # one entry, its own bytes, one block
    python bisect_one.py --entries 20    # widen once a narrower run has passed
    python bisect_one.py --scale 0.5     # same entries, but actually change them
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


wanted = option("--package", "mercury_destination_03a7")
entries = option("--entries", 1)
scale = option("--scale", 1.0)
if not 0.0 < scale <= 1.0:
    raise SystemExit("--scale must be within (0, 1]")


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


matches = [p for p in PACKAGES.glob("*.pkg") if wanted.lower() in p.name.lower()]
if not matches:
    raise SystemExit(f"no package matching {wanted!r}")
source = max(matches, key=patch_index)
pkg = Package(source)

available = []
for entry in pkg.entries:
    if entry.reference != VERTEX_BUFFER_CLASS or entry.size <= HEADER_SIZE:
        continue
    if entry.start_block >= pkg.header.block_count:
        continue
    if not pkg.blocks[entry.start_block].encrypted:
        raise SystemExit(
            f"{source.name} is one of ours; run 'python gametest.py --undo' first")
    dumped = DUMP / f"tag_{pkg.tag_for(entry.index):08X}.bin"
    if dumped.is_file():
        available.append((entry, dumped))
if not available:
    raise SystemExit(f"no dumped buffers under {DUMP}; run request_buffers.py and launch first")

# Smallest first, so the default run stays inside a single block and adds one record. A body that
# spans blocks is a second variable, and the point of this tool is to have only one.
available.sort(key=lambda pair: pair[0].size)
chosen = available[:entries]

replacements: dict[int, bytes] = {}
for entry, dumped in chosen:
    data = bytearray(dumped.read_bytes())
    if len(data) != entry.size:
        raise SystemExit(f"entry {entry.index}: dumped {len(data):,}, declared {entry.size:,}")
    size, count, stride = struct.unpack_from("<QQQ", data, 0)
    if size != len(data) or stride != STRIDE or HEADER_SIZE + count * stride != len(data):
        raise SystemExit(f"entry {entry.index}: header does not describe a {STRIDE}-byte buffer")
    if scale != 1.0:
        for index in range(count):
            at = HEADER_SIZE + index * STRIDE
            x, y, z = struct.unpack_from("<3h", data, at)
            struct.pack_into("<3h", data, at, int(x * scale), int(y * scale), int(z * scale))
    replacements[entry.index] = bytes(data)

if RECEIPT.is_file():
    raise SystemExit(f"{RECEIPT.name} exists; run 'python gametest.py --undo' first")

spans = sum(max(1, (len(v) + BLOCK_SIZE - 1) // BLOCK_SIZE) for v in replacements.values())
print(f"{source.name}: patch {pkg.header.patch_id}")
print(f"  {len(replacements)} entr{'y' if len(replacements)==1 else 'ies'}, "
      f"{spans} block record(s), content {'unchanged' if scale == 1.0 else f'scaled {scale:g}x'}")
for entry, _ in chosen[:6]:
    print(f"    entry {entry.index:5d}  {entry.size:9,} bytes  "
          f"{(entry.size - HEADER_SIZE) // STRIDE:6,} vertices")

plans = write_patch_package_multi(source, replacements)
written = source.with_name(f"{pkg.stem}_{pkg.header.patch_id + 1}.pkg")
print(f"  {written.name}: {written.stat().st_size:,} bytes")

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
cleared = 0
for directory, pattern in CACHE_GLOBS:
    for cache in directory.glob(pattern):
        cache.replace(spare / cache.name)
        cleared += 1
print(f"  cleared {cleared} validation cache file(s)")

RECEIPT.write_text(json.dumps({"written": [written.name], "mode": "bisect"}, indent=2))
print(f"\nInstalled {written.name}. Travel to Mercury.")
print("  loads -> plain blocks are fine; the cause is entry count, body size or the edit")
print("  hangs -> a destination package needs blocks that match its family")
print("Undo with: python gametest.py --undo")
