"""
Closes the remaining hop: art-arrangement index → SEntityModel tag.

Charm does this for Witch Queen. Shadowkeep uses different class ids, so the tables are found by
shape instead:

* Arrangement indices in this install run 0..4300 (4,301 slots). Confirmed against `build_data.bin`.
* `0x81319329` (class `0x80807546`) is exactly `4,301 × 4 + 48` bytes — Monteven's hash table, the
  same `index * 4 + 48` layout.
* `0x8080744A` is a 24-byte singleton class with 16,236 members, the size of Charm's entity-parent
  stub. Average ~3.8 parents per arrangement.

The assignment table that indexes those parents is still encrypted. `--request` writes a dump of
the small set of tables that can be that table. After one launch this script reads the dumps and
walks the equipped loadout.

Usage:
    python lookup_arrangement.py --request
    python lookup_arrangement.py --census     # offline size hunt, no launch
    python lookup_arrangement.py              # equipped Warlock loadout
    python lookup_arrangement.py 2293 2295
    python lookup_arrangement.py --inspect 0x81319329
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from lookup_item import UNSET, load_details
from tigerpkg import TAG_BASE, TAG_ENTRY_BITS, TAG_ENTRY_MASK, Package, PackageError

# Do not import resolve.py — it runs a CLI on import and breaks `--request`.
TAG_MIN = TAG_BASE
TAG_MAX = TAG_BASE + (0x0FFF << TAG_ENTRY_BITS) + TAG_ENTRY_MASK
PACKAGES = Path(r"C:\Sunrise\packages")
_index: dict[int, Path] | None = None
_packages: dict[Path, Package] = {}


def _package_index() -> dict[int, Path]:
    global _index
    if _index is not None:
        return _index
    newest: dict[str, Path] = {}
    for path in PACKAGES.glob("*.pkg"):
        stem = path.stem.rsplit("_", 1)[0]
        tail = path.stem.rsplit("_", 1)[1]
        patch = int(tail) if tail.isdigit() else -1
        if stem not in newest or patch > int(newest[stem].stem.rsplit("_", 1)[1]):
            newest[stem] = path
    _index = {}
    for path in newest.values():
        try:
            with open(path, "rb") as handle:
                head = handle.read(0x180)
        except OSError:
            continue
        if len(head) < 0x180 or struct.unpack_from("<H", head, 0)[0] != 38:
            continue
        _index[struct.unpack_from("<H", head, 0x04)[0]] = path
    return _index


def resolve(tag: int):
    """@return `(class, size, family, filename)` from the plain entry table, or None."""
    if not TAG_MIN <= tag <= TAG_MAX:
        return None
    handle = tag - TAG_BASE
    package_id = handle >> TAG_ENTRY_BITS
    entry_index = handle & TAG_ENTRY_MASK
    path = _package_index().get(package_id)
    if path is None:
        return None
    if path not in _packages:
        try:
            _packages[path] = Package(path)
        except PackageError:
            return None
    pkg = _packages[path]
    if entry_index >= len(pkg.entries):
        return None
    entry = pkg.entries[entry_index]
    family = path.name.lower()
    return entry.reference, entry.size, family, path.name

DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
REQUEST = DUMP / "request.txt"
INLINE_MAX = 0x80801000
ARRAY_HEADER = 0x10

# Proven offline. Bodies still need one dump.
INVESTMENT_ROOT = 0x81327D22
ARRANGEMENT_HASH_TABLE = 0x81319329
SLOT4_ASSIGNMENT_CANDIDATE = 0x81327CF0
SLOT4_SIBLING = 0x81327D0A
PARENT_38_A = 0x8131914C
PARENT_38_B = 0x81319343
HASH_TABLE_SIBLING = 0x81319135
ENTITY_PARENT_CLASS = 0x8080744A
ENTITY = 0x80809C0F
ENTITY_RESOURCE = 0x80809C36
ENTITY_MODEL = 0x808073A5

# Exact-size 4,301-row tables from --census. All false positives:
# 0361 dumps are float transforms / uint16 remaps; 0913 is class 0x80809A8A (57k generic buffers).
SIZE_MAP_CANDIDATES = (
    0x80EC259F,
    0x80EC342D,
    0x80EC3827,
)
# Undumped tables large enough to hold nested assignment rows for 4,301 arrangements.
MAP_CANDIDATES = (
    0x81319344,
    0x81319335,
    0x81319324,
    0x81327CD4,
    0x81319349,
    0x8131933F,
)
ENTITY_PARENT_SAMPLES = (0x80B7403A, 0x80B7403B, 0x80B7403C)

EQUIPPED = (
    ("helmet", 0xEA042965),
    ("gauntlets", 0x188C5834),
    ("chest", 0xF8689C4C),
    ("legs", 0x083E04B6),
    ("class item", 0x99446581),
)

ARRANGEMENT_COUNT = 4301


def dumped(tag: int) -> bytes | None:
    path = DUMP / f"tag_{tag:08X}.bin"
    return path.read_bytes() if path.is_file() else None


def is_tag(value: int) -> bool:
    return INLINE_MAX < value <= TAG_MAX


def array_at(data: bytes, at: int) -> tuple[int, int, int]:
    """@return `(count, data offset, element class)`, or `(0, 0, 0)`."""
    if at + 16 > len(data):
        return 0, 0, 0
    count, relative = struct.unpack_from("<qq", data, at)
    header = at + 8 + relative
    start = header + ARRAY_HEADER
    if count <= 0 or count > 300000 or header < 4 or start > len(data):
        return 0, 0, 0
    if header + 16 > len(data):
        return 0, 0, 0
    header_count, element = struct.unpack_from("<QI", data, header)
    if header_count != count:
        return 0, 0, 0
    return count, start, element


def tags_in(data: bytes, limit: int | None = None) -> list[tuple[int, int]]:
    """@return `(offset, tag)` for every aligned dword in tag range."""
    end = len(data) - 3 if limit is None else min(len(data) - 3, limit)
    out = []
    for at in range(0, end, 4):
        (value,) = struct.unpack_from("<I", data, at)
        if is_tag(value):
            out.append((at, value))
    return out


def describe(tag: int) -> str:
    got = resolve(tag)
    if got is None:
        return "unresolved"
    class_id, size, family, name = got
    dumped_mark = " dumped" if dumped(tag) is not None else ""
    return f"class 0x{class_id:08X}  {size:>9,} B  {family}{dumped_mark}  {name}"


def show_tag(tag: int) -> None:
    print(f"  0x{tag:08X}  {describe(tag)}")


def hash_table_hash(data: bytes, index: int) -> int | None:
    """Reads one art-arrangement hash from Monteven's `index * 4 + 48` table."""
    at = 48 + index * 4
    if at + 4 > len(data) or index < 0:
        return None
    (value,) = struct.unpack_from("<I", data, at)
    return value


