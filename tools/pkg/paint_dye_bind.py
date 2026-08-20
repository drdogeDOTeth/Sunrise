"""One launch, two distinguishable albedo tests.

Inline DyeData tints (`037c_24`) showed nothing. Remaining-mip sRGB tiles (t3/t5/t7) showed
nothing. Remaining-mip **normals** did (clay-white). Colour is not in those diffuse tiles and
is not in the inline DyeData copy Charm reads.

This layer, all in `037c_25`:

1. Restores the three 1,515 B dye bodies from the original dumps (undoes `_24` tints).
2. Suit DyeTextures[0].slot **5 → 0**, so the already-painted green remaining-mip binds as **t0**.
3. Writes plate DyeInfo sidecar `0x80EF9661` red and cloth `0x80EF969D` blue (27 × float4,
   same bytes as DyeData). Suit sidecar stays shipped so a dump of it is still honest.

Legend after one character-screen look:

- **green only** — t0 remap. Albedo is t0; the slot field is live. Ignore the sidecar.
- **red and/or blue, no green** — DyeInfo sidecar. Inline DyeData was the wrong copy.
- **green plus red/blue** — both paths live.
- **still nothing** — neither. Next is material float4s / whoever else binds t0.

Do not flatten t4/t6/t8. Do not paint more sandbox 2048s. Do not `--undo`.

Usage:
    python paint_dye_bind.py --dry-run
    python paint_dye_bind.py
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from bone_probe import dumped, entry_of
from paint_dye_tints import TINT_INDICES, dye_data_start
from paint_dye_textures import entry_index_of, package_of, write_all

LEGEND = Path(__file__).with_name("dye_bind_legend.json")
REQUEST = Path(r"C:\Sunrise\bin\x64\Sunrise\dump\request.txt")
DYE_TEXTURES_AT = 0x40

# Dye bodies: restore original dumps, then change only the suit slot.
PLATE_BODY = 0x80EF9662
SUIT_BODY = 0x80EF9666
CLOTH_BODY = 0x80EF96AA

# 16-byte DyeInfo headers we do not write; 432 B bodies we do (plate/cloth only).
PLATE_SIDECAR = 0x80EF9661
SUIT_SIDECAR = 0x80EF9663
CLOTH_SIDECAR = 0x80EF969D
HEADER_TAGS = (0x80EF9660, 0x80EF9664, 0x80EF96A9)

TINTS = {
    "plate": (1.0, 0.0, 0.0, 1.0),
    "cloth": (0.0, 0.0, 1.0, 1.0),
}


def texture_table(body: bytes) -> tuple[int, int]:
    """@return `(count, data offset)` for DyeTextures."""
    count, relative = struct.unpack_from("<qq", body, DYE_TEXTURES_AT)
    header = DYE_TEXTURES_AT + 8 + relative
    start = header + 16
    header_count = struct.unpack_from("<Q", body, header)[0]
    if header_count != count or not 1 <= count <= 8:
        raise SystemExit(f"DyeTextures array looks wrong: count={count} header_count={header_count}")
    return count, start


def tinted_sidecar(body: bytes, rgba: tuple[float, float, float, float]) -> bytes:
    """27 × float4 copied from DyeData, with albedo-like slots replaced."""
    count, start = dye_data_start(body)
    if count != 27:
        raise SystemExit(f"DyeData count {count}, expected 27")
    data = bytearray(body[start : start + 27 * 16])
    if len(data) != 432:
        raise SystemExit(f"sidecar payload {len(data)} B, expected 432")
    for index in TINT_INDICES:
        struct.pack_into("<ffff", data, index * 16, *rgba)
    return bytes(data)


def remap_suit_slot(body: bytes) -> bytes:
    """Points the suit sRGB tile at t0 instead of t5."""
    count, start = texture_table(body)
    slot, header = struct.unpack_from("<II", body, start)
    if slot != 5 or header != 0x80C1D3CA:
        raise SystemExit(f"suit DyeTextures[0] is t{slot} 0x{header:08X}, expected t5 0x80C1D3CA")
    patched = bytearray(body)
    struct.pack_into("<I", patched, start, 0)
    return bytes(patched)


def queue(by_package: dict, tag: int, data: bytes) -> None:
    entry = entry_of(tag)
    if entry is None or entry.size != len(data):
        raise SystemExit(f"0x{tag:08X} size mismatch: entry={None if entry is None else entry.size} data={len(data)}")
    by_package.setdefault(package_of(tag), {})[entry_index_of(tag)] = data


def write_request() -> None:
    """16-byte DyeInfo headers plus the suit sidecar we did not overwrite."""
    tags = list(HEADER_TAGS) + [SUIT_SIDECAR]
    lines = [
        "# DyeInfo 16-byte headers (we never write these) and the shipped suit 432 B sidecar.",
        "# Plate/cloth sidecars and the three dye bodies are rewritten in 037c_25; do not dump them.",
        "",
    ]
    lines += [f"tag 0x{tag:08X}" for tag in tags]
    REQUEST.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    print(f"request -> {REQUEST} ({len(tags)} tags)")


def main() -> None:
    plate = dumped(PLATE_BODY)
    suit = dumped(SUIT_BODY)
    cloth = dumped(CLOTH_BODY)
    for name, body, tag in (("plate", plate, PLATE_BODY), ("suit", suit, SUIT_BODY), ("cloth", cloth, CLOTH_BODY)):
        if body is None or len(body) != 1515:
            raise SystemExit(f"0x{tag:08X} dump missing or not 1515 B")
        count, start = dye_data_start(body)
        primary = struct.unpack_from("<ffff", body, start + 3 * 16)
        if name == "plate" and primary[0] >= 0.99:
            raise SystemExit("plate dump is already tinted red; dump is poisoned by _24")
        print(f"  dump {name:6} 0x{tag:08X}  DyeData[{count}] [3]={tuple(round(v, 4) for v in primary)}")

    suit_patched = remap_suit_slot(suit)
    plate_sidecar = tinted_sidecar(plate, TINTS["plate"])
    cloth_sidecar = tinted_sidecar(cloth, TINTS["cloth"])

    by_package: dict[Path, dict[int, bytes]] = {}
    queue(by_package, PLATE_BODY, plate)  # original: undoes _24
    queue(by_package, SUIT_BODY, suit_patched)
    queue(by_package, CLOTH_BODY, cloth)  # original: undoes _24
    queue(by_package, PLATE_SIDECAR, plate_sidecar)
    queue(by_package, CLOTH_SIDECAR, cloth_sidecar)

    legend = {
        "layer": "037c_25",
        "tests": {
            "green": "suit remaining-mip rebound t5 -> t0 (texture 0x80C1D3CB still painted #00FE00)",
            "red": "plate DyeInfo sidecar 0x80EF9661, DyeData albedo slots",
            "blue": "cloth DyeInfo sidecar 0x80EF969D, DyeData albedo slots",
        },
        "untouched": {
            "suit_sidecar": f"0x{SUIT_SIDECAR:08X}",
            "normals": "t4/t6/t8",
            "sandbox_2048s": "_23 still installed, inert",
        },
        "entries": [
            {"tag": f"0x{PLATE_BODY:08X}", "what": "dye body restored from dump"},
            {"tag": f"0x{SUIT_BODY:08X}", "what": "dye body restored + slot 5 -> 0"},
            {"tag": f"0x{CLOTH_BODY:08X}", "what": "dye body restored from dump"},
            {"tag": f"0x{PLATE_SIDECAR:08X}", "what": "432 B sidecar tinted red", "hex": "#FF0000"},
            {"tag": f"0x{CLOTH_SIDECAR:08X}", "what": "432 B sidecar tinted blue", "hex": "#0000FF"},
        ],
    }
    LEGEND.write_text(json.dumps(legend, indent=2), encoding="utf-8")
    print(f"legend -> {LEGEND}")
    for path, replacements in sorted(by_package.items()):
        print(f"  {path.name}: {len(replacements)} entries")

    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return

    write_all(by_package)
    write_request()
    print("Launch once, reach the character screen, quit.")
    print("green = t0 remap. red/blue = DyeInfo sidecar. nothing = neither.")
    print("Do not dump 0x80EF9662 / 666 / 6AA / 9661 / 969D while _25 is in.")


if __name__ == "__main__":
    main()
