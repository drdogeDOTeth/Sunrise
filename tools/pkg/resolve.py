"""
Resolves tag handles to their class, size and package, without decrypting anything.

A tag encodes the package id and entry index directly, and entry tables are plain file data, so the
class and size behind any reference can be looked up from disk. Only the body needs the game. That
makes it cheap to map the reference graph of an already-dumped blob before deciding what is worth
dumping next.

Usage:
    python resolve.py 0x80B363C2 0x80B364F4 ...   # resolve specific tags
    python resolve.py --refs 80B363C9             # resolve everything one dumped blob references
    python resolve.py --graph                     # resolve the references of every dumped blob
"""
from __future__ import annotations

import collections
import re
import struct
import sys
from pathlib import Path

from tigerpkg import TAG_BASE, TAG_ENTRY_BITS, TAG_ENTRY_MASK, Package, PackageError

PACKAGES = Path(r"C:\Sunrise\packages")
DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
FAMILY_RE = re.compile(r"^w64_(?P<family>.+?)_[0-9a-f]{4}(_[a-z]{2})?_\d+\.pkg$", re.IGNORECASE)
TAG_MIN, TAG_MAX = 0x80800000, 0x80FFFFFF
INLINE_MAX = 0x80801000


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


def build_index() -> dict[int, Path]:
    """@return Package id -> newest file of that package, which owns the authoritative tables."""
    newest: dict[str, Path] = {}
    for path in PACKAGES.glob("*.pkg"):
        stem = path.stem.rsplit("_", 1)[0]
        if stem not in newest or patch_index(path) > patch_index(newest[stem]):
            newest[stem] = path
    index: dict[int, Path] = {}
    for path in newest.values():
        try:
            with open(path, "rb") as fh:
                head = fh.read(0x180)
            if len(head) < 0x180 or struct.unpack_from("<H", head, 0)[0] != 38:
                continue
            index[struct.unpack_from("<H", head, 0x04)[0]] = path
        except OSError:
            continue
    return index


INDEX = build_index()
_cache: dict[Path, Package] = {}


def resolve(tag: int):
    """@return (class id, size, family, package name) for one tag, or None when unresolvable."""
    if not TAG_MIN <= tag <= TAG_MAX:
        return None
    handle = tag - TAG_BASE
    package_id = handle >> TAG_ENTRY_BITS
    entry_index = handle & TAG_ENTRY_MASK
    path = INDEX.get(package_id)
    if path is None:
        return None
    if path not in _cache:
        try:
            _cache[path] = Package(path)
        except PackageError:
            return None
    pkg = _cache[path]
    if entry_index >= len(pkg.entries):
        return None
    entry = pkg.entries[entry_index]
    m = FAMILY_RE.match(path.name)
    return entry.reference, entry.size, (m["family"].lower() if m else "?"), path.name


def references_of(stem: str) -> list[int]:
    """@return Distinct outward tag references in one dumped blob, type markers excluded."""
    data = (DUMP / f"tag_{stem.upper()}.bin").read_bytes()
    seen = []
    for at in range(0, len(data) - 3, 4):
        (value,) = struct.unpack_from("<I", data, at)
        if INLINE_MAX < value <= TAG_MAX and value not in seen:
            seen.append(value)
    return seen


def show(tags: list[int], indent: str = "  ") -> None:
    for tag in tags:
        got = resolve(tag)
        if got is None:
            print(f"{indent}0x{tag:08X}  unresolved")
            continue
        class_id, size, family, name = got
        print(f"{indent}0x{tag:08X}  class 0x{class_id:08X}  {size:>9,} B  {family:<28} {name}")


if "--graph" in sys.argv:
    dumped = sorted(p.stem[4:] for p in DUMP.glob("tag_*.bin"))
    classes: collections.Counter[int] = collections.Counter()
    for stem in dumped:
        own = resolve(int(stem, 16))
        label = f"class 0x{own[0]:08X}" if own else "unknown"
        refs = references_of(stem)
        print(f"\n0x{stem} ({label}) references {len(refs)}:")
        show(refs)
        for tag in refs:
            got = resolve(tag)
            if got:
                classes[got[0]] += 1
    print("\n--- classes most referenced across every dumped blob ---")
    for class_id, count in classes.most_common(15):
        print(f"  0x{class_id:08X}  {count:5,} references")
elif "--refs" in sys.argv:
    stem = sys.argv[sys.argv.index("--refs") + 1]
    refs = references_of(stem)
    print(f"0x{stem.upper()} references {len(refs)} distinct tags:\n")
    show(refs)
else:
    show([int(a, 0) for a in sys.argv[1:]], indent="")