def characterize(tag: int) -> None:
    data = dumped(tag)
    print(f"\n=== 0x{tag:08X}  {describe(tag)} ===")
    if data is None:
        print("  not dumped")
        return
    print(f"  {len(data):,} bytes")
    count, start, element = array_at(data, 8)
    if count:
        payload = len(data) - start
        stride = payload // count if count else 0
        print(f"  array@8  count={count:,}  data@{start}  element 0x{element:08X}  "
              f"stride~{stride}  leftover={payload - stride * count}")
        if 0 < stride <= 64 and count > 0:
            row = min(2293, count - 1)
            raw = data[start + row * stride : start + (row + 1) * stride]
            print(f"  row {row} ({stride} B): {raw.hex(' ')}")
            for at in range(0, len(raw) - 3, 4):
                (value,) = struct.unpack_from("<I", raw, at)
                extra = f"  {describe(value)}" if is_tag(value) else ""
                print(f"    +{at:02X}  0x{value:08X}{extra}")
    else:
        print("  no DynamicArray at offset 8")
    found = tags_in(data, 96 if len(data) > 256 else None)
    if found:
        print("  tags in prefix:")
        for at, value in found[:16]:
            print(f"    +{at:04X}  0x{value:08X}  {describe(value)}")
    if len(data) == 0x38:
        parse_38_parent(data)


def find_hash_in_table(data: bytes, needle: int) -> list[tuple[int, int, bytes]]:
    """@return `(stride, offset, row bytes)` for every aligned hit treated as a record start."""
    hits = []
    if needle == 0:
        return hits
    packed = struct.pack("<I", needle)
    start = 0
    while True:
        at = data.find(packed, start)
        if at < 0:
            return hits
        if at % 4 == 0:
            for stride in (8, 16, 20, 24, 32):
                if at >= 24 and (at - 24) % stride == 0:
                    hits.append((stride, at, data[at : at + stride]))
                    break
            else:
                hits.append((0, at, data[at : at + 16]))
        start = at + 4


