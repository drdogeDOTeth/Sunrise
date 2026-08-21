"""Diff an entry's *table row* between the layer we shipped and the layer underneath it.

`verify_bind_layer.py` proves the entry's **body** is intact. It cannot see a mistake in the
entry-table row that carries it - `reference`, `type_info`, and the block placement - because it
reads the body *through* that row, so a wrong row reads back its own wrong answer consistently.

For a structured record `reference` is its class id, and the loader dispatches on it. A resized
entry whose row lost its class would register fine and hang exactly where character select hangs.

This compares each tag's row in the newest patch against the newest patch that predates our layer,
which is the same comparison the loader makes when it takes the newer file.

Usage:
    python diff_entry_rows.py
    python diff_entry_rows.py 0x80EF98DB 0x80EFA1F7
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from tigerpkg import Package

PACKAGES = Path(r"C:\Sunrise\packages")
NAME_RE = re.compile(r"^(?P<stem>.+)_(?P<patch>\d+)\.pkg$", re.IGNORECASE)
TAG_BASE = 0x80800000
TAG_ENTRY_BITS = 13
TAG_ENTRY_MASK = (1 << TAG_ENTRY_BITS) - 1

# The bind layer's seventeen, plus the two chest models it draws through.
DEFAULT_TAGS = [
    0x80EFA1CA, 0x80EFA1A9,                                          # chest models
    0x80EF98DB, 0x80EFA1F7, 0x80EFA1DC, 0x81532AE0, 0x81531EF0,      # carrier materials
    0x80EF880E, 0x80EF8811, 0x80EF881F, 0x80EF881D, 0x80EF89F3,      # atlases + headers
    0x80EF89F1, 0x80EFAD63, 0x80EFAD60, 0x80EFB7B9, 0x80EFB76B,
]


def patches_of(stem: str) -> list[tuple[int, Path]]:
    out = []
    for path in PACKAGES.glob(f"{stem}_*.pkg"):
        match = NAME_RE.match(path.name)
        if match and match["stem"].lower() == stem.lower():
            out.append((int(match["patch"]), path))
    return sorted(out)


def stem_for(package_id: int) -> str | None:
    for path in PACKAGES.glob("*.pkg"):
        match = NAME_RE.match(path.name)
        if match is None:
            continue
        try:
            package = Package(path)
        except Exception:  # noqa: BLE001
            continue
        if package.header.package_id == package_id:
            return package.stem
    return None


def describe(package: Package, index: int) -> str:
    entry = package.entries[index]
    return (f"ref=0x{entry.reference:08X}  type_info=0x{entry.type_info:08X} "
            f"(type {entry.entry_type}/sub {entry.entry_subtype})  "
            f"size={entry.size:,}  block={entry.start_block}+{entry.start_offset:#x}")


def main() -> None:
    wanted = [int(a, 0) for a in sys.argv[1:]] or DEFAULT_TAGS

    stems: dict[int, str] = {}
    for path in PACKAGES.glob("*.pkg"):
        match = NAME_RE.match(path.name)
        if match is None:
            continue
        try:
            package = Package(path)
        except Exception:  # noqa: BLE001
            continue
        stems.setdefault(package.header.package_id, package.stem)

    changed = 0
    for tag in wanted:
        package_id = (tag - TAG_BASE) >> TAG_ENTRY_BITS
        index = (tag - TAG_BASE) & TAG_ENTRY_MASK
        stem = stems.get(package_id)
        if stem is None:
            print(f"0x{tag:08X}: package 0x{package_id:04X} not installed")
            continue
        history = patches_of(stem)
        if len(history) < 2:
            print(f"0x{tag:08X}: {stem} has only one patch")
            continue

        newest = Package(history[-1][1])
        now = describe(newest, index)

        # Walk back until the row differs, so we compare against whatever last defined it.
        previous = None
        for _, path in reversed(history[:-1]):
            older = Package(path)
            if index >= len(older.entries):
                continue
            was = describe(older, index)
            previous = (older, was)
            if was != now:
                break

        if previous is None:
            print(f"0x{tag:08X}: no earlier row")
            continue
        older, was = previous
        mark = "SAME" if was == now else "CHANGED"
        if was != now:
            changed += 1
        print(f"0x{tag:08X}  entry {index:>5}  {mark}")
        print(f"   {older.path.name:<26} {was}")
        print(f"   {newest.path.name:<26} {now}")

    print(f"\n{changed} of {len(wanted)} rows differ from the layer beneath them.")


if __name__ == "__main__":
    main()
