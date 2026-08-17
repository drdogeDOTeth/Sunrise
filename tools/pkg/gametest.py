"""
Writes one hand-built patch file into the real game install so the game can be asked to load it.

Everything up to now is verified against our own reader. The renderer is the game's own loader, and
whether it accepts a package we wrote is the open question that can still invalidate the approach.

## Why this is safe to try

The tool only ever **adds** a file. No shipped package is modified, moved or truncated, so undoing
the test is deleting one file and the install is byte-for-byte what it was. `--undo` does that.

Audio packages are the target on purpose: they are the only family with plain, unencrypted blocks,
which means the result can be verified offline first, and a failure costs a sound rather than a
character.

## What a run proves

A patch file carries the full entry and block tables for its package, so if the game reads our file
at all, it resolves *every* entry of that package through our tables. Loading the package's content
successfully therefore means the tables were accepted, not merely tolerated.

Usage:
    python gametest.py --list            # rank candidates, write nothing
    python gametest.py                   # write the patch file into the install
    python gametest.py --undo            # remove whatever this tool added
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from patch import write_noop_patch_package, write_patch_package
from tigerpkg import BLOCK_SIZE, Package, PackageError

# The game validates a package set only when it changes, then caches the verdict and skips
# revalidation. A launch without clearing these repeats the previous answer whatever the files say,
# which made one earlier run look like a pass when nothing had been re-checked.
#
# The game cache is named for a hash of the package set (cache_phr_0000fbbe -> cache_phr_0000fb13
# when one file was added), so changing the files already invalidates it. Clearing is belt and
# braces, and it has to glob rather than name one file, or it silently clears nothing.
CACHE_GLOBS = (
    (Path(r"C:\Sunrise"), "cache_phr_*.dat"),
    (Path(r"C:\Sunrise\bin\x64\Sunrise\cache"), "content_manifest.bin"),
)

PACKAGES = Path(r"C:\Sunrise\packages")
# Kept beside this tool rather than in the packages directory. That directory holds nothing but
# .pkg files, and a stray file there is an uncontrolled variable in the very test being run.
RECEIPT = Path(__file__).with_name("gametest_receipt.json")


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


def newest_of_family(pkg: Package) -> Path:
    return max(PACKAGES.glob(f"{pkg.stem}_*.pkg"), key=patch_index)


def candidates() -> list[tuple]:
    """Ranks newest-in-family audio packages by how much of them can be checked offline."""
    rows = []
    for path in sorted(PACKAGES.glob("w64_audio_*.pkg")):
        if path.stat().st_size > 128 * 1024 * 1024:
            continue
        try:
            pkg = Package(path)
        except PackageError:
            continue
        if newest_of_family(pkg) != path:
            continue
        readable = []
        for entry in pkg.entries:
            if not entry.size or entry.start_block >= pkg.header.block_count:
                continue
            block = pkg.blocks[entry.start_block]
            if not (block.compressed or block.encrypted):
                readable.append(entry.index)
        if not readable:
            continue
        plain = sum(1 for b in pkg.blocks if not (b.compressed or b.encrypted))
        family = sum(p.stat().st_size for p in PACKAGES.glob(f"{pkg.stem}_*.pkg"))
        rows.append((-len(readable), family, path, pkg, readable, plain))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def clear_caches() -> None:
    """Moves the validation caches aside so the next launch re-checks the package set."""
    spare = Path(__file__).parent / "cache_backup"
    spare.mkdir(exist_ok=True)
    moved = 0
    for directory, pattern in CACHE_GLOBS:
        for cache in directory.glob(pattern):
            cache.replace(spare / cache.name)
            print(f"  moved {cache.name} aside")
            moved += 1
    if moved == 0:
        print("  no caches present (the game rebuilds them on the next launch)")


def undo() -> None:
    if not RECEIPT.is_file():
        print("no receipt found; nothing this tool wrote is recorded")
        return
    receipt = json.loads(RECEIPT.read_text())
    # distort.py writes several files in one run and shares this receipt on purpose, so that there
    # is one undo command rather than one per tool.
    names = receipt["written"]
    for name in [names] if isinstance(names, str) else names:
        written = PACKAGES / name
        if written.is_file():
            written.unlink()
            print(f"removed {written.name}")
        else:
            print(f"{name} was already gone")
    RECEIPT.unlink()
    print("install is back to its shipped state")


if "--undo" in sys.argv:
    undo()
    print("\nclearing validation caches so the next launch re-checks:")
    clear_caches()
    raise SystemExit(0)

rows = candidates()
if not rows:
    raise SystemExit("no suitable candidate package found")

if "--noop" in sys.argv:
    # Bisection: change nothing but the patch id. See write_noop_patch_package.
    _neg, _family, source, pkg, _readable, _plain = rows[0]
    if RECEIPT.is_file():
        raise SystemExit(f"{RECEIPT.name} exists; run --undo first")
    written = write_noop_patch_package(source)
    print(f"\nno-op patch of {source.name} (patch {pkg.header.patch_id}, layout {pkg.header.layout})")
    print(f"  {written.name}: {written.stat().st_size:,} bytes, identical but for the patch id")
    check = Package(written)
    if set(check.check()) - set(pkg.check()):
        written.unlink()
        raise SystemExit("no-op copy did not verify; removed")
    print("  verifies clean")
    RECEIPT.write_text(json.dumps({"written": written.name, "source": source.name,
                                   "entry": None, "mode": "noop"}, indent=2))
    print("\nclearing validation caches so the launch re-checks:")
    clear_caches()
    print(f"\nInstalled {written.name}. Launch, then: python readlog.py")
    raise SystemExit(0)

print(f"{'file':40s} {'patch':>5} {'checkable':>9} {'blocks':>7} {'plain':>6}  family MB")
for _neg, family, path, pkg, readable, plain in rows[:8]:
    print(f"{path.name:40s} {pkg.header.patch_id:5d} {len(readable):9,} "
          f"{pkg.header.block_count:7,} {plain:6,}  {family/1e6:9.1f}")

if "--list" in sys.argv:
    raise SystemExit(0)

_neg, _family, source, pkg, readable, _plain = rows[0]
print(f"\nusing {source.name} (patch {pkg.header.patch_id}, layout {pkg.header.layout})")

if RECEIPT.is_file():
    raise SystemExit(f"{RECEIPT.name} already exists; run --undo first")

print("\n=== reading checkable entries before ===")
before: dict[int, bytes] = {}
for index in readable:
    try:
        before[index] = pkg.read_entry(index)
    except PackageError:
        pass
print(f"  {len(before):,} entries read of {pkg.header.entry_count:,} in the package")
if not before:
    raise SystemExit("nothing could be read offline, so the result could not be verified")

# The largest entry that still fits one block: big enough to be a real exercise of the block
# writer, small enough that the replacement does not need splitting across records.
fits = [i for i in before if len(before[i]) <= BLOCK_SIZE]
if not fits:
    raise SystemExit("no readable entry fits a single block")
target = max(fits, key=lambda i: len(before[i]))
replacement = bytes(((b + 1) & 0xFF) for b in before[target])

print(f"\n=== writing the next patch file, redirecting entry {target} ===")
was = set(pkg.check())
plan = write_patch_package(source, target, replacement)
written = source.with_name(f"{pkg.stem}_{pkg.header.patch_id + 1}.pkg")
print(f"  {written.name}: {written.stat().st_size:,} bytes")
print(f"  entry {plan.entry_index} -> new block {plan.block_index}, {plan.new_size:,} bytes")

print("\n=== verifying offline before the game ever sees it ===")
patched = Package(written)
introduced = set(patched.check()) - was
if introduced:
    written.unlink()
    for complaint in sorted(introduced)[:5]:
        print(f"  {complaint}")
    raise SystemExit("new structural complaints; the file was removed and nothing was installed")

if patched.read_entry(target) != replacement:
    written.unlink()
    raise SystemExit("redirected entry did not read back; the file was removed")

changed = []
for index, expected in before.items():
    if index == target:
        continue
    try:
        if patched.read_entry(index) != expected:
            changed.append(index)
    except PackageError:
        changed.append(index)
if changed:
    written.unlink()
    raise SystemExit(f"{len(changed)} neighbouring entries changed; the file was removed")

print(f"  no new structural complaints, redirected entry exact, "
      f"{len(before) - 1:,} neighbours unchanged")

RECEIPT.write_text(json.dumps({"written": written.name, "source": source.name,
                               "entry": target, "mode": "redirect"}, indent=2))
print("\nclearing validation caches so the launch re-checks:")
clear_caches()
print(f"\nInstalled {written.name}")
print(f"Undo with:  python gametest.py --undo")
