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

from parse_models import SUNRISE, TAG_MAX, TAG_MIN, lookup, option
RECEIPT = Path(__file__).with_name("inject_receipt.json")

# The check compares a dump against the newest installed package - so with one of our patch files
# installed it compares our bytes against our bytes and reports a clean bill of health for dumps
# that are actually poisoned. Observed exactly that: 72 known-bad buffers passed silently because
# the patch had been reinstalled between the two runs. Refuse rather than mislead.
if RECEIPT.is_file() and "--anyway" not in sys.argv:
    import json
    installed = json.loads(RECEIPT.read_text()).get("written", [])
    if any(Path(name).is_file() for name in installed):
        raise SystemExit(
            "a patch written by inject_mesh.py is installed, so the shipped entry sizes this check\n"
            "needs are not readable - it would compare our bytes against our bytes and pass.\n"
            "Run 'python inject_mesh.py --undo' first, or --anyway if you know what you are doing.")

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
