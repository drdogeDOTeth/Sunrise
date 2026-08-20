"""Inline DyeData albedo tints. Installed as `037c_24`. User saw no colour. Do not rerun.

`paint_dye_bind.py` is the current texture step. This file still exports `TINT_INDICES`
and `dye_data_start` for that probe.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from bone_probe import dumped, entry_of
from paint_dye_textures import entry_index_of, package_of, write_all
from tigerpkg import Package

LEGEND = Path(__file__).with_name("dye_tint_legend.json")
DYE_DATA_AT = 0x88
# Charm WQ albedo slots, plus the SK vec4s that already store 0..1 RGB on Scatterhorn.
TINT_INDICES = (3, 4, 9, 13, 14, 17, 20, 21, 24)
BODIES = (
    ("plate", 0x80EF9662, (1.0, 0.0, 0.0, 1.0)),
    ("suit", 0x80EF9666, (0.0, 1.0, 0.0, 1.0)),
    ("cloth", 0x80EF96AA, (0.0, 0.0, 1.0, 1.0)),
)


def dye_data_start(body: bytes) -> tuple[int, int]:
    count, relative = struct.unpack_from("<qq", body, DYE_DATA_AT)
    header = DYE_DATA_AT + 8 + relative
    start = header + 16
    header_count = struct.unpack_from("<Q", body, header)[0]
    if header_count != count or not 1 <= count <= 64:
        raise SystemExit(f"DyeData array looks wrong: count={count} header_count={header_count}")
    return count, start


def main() -> None:
    by_package: dict[Path, dict[int, bytes]] = {}
    legend = []
    for channel, tag, rgba in BODIES:
        original = dumped(tag)
        if original is None or len(original) != 1515:
            raise SystemExit(f"0x{tag:08X} dump missing or not 1515 B")
        entry = entry_of(tag)
        if entry is None or entry.size != 1515:
            raise SystemExit(f"0x{tag:08X} is not a 1515 B dye body")
        count, start = dye_data_start(original)
        body = bytearray(original)
        changed = []
        for index in TINT_INDICES:
            if index >= count:
                continue
            at = start + index * 16
            before = struct.unpack_from("<ffff", original, at)
            struct.pack_into("<ffff", body, at, *rgba)
            changed.append({"index": index, "before": [round(v, 4) for v in before]})
        path = package_of(tag)
        by_package.setdefault(path, {})[entry_index_of(tag)] = bytes(body)
        hex_colour = "#" + "".join(f"{int(c * 255):02X}" for c in rgba[:3])
        legend.append({
            "channel": channel,
            "tag": f"0x{tag:08X}",
            "package": path.stem,
            "rgba": list(rgba),
            "hex": hex_colour,
            "changed": changed,
        })
        print(f"  {channel:6} 0x{tag:08X}  {path.stem}  {hex_colour}  slots {TINT_INDICES}")
        for row in changed[:4]:
            print(f"           [{row['index']}] {row['before']} -> {list(rgba)}")

    LEGEND.write_text(json.dumps(legend, indent=2), encoding="utf-8")
    print(f"\nlegend -> {LEGEND}")
    for path, replacements in sorted(by_package.items()):
        print(f"  {path.name}: {len(replacements)} entries")

    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return

    write_all(by_package)
    print("Launch once, reach the character screen, quit.")
    print("Expect plate red / suit green / cloth blue from DyeData tints.")
    print("No colour = albedo is not these vec4s; next is t0/t1/t2.")


if __name__ == "__main__":
    main()
