"""List the tags one dumped entity blob references, so the next dump pass can be written for it.

Step 2 of enemy scoping. `dump/request.txt` writes an SEntity's decrypted bytes; this reads those
bytes back and reports every tag-shaped word inside, named where `EntityNames.json` knows it. From
that list the model and its buffers can be requested in a single follow-up pass rather than guessed
at a launch at a time.

Tag-shaped means the high byte is 0x80/0x81, which is what every tag in this project's notes looks
like (`0x80EFA1D8` the chest SEntity, `0x81531EE8` one of its resources). Resources do **not** sit
next to their entity - those two differ in the second byte - so a window scan around the entity tag
finds nothing. Reading the references out is the only reliable route.

Usage:
    python scan_entity_tags.py                     # every enemy tag from the current request
    python scan_entity_tags.py --tag 0x80BF8A1D    # just one
    python scan_entity_tags.py --request           # emit a ready-to-paste request.txt block
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
from collections import Counter
from pathlib import Path

GAME = Path(os.environ.get("SUNRISE_GAME", r"C:\Sunrise"))
ART = GAME / "bin" / "x64" / "Sunrise"
DUMP = ART / "dump"
NAMES = ART / "EntityNames.json"


def names() -> dict[str, list[str]]:
    if not NAMES.is_file():
        return {}
    return json.loads(NAMES.read_text(encoding="utf-8", errors="replace")).get("entities", {})


def requested() -> list[tuple[int, str]]:
    """The tags the live request file asks for, with their trailing comment as a label."""
    path = DUMP / "request.txt"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        match = re.match(r"\s*tag\s+(0x[0-9A-Fa-f]+)\s*(?:#\s*(.*))?", line)
        if match:
            out.append((int(match.group(1), 16), (match.group(2) or "").strip()))
    return out


def referenced(blob: bytes) -> Counter:
    """Every 4-byte aligned word that looks like a tag, counted."""
    found: Counter = Counter()
    for offset in range(0, len(blob) - 3, 4):
        value = struct.unpack_from("<I", blob, offset)[0]
        if (value >> 24) in (0x80, 0x81):
            found[value] += 1
    return found


def main() -> int:
    table = names()
    only = sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else None
    targets = ([(int(only, 16), "")] if only else requested())
    if not targets:
        print("nothing to scan: no --tag and no 'tag' lines in dump/request.txt")
        return 2

    every: Counter = Counter()
    missing = []
    for tag, label in targets:
        path = DUMP / f"tag_{tag:08X}.bin"
        if not path.is_file():
            missing.append((tag, label))
            continue
        blob = path.read_bytes()
        found = referenced(blob)
        # The entity's own tag appears inside it; it is not a reference to follow.
        found.pop(tag, None)
        print(f"\n{tag:08X}  {label or '?'}   {len(blob):,} B   {len(found)} distinct tags referenced")
        for ref, count in found.most_common(12):
            named = table.get(f"{ref:08X}")
            suffix = f"   {named[:2]}" if named else ""
            print(f"    {ref:08X}  x{count}{suffix}")
        every.update(found)

    if missing:
        print("\nnot dumped yet (launch to character select with these in request.txt):")
        for tag, label in missing:
            print(f"    {tag:08X}  {label}")

    if "--request" in sys.argv and every:
        print("\n# ---- paste into dump/request.txt for the next pass ----")
        for ref, _ in every.most_common(64):
            named = table.get(f"{ref:08X}")
            note = f"   # {named[0]}" if named else ""
            print(f"tag 0x{ref:08X}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