def follow_parent(tag: int, depth: int = 0) -> list[int]:
    """Walks entity-parent → SEntity → resource → SEntityModel. Returns model tags found."""
    indent = "    " + "  " * depth
    data = dumped(tag)
    got = resolve(tag)
    class_id = got[0] if got else 0
    print(f"{indent}0x{tag:08X}  {describe(tag)}")
    models: list[int] = []
    if data is None:
        print(f"{indent}  (need dump)")
        return models
    if class_id == ENTITY_MODEL:
        return [tag]
    if class_id == ENTITY_PARENT_CLASS or len(data) == 0x18:
        for at, value in tags_in(data):
            print(f"{indent}  +{at:02X} →")
            models.extend(follow_parent(value, depth + 1))
        return models
    if class_id == ENTITY:
        count, start, _element = array_at(data, 0x10)
        print(f"{indent}  SEntity resources {count} @ {start}")
        for index in range(min(count, 16)):
            (resource,) = struct.unpack_from("<I", data, start + index * 12)
            if is_tag(resource):
                models.extend(follow_parent(resource, depth + 1))
        return models
    if class_id == ENTITY_RESOURCE:
        for at, value in tags_in(data):
            child = resolve(value)
            if child and child[0] == ENTITY_MODEL:
                print(f"{indent}  model @ +{at:02X}")
                models.append(value)
                show_tag(value)
        if not models:
            print(f"{indent}  (no SEntityModel tag in resource; {len(tags_in(data))} other tags)")
        return models
    for at, value in tags_in(data)[:8]:
        child = resolve(value)
        if child and child[0] in (ENTITY, ENTITY_RESOURCE, ENTITY_MODEL, ENTITY_PARENT_CLASS):
            models.extend(follow_parent(value, depth + 1))
    return models


def tag_for(package_id: int, entry_index: int) -> int:
    return TAG_BASE + (package_id << TAG_ENTRY_BITS) + entry_index


def census_assignment_sizes() -> None:
    """Offline: find investment entries whose size matches a 4,301-row table."""
    n = ARRANGEMENT_COUNT
    targets: dict[int, str] = {}
    for header in (0, 8, 16, 24, 40, 48, 56):
        for stride, label in (
            (4, "hash"),
            (8, "hash+parent / D1 map"),
            (16, "WQ-ish row"),
            (20, "row"),
            (24, "D1 assignment"),
            (32, "WQ assignment"),
        ):
            targets[header + n * stride] = f"{n}×{stride}+{header} ({label})"
    targets[24 + 16236 * 8] = "16,236×8+24 (entity map if 1:1)"
    targets[48 + 16236 * 8] = "16,236×8+48"
    print(f"searching investment packages for {len(targets)} exact sizes")
    newest: dict[str, Path] = {}
    for path in PACKAGES.glob("*.pkg"):
        if "investment" not in path.name.lower():
            continue
        stem = path.stem.rsplit("_", 1)[0]
        tail = path.stem.rsplit("_", 1)[1]
        patch = int(tail) if tail.isdigit() else -1
        if stem not in newest or patch > int(newest[stem].stem.rsplit("_", 1)[1]):
            newest[stem] = path
    hits = 0
    for path in sorted(newest.values()):
        try:
            pkg = Package(path)
        except PackageError:
            continue
        for entry in pkg.entries:
            label = targets.get(entry.size)
            if label is None:
                continue
            tag = pkg.tag_for(entry.index)
            dumped_mark = " dumped" if dumped(tag) is not None else ""
            print(f"  0x{tag:08X}  class 0x{entry.reference:08X}  {entry.size:>9,} B  "
                  f"{label}  {path.name}{dumped_mark}")
            hits += 1
    print(f"{hits} hits")


def parse_38_parent(data: bytes) -> None:
    """Charm A44E8080: Tag64 at 0x10 (sandbox patterns), Tag64 at 0x28 (entity assignment map)."""
    if len(data) < 0x38:
        print(f"  0x38 parent is {len(data)} B, not 56")
        return
    for at, name in ((0x10, "sandboxPatternAssignments?"), (0x28, "entityAssignmentsMap?")):
        (value,) = struct.unpack_from("<I", data, at)
        extra = f"  {describe(value)}" if is_tag(value) else ""
        print(f"  +{at:02X} {name}  0x{value:08X}{extra}")
        if is_tag(value):
            follow_parent(value)


