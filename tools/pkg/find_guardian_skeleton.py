"""Find the Shadowkeep Guardian bind-pose table.

Charm does not store the SK skeleton as a package-entry type. Entity.Load treats an
entity-resource as FK skeleton when Unk10 is class 0x80808545 (45858080); GetBoneNodes
then reads Unk18 as 0x80808546 (46858080). NodeHierarchy rows are class 0x80808A08;
transform rows are 0x80809F75.

This hunts:
  1. Chest SEntity extra resources (0x81531EE8 / EE9) — encrypted, dump required
  2. F8 paytags/r9tags sitting next to 0x80808545/46
  3. Already-dumped blobs for Monteven bone-name hashes and the bind-pose class
"""
from __future__ import annotations

import collections
import re
import struct
import sys
from pathlib import Path

from lookup_arrangement import array_at, dumped, resolve, tags_in
from tigerpkg import TAG_BASE, TAG_ENTRY_BITS, Package

DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
DUMP_MODELS = Path(r"C:\Sunrise\bin\x64\Sunrise\dump_models")
CAPTURES = Path(r"C:\Users\Round\OneDrive\Desktop\Destiny2ProjectSunrise\reference\captures")
CHEST_ENTITY = 0x80EFA1D8
CHEST_EXTRAS = (0x81531EE8, 0x81531EE9)
FK = 0x80808545
BIND = 0x80808546
NODE_ROW = 0x80808A08
XF_ROW = 0x80809F75
ENTITY_RESOURCE = 0x80809C36
TAG_RE = re.compile(r"0x([0-9A-Fa-f]{8})")
FIELD_RE = re.compile(
    r"\b(r9tags|paytags|dsttags)=((?:0x[0-9A-Fa-f]{8})(?:,0x[0-9A-Fa-f]{8})*)"
)

# MontevenDynamicExtractor/skeleton.h — enough to prove a bind-pose table.
BONE_HASHES = {
    1857732807: "Pelvis",
    2728809121: "Spine_1",
    2728809122: "Spine_2",
    2728809123: "Spine_3",
    976064003: "Clav.L",
    2720736209: "UpperArm.L",
    68516489: "ForeArm.L",
    1741390732: "Hand.L",
    375707561: "Clav.R",
    1348403735: "UpperArm.R",
    3441260707: "ForeArm.R",
    3932921310: "Hand.R",
    1087804030: "Head",
    280710669: "Neck_1",
    162487657: "Thigh.L",
    2362576799: "Thigh.R",
}


def is_class(value: int) -> bool:
    return 0x80800000 <= value <= 0x8080FFFF


def describe(tag: int) -> str:
    got = resolve(tag)
    if got is None:
        return "unresolved"
    class_id, size, family, name = got
    mark = " dumped" if dumped(tag) is not None else ""
    return f"class 0x{class_id:08X}  {size:>7,} B  {family}{mark}  {name}"


def resource_pointer(data: bytes, field: int) -> tuple[int, int]:
    """@return (absolute offset, class hash) for a Charm ResourcePointer."""
    if field + 8 > len(data):
        return 0, 0
    relative = struct.unpack_from("<q", data, field)[0]
    if relative == 0:
        return 0, 0
    absolute = field + relative
    if absolute < 4 or absolute > len(data):
        return absolute, 0
    return absolute, struct.unpack_from("<I", data, absolute - 4)[0]


def parse_entity_resource(tag: int, data: bytes) -> None:
    print(f"  entity resource 0x{tag:08X}  {len(data)} B")
    for field, name in ((0x08, "Unk08"), (0x10, "Unk10"), (0x18, "Unk18")):
        absolute, class_id = resource_pointer(data, field)
        mark = ""
        if class_id == FK:
            mark = "  *** FK skeleton ***"
        elif class_id == BIND:
            mark = "  *** BIND POSE ***"
        print(f"    {name} @ +0x{field:02X} -> +0x{absolute:X}  class 0x{class_id:08X}{mark}")
    if len(data) >= 0x88:
        h80, h84 = struct.unpack_from("<II", data, 0x80)
        if h80:
            print(f"    UnkHash80 0x{h80:08X}  {describe(h80)}")
        if h84:
            print(f"    UnkHash84 0x{h84:08X}  {describe(h84)}")
    found = [name for name, hash_ in ((n, h) for h, n in BONE_HASHES.items())
             if struct.pack("<I", hash_) in data]
    # rebuild properly
    found = [BONE_HASHES[h] for h in BONE_HASHES if struct.pack("<I", h) in data]
    if found:
        print(f"    Monteven names in blob: {', '.join(found)}")
    joints = try_bind_pose(data)
    if joints:
        print(f"    parsed {len(joints)} joints from this blob")


