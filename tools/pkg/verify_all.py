"""
Parses every installed package and reports whether the reader agrees with all of them.

This is the check that earns the right to write packages: if the layout transcribed from Sunrise's
reader parses thousands of shipped files with no structural complaints, the format is understood.
Any file that fails is far more likely to be a gap in this reader than a bad package.

Usage: python verify_all.py [packages-dir]
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

from tigerpkg import BLOCK_SIZE, FLAG_ALTERNATE_KEY, FLAG_COMPRESSED, FLAG_ENCRYPTED, Package, PackageError

root = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Sunrise\packages")
files = sorted(root.glob("*.pkg"))
if not files:
    raise SystemExit(f"no .pkg files under {root}")

print(f"parsing {len(files):,} packages from {root}\n")

ok = 0
failed: list[tuple[str, str]] = []
structural: list[tuple[str, list[str]]] = []
flag_counts: collections.Counter[int] = collections.Counter()
entry_total = block_total = 0
biggest_block = 0
uncompressed_blocks = 0

for path in files:
    try:
        pkg = Package(path)
    except PackageError as exc:
        failed.append((path.name, str(exc)))
        continue
    except Exception as exc:  # noqa: BLE001 - want the reason, whatever it is
        failed.append((path.name, f"{type(exc).__name__}: {exc}"))
        continue

    ok += 1
    entry_total += pkg.header.entry_count
    block_total += pkg.header.block_count
    for block in pkg.blocks:
        flag_counts[block.flags] += 1
        biggest_block = max(biggest_block, block.size)
        if not block.compressed:
            uncompressed_blocks += 1

    problems = pkg.check()
    if problems:
        structural.append((path.name, problems))

print(f"parsed cleanly : {ok:,} / {len(files):,}")
print(f"entries        : {entry_total:,}")
print(f"blocks         : {block_total:,}")
print(f"largest block  : {biggest_block:,} bytes (decompressed cap {BLOCK_SIZE:,})")
print(f"uncompressed   : {uncompressed_blocks:,} blocks")

print("\n--- block flag combinations ---")
for flags, count in sorted(flag_counts.items()):
    names = []
    if flags & FLAG_COMPRESSED:
        names.append("compressed")
    if flags & FLAG_ENCRYPTED:
        names.append("encrypted")
    if flags & FLAG_ALTERNATE_KEY:
        names.append("altkey")
    extra = flags & ~(FLAG_COMPRESSED | FLAG_ENCRYPTED | FLAG_ALTERNATE_KEY)
    if extra:
        names.append(f"unknown:{extra:#x}")
    print(f"  0x{flags:04X}  {count:8,}  {'+'.join(names) or 'none'}")

if failed:
    print(f"\n--- {len(failed)} FAILED TO PARSE ---")
    for name, reason in failed[:20]:
        print(f"  {name}: {reason}")

if structural:
    print(f"\n--- {len(structural)} with structural complaints ---")
    for name, problems in structural[:20]:
        print(f"  {name}: {problems[0]}")

if not failed and not structural:
    print("\nAll packages parsed with no structural complaints.")
