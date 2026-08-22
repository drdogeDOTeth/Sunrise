"""Swap only the pixel-shader tag on the two chest-native binders.

Unique RGB cannot get through dye PS `0x81531EE6` (look-alike probe: white pants,
no green). This changes `+0x2C8` on `0x80EFA1DC` / `0x80EFA1E2` to `0x81531CBE`.

That PS already sits on `0x80EF99F9`: same armor VS family, same single slot-3
bind, same header `0x80C1D3CD`. Not a steal. Not an array append. Vertex shader
and the texture array stay put.

Usage:
    python swap_chest_ps.py --dry-run
    python swap_chest_ps.py          # Destiny closed
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from bone_probe import dumped
from inject_scatterhorn import put
from inject_mesh import write_all
from known_good import game_running
from scan_material_textures import PS_FIELD, texture_array

REQUEST = Path(r"C:\Sunrise\bin\x64\Sunrise\dump\request.txt")

MATERIALS = (0x80EFA1DC, 0x80EFA1E2)
OLD_PS = 0x81531EE6
NEW_PS = 0x81531CBE
ARMOR_VS = {0x81532C62, 0x81532C5B}
BIND_HEADER = 0x80C1D3CD
PS_AT = 0x2C8
VS_AT = 0x048

# Header + DXBC for the dye PS, the candidate, and the unique-1024 PS (decode
# offline if 1CBE is also a luma gate). Materials so the write can be confirmed.
REQUEST_TAGS = (
    (0x81531EE6, "dye PS header (current luma gate)"),
    (0x81531EE5, "dye PS DXBC"),
    (0x81531CBE, "candidate PS header"),
    (0x81531CBF, "candidate PS DXBC"),
    (0x81530030, "unique-1024 PS header"),
    (0x81530031, "unique-1024 PS DXBC"),
    (0x80EFA1DC, "mask material after swap"),
    (0x80EFA1E2, "sibling material after swap"),
    (0x80EF99F9, "donor material (already 1CBE)"),
)


def rewrite(tag: int) -> bytes:
    """@return Material body with only +0x2C8 changed to the candidate PS."""
    data = dumped(tag)
    if data is None or len(data) < PS_AT + 4:
        raise SystemExit(f"0x{tag:08X}: material not dumped")
    vs = struct.unpack_from("<I", data, VS_AT)[0]
    ps = struct.unpack_from("<I", data, PS_AT)[0]
    binds = texture_array(data, PS_FIELD) or []
    if vs not in ARMOR_VS:
        raise SystemExit(f"0x{tag:08X}: VS 0x{vs:08X} is not armor")
    if ps != OLD_PS:
        raise SystemExit(f"0x{tag:08X}: PS 0x{ps:08X}, expected dye 0x{OLD_PS:08X}")
    if binds != [(3, BIND_HEADER)]:
        raise SystemExit(f"0x{tag:08X}: binds {binds}, expected slot 3 -> 0x{BIND_HEADER:08X}")
    out = bytearray(data)
    struct.pack_into("<I", out, PS_AT, NEW_PS)
    seen = struct.unpack_from("<I", out, PS_AT)[0]
    if seen != NEW_PS or out[PS_AT + 4 :] != data[PS_AT + 4 :] or out[:PS_AT] != data[:PS_AT]:
        raise SystemExit(f"0x{tag:08X}: rewrite touched more than +0x2C8")
    print(f"  0x{tag:08X}  VS 0x{vs:08X}  PS 0x{ps:08X} -> 0x{NEW_PS:08X}  bind slot 3 unchanged")
    return bytes(out)


def write_request() -> None:
    lines = [
        "# PS-only swap on chest binders. Same launch as the +0x2C8 write.",
        "# Dye PS vs candidate vs unique-1024. Decode if character select luma-gates again.",
        "",
    ]
    for tag, note in REQUEST_TAGS:
        lines.append(f"tag 0x{tag:08X}   # {note}")
    REQUEST.parent.mkdir(parents=True, exist_ok=True)
    REQUEST.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    print(f"request.txt: {len(REQUEST_TAGS)} tags -> {REQUEST}")


def main() -> None:
    dry = "--dry-run" in sys.argv
    if not dry and game_running():
        raise SystemExit("Destiny is running; close it before writing packages")
    work: dict[Path, dict[int, bytes]] = {}
    print(f"=== +0x2C8  0x{OLD_PS:08X} -> 0x{NEW_PS:08X}  (VS and binds stay) ===")
    for tag in MATERIALS:
        put(work, tag, rewrite(tag))
    print(f"\n{sum(len(v) for v in work.values())} entries across {len(work)} packages")
    for path, replacements in sorted(work.items()):
        print(f"  {path.name}: {len(replacements)} entries")
    write_request()
    if dry:
        print("dry-run; nothing written")
        return
    write_all(work, "")
    print("wrote. Launch character select.")
    print("  green tattoos / black stripe -> dye PS was the wall. Keep it.")
    print("  still brown/white/black     -> 1CBE is also luma. restore 135112.")
    print("  hang or vanish              -> python known_good.py --restore")


if __name__ == "__main__":
    main()
