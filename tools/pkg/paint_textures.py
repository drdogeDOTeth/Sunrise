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
once: give each a flat colour and look.

**Find textures structurally, never by size.** The first version of this tool matched two exact
byte counts and painted 55 of the **1,638** textures that are really in these packages; when the
Guardian showed no colour that looked like a clean negative and was not one. A texture may also
be split, with mip 0+1 in the buffer named at `+0x24` of the header and the rest in the entry the
header pairs with, so sizes range from 184 B to 5,586,944 B.

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


def ours(pkg: Package, entry) -> bool:
    """
    @return True when this entry's bytes are ones we wrote.

    Everything the writer emits is a plain block, and plain blocks are the *only* thing in a
    shipped destination package that is neither encrypted nor compressed - all 55 textures were
    `encrypted+compressed` before we touched them. So "reads from a plain block" identifies our
    own work exactly, which keeps this tool off the injected vertex, index and UV buffers. Those
    pair a header with a body the same way a texture does and would otherwise look identical.
    """
    span, block_index = 0, entry.start_block
    while span < entry.size and block_index < pkg.header.block_count:
        block = pkg.blocks[block_index]
        if not block.encrypted:
            return True
        span += block.size
        block_index += 1
    return False


def textures() -> list[tuple[int, int, str]]:
    """
    @return `(tag, size, package stem)` for every texture body in the Scatterhorn packages.

    Found **structurally**: a 40-byte header entry and a body that reference each other. Matching
    on size instead - the two byte counts a 2048x2048 and a 1024x2048 BC7 chain happen to come to
    - found 55 of the 1,638 that are actually there, and painting 3.4% of the textures proved only
    that those 3.4% are not sampled. Sizes here run from 184 B to 5,586,944 B, because a texture
    may also be split: `+0x24` of the header names a buffer holding mip 0+1, and the entry paired
    with the header holds the rest. Character select samples the remaining mips, so the paired
    entry - the one found here - is the right one to paint.
    """
    out = []
    for package_id, path in sorted(INDEX.items()):
        if not any(family in path.stem for family in FAMILIES):
            continue
        pkg = Package(path)
        by_index = dict(enumerate(pkg.entries))
        for index, entry in by_index.items():
            if entry.size != 40:
                continue
            partner = by_index.get((entry.reference - TAG_BASE) & 0x1FFF) \
                if (entry.reference - TAG_BASE) >> TAG_ENTRY_BITS == package_id else None
            tag = TAG_BASE + (package_id << TAG_ENTRY_BITS) + index
            if partner is None or partner.reference != tag or partner.size < BC7_BLOCK_BYTES:
                continue
            if ours(pkg, partner) or ours(pkg, entry):
                continue
            out.append((entry.reference, partner.size, path.stem))
    return sorted(out)


# Twelve colours nobody can confuse on a screenshot, every component even so a mode-6 block with
# both p-bits zero reproduces them exactly.
BUCKET_COLOURS = [(0xFE, 0x00, 0x00), (0xFE, 0x80, 0x00), (0xFE, 0xFE, 0x00), (0x80, 0xFE, 0x00),
                  (0x00, 0xFE, 0x00), (0x00, 0xFE, 0x80), (0x00, 0xFE, 0xFE), (0x00, 0x80, 0xFE),
                  (0x00, 0x00, 0xFE), (0x80, 0x00, 0xFE), (0xFE, 0x00, 0xFE), (0xFE, 0x00, 0x80)]


def palette(count: int) -> list[tuple[int, tuple[int, int, int]]]:
    """
    @return `(bucket, rgb)` per texture, in tag order.

    With 1,638 textures a distinct colour each is unreadable - neighbouring hues are a coin flip
    on a screenshot. Contiguous **buckets** are readable and still narrow the search: whichever of
    twelve colours appears names a range of ~140 tags, and re-running over just that range splits
    it twelve ways again. Two launches get from 1,638 candidates to about a dozen.
    """
    buckets = min(len(BUCKET_COLOURS), count)
    return [(index * buckets // count, BUCKET_COLOURS[index * buckets // count])
            for index in range(count)]


def main() -> None:
    found = textures()
    if not found:
        raise SystemExit("no texture-sized entries found")
    colours = palette(len(found))

    # A flat block is its own proof: pack one, unpack it with separate code, compare.
    for red, green, blue in BUCKET_COLOURS:
        decoded = unpack_mode6(bc7_mode6_flat(red, green, blue))
        if decoded[:3] != (red, green, blue):
            raise SystemExit(f"BC7 round trip failed: {(red, green, blue)} -> {decoded[:3]}")
    print(f"BC7 mode-6 round trip clean for all {len(BUCKET_COLOURS)} bucket colours")

    by_package: dict[Path, dict[int, bytes]] = {}
    legend = []
    for (tag, size, stem), (bucket, (red, green, blue)) in zip(found, colours):
        if size % BC7_BLOCK_BYTES:
            # A texture body is a whole number of BC7 blocks. Anything else is not one, whatever
            # its entry pairing looks like, so leave it alone rather than write ragged bytes.
            continue
        body = bc7_mode6_flat(red, green, blue) * (size // BC7_BLOCK_BYTES)
        path = package_of(tag)
        by_package.setdefault(path, {})[entry_index_of(tag)] = body
        legend.append({"tag": f"0x{tag:08X}", "package": stem, "size": size, "bucket": bucket,
                       "rgb": [red, green, blue], "hex": f"#{red:02X}{green:02X}{blue:02X}"})

    painted = sum(len(replacements) for replacements in by_package.values())
    print(f"\n{len(found)} texture-shaped pairs found, {painted} of them a whole number of BC7 "
          f"blocks and painted, across {len(by_package)} packages:")
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
    for bucket, (red, green, blue) in enumerate(BUCKET_COLOURS):
        members = [row for row in legend if row["bucket"] == bucket]
        if members:
            print(f"  #{red:02X}{green:02X}{blue:02X}  bucket {bucket:>2}  "
                  f"{len(members):>4} textures  {members[0]['tag']}..{members[-1]['tag']}")

    if "--dry-run" in sys.argv:
        print("\ndry run; nothing written")
        return

    write_all(by_package, "")
    print("\nEvery Scatterhorn texture is now one flat colour, sizes and headers untouched.")
    print("Launch, look at the Guardian, and match what you see against texture_legend.json.")


if __name__ == "__main__":
    main()
