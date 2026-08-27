"""
Proves the writer handles bodies that need more than one block.

A single-block writer was enough to get a package accepted by the game, but vertex buffers run to
2.6 MB, so anything real needs splitting across records. The failure mode this guards against is
subtle: a body that reassembles correctly for the patched entry while quietly corrupting the blocks
its neighbours read, which only shows up as missing content much later.

Everything happens on a copy. The game install is never written to.

Usage: python multiblock_test.py [packages-dir]
"""
from __future__ import annotations

import os
import sys
import shutil
import tempfile
from pathlib import Path

from oodle import Oodle
from patch import copy_family, write_patch_package
from tigerpkg import BLOCK_SIZE, Package, PackageError

root = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Sunrise\packages")
scratch = Path(os.environ.get("TEMP", tempfile.gettempdir())) / "tigerpkg_multiblock"

print("=== picking a package with enough offline-readable entries ===")


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


# No newest-in-family package is entirely plain, so the bar is entries whose own blocks are
# readable offline rather than a package with no encrypted block anywhere. Encrypted neighbours are
# simply not part of the comparison; the ones that are still catch a mis-written table.
candidate = None
for path in sorted(root.glob("w64_audio_*.pkg"), key=lambda p: p.stat().st_size):
    if path.stat().st_size > 400 * 1024 * 1024:
        break
    try:
        pkg = Package(path)
    except PackageError:
        continue
    if max(root.glob(f"{pkg.stem}_*.pkg"), key=patch_index) != path:
        continue
    readable = [
        e.index
        for e in pkg.entries
        if e.size
        and e.start_block < pkg.header.block_count
        and not (pkg.blocks[e.start_block].encrypted or pkg.blocks[e.start_block].compressed)
    ]
    if len(readable) >= 32:
        candidate = (path, readable)
        break

if candidate is None:
    raise SystemExit("no fully plain, newest-in-family package found")

source, readable = candidate
pkg = Package(source)
print(f"  {source.name}: {len(readable):,} readable entries, patch {pkg.header.patch_id}, "
      f"{pkg.header.block_count:,} blocks")

# A patch file left by an earlier run would sit newer than the base and make it
# illegal, so the scratch copy starts empty every time.
if scratch.exists():
    shutil.rmtree(scratch)
work = copy_family(source, scratch)
original = Package(work)
before = {}
for index in readable:
    try:
        before[index] = original.read_entry(index)
    except PackageError:
        pass
print(f"  read {len(before):,} entries before patching")

# Deliberately several blocks' worth, and not a whole multiple, so the final short chunk is
# exercised too. A repeating-but-not-uniform pattern catches chunks written out of order.
size = BLOCK_SIZE * 3 + 12345
replacement = bytes((i * 7 + (i >> 11)) & 0xFF for i in range(size))
target = next(iter(before))

print(f"\n=== redirecting entry {target} to {size:,} bytes ===")
expected_blocks = -(-size // BLOCK_SIZE)
plan = write_patch_package(work, target, replacement)
written = work.with_name(f"{original.stem}_{original.header.patch_id + 1}.pkg")
print(f"  {written.name}: {written.stat().st_size:,} bytes")
print(f"  {len(plan.spans)} block records appended, indices {plan.spans[0]}..{plan.spans[-1]}")
if len(plan.spans) != expected_blocks:
    raise SystemExit(f"expected {expected_blocks} blocks, wrote {len(plan.spans)}")

print("\n=== reading it back ===")
patched = Package(written)
# Written blocks are Oodle-compressed where that pays, so the read-back goes through the codec.
# That also proves the stored block genuinely decodes, not just that it is well-formed.
decode = Oodle().decompress_block
introduced = set(patched.check()) - set(original.check())
if introduced:
    for complaint in sorted(introduced)[:5]:
        print(f"  {complaint}")
    raise SystemExit("patch introduced structural problems")

got = patched.read_entry(target, decoder=decode)
if got != replacement:
    where = next((i for i in range(min(len(got), len(replacement)))
                  if got[i] != replacement[i]), min(len(got), len(replacement)))
    raise SystemExit(f"mismatch: got {len(got):,} bytes, wanted {len(replacement):,}, "
                     f"first difference at {where:,}")
print(f"  entry {target} reassembled across {len(plan.spans)} blocks, byte-exact")

changed = []
for index, want in before.items():
    if index == target:
        continue
    try:
        if patched.read_entry(index, decoder=decode) != want:
            changed.append(index)
    except PackageError:
        changed.append(index)
if changed:
    raise SystemExit(f"{len(changed)} neighbouring entries changed")
print(f"  all {len(before) - 1:,} other entries byte-identical")
print(f"\nWrote {written}. It is a copy; the install was not touched.")
