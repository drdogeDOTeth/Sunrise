"""
Proves the patch pipeline offline: redirect one entry, then read the whole package back.

The check that matters is not that the redirected entry reads back — that much would pass even if
the file were wrecked around it. It is that *every other* entry still yields byte-identical data
afterwards. Entries pack several to a block and spill across block boundaries, so a careless write
corrupts neighbours silently, and the game would be the one to report it, late and unhelpfully.

Also round-trips the Oodle binding, since the compressor is what a block writer will lean on.

Everything happens on a copy. The game install is never written to.

Usage: python roundtrip_test.py [packages-dir]
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from oodle import Oodle, OodleError
from patch import copy_family, write_patch_package
from tigerpkg import Package, PackageError

root = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Sunrise\packages")
scratch = Path(os.environ.get("TEMP", tempfile.gettempdir())) / "tigerpkg_roundtrip"

print("=== Oodle round trip ===")
try:
    codec = Oodle()
    sample = bytes(range(256)) * 400
    squeezed = codec.compress(sample)
    restored = codec.decompress_exact(squeezed, len(sample))
    if restored != sample:
        raise SystemExit("Oodle round trip did not reproduce the input")
    print(f"  {codec.path.name}: {len(sample):,} -> {len(squeezed):,} -> identical")
except OodleError as exc:
    print(f"  unavailable: {exc}")

print("\n=== picking a package whose entries are all readable offline ===")
# Every entry must be readable before and after, or the comparison proves nothing. Encrypted
# packages cannot be read without the game's key table, so the test uses a fully plain one.
candidate = None
for path in sorted(root.glob("w64_audio_*.pkg"), key=lambda p: p.stat().st_size):
    if path.stat().st_size > 400 * 1024 * 1024:
        break
    try:
        pkg = Package(path)
    except PackageError:
        continue
    if any(b.encrypted or b.compressed for b in pkg.blocks):
        continue
    readable = [e.index for e in pkg.entries if e.size and e.start_block < pkg.header.block_count]
    if len(readable) >= 2:
        candidate = (path, readable)
        break

if candidate is None:
    raise SystemExit("no fully plain package found to test against")

source, readable = candidate
pkg = Package(source)
print(f"  {source.name}: {len(readable):,} readable entries, "
      f"{pkg.header.block_count:,} blocks, layout {pkg.header.layout}, "
      f"patch {pkg.header.patch_id}")

print("\n=== reading every entry before ===")
work = copy_family(source, scratch)
original = Package(work)
before: dict[int, bytes] = {}
for index in readable:
    try:
        before[index] = original.read_entry(index)
    except PackageError:
        pass
print(f"  {len(before):,} entries read")

target = max(before, key=lambda i: len(before[i]))
print(f"\n=== writing the next patch file, redirecting entry {target} ===")
replacement = bytes(((b + 1) & 0xFF) for b in before[target])
plan = write_patch_package(work, target, replacement)
new_file = work.with_name(f"{original.stem}_{original.header.patch_id + 1}.pkg")
print(f"  {new_file.name}: {new_file.stat().st_size:,} bytes "
      f"(source was {work.stat().st_size:,})")
print(f"  entry {plan.entry_index} -> new block {plan.block_index}, "
      f"{plan.old_size:,} -> {plan.new_size:,} bytes")

print("\n=== reading every entry back from the new patch file ===")
patched = Package(new_file)
# Some shipped packages already reference patch files that were never installed, so the bar is no
# *new* complaint rather than none at all.
was = set(original.check())
now = set(patched.check())
introduced = now - was
if introduced:
    print(f"  *** {len(introduced)} new structural complaints ***")
    for complaint in sorted(introduced)[:5]:
        print(f"    {complaint}")
    raise SystemExit("patch file introduced structural problems")
print(f"  no new structural complaints ({len(was)} pre-existing, carried over)")

got = patched.read_entry(target)
if got != replacement:
    raise SystemExit(f"redirected entry read back {len(got):,} bytes, wanted {len(replacement):,}")
print(f"  entry {target} reads back the replacement, byte for byte")

changed = []
for index, expected in before.items():
    if index == target:
        continue
    try:
        if patched.read_entry(index) != expected:
            changed.append((index, "contents differ"))
    except PackageError as exc:
        changed.append((index, f"now unreadable: {exc}"))

if changed:
    print(f"  *** {len(changed):,} neighbouring entries changed ***")
    for index, why in changed[:10]:
        print(f"    entry {index}: {why}")
    raise SystemExit("patch corrupted neighbouring entries")

print(f"  all {len(before) - 1:,} other entries byte-identical")
print(f"\nWrote {new_file}")
print("It is a copy. The game install was not touched.")
