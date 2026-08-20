"""Resolve Scatterhorn dye indices to the entity-parents that bind live albedo.

The `_23` flat-colour probe proved the 55 large sandbox textures are not sampled by the Guardian.
Armor materials name shaders and samplers only; the pixel shader declares t0/t1/t2/t5/t6 but the
material's Charm PSTextures array is empty. Textures arrive at draw time from the appearance
record's six material pairs (a shader plug), which index dyes.

Scatterhorn Robe's default pairs, from `build_data.bin`:

    key 0 -> dye 6714
    key 1 -> dye 6715
    key 2 -> dye 6716

Hop, proven:

    dye index
      -> 0x81613D24 art-dye row (hash, dyeManifestHash)
      -> 0x80EC3F60 assignment (dyeManifestHash, entity-parent)
      -> 0x8080744A entity-parent, 24 B, child at +0x10
      -> 0x808071CD dye stub, 24 B, 4,966 of them, sandbox_0207
         layout: size at +0x00, channel at +0x08 (0 plate / 1 suit / 2 cloth),
                 dye-body tag at +0x0C, 0xFFFFFFFF at +0x10
         (WQ used 0x80806FA3 here; SK does not)
      -> 0x808071F3 dye body, typically 1,515 B, sandbox_037c, encrypted
         +0x40 DyeTextures: count 2, class 0x80807211, {slot, 40-byte header}
         +0x88 DyeData: count 27, class 0x80800090 (float4)
         Scatterhorn slots: plate t3/t4 (64x64), suit t5/t6 (128x128), cloth t7/t8 (128x128)

`--request` writes whatever the chain still needs (undumped dye bodies, then their headers).

Usage:
    python dye_probe.py             # print the hop; follow any dumped parents
    python dye_probe.py --request   # write dump/request.txt, then launch once
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from bone_probe import dumped, entry_of
from lookup_item import CACHE, DETAIL, NAMED, ITEM, COLL, MATSET, UNSET, load_details

REQUEST = Path(r"C:\Sunrise\bin\x64\Sunrise\dump\request.txt")
CHEST = 0xF8689C4C
ART_DYE_TABLE = 0x81613D24
ASSIGNMENT_TABLE = 0x80EC3F60
ENTITY_PARENT_CLASS = 0x8080744A
# Shadowkeep dye relation. WQ uses 0x80806FA3; SK is this 24-byte stub, 4,966 of them.
DYE_STUB_CLASS = 0x808071CD
# SK dye payload. WQ's SDye is 0x80806DBA (888 B); SK is this 1,515 B class, 5,029 of them.
DYE_BODY_CLASS = 0x808071F3
DYE_TEXTURE_CLASS = 0x80807211
DYE_VEC4_CLASS = 0x80800090
INLINE = 0x80801000
TAG_HI = 0x83FFFFFF


def is_tag(value: int) -> bool:
    return INLINE < value <= TAG_HI


def describe(tag: int) -> str:
    entry = entry_of(tag)
    if entry is None:
        return "unresolved"
    body = dumped(tag)
    extra = f" dumped={len(body)}" if body is not None else ""
    return f"ref 0x{entry.reference:08X} {entry.size} B{extra}"


def array_at(data: bytes, at: int = 8) -> tuple[int, int, int]:
    count, relative = struct.unpack_from("<qq", data, at)
    header = at + 8 + relative
    start = header + 16
    _hcount, element = struct.unpack_from("<QI", data, header)
    return count, start, element


def overrides_of(item_hash: int) -> list[tuple[int, int, int]]:
    """@return `(stage, key, value)` rows for one item definition."""
    blob = CACHE.read_bytes()
    counts = struct.unpack_from("<" + "I" * 22, blob, 28)
    offset = 132 + counts[0] * NAMED + counts[1] * ITEM + counts[2] * COLL + counts[3] * MATSET
    out = []
    for index in range(counts[4]):
        rec = blob[offset + index * DETAIL : offset + (index + 1) * DETAIL]
        (found,) = struct.unpack_from("<I", rec, 142)
        if found != item_hash:
            continue
        count = rec[165]
        for row in range(count):
            stage = rec[166 + row]
            key = struct.unpack_from("<b", rec, 198 + row)[0]
            (value,) = struct.unpack_from("<H", rec, 230 + row * 2)
            out.append((stage, key, value))
        return out
    return out


def lookup_row(tag: int, index: int) -> bytes | None:
    data = dumped(tag)
    if data is None:
        return None
    count, start, _element = array_at(data)
    stride = (len(data) - start) // count
    if not 0 <= index < count:
        return None
    return data[start + index * stride : start + (index + 1) * stride]


def find_hash(tag: int, needle: int) -> tuple[int, bytes] | None:
    data = dumped(tag)
    if data is None:
        return None
    packed = struct.pack("<I", needle)
    at = data.find(packed)
    while at >= 0:
        if at % 4 == 0:
            return at, data[at : at + 8]
        at = data.find(packed, at + 1)
    return None


def child_offset(class_id: int) -> int:
    """Entity-parent keeps the child at +0x10; the SK dye stub puts it at +0x0C."""
    return 0x0C if class_id == DYE_STUB_CLASS else 0x10


def child_at(body: bytes, class_id: int = 0) -> tuple[int, int] | None:
    """@return `(offset, tag)` for the stub's child, or None."""
    at = child_offset(class_id)
    if len(body) < at + 4:
        return None
    (value,) = struct.unpack_from("<I", body, at)
    if not is_tag(value):
        return None
    return at, value


