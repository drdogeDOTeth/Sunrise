"""Report which dumped materials already bind a texture.

`request_bindable_materials.py` fills a launch. This reads the dumps back. A material with a
populated pixel-texture array at +0x2D0 is a carrier we can point a five-part-split slot at, then
paint the buffer that array already names - no tag write, which is the operation that hangs.

Usage:
    python scan_material_textures.py
    python scan_material_textures.py --json
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from bone_probe import DUMP, dumped, entry_of

MATERIAL = 0x808071E8
ARRAY_CLASS = 0x80809FBD
TEXTURE_BINDING = 0x80807211
PS_FIELD, VS_FIELD = 0x2D0, 0x50
OFF_TOTAL, OFF_FORMAT, OFF_WIDTH, OFF_HEIGHT, OFF_BUFFER = 0x00, 0x04, 0x0E, 0x10, 0x24


def texture_array(data: bytes, field: int) -> list[tuple[int, int]] | None:
    """@return `[(sampler slot, header tag), ...]` or None when the field is null / unreadable."""
    if field + 16 > len(data):
        return None
    count, offset = struct.unpack_from("<qq", data, field)
    if count <= 0:
        return []
    marker = field + 8 + offset - 4
    if not 0 <= marker <= len(data) - 20:
        return None
    seen, its_count, _, element, _ = struct.unpack_from("<5I", data, marker)
    if seen != ARRAY_CLASS or element != TEXTURE_BINDING:
        return None
    binds: list[tuple[int, int]] = []
    for index in range(min(its_count, 32)):
        at = marker + 20 + index * 8
        if at + 8 > len(data):
            break
        binds.append(struct.unpack_from("<2I", data, at))
    return binds


def describe_header(tag: int) -> str:
    body = dumped(tag)
    info = entry_of(tag)
    if body is None or len(body) != 40:
        size = info.size if info else "?"
        return f"0x{tag:08X} (header not dumped, {size} B)"
    total, fmt = struct.unpack_from("<II", body, OFF_TOTAL)
    width, height = struct.unpack_from("<HH", body, OFF_WIDTH)
    buffer = struct.unpack_from("<I", body, OFF_BUFFER)[0]
    buf = f"buf=0x{buffer:08X}" if buffer not in (0, 0xFFFFFFFF) else "unsplit"
    return f"0x{tag:08X} {width}x{height} fmt={fmt} {total:,} B {buf}"


def main() -> None:
    hits = []
    scanned = 0
    for path in DUMP.glob("tag_*.bin"):
        tag = int(path.stem.split("_")[1], 16)
        info = entry_of(tag)
        if info is None or info.reference != MATERIAL:
            continue
        scanned += 1
        data = path.read_bytes()
        ps = texture_array(data, PS_FIELD)
        vs = texture_array(data, VS_FIELD)
        if not ps and not vs:
            continue
        vs_shader = struct.unpack_from("<I", data, 0x048)[0] if len(data) >= 0x04C else 0
        ps_shader = struct.unpack_from("<I", data, 0x2C8)[0] if len(data) >= 0x2CC else 0
        hits.append({
            "material": tag,
            "size": info.size,
            "vs_shader": vs_shader,
            "ps_shader": ps_shader,
            "ps": [{"slot": slot, "header": header} for slot, header in (ps or [])],
            "vs": [{"slot": slot, "header": header} for slot, header in (vs or [])],
        })

    print(f"dumped materials {scanned}  with a texture array {len(hits)}")
    if "--json" in sys.argv:
        Path("material_texture_hits.json").write_text(json.dumps(hits, indent=2), encoding="utf-8")
        print("wrote material_texture_hits.json")
    for row in hits:
        print(f"\n  0x{row['material']:08X}  {row['size']} B  "
              f"VS=0x{row['vs_shader']:08X} PS=0x{row['ps_shader']:08X}")
        for bind in row["ps"]:
            print(f"    PS slot {bind['slot']}: {describe_header(bind['header'])}")
        for bind in row["vs"]:
            print(f"    VS slot {bind['slot']}: {describe_header(bind['header'])}")
    if not hits:
        print("none yet - run request_bindable_materials.py and launch once")


if __name__ == "__main__":
    main()
