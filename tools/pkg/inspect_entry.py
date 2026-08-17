"""
Prints one dumped entry in detail, annotating the fields that are understood.

`analyze.py` says which class a blob might be; this says what is actually in one. Values are
annotated as they are recognised — the leading size, tag handles, plausible floats — so the parts
that are not yet understood stand out by contrast rather than getting lost in a hex dump.

Named `inspect_entry` rather than `inspect` on purpose: a module named `inspect.py` on the path
shadows the standard library's, which `dataclasses` imports, breaking every other tool in this
directory with an error that points nowhere near the cause.

Usage: python inspect_entry.py <tag-hex> [byte-count]
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
TAG_MIN, TAG_MAX = 0x80800000, 0x80FFFFFF
# Values in this range recur across unrelated blobs, so they mark inline structure types rather
# than pointing at another entry.
INLINE_MAX = 0x80801000

tag = sys.argv[1].removeprefix("0x").upper()
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 256

path = DUMP / f"tag_{tag}.bin"
data = path.read_bytes()
(declared,) = struct.unpack_from("<Q", data, 0)

print(f"{path.name}: {len(data):,} bytes")
print(f"  leading u64 = {declared:,} ({'matches file size' if declared == len(data) else 'NOT the size'})\n")

print(f"{'offset':>8}  {'u32':>10}  {'hex':>10}  {'float':>14}  note")
for at in range(0, min(limit, len(data) - 3), 4):
    (value,) = struct.unpack_from("<I", data, at)
    (real,) = struct.unpack_from("<f", data, at)
    note = ""
    if TAG_MIN <= value <= TAG_MAX:
        note = "inline struct type" if value < INLINE_MAX else "-> tag"
    elif value == 0:
        note = ""
    elif real == real and 1e-3 < abs(real) < 1e4:
        note = "float?"
    shown = f"{real:14.5f}" if note == "float?" else " " * 14
    print(f"  {at:6d}  {value:10d}  0x{value:08X}  {shown}  {note}")

if len(data) > limit:
    print(f"\n  ... {len(data) - limit:,} more bytes")

print(f"\n--- printable strings ---")
run = bytearray()
found = 0
for index, byte in enumerate(data):
    if 32 <= byte < 127:
        run.append(byte)
        continue
    if len(run) >= 4:
        print(f"  {index - len(run):6d}  {run.decode('ascii')!r}")
        found += 1
    run.clear()
if found == 0:
    print("  (none)")