def scan_dumped_for_count(want: int = ARRANGEMENT_COUNT) -> None:
    """Any dumped blob whose DynamicArray count is 4,301 is the assignment table."""
    print(f"\n=== dumped arrays with count {want} ===")
    found = False
    for path in sorted(DUMP.glob("tag_*.bin")):
        data = path.read_bytes()
        for at in range(0, min(len(data), 256) - 15, 8):
            count, start, element = array_at(data, at)
            if count != want:
                continue
            tag = int(path.stem.split("_")[1], 16)
            payload = len(data) - start
            stride = payload // count if count else 0
            print(f"  0x{tag:08X}  array@{at}  data@{start}  element 0x{element:08X}  "
                  f"stride~{stride}  {describe(tag)}")
            found = True
    if not found:
        print("  none (need the 0x38 parents / large tables dumped)")


def walk_index(index: int) -> None:
    print(f"\n--- arrangement {index} ---")
    hashes = dumped(ARRANGEMENT_HASH_TABLE)
    if hashes is None:
        print("  hash table not dumped; cannot name the arrangement hash yet")
    else:
        arrangement_hash = hash_table_hash(hashes, index)
        print(f"  artArrangementHash 0x{arrangement_hash:08X}" if arrangement_hash is not None
              else "  index past end of hash table")
        if arrangement_hash:
            for tag in (SLOT4_ASSIGNMENT_CANDIDATE, HASH_TABLE_SIBLING, PARENT_38_A, PARENT_38_B,
                        *MAP_CANDIDATES, *SIZE_MAP_CANDIDATES):
                blob = dumped(tag)
                if blob is None:
                    continue
                hits = find_hash_in_table(blob, arrangement_hash)
                if not hits:
                    continue
                print(f"  hash occurs in 0x{tag:08X} ({len(hits)} hits):")
                for stride, at, row in hits[:6]:
                    print(f"    stride {stride} @{at}: {row.hex(' ')}")
                    for off in range(0, len(row) - 3, 4):
                        (value,) = struct.unpack_from("<I", row, off)
                        if is_tag(value):
                            print(f"      +{off:02X} 0x{value:08X}")
                            follow_parent(value)
    assignment = dumped(SLOT4_ASSIGNMENT_CANDIDATE)
    if assignment is None:
        print("  slot 4 not dumped")
        return
    count, start, element = array_at(assignment, 8)
    if not count:
        print("  slot 4 has no array at offset 8; prefix tags:")
        for at, value in tags_in(assignment, 128)[:12]:
            print(f"    +{at:04X}  0x{value:08X}  {describe(value)}")
        return
    if index >= count:
        print(f"  slot 4 count is {count}, index {index} is past the end")
        return
    if count != ARRANGEMENT_COUNT:
        print(f"  slot 4 is not the assignment table (count {count}, need {ARRANGEMENT_COUNT})")
        return
    payload = len(assignment) - start
    stride = payload // count
    print(f"  slot 4 row {index}/{count}  element 0x{element:08X}  stride~{stride}")
    if stride <= 0 or stride > 512:
        return
    row = assignment[start + index * stride : start + (index + 1) * stride]
    print(f"  {row[:64].hex(' ')}{' …' if len(row) > 64 else ''}")
    for at in range(0, min(len(row), 64) - 3, 4):
        (value,) = struct.unpack_from("<I", row, at)
        if is_tag(value) or value not in (0, 0xFFFFFFFF):
            extra = f"  {describe(value)}" if is_tag(value) else ""
            print(f"    +{at:02X}  0x{value:08X}{extra}")
            if is_tag(value):
                follow_parent(value)