def try_bind_pose(data: bytes) -> list[dict] | None:
    """Charm SK D2Class_DE818080: NodeHierarchy at +0x80 of the bind-pose struct."""
    for base in (0, 0x10, 0x18, 0x40, 0x78, 0x80):
        if base + 48 > len(data):
            continue
        count, start, element = array_at(data, base)
        if not (20 <= count <= 256):
            continue
        xf = array_at(data, base + 16)
        inv = array_at(data, base + 32)
        if xf[0] != count or inv[0] != count:
            continue
        named = 0
        joints = []
        for index in range(count):
            node_hash, parent, child, sibling = struct.unpack_from("<Iiii", data, start + index * 16)
            quat = struct.unpack_from("<4f", data, xf[1] + index * 32)
            trans = struct.unpack_from("<4f", data, xf[1] + index * 32 + 16)
            name = BONE_HASHES.get(node_hash, "")
            if name:
                named += 1
            joints.append({
                "index": index, "hash": node_hash, "name": name,
                "parent": parent, "child": child, "sibling": sibling,
                "t": trans[:3],
            })
        if named >= 4 or (element in (0, NODE_ROW) and count >= 40):
            print(f"    bind-pose array @ +0x{base:02X}: {count} nodes, {named} named, "
                  f"element 0x{element:08X}")
            return joints
    return None


def chest_sentity() -> None:
    print("=== chest SEntity 0x80EFA1D8 ===")
    data = dumped(CHEST_ENTITY)
    if data is None:
        print("  not dumped")
        return
    count, start, element = array_at(data, 0x10)
    print(f"  {len(data)} B, {count} resources, element 0x{element:08X}")
    for index in range(count):
        tag = struct.unpack_from("<I", data, start + index * 12)[0]
        extra = "  CHEST-ONLY CANDIDATE" if tag in CHEST_EXTRAS else ""
        print(f"  [{index}] 0x{tag:08X}  {describe(tag)}{extra}")
        body = dumped(tag)
        if body is not None:
            parse_entity_resource(tag, body)
        elif tag in CHEST_EXTRAS:
            print("    encrypted in 0698 — needs dump/request.txt")


def scan_dumps_for_bones() -> None:
    print("\n=== dump scan: Monteven hashes / bind-pose class ===")
    hits_hash: dict[Path, list[str]] = {}
    hits_bind: list[Path] = []
    needle_bind = struct.pack("<I", BIND)
    for folder in (DUMP, DUMP_MODELS):
        if not folder.is_dir():
            continue
        for path in folder.glob("tag_*.bin"):
            blob = path.read_bytes()
            names = [BONE_HASHES[h] for h in BONE_HASHES if struct.pack("<I", h) in blob]
            if names:
                hits_hash[path] = names
            if needle_bind in blob:
                hits_bind.append(path)
    if not hits_hash:
        print("  no Monteven bone hashes in any dumped tag")
    else:
        for path, names in sorted(hits_hash.items()):
            print(f"  {path.name}  {len(path.read_bytes()):,} B  {', '.join(names)}")
    if not hits_bind:
        print("  class 0x80808546 (bind pose) still absent from dumps")
    else:
        for path in hits_bind:
            print(f"  bind class in {path.name}")


def f8_neighbors() -> dict[int, int]:
    print("\n=== F8 neighbors of 0x80808545 / 0x80808546 ===")
    counts: collections.Counter[int] = collections.Counter()
    windows = 0
    for path in sorted(CAPTURES.glob("*.log")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "ev=model_class_trace" not in line:
                continue
            if "80808545" not in line and "80808546" not in line:
                continue
            values = set()
            for field, blob in FIELD_RE.findall(line):
                values.update(int(part, 16) for part in blob.split(","))
            if FK not in values and BIND not in values:
                continue
            windows += 1
            for value in values:
                if not is_class(value) and 0x80801000 < value:
                    counts[value] += 1
    print(f"  {windows} capture windows")
    print(f"  {'tag':12} {'n':>5}  resolve")
    for tag, n in counts.most_common(40):
        print(f"  0x{tag:08X}  {n:5d}  {describe(tag)}")
    return counts


def extras_package() -> None:
    print("\n=== 0x81531EE8 / EE9 package rows ===")
    for tag in CHEST_EXTRAS:
        print(f"  0x{tag:08X}  {describe(tag)}")


def main() -> None:
    extras_package()
    chest_sentity()
    scan_dumps_for_bones()
    f8_neighbors()
    print("\nDump next (character select, then close):")
    print("  tag 0x81531EE8")
    print("  tag 0x81531EE9")
    print("plus any F8 neighbor that is an entity resource (~1–20 KB) and not yet dumped.")


if __name__ == "__main__":
    main()
