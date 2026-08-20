"""Paint every Scatterhorn texture a distinct flat colour, to find which ones reach our mesh.

**A Destiny material does not name its textures.** Fully decoded, `0x80EF98DB` is 1,616 bytes of:

    +0x048  shader header -> DXBC          +0x2C8  shader header -> DXBC
    +0x420  70 B binding bytecode          +0x480  3 x float4
    +0x4D0  5 x {tag, pad, hash64}         +0x540  17 x float4

and all five of those resource refs are **samplers** (`D3D11_SAMPLER_DESC`, 52 bytes, exact field
for field). Searching all 12 dumped materials for any of the game's 181,141 forty-byte entries
turns up only the two shaders. So the texture binding lives somewhere above the material, and no
amount of reading materials will find it.

Offline search is exhausted too: of 32,713 entries in these four packages only 48 are unencrypted,
and none of them names a texture. That leaves one question worth a launch - *which* texture data
entries actually land on the custom body - and one experiment that answers it for all of them at
once: give each a different flat colour and look.

Two things make this cheap. A flat colour in BC7 is **the same 16-byte block repeated**, so an
entry is filled without knowing its width, height or mip count, and its **size never changes** -
the 40-byte header is not touched, so nothing here rests on our reading of it. And the multi-entry
writer streams bodies across blocks, so a 5.6 MB entry is no harder to write than a small one.

What comes back is a legend: every colour seen on the Guardian names the entry that produced it.

Usage:
    python paint_textures.py --dry-run    # sizes and block headroom, nothing written
    python paint_textures.py              # write the patch layer, then launch once
"""
from __future__ import annotations

import colorsys
import json
import struct
import sys
from pathlib import Path

from bone_probe import INDEX, entry_of
from inject_mesh import entry_index_of, package_of, write_all
from tigerpkg import BLOCK_SIZE, Package, TAG_BASE, TAG_ENTRY_BITS

LEGEND = Path(__file__).with_name("texture_legend.json")
# 2048x2048 BC7 with mips to 128, and the same at half. Both are exact across the install.
TEXTURE_SIZES = (5_586_944, 2_793_472)
FAMILIES = ("037c", "037d", "0698", "0699")
BC7_BLOCK_BYTES = 16
# The entry start-block field is 14 bits, so a package cannot hold more than this many blocks.
BLOCK_CEILING = 0x3FFF


def bc7_mode6_flat(red: int, green: int, blue: int) -> bytes:
    """
    @return One BC7 mode-6 block that decodes to a single opaque colour.

    Mode 6 stores two endpoints of 7 bits per channel plus a per-endpoint p-bit, and rebuilds each
    8-bit channel as `(value << 1) | p`. Holding both endpoints equal makes every interpolation land
    on the same colour, and every index can then stay zero. With `p = 0` the channel is exactly
    `value << 1`, so the caller's components must be even to survive the round trip.
    """
    for component in (red, green, blue):
        if not 0 <= component <= 255 or component % 2:
            raise ValueError(f"component {component} must be even and in 0..255")

    bits, position = 0, 0

    def put(value: int, width: int) -> None:
        nonlocal bits, position
        bits |= (value & ((1 << width) - 1)) << position
        position += width

    put(0b1000000, 7)                       # mode 6: six zeros then a one
    for component in (red, green, blue, 255):
        # R0 R1 G0 G1 B0 B1 A0 A1, seven bits each. Alpha 255 needs value 127 with p = 1, so keep
        # alpha at 254 instead and let the two p-bits stay zero for every channel alike.
        value = (component if component != 255 else 254) >> 1
        put(value, 7)
        put(value, 7)
    put(0, 1)                               # p0
    put(0, 1)                               # p1
    put(0, 3)                               # index 0 is the anchor and carries one bit less
    put(0, 60)                              # indices 1..15
    if position != 128:
        raise AssertionError(f"packed {position} bits, not 128")
    return bits.to_bytes(BC7_BLOCK_BYTES, "little")


