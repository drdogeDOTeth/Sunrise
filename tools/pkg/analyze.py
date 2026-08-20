"""
Characterises dumped package entries so their classes can be identified.

Nothing here decodes a known format — the point is to work out *which* class is which, using signals
that survive not knowing the layout: embedded tag handles (0x8080xxxx) show how records reference
each other, plausible float runs suggest geometry, and byte entropy separates packed buffers from
struct-shaped records.

Usage: python analyze.py [dump-dir]
"""
from __future__ import annotations

import collections
import math
import re
import struct
import sys
from pathlib import Path

from tigerpkg import TAG_BASE, TAG_ENTRY_BITS, TAG_ENTRY_MASK

DUMP = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Sunrise\bin\x64\Sunrise\dump")
NAME_RE = re.compile(r"^tag_([0-9A-Fa-f]{8})\.bin$")
TAG_MIN = TAG_BASE
TAG_MAX = TAG_BASE + (0x0FFF << TAG_ENTRY_BITS) + TAG_ENTRY_MASK


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = collections.Counter(data)
    return -sum((c / len(data)) * math.log2(c / len(data)) for c in counts.values())


def embedded_tags(data: bytes) -> list[int]:
    """@return Every 4-byte aligned value that looks like a tag handle."""
    out = []
    for at in range(0, len(data) - 3, 4):
        (value,) = struct.unpack_from("<I", data, at)
        if TAG_MIN <= value <= TAG_MAX:
            out.append(value)
    return out


def float_runs(data: bytes) -> int:
    """@return How many 4-byte slots hold a float in a range typical of model coordinates."""
    good = 0
    for at in range(0, len(data) - 3, 4):
        (value,) = struct.unpack_from("<f", data, at)
        if value == value and abs(value) != float("inf") and 1e-4 < abs(value) < 1e4:
            good += 1
    return good


files = sorted(DUMP.glob("tag_*.bin"))
if not files:
    raise SystemExit(f"no dumped entries under {DUMP}")

print(f"{len(files)} dumped entries in {DUMP}\n")
print(f"{'tag':>10} {'bytes':>8} {'entropy':>7} {'tagrefs':>8} {'float%':>7}  first 16 bytes")

by_size: dict[int, list[Path]] = collections.defaultdict(list)
for path in files:
    data = path.read_bytes()
    tags = embedded_tags(data)
    slots = max(1, len(data) // 4)
    tag_name = NAME_RE.match(path.name).group(1)
    print(f"  {tag_name} {len(data):8,} {entropy(data):7.2f} {len(tags):8,} "
          f"{100.0 * float_runs(data) / slots:6.1f}%  {data[:16].hex(' ')}")
    by_size[len(data)].append(path)

print("\n--- entries sharing an exact size (a fixed struct, or padding) ---")
for size, paths in sorted(by_size.items()):
    if len(paths) > 1:
        print(f"  {size:,} bytes x{len(paths)}: {', '.join(p.stem[4:] for p in paths)}")

print("\n--- where the referenced tags point ---")
for path in files:
    data = path.read_bytes()
    tags = embedded_tags(data)
    if not tags:
        continue
    unique = sorted(set(tags))
    shown = ", ".join(f"0x{t:08X}" for t in unique[:6])
    more = f" (+{len(unique) - 6} more)" if len(unique) > 6 else ""
    print(f"  {path.stem[4:]} -> {len(unique)} distinct: {shown}{more}")