def scan_tags(body: bytes, indent: str, limit: int = 0) -> None:
    end = len(body) if limit <= 0 else min(len(body), limit)
    for at in range(0, end - 3, 4):
        (value,) = struct.unpack_from("<I", body, at)
        if is_tag(value) and entry_of(value) is not None:
            print(f"{indent}+{at:03X} 0x{value:08X}  {describe(value)}")


def try_array(data: bytes, at: int) -> tuple[int, int, int] | None:
    """@return `(count, data offset, element class)` for a Charm DynamicArray, or None."""
    if at + 16 > len(data):
        return None
    count, relative = struct.unpack_from("<qq", data, at)
    header = at + 8 + relative
    start = header + 16
    if not 1 <= count <= 64 or header < 0 or header + 16 > len(data) or start > len(data):
        return None
    header_count, element = struct.unpack_from("<QI", data, header)
    if header_count != count:
        return None
    return count, start, element


def show_dye_body(body: bytes, indent: str) -> list[int]:
    """Prints SK DyeTextures / DyeData and returns undumped 40-byte texture headers."""
    needed: list[int] = []
    name_at = 0x08 + struct.unpack_from("<q", body, 0x08)[0]
    if 0 <= name_at < len(body):
        name = body[name_at:].split(b"\x00", 1)[0].decode("ascii", "replace")
        print(f"{indent}name {name!r}")
    textures = try_array(body, 0x40)
    if textures:
        count, start, element = textures
        print(f"{indent}DyeTextures count={count} class 0x{element:08X}")
        for index in range(count):
            slot, header = struct.unpack_from("<II", body, start + index * 8)
            header_entry = entry_of(header)
            data_tag = header_entry.reference if header_entry else 0
            data_entry = entry_of(data_tag)
            data_bits = f" small 0x{data_tag:08X} {data_entry.size} B" if data_entry else ""
            header_body = dumped(header)
            extra = ""
            if header_body and len(header_body) >= 40:
                data_size, fmt = struct.unpack_from("<II", header_body, 0)
                width = struct.unpack_from("<H", header_body, 0x0E)[0]
                (large,) = struct.unpack_from("<I", header_body, 0x24)
                large_entry = entry_of(large)
                large_bits = f"{large_entry.size} B" if large_entry else "?"
                extra = (f"  {width}px fmt={fmt} large 0x{large:08X} {large_bits}"
                         f" (header size {data_size})")
            dumped_mark = f" dumped={len(header_body)}" if header_body else ""
            print(f"{indent}  t{slot}  header 0x{header:08X}{data_bits}{extra}{dumped_mark}")
            if dumped(header) is None:
                needed.append(header)
    data = try_array(body, 0x88)
    if data:
        count, start, element = data
        print(f"{indent}DyeData count={count} class 0x{element:08X}")
        for index in (0, 1, 3, 6):
            if index >= count or start + index * 16 + 16 > len(body):
                continue
            vec = struct.unpack_from("<ffff", body, start + index * 16)
            print(f"{indent}  [{index}] " + " ".join(f"{value:7.3f}" for value in vec))
    return needed