def unpack_mode6(block: bytes) -> tuple[int, int, int, int]:
    """@return The first endpoint of a mode-6 block, decoded independently of the packer."""
    bits = int.from_bytes(block, "little")

    def take(at: int, width: int) -> int:
        return (bits >> at) & ((1 << width) - 1)

    if take(0, 7) != 0b1000000:
        raise ValueError("not a mode-6 block")
    parity = take(63, 1)
    return tuple((take(7 + slot * 14, 7) << 1) | parity for slot in range(4))


def textures() -> list[tuple[int, int, str]]:
    """@return `(tag, size, package stem)` for every texture body in the Scatterhorn packages."""
    out = []
    for package_id, path in sorted(INDEX.items()):
        if not any(family in path.stem for family in FAMILIES):
            continue
        for index, entry in enumerate(Package(path).entries):
            if entry.size in TEXTURE_SIZES:
                out.append((TAG_BASE + (package_id << TAG_ENTRY_BITS) + index,
                            entry.size, path.stem))
    return sorted(out)


def palette(count: int) -> list[tuple[int, int, int]]:
    """@return `count` colours spaced round the hue wheel, every component even."""
    out = []
    for index in range(count):
        # Walk the wheel while alternating value and saturation, so neighbours in tag order are
        # never neighbours in appearance and a misread on screen is obvious rather than plausible.
        hue = (index * 0.618033988749895) % 1.0
        saturation = 1.0 if index % 2 == 0 else 0.55
        value = 1.0 if index % 3 else 0.6
        rgb = colorsys.hsv_to_rgb(hue, saturation, value)
        out.append(tuple(int(round(channel * 255)) & 0xFE for channel in rgb))
    return out


def main() -> None:
    found = textures()
    if not found:
        raise SystemExit("no texture-sized entries found")
    colours = palette(len(found))

    # A flat block is its own proof: pack one, unpack it with separate code, compare.
    for red, green, blue in colours:
        decoded = unpack_mode6(bc7_mode6_flat(red, green, blue))
        if decoded[:3] != (red, green, blue):
            raise SystemExit(f"BC7 round trip failed: {(red, green, blue)} -> {decoded[:3]}")
    print(f"BC7 mode-6 round trip clean for all {len(colours)} colours")

    by_package: dict[Path, dict[int, bytes]] = {}
    legend = []
    for (tag, size, stem), (red, green, blue) in zip(found, colours):
        if size % BC7_BLOCK_BYTES:
            raise SystemExit(f"0x{tag:08X} is {size:,} B, not a whole number of BC7 blocks")
        body = bc7_mode6_flat(red, green, blue) * (size // BC7_BLOCK_BYTES)
        path = package_of(tag)
        by_package.setdefault(path, {})[entry_index_of(tag)] = body
        legend.append({"tag": f"0x{tag:08X}", "package": stem, "size": size,
                       "rgb": [red, green, blue], "hex": f"#{red:02X}{green:02X}{blue:02X}"})

    print(f"\n{len(found)} textures across {len(by_package)} packages:")
    total = 0
    for path, replacements in sorted(by_package.items()):
        written = sum(len(body) for body in replacements.values())
        total += written
        blocks = -(-written // BLOCK_SIZE)
        have = Package(path).header.block_count
        headroom = "ok" if have + blocks <= BLOCK_CEILING else "OVER THE 14-BIT BLOCK LIMIT"
        print(f"  {path.name:<28} {len(replacements):>3} entries  {written / 1e6:>7.1f} MB  "
              f"blocks {have} + {blocks} -> {have + blocks}  {headroom}")
        if have + blocks > BLOCK_CEILING:
            raise SystemExit("a package would exceed the block ceiling; paint fewer textures")
    print(f"  {'total':<28}     {' ' * 8} {total / 1e6:>7.1f} MB")

    LEGEND.write_text(json.dumps(legend, indent=2), encoding="utf-8")
    print(f"\nlegend -> {LEGEND}")
    for row in legend[:8]:
        print(f"  {row['tag']}  {row['hex']}  {row['package']}")
    print(f"  ... {len(legend) - 8} more")

    if "--dry-run" in sys.argv:
        print("\ndry run; nothing written")
        return

    write_all(by_package, "")
    print("\nEvery Scatterhorn texture is now one flat colour, sizes and headers untouched.")
    print("Launch, look at the Guardian, and match what you see against texture_legend.json.")


if __name__ == "__main__":
    main()
