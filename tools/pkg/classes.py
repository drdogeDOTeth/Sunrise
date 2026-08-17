"""
Histograms entry classes across every installed package.

Entry tables are plain file data even when every block body is encrypted, and an entry record's
`reference` field is the class id the reader reports for that tag. So the whole class census can be
taken offline, with no keys and no running game — which is worth doing before spending a launch
guessing which classes to ask the in-game dumper for.

Usage: python classes.py [packages-dir] [--families 0x1234abcd]
"""
from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

from tigerpkg import Package, PackageError

args = [a for a in sys.argv[1:] if not a.startswith("--")]
root = Path(args[0] if args else r"C:\Sunrise\packages")
want_families = None
if "--families" in sys.argv:
    want_families = int(sys.argv[sys.argv.index("--families") + 1], 0)

FAMILY_RE = re.compile(r"^w64_(?P<family>.+?)_[0-9a-f]{4}(_[a-z]{2})?_\d+\.pkg$", re.IGNORECASE)


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


# Only the newest file of each family carries the authoritative tables, so scanning every file
# would count most entries several times over.
newest: dict[str, Path] = {}
for path in root.glob("*.pkg"):
    stem = path.stem.rsplit("_", 1)[0]
    if stem not in newest or patch_index(path) > patch_index(newest[stem]):
        newest[stem] = path

print(f"scanning {len(newest):,} package families\n")

classes: collections.Counter[int] = collections.Counter()
class_families: dict[int, collections.Counter[str]] = collections.defaultdict(collections.Counter)
family_hits: collections.Counter[str] = collections.Counter()
sizes: dict[int, list[int]] = collections.defaultdict(list)

for path in sorted(newest.values()):
    try:
        pkg = Package(path)
    except PackageError:
        continue
    m = FAMILY_RE.match(path.name)
    family = m["family"].lower() if m else "?"
    for entry in pkg.entries:
        if entry.size == 0 or entry.start_block >= pkg.header.block_count:
            continue
        classes[entry.reference] += 1
        class_families[entry.reference][family] += 1
        if len(sizes[entry.reference]) < 4096:
            sizes[entry.reference].append(entry.size)
        if want_families is not None and entry.reference == want_families:
            family_hits[family] += 1

print(f"{len(classes):,} distinct classes over {sum(classes.values()):,} entries\n")
print(f"{'class':>12} {'entries':>10} {'median size':>12}  top families")
for class_id, count in classes.most_common(30):
    seen = sorted(sizes[class_id])
    median = seen[len(seen) // 2] if seen else 0
    top = ", ".join(f"{n}" for n, _c in class_families[class_id].most_common(3))
    print(f"  0x{class_id:08X} {count:10,} {median:12,}  {top}")

if want_families is not None:
    print(f"\n--- families carrying class 0x{want_families:08X} ---")
    for family, n in family_hits.most_common(20):
        print(f"  {n:8,}  {family}")
