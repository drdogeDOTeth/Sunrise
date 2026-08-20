"""Rebind every dye channel's albedo tile to one t-slot, and see which channel reaches the body.

The `_26` sweep painted all 228 textures in the four Scatterhorn packages. **The weapon turned
magenta and the body did not change at all.** That is the most useful result yet, for two reasons:
it proves a painted texture does reach the GPU and show on screen, and it rules out those packages
as the source of the body's albedo.

Put together with what the earlier probes found, the body is white because **nothing is bound to
its albedo slot**, not because the right texture has not been found yet:

- The part-10 pixel shader declares `t0, t1, t2, t5, t6`.
- DyeTextures only ever name `t3`-`t8`.
- Painting the dye **normals** (t4/t6/t8) visibly changed the body, so dye textures do reach it.
- Painting the dye **sRGB albedo** tiles (t3/t5/t7) did not, so the shader is not reading them.

So the visible albedo is `t0`/`t1`/`t2`, which no dye supplies. The job is to **bind**, not to
find. `_25` tested exactly that and came back empty - but it remapped only the **suit** channel,
and there are three. If the carrier parts draw on plate or cloth, that test could not have shown
anything whatever the truth was.

This holds the slot fixed and lets the channel report itself. All three channels' albedo tiles are
rebound to the same target slot, and each channel is already painted a different colour by
`paint_dye_textures.py`, so one look names the channel:

| seen | means |
|---|---|
| red | plate is the channel, and the target slot is the albedo |
| green | suit |
| blue | cloth |
| nothing | that slot is not the albedo; try the next one |

One variable per launch. `--slot 1` and `--slot 2` are the follow-ups.

Usage:
    python paint_dye_slots.py --dry-run        # print each channel's DyeTextures table
    python paint_dye_slots.py                  # rebind all three to t0
    python paint_dye_slots.py --slot 1
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from bone_probe import dumped, entry_of
from paint_dye_bind import CLOTH_BODY, PLATE_BODY, SUIT_BODY, texture_table
from paint_dye_textures import entry_index_of, package_of, write_all
from paint_textures import BC7_BLOCK_BYTES, bc7_mode6_flat

LEGEND = Path(__file__).with_name("dye_slot_legend.json")
DYE_BODY_BYTES = 1515
# Charm's STextureHeader: `+0x24` names the buffer holding mip 0+1, and the entry the header pairs
# with holds the rest. An earlier probe painted only the remaining mips, so a bound texture whose
# top mip the game happens to sample would have shown its original pixels and read as a failure.
# Painting both halves the same colour removes that ambiguity from the answer.
LARGE_BUFFER_AT = 0x24
# The sRGB albedo tile of each channel, and the colour it is painted here. Components must be even
# for a mode-6 block with both p-bits zero to reproduce them exactly.
CHANNELS = {
    "plate": {"body": PLATE_BODY, "albedo_slot": 3, "rgb": (0xFE, 0x00, 0x00), "colour": "red"},
    "suit": {"body": SUIT_BODY, "albedo_slot": 5, "rgb": (0x00, 0xFE, 0x00), "colour": "green"},
    "cloth": {"body": CLOTH_BODY, "albedo_slot": 7, "rgb": (0x00, 0x00, 0xFE), "colour": "blue"},
}


def option(name: str, fallback: int) -> int:
    """@return The integer after `name` on the command line, or `fallback`."""
    if name not in sys.argv:
        return fallback
    return int(sys.argv[sys.argv.index(name) + 1], 0)


def slots_of(body: bytes) -> list[tuple[int, int, int]]:
    """@return `(offset, slot, texture header tag)` for every DyeTextures entry."""
    count, start = texture_table(body)
    out = []
    for index in range(count):
        at = start + index * 8
        slot, header = struct.unpack_from("<II", body, at)
        out.append((at, slot, header))
    return out


def rebind(body: bytes, albedo_slot: int, target: int) -> tuple[bytes, int]:
    """
    Points this channel's albedo tile at `target`, leaving every other entry alone.

    Finds the entry **by its current slot** rather than assuming index 0. Each channel carries two
    tiles - an sRGB one and a normal - and flattening the normal is what turned the body clay-white
    last time, so picking the wrong element here would repeat a known failure.
    """
    for at, slot, header in slots_of(body):
        if slot == albedo_slot:
            patched = bytearray(body)
            struct.pack_into("<I", patched, at, target)
            return bytes(patched), header
    raise SystemExit(f"no DyeTextures entry on t{albedo_slot}; "
                     f"table is {[(s, hex(h)) for _at, s, h in slots_of(body)]}")


def paint_both_halves(by_package: dict, header_tag: int, rgb: tuple[int, int, int]) -> list[str]:
    """
    Paints a texture's remaining mips **and** its mip 0+1 buffer the same flat colour.

    @return A description of each entry painted, for the legend.
    """
    header = dumped(header_tag)
    if header is None or len(header) != 40:
        raise SystemExit(f"texture header 0x{header_tag:08X} not dumped; it is needed for "
                         f"the mip 0+1 buffer tag at +0x{LARGE_BUFFER_AT:02X}")
    entry = entry_of(header_tag)
    (large_tag,) = struct.unpack_from("<I", header, LARGE_BUFFER_AT)
    done = []
    for tag, what in ((entry.reference, "remaining mips"), (large_tag, "mip 0+1")):
        target = entry_of(tag)
        if target is None or target.size % BC7_BLOCK_BYTES:
            print(f"         skipped {what} 0x{tag:08X}: "
                  f"{'not an entry' if target is None else f'{target.size} B, not whole blocks'}")
            continue
        body = bc7_mode6_flat(*rgb) * (target.size // BC7_BLOCK_BYTES)
        by_package.setdefault(package_of(tag), {})[entry_index_of(tag)] = body
        done.append(f"0x{tag:08X} {what} {target.size:,} B")
        print(f"         painted {what:<15} 0x{tag:08X}  {target.size:>9,} B")
    return done


def main() -> None:
    target = option("--slot", 0)
    if not 0 <= target <= 2:
        raise SystemExit(f"--slot {target}: only t0, t1 and t2 are declared by the pixel shader")

    by_package: dict[Path, dict[int, bytes]] = {}
    legend = {"layer": "next 037c", "target_slot": f"t{target}", "channels": {}}
    for name, channel in CHANNELS.items():
        body = dumped(channel["body"])
        if body is None or len(body) != DYE_BODY_BYTES:
            raise SystemExit(f"0x{channel['body']:08X} dump missing or not {DYE_BODY_BYTES} B; "
                             "the dye bodies must be the original dumps, not our patched ones")
        print(f"  {name:6} 0x{channel['body']:08X}  DyeTextures "
              + "  ".join(f"t{slot}->0x{header:08X}" for _at, slot, header in slots_of(body)))
        patched, header = rebind(body, channel["albedo_slot"], target)
        entry = entry_of(channel["body"])
        if entry is None or entry.size != len(patched):
            raise SystemExit(f"0x{channel['body']:08X} size mismatch")
        by_package.setdefault(package_of(channel["body"]), {})[
            entry_index_of(channel["body"])] = patched
        red, green, blue = channel["rgb"]
        print(f"         t{channel['albedo_slot']} -> t{target}, "
              f"texture 0x{header:08X}, expect {channel['colour']}")
        painted = paint_both_halves(by_package, header, channel["rgb"])
        legend["channels"][name] = {
            "body": f"0x{channel['body']:08X}",
            "was": f"t{channel['albedo_slot']}",
            "now": f"t{target}",
            "texture": f"0x{header:08X}",
            "painted": painted,
            "expect": f"{channel['colour']} #{red:02X}{green:02X}{blue:02X}",
        }

    for path, replacements in sorted(by_package.items()):
        print(f"\n  {path.name}: {len(replacements)} entries")

    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return

    LEGEND.write_text(json.dumps(legend, indent=2), encoding="utf-8")
    write_all(by_package)
    print(f"\nlegend -> {LEGEND}")
    print(f"All three dye channels now bind their albedo tile to t{target}.")
    print("Launch, one character-screen look, quit. red = plate, green = suit, blue = cloth.")
    print(f"Nothing means t{target} is not the albedo slot - re-run with --slot {target + 1}.")


if __name__ == "__main__":
    main()
