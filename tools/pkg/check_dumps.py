r"""
Checks dumped entries against the shipped entry tables, to catch dumps taken through our own patch.

## The trap

`dump_if_requested()` reads entries through whatever packages are installed. If one of our patch
files is installed, a dump of a redirected entry returns **our** bytes, not the game's. The result
looks like a perfectly good dump and silently poisons everything downstream - it has cost this
project three separate debugging rounds, once rescaling 97 Mercury "originals" and once overwriting
72 helmet buffers with the gas mask.

The check is arithmetic rather than judgement. Entry tables are plain file data, so the size an
entry *should* have is readable offline with no keys and no launch. A dumped file whose length
disagrees with the shipped table was read through a patch.

Size only proves the obvious cases, but the obvious cases are the ones that happen: a replacement
almost never lands on exactly the original length.

## Avoiding it

Blank `request.txt` before launching with a patch installed, or undo the patch before dumping.
`inject_mesh.py --undo` restores shipped state.

Usage:
    python check_dumps.py
    python check_dumps.py --dump C:\Sunrise\bin\x64\Sunrise\dump_models
"""
from __future__ import annotations

import sys
from pathlib import Path

from parse_models import SUNRISE, lookup, option

TAG_MIN, TAG_MAX = 0x80800000, 0x80FFFFFF

directory = Path(option("--dump", str(SUNRISE / "dump")))
files = sorted(directory.glob("tag_*.bin"))
if not files:
    raise SystemExit(f"no dumps under {directory}")

clean = 0
suspect = []
unknown = 0
for path in files:
    try:
        tag = int(path.stem[4:], 16)
    except ValueError:
        continue
    entry = lookup(tag)
    if entry is None or not TAG_MIN <= tag <= TAG_MAX:
        unknown += 1
        continue
    size = path.stat().st_size
    if entry.size == size:
        clean += 1
    else:
        suspect.append((tag, entry.size, size))

print(f"{directory}")
print(f"  {clean:,} agree with the shipped entry table")
print(f"  {unknown:,} could not be resolved to an entry")
print(f"  {len(suspect):,} DISAGREE - dumped through an installed patch")
for tag, shipped, dumped in suspect[:20]:
    print(f"    0x{tag:08X}  shipped {shipped:>9,} B, dump {dumped:>9,} B")
if suspect:
    print("\nRe-dump these on a shipped-state install:  python inject_mesh.py --undo")
raise SystemExit(1 if suspect else 0)
