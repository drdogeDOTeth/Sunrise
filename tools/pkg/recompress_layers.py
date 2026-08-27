"""Rewrite this fork's own patch layers with compressed blocks, without touching their content.

The writer stored blocks plain for a long time. Making the existing layers match what the format
actually does could be had by re-running the character intake, but that re-derives the mesh from the
GLB and would only reproduce the current character if every retarget flag were replayed exactly.
The exact flags behind the live build are not recorded, so a rebuild risks changing a character that
works.

This does the safe thing instead: **read each layer's own entries back, drop the layer, and write it
again from the same base with the same bytes** — now compressed. The content is carried across
verbatim, so the character cannot change. Only the encoding does.

Every entry in the package is read before and after and compared, so a mistake shows up here rather
than in the game.

Usage:
    python recompress_layers.py --list
    python recompress_layers.py --dry-run
    python recompress_layers.py --apply
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from oodle import Oodle
from patch import write_patch_package_multi
from tigerpkg import Package, PackageError

PACKAGES = Path(r"C:\Sunrise\packages")
# The newest file each family ships with. Anything above these is ours.
STOCK_TOP = {"037c": 6, "037d": 5, "0698": 5}


def patch_level(path: Path) -> int:
    suffix = path.stem.rsplit("_", 1)[-1]
    return int(suffix) if suffix.isdigit() else -1


def our_layers() -> list[Path]:
    """@return Every layer this fork wrote, newest last within each family."""
    found = []
    for family, top in STOCK_TOP.items():
        for path in PACKAGES.glob(f"w64_sandbox_{family}_*.pkg"):
            if patch_level(path) > top:
                found.append(path)
    return sorted(found, key=lambda p: (p.stem.rsplit("_", 2)[1], patch_level(p)))


def owned_entries(pkg: Package) -> dict[int, bytes]:
    """
    @return Every entry whose body this file stores, with its current bytes.

    An entry belongs to this layer when its first block carries this file's patch id. Blocks
    inherited from earlier files keep theirs, which is exactly what makes a patch file incremental.
    """
    decode = Oodle().decompress_block
    mine: dict[int, bytes] = {}
    for entry in pkg.entries:
        if not entry.size or entry.start_block >= pkg.header.block_count:
            continue
        if pkg.blocks[entry.start_block].patch_id != pkg.header.patch_id:
            continue
        mine[entry.index] = pkg.read_entry(entry.index, decoder=decode)
    return mine


def read_all(path: Path) -> dict[int, bytes]:
    """@return Every readable entry of a package, for before/after comparison."""
    pkg = Package(path)
    decode = Oodle().decompress_block
    out: dict[int, bytes] = {}
    for entry in pkg.entries:
        if not entry.size or entry.start_block >= pkg.header.block_count:
            continue
        try:
            out[entry.index] = pkg.read_entry(entry.index, decoder=decode)
        except PackageError:
            pass
    return out


def recompress(path: Path, apply: bool) -> tuple[int, int, int]:
    """
    @param path Layer to rewrite.
    @param apply False to report only.
    @return Bytes before, bytes after, entries carried.
    """
    pkg = Package(path)
    mine = owned_entries(pkg)
    if not mine:
        raise PackageError(f"{path.name} stores no bodies of its own")
    base = path.with_name(f"{pkg.stem}_{pkg.header.patch_id - 1}.pkg")
    if not base.is_file():
        raise PackageError(f"{path.name}: its base {base.name} is missing")

    before_size = path.stat().st_size
    if not apply:
        return before_size, 0, len(mine)

    before = read_all(path)
    attic = path.parent / "recompress_attic" / time.strftime("%Y%m%d-%H%M%S")
    attic.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(attic / path.name))
    try:
        write_patch_package_multi(base, mine, compress=True)
    except Exception:
        shutil.move(str(attic / path.name), str(path))
        raise

    after = read_all(path)
    if set(before) != set(after):
        raise PackageError(f"{path.name}: entry set changed ({len(before)} -> {len(after)})")
    differing = [i for i, data in before.items() if after[i] != data]
    if differing:
        raise PackageError(f"{path.name}: {len(differing)} entries changed, first is {differing[0]}")
    return before_size, path.stat().st_size, len(mine)


def main() -> int:
    layers = our_layers()
    if not layers:
        print("no layers of ours found above stock")
        return 1
    if "--list" in sys.argv:
        for path in layers:
            pkg = Package(path)
            print(f"  {path.stat().st_size:>10,}  {path.name}  "
                  f"({len(owned_entries(pkg))} entries of its own)")
        return 0

    apply = "--apply" in sys.argv
    if not apply and "--dry-run" not in sys.argv:
        print(__doc__)
        return 2

    total_before = total_after = 0
    for path in layers:
        try:
            before, after, entries = recompress(path, apply)
        except PackageError as problem:
            print(f"  {path.name}: {problem}")
            return 1
        total_before += before
        total_after += after
        if apply:
            print(f"  {path.name}: {before:,} -> {after:,} bytes "
                  f"({100 * after / before:.1f}%), {entries} entries verified identical")
        else:
            print(f"  {path.name}: {before:,} bytes, {entries} entries of its own")
    if apply:
        print(f"\n  total {total_before:,} -> {total_after:,} "
              f"({100 * total_after / total_before:.1f}%)")
        print("  every entry in every package read back byte-identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
