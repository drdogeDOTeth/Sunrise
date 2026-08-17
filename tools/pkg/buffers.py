"""
Tests the buffer-header hypothesis against every dumped entry.

Some classes look like a plain typed buffer: a 48-byte header carrying the entry's own size, an
element count and an element stride, followed by exactly `count * stride` bytes of payload. That
is falsifiable arithmetic rather than a guess, so it is worth checking against every entry on hand
instead of the two it was noticed in.

A class where the arithmetic holds for every sample is a buffer whose element size is known, which
is the first thing needed to read geometry out of one.

Usage: python buffers.py [dump-dir]
"""
from __future__ import annotations

import collections
import math
import re
import struct
import sys
from pathlib import Path

from resolve import resolve

DUMP = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Sunrise\bin\x64\Sunrise\dump")
NAME_RE = re.compile(r"^tag_([0-9A-Fa-f]{8})\.bin$")
HEADER_SIZE = 48


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = collections.Counter(data)
    return -sum((c / len(data)) * math.log2(c / len(data)) for c in counts.values())


rows = []
for path in sorted(DUMP.glob("tag_*.bin")):
    tag = int(NAME_RE.match(path.name).group(1), 16)
    data = path.read_bytes()
    if len(data) < 24:
        continue
    size, count, stride = struct.unpack_from("<QQQ", data, 0)
    got = resolve(tag)
    class_id = got[0] if got else 0
    fits = (
        size == len(data)
        and 0 < stride <= 4096
        and 0 < count
        and HEADER_SIZE + count * stride == len(data)
    )
    rows.append((class_id, tag, len(data), count, stride, fits, entropy(data)))

by_class: dict[int, list] = collections.defaultdict(list)
for row in rows:
    by_class[row[0]].append(row)

print(f"{len(rows)} dumped entries, {len(by_class)} classes\n")
print(f"{'class':>12} {'samples':>7} {'buffers':>7} {'strides':>12} {'entropy':>8}  verdict")

for class_id, group in sorted(by_class.items()):
    fitting = [r for r in group if r[5]]
    strides = sorted({r[4] for r in fitting})
    mean_entropy = sum(r[6] for r in group) / len(group)
    if fitting and len(fitting) == len(group):
        verdict = "BUFFER - header + count*stride holds for every sample"
    elif fitting:
        verdict = f"buffer for {len(fitting)}/{len(group)}"
    else:
        verdict = "not a plain buffer"
    shown = ", ".join(str(s) for s in strides) if strides else "-"
    print(f"  0x{class_id:08X} {len(group):7} {len(fitting):7} {shown:>12} {mean_entropy:8.2f}  {verdict}")

print("\n--- confirmed buffers, element by element ---")
for class_id, group in sorted(by_class.items()):
    fitting = [r for r in group if r[5]]
    if not fitting or len(fitting) != len(group):
        continue
    for _c, tag, size, count, stride, _f, ent in fitting:
        print(f"  0x{class_id:08X}  tag 0x{tag:08X}  {count:>8,} x {stride:>4} B "
              f"= {size - HEADER_SIZE:>10,}  entropy {ent:.2f}")