def write_request() -> None:
    lines = [
        "# Arrangement index -> SEntityModel. One launch, then python lookup_arrangement.py",
        "# Bodies need the running game's block keys. Entry tables already named every tag.",
        "",
        "# Investment root - 1,912 B, every slot tag.",
        f"tag 0x{INVESTMENT_ROOT:08X}",
        "",
        "# Art-arrangement index -> hash. 4,301 x 4 + 48. Monteven's +48 table.",
        f"tag 0x{ARRANGEMENT_HASH_TABLE:08X}",
        "",
        "# 0x38 parents (Charm A44E8080 shape) - should name the assignment-map tag.",
        f"tag 0x{PARENT_38_A:08X}",
        f"tag 0x{PARENT_38_B:08X}",
        f"tag 0x{HASH_TABLE_SIBLING:08X}   # 24-byte sibling of the hash table",
        "",
        "# Slot 4 is NOT the assignment table (1,170 rows). Dump it only if still missing.",
        f"tag 0x{SLOT4_ASSIGNMENT_CANDIDATE:08X}",
        f"tag 0x{SLOT4_SIBLING:08X}",
        "",
        "# Large undumped tables that can hold nested assignment rows.",
    ]
    for tag in MAP_CANDIDATES:
        lines.append(f"tag 0x{tag:08X}")
    lines += ["", "# Entity-parent samples. Class 0x8080744A, 16,236 x 24 B."]
    for tag in ENTITY_PARENT_SAMPLES:
        lines.append(f"tag 0x{tag:08X}")
    kept: list[str] = []
    skipped = 0
    for line in lines:
        if line.startswith("tag 0x"):
            tag = int(line.split()[1], 16)
            if dumped(tag) is not None:
                skipped += 1
                continue
        kept.append(line)
    REQUEST.parent.mkdir(parents=True, exist_ok=True)
    REQUEST.write_text("\r\n".join(kept) + "\r\n", encoding="ascii")
    print(f"wrote {sum(1 for line in kept if line.startswith('tag '))} requests "
          f"({skipped} already dumped) to {REQUEST}")
    print("Launch the game once (no patch installed). Dump runs at investment refresh,")
    print("before orbit. Then:  python lookup_arrangement.py")


def main() -> None:
    if "--request" in sys.argv:
        write_request()
        return
    if "--census" in sys.argv:
        census_assignment_sizes()
        return
    if "--inspect" in sys.argv:
        tag = int(sys.argv[sys.argv.index("--inspect") + 1], 0)
        characterize(tag)
        return

    print(f"dumps: {DUMP}")
    needed = [
        PARENT_38_A,
        PARENT_38_B,
        *MAP_CANDIDATES,
    ]
    missing = [tag for tag in needed if dumped(tag) is None]
    if missing:
        print("not yet dumped:")
        for tag in missing:
            show_tag(tag)
        print("\nWrite the request and launch once:")
        print("  python lookup_arrangement.py --request")
        print("  (start Destiny, then close it)")
        print("  python lookup_arrangement.py")
        if not any(dumped(tag) is not None for tag in
                   (ARRANGEMENT_HASH_TABLE, SLOT4_ASSIGNMENT_CANDIDATE, INVESTMENT_ROOT)):
            return

    for tag in (INVESTMENT_ROOT, ARRANGEMENT_HASH_TABLE, PARENT_38_A, PARENT_38_B,
                HASH_TABLE_SIBLING, SLOT4_ASSIGNMENT_CANDIDATE, SLOT4_SIBLING,
                *ENTITY_PARENT_SAMPLES, *MAP_CANDIDATES, *SIZE_MAP_CANDIDATES):
        if dumped(tag) is not None:
            characterize(tag)

    scan_dumped_for_count()

    root = dumped(INVESTMENT_ROOT)
    if root is not None:
        print("\n=== investment-root slots ===")
        for index in range((len(root) - 8) // 16):
            (tag,) = struct.unpack_from("<I", root, 8 + index * 16)
            if tag == 0:
                continue
            mark = ""
            if index == 4:
                mark = "  <- slot 4"
            elif index == 48:
                mark = "  <- item table"
            elif index == 17:
                mark = "  <- buckets"
            print(f"  slot {index:3d}  0x{tag:08X}  {describe(tag)}{mark}")

    indices = [int(argument) for argument in sys.argv[1:] if argument.lstrip("-").isdigit()]
    if not indices:
        rows = {row["hash"]: row for row in load_details()}
        print("\n=== equipped loadout ===")
        for name, item_hash in EQUIPPED:
            row = rows.get(item_hash)
            if row is None:
                print(f"  {name}  0x{item_hash:08X}  not in cache")
                continue
            arrangement = next((value for value in row["arr"] if value != UNSET), UNSET)
            print(f"  {name:10}  0x{item_hash:08X}  arr={arrangement}")
            if arrangement != UNSET:
                indices.append(arrangement)
    for index in indices:
        walk_index(index)


if __name__ == "__main__":
    main()