def follow_stub(tag: int, indent: str = "    ") -> list[int]:
    """Prints one hop and returns tags that still need a dump."""
    needed: list[int] = []
    entry = entry_of(tag)
    class_id = entry.reference if entry else 0
    print(f"{indent}0x{tag:08X}  {describe(tag)}")
    body = dumped(tag)
    if body is None:
        print(f"{indent}  not dumped")
        needed.append(tag)
        return needed
    if class_id == DYE_STUB_CLASS and len(body) >= 12:
        (channel,) = struct.unpack_from("<I", body, 0x08)
        print(f"{indent}  channel={channel}  (0 plate / 1 suit / 2 cloth)")
    if len(body) <= 32:
        print(f"{indent}  {body.hex(' ')}")
    hit = child_at(body, class_id)
    if hit is None:
        scan_tags(body, indent + "  ")
        return needed
    at, child = hit
    child_entry = entry_of(child)
    child_class = child_entry.reference if child_entry else 0
    print(f"{indent}  +{at:02X} -> 0x{child:08X}  {describe(child)}")
    if dumped(child) is None:
        needed.append(child)
        return needed
    if child_class in (ENTITY_PARENT_CLASS, DYE_STUB_CLASS) or (child_entry and child_entry.size == 24):
        needed.extend(follow_stub(child, indent + "  "))
        return needed
    nested = dumped(child)
    print(f"{indent}  child class 0x{child_class:08X} {len(nested):,} B")
    if child_class == DYE_BODY_CLASS:
        needed.extend(show_dye_body(nested, indent + "    "))
        return needed
    scan_tags(nested, indent + "    ")
    return needed


def dye_indices() -> list[tuple[str, int]]:
    """Default robe dyes plus the initial shader plug's key 0/1/2, if it has any."""
    out = [("robe_plate", 6714), ("robe_suit", 6715), ("robe_cloth", 6716)]
    rows = {row["hash"]: row for row in load_details()}
    by_idx = {row["idx"]: row for row in rows.values()}
    chest = rows[CHEST]
    for plug in chest["plugs"][: chest["sockets"]]:
        if plug == UNSET:
            continue
        owned = by_idx.get(plug)
        if owned is None:
            continue
        pairs = overrides_of(owned["hash"])
        if len(pairs) < 3:
            continue
        by_key = {key: value for stage, key, value in pairs if stage == 0}
        if {0, 1, 2} <= by_key.keys():
            out += [
                (f"shader_0x{owned['hash']:08X}_k0", by_key[0]),
                (f"shader_0x{owned['hash']:08X}_k1", by_key[1]),
                (f"shader_0x{owned['hash']:08X}_k2", by_key[2]),
            ]
            break
    return out


def parents_for(indices: list[tuple[str, int]]) -> list[int]:
    wanted: list[int] = []
    seen: set[int] = set()
    for name, index in indices:
        row = lookup_row(ART_DYE_TABLE, index)
        if row is None or len(row) < 8:
            print(f"  {name} dye {index}: art-dye row missing")
            continue
        art_hash, manifest = struct.unpack_from("<II", row, 0)
        hit = find_hash(ASSIGNMENT_TABLE, manifest)
        parent = None
        if hit:
            _at, pair = hit
            (_key, parent) = struct.unpack_from("<II", pair, 0)
        print(f"  {name} dye {index}: art=0x{art_hash:08X} manifest=0x{manifest:08X}"
              + (f" parent=0x{parent:08X} {describe(parent)}" if parent else " parent=?"))
        if parent:
            for tag in follow_stub(parent):
                if tag not in seen:
                    seen.add(tag)
                    wanted.append(tag)
    return wanted


def write_request(tags: list[int]) -> None:
    kinds = []
    for tag in tags:
        entry = entry_of(tag)
        if entry and entry.reference == DYE_BODY_CLASS:
            kinds.append("0x808071F3 dye body, 1515 B, sandbox_037c. Encrypted; we never rewrite it.")
            break
        if entry and entry.size == 40:
            kinds.append("40-byte dye texture headers. We never rewrite these.")
            break
        if entry and entry.reference == DYE_STUB_CLASS:
            kinds.append("0x808071CD dye stub, 24 B, sandbox_0207. Safe: we never rewrite it.")
            break
    comment = kinds[0] if kinds else "Next dye hop."
    lines = [f"# {comment}", ""]
    for tag in tags:
        lines.append(f"tag 0x{tag:08X}")
    REQUEST.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    print(f"\nwrote {len(tags)} requests to {REQUEST}")
    print("Launch once, reach the character screen, quit, then: python dye_probe.py")


def main() -> None:
    print("Scatterhorn Robe material pairs:")
    for stage, key, value in overrides_of(CHEST):
        print(f"  stage={stage} key={key} dye={value}")
    print()
    indices = dye_indices()
    needed = parents_for(indices)
    if "--request" in sys.argv:
        if not needed:
            print("\nchain is fully dumped; nothing to request")
            return
        write_request(needed)
    elif needed:
        print(f"\n{len(needed)} tags still need a dump. Re-run with --request.")


if __name__ == "__main__":
    main()
