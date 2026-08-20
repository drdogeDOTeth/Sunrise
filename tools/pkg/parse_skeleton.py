"""Parse the Guardian bind-pose table once the chest skeleton resources are dumped.

Charm SK: an entity resource is FK skeleton when Unk10 is class 0x80808545 (45858080).
GetBoneNodes() then reads Unk18 as 0x80808546 (46858080). NodeHierarchy sits at +0x80 of
that struct (class 0x80808A08 rows); object-space and inverse transforms follow.

Those classes are nested, not package-entry types. The equipped chest SEntity 0x80EFA1D8
names two encrypted resources that were never dumped:

    0x81531EE8 / 0x81531EE9   sandbox_0698  976 B  class 0x80809C36

Dump (character select, then close):

    C:\\Sunrise\\bin\\x64\\Sunrise\\dump\\request.txt

then:

    python parse_skeleton.py

**2026-08-20:** both tags dumped. Unk10 is 0x80803EB6, Unk18 is 0x80803EB7 — not Charm FK/bind.
Armor SEntities do not carry the player bind-pose table. See HANDOFF.md.

Do not dump rewritten 037c / 037d / 0698 mesh buffers. These two entries were never patched.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from lookup_arrangement import array_at, dumped, resolve, tags_in

DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
OUT = Path(__file__).with_name("objs") / "skeleton"
REQUEST = DUMP / "request.txt"
CHEST_ENTITY = 0x80EFA1D8
SK_RESOURCES = (0x81531EE8, 0x81531EE9)
FK = 0x80808545
BIND = 0x80808546
NODE_ROW = 0x80808A08
XF_ROW = 0x80809F75
INLINE_MAX = 0x80801000
TAG_MAX = 0x80800000 + (0x0FFF << 13) + 0x1FFF

BONE_HASHES = {
    3848821786: "Pedestal",
    1857732807: "Pelvis",
    162487657: "Thigh.L",
    458076469: "Calf.L",
    1565559567: "Foot.L",
    988249757: "Toe.L",
    2362576799: "Thigh.R",
    1061868683: "Calf.R",
    1575008697: "Foot.R",
    847516523: "Toe.R",
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
    280710669: "Neck_1",
    280710670: "Neck_2",
    1087804030: "Head",
}


def option(name: str, fallback):
    return type(fallback)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


def is_tag(value: int) -> bool:
    return INLINE_MAX < value <= TAG_MAX


def describe(tag: int) -> str:
    got = resolve(tag)
    if got is None:
        return "unresolved"
    class_id, size, family, name = got
    mark = " dumped" if dumped(tag) is not None else ""
    return f"class 0x{class_id:08X}  {size:>7,} B  {family}{mark}  {name}"


def resource_pointer(data: bytes, field: int) -> tuple[int, int]:
    if field + 8 > len(data):
        return 0, 0
    relative = struct.unpack_from("<q", data, field)[0]
    if relative == 0:
        return 0, 0
    absolute = field + relative
    if absolute < 4 or absolute > len(data):
        return absolute, 0
    return absolute, struct.unpack_from("<I", data, absolute - 4)[0]


def parse_nodes(data: bytes, base: int) -> list[dict] | None:
    """Charm SK D2Class_DE818080 NodeHierarchy at `base`."""
    hier = array_at(data, base)
    xf = array_at(data, base + 16)
    inv = array_at(data, base + 32)
    count, start, element = hier
    if not (8 <= count <= 256) or xf[0] != count:
        return None
    if start + count * 16 > len(data) or xf[1] + count * 32 > len(data):
        return None
    joints = []
    named = 0
    for index in range(count):
        node_hash, parent, child, sibling = struct.unpack_from("<Iiii", data, start + index * 16)
        quat = struct.unpack_from("<4f", data, xf[1] + index * 32)
        trans = struct.unpack_from("<4f", data, xf[1] + index * 32 + 16)
        inv_t = (0.0, 0.0, 0.0, 1.0)
        if inv[0] == count and inv[1] + count * 32 <= len(data):
            inv_t = struct.unpack_from("<4f", data, inv[1] + index * 32 + 16)
        name = BONE_HASHES.get(node_hash, "")
        if name:
            named += 1
        joints.append({
            "index": index,
            "hash": f"0x{node_hash:08X}",
            "name": name,
            "parent": parent,
            "child": child,
            "sibling": sibling,
            "quat": [round(v, 6) for v in quat],
            "t": [round(v, 6) for v in trans[:3]],
            "s": round(trans[3], 6),
            "inv_t": [round(v, 6) for v in inv_t[:3]],
        })
    print(f"    NodeHierarchy @ +0x{base:02X}: {count} joints, {named} Monteven names, "
          f"element 0x{element:08X}")
    return joints


def parse_entity_resource(tag: int, data: bytes) -> tuple[list[dict] | None, list[int]]:
    print(f"\n=== entity resource 0x{tag:08X}  {len(data)} B  {describe(tag)} ===")
    joints = None
    follow: list[int] = []
    for field, name in ((0x08, "Unk08"), (0x10, "Unk10"), (0x18, "Unk18")):
        absolute, class_id = resource_pointer(data, field)
        mark = ""
        if class_id == FK:
            mark = "  *** FK skeleton (Charm 45858080) ***"
        elif class_id == BIND:
            mark = "  *** BIND POSE (Charm 46858080) ***"
        print(f"  {name} -> +0x{absolute:X}  class 0x{class_id:08X}{mark}")
        if class_id == BIND or name == "Unk18":
            for extra in (0x80, 0x90, 0x78, 0):
                found = parse_nodes(data, absolute + extra) if absolute else None
                if found:
                    joints = found
                    break
        if class_id == FK and absolute:
            found = parse_nodes(data, absolute + 0x30) or parse_nodes(data, absolute + 0x20)
            if found:
                joints = joints or found
    if len(data) >= 0x88:
        h80, h84 = struct.unpack_from("<II", data, 0x80)
        for label, value in (("UnkHash80", h80), ("UnkHash84", h84)):
            if is_tag(value):
                print(f"  {label} 0x{value:08X}  {describe(value)}")
                follow.append(value)
    for at, value in tags_in(data):
        if is_tag(value) and value not in follow and value != tag:
            follow.append(value)
    named = [BONE_HASHES[h] for h in BONE_HASHES if struct.pack("<I", h) in data]
    if named:
        print(f"  Monteven names in blob: {', '.join(named)}")
    if follow:
        print("  follow-on tags:")
        for value in follow[:24]:
            print(f"    0x{value:08X}  {describe(value)}")
    return joints, follow


def write_follow_on(tags: list[int]) -> None:
    need = []
    seen = set()
    for tag in tags:
        if tag in seen or not is_tag(tag) or dumped(tag) is not None:
            continue
        seen.add(tag)
        got = resolve(tag)
        if got is None:
            continue
        class_id, size, family, _ = got
        # Skip rewritten mesh packages. EE8/EE9 themselves are allowed (never patched).
        if "sandbox_037c" in family or "sandbox_037d" in family:
            continue
        if "sandbox_0698" in family and tag not in SK_RESOURCES:
            # 0698 holds rewritten hanging-panel buffers; only the two skeleton
            # resources on this SEntity are safe.
            continue
        if class_id in (0x80809C36, BIND, FK, 0x80809C0F) or 200 <= size <= 80_000:
            need.append(tag)
    if not need:
        return
    lines = [
        "# Follow-on from chest skeleton resources. Character select, then close.",
        "# python parse_skeleton.py",
        "",
    ]
    for tag in need:
        lines.append(f"tag 0x{tag:08X}")
    REQUEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {len(need)} follow-on dumps -> {REQUEST}")
    for tag in need:
        print(f"  0x{tag:08X}  {describe(tag)}")


def parse_chest_resources() -> list[dict] | None:
    print("=== chest SEntity 0x80EFA1D8 ===")
    data = dumped(CHEST_ENTITY)
    if data is None:
        print("  not dumped")
        return None
    count, start, _ = array_at(data, 0x10)
    print(f"  {len(data)} B, {count} resources")
    joints = None
    follow: list[int] = []
    for index in range(count):
        tag = struct.unpack_from("<I", data, start + index * 12)[0]
        extra = "  <- dump target" if tag in SK_RESOURCES else ""
        print(f"  [{index}] 0x{tag:08X}  {describe(tag)}{extra}")
        if tag not in SK_RESOURCES:
            continue
        body = dumped(tag)
        if body is None:
            continue
        got, kids = parse_entity_resource(tag, body)
        follow.extend(kids)
        if got and (joints is None or sum(1 for j in got if j["name"]) >
                    sum(1 for j in (joints or []) if j["name"])):
            joints = got
    write_follow_on(follow)
    return joints


def write_hierarchy(joints: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "hierarchy.json"
    path.write_text(json.dumps({"count": len(joints), "joints": joints}, indent=2), encoding="utf-8")
    obj = ["# Guardian bind pose from Charm SK Unk18", "o guardian_bind"]
    for joint in joints:
        t = joint["t"]
        label = joint["name"] or joint["hash"]
        obj.append(f"v {t[0]} {t[1]} {t[2]}  # {joint['index']} {label}")
    (OUT / "hierarchy.obj").write_text("\n".join(obj) + "\n", encoding="ascii")
    print(f"\nwrote {len(joints)} joints -> {path}")
    print(f"{'idx':>4} {'parent':>6} {'name':<16} {'hash':12} {'x':>8} {'y':>8} {'z':>8}")
    for joint in joints:
        t = joint["t"]
        print(f"{joint['index']:4d} {joint['parent']:6d} {joint['name']:<16} {joint['hash']:12} "
              f"{t[0]:8.3f} {t[1]:8.3f} {t[2]:8.3f}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    missing = [tag for tag in SK_RESOURCES if dumped(tag) is None]
    if missing:
        print("Chest skeleton resources are still encrypted. Not dumped:")
        for tag in missing:
            print(f"  0x{tag:08X}  {describe(tag)}")
        print(f"\nrequest.txt is {REQUEST}")
        print("Close Destiny, launch to character select, close again, then re-run this.")
        return
    joints = parse_chest_resources()
    if joints:
        write_hierarchy(joints)
        return
    print("\nNo bind-pose array in the chest resources yet (follow-on dump may be queued).")


if __name__ == "__main__":
    main()
