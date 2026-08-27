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
import shutil
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

def patch_level(path: Path) -> int:
    """@return The numeric patch suffix of a package file, or -1 when it has none."""
    suffix = path.stem.rsplit("_", 1)[-1]
    return int(suffix) if suffix.isdigit() else -1


print("\n=== picking a package whose entries are all readable offline ===")
# Every entry must be readable before and after, or the comparison proves nothing. Encrypted
# packages cannot be read without the game's key table, so the test uses a fully plain one.
#
# Those are early files. A family's newest file inherits block records from every earlier patch,
# and in this install that always drags in encrypted ones - no package anywhere carries compressed
# blocks that are not also encrypted. So the base is chosen for readability and the *copy* is then
# truncated to it, which is what makes it the newest file of its family and a legal patch base.
candidates = []
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
        candidates.append((path, readable))
    if len(candidates) >= 12:
        break

if not candidates:
    raise SystemExit("no fully plain package found to test against")

# Not every plain package is patchable: some carry an empty hashed-region descriptor, which the
# writer refuses rather than guess at. Try candidates until one takes the write, so the test
# exercises the pipeline instead of failing on the luck of which file sorted first.
work = original = before = target = replacement = plan = source = None
rejected = []
for path, readable in candidates:
    if scratch.exists():
        shutil.rmtree(scratch)
    trial = copy_family(path, scratch)
    for sibling in list(scratch.glob("*.pkg")):
        if patch_level(sibling) > patch_level(path):
            sibling.unlink()
    opened = Package(trial)
    seen: dict[int, bytes] = {}
    for index in readable:
        try:
            seen[index] = opened.read_entry(index)
        except PackageError:
            pass
    if not seen:
        continue
    pick = max(seen, key=lambda i: len(seen[i]))
    swap = bytes(((b + 1) & 0xFF) for b in seen[pick])
    try:
        plan = write_patch_package(trial, pick, swap)
    except PackageError as problem:
        rejected.append(f"{path.name}: {problem}")
        continue
    source, work, original, before, target, replacement = path, trial, opened, seen, pick, swap
    break

if plan is None:
    for line in rejected[:5]:
        print(f"  rejected {line}")
    raise SystemExit("no plain package could be patched")

print(f"  {source.name}: {len(before):,} readable entries, "
      f"{original.header.block_count:,} blocks, layout {original.header.layout}, "
      f"patch {original.header.patch_id}")
if rejected:
    print(f"  ({len(rejected)} earlier candidate(s) refused the write)")
print(f"\n=== wrote the next patch file, redirecting entry {target} ===")
new_file = work.with_name(f"{original.stem}_{original.header.patch_id + 1}.pkg")
print(f"  {new_file.name}: {new_file.stat().st_size:,} bytes "
      f"(source was {work.stat().st_size:,})")
print(f"  entry {plan.entry_index} -> new block {plan.block_index}, "
      f"{plan.old_size:,} -> {plan.new_size:,} bytes")

print("\n=== reading every entry back from the new patch file ===")
patched = Package(new_file)
# Blocks we write are Oodle-compressed where that pays, so reading back has to go through the codec.
# That makes this a stronger check than it was: a block that is well-formed on paper but does not
# actually decode fails right here rather than in the game.
decode = codec.decompress_block
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

got = patched.read_entry(target, decoder=decode)
if got != replacement:
    raise SystemExit(f"redirected entry read back {len(got):,} bytes, wanted {len(replacement):,}")
print(f"  entry {target} reads back the replacement, byte for byte")

changed = []
for index, expected in before.items():
    if index == target:
        continue
    try:
        if patched.read_entry(index, decoder=decode) != expected:
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
