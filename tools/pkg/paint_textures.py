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
    python paint_textures.py --only=0x80EFB30A..0x80EFB7EE   # re-bucket one bucket's range
"""
from __future__ import annotations

import colorsys
import json
import struct
import sys
from pathlib import Path

from bone_probe import INDEX, entry_of
from inject_mesh import entry_index_of, package_of, write_all
from tigerpkg import BLOCK_SIZE, Package, PackageError, TAG_BASE, TAG_ENTRY_BITS

LEGEND = Path(__file__).with_name("texture_legend.json")
# 2048x2048 BC7 with mips to 128, and the same at half. Both are exact across the install.
TEXTURE_SIZES = (5_586_944, 2_793_472)
FAMILIES = ("037c", "037d", "0698", "0699")
BC7_BLOCK_BYTES = 16
# The entry start-block field is 14 bits, so a package cannot hold more than this many blocks.
BLOCK_CEILING = 0x3FFF
# `entry_type` from the plain entry table, so this costs no launch and no key. All 55 textures
# found by exact mip-chain size are `(40, 1)` with a `(32, 1)` header, without exception.
# **Geometry buffers are type 41** and pair a header with a body exactly as a texture does, so
# pairing alone cannot tell them apart - painting 356 of them with flat BC7 made the GPU read
# colour as vertex data and cost a device loss ("error code broccoli") at character select.
TEXTURE_BODY_TYPE = 40
TEXTURE_HEADER_TYPE = 32


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


def already_painted(pkg: Package, entry) -> bool:
    """
    @return True when this entry is a flat colour we painted on an earlier pass.

    Our own paint has to stay *re*-paintable or a bucket can never be bisected - the second sweep
    needs exactly the tags the first one already covered. Telling paint from geometry needs no
    bookkeeping file, because the paint describes itself: it is one BC7 mode-6 block repeated, so
    four consecutive blocks are byte-identical and the first is a valid mode 6. An injected vertex
    or index buffer is neither. Only ever asked of plain entries, so a flat *shipped* texture -
    encrypted, and never read here - cannot be mistaken for ours.
    """
    try:
        body = pkg.block_body(entry.start_block)[entry.start_offset:entry.start_offset + 64]
    except PackageError:
        return False
    if len(body) < 64:
        return False
    blocks = [body[at:at + BC7_BLOCK_BYTES] for at in range(0, 64, BC7_BLOCK_BYTES)]
    if any(block != blocks[0] for block in blocks[1:]):
        return False
    try:
        unpack_mode6(blocks[0])
    except ValueError:
        return False
    return True


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
    out, opened = [], {}

    def package(path: Path) -> Package:
        if path not in opened:
            opened[path] = Package(path)
        return opened[path]

    # Walk **bodies**, not headers. A pair may straddle packages in either direction: 0x80EFADA6's
    # body is in 037d with its header in 037c, and 0x80EFB8FC's header lives in 03c1 entirely
    # outside this set. Only the body's location decides whether we want to paint it.
    for package_id, path in sorted(INDEX.items()):
        if not any(family in path.stem for family in FAMILIES):
            continue
        pkg = package(path)
        for index, body in enumerate(pkg.entries):
            if body.entry_type != TEXTURE_BODY_TYPE or body.size < BC7_BLOCK_BYTES:
                continue
            body_tag = TAG_BASE + (package_id << TAG_ENTRY_BITS) + index
            header = entry_of(body.reference)
            if header is None or header.size != 40 or header.entry_type != TEXTURE_HEADER_TYPE:
                continue
            if header.reference != body_tag:
                continue
            if ours(pkg, body) and not already_painted(pkg, body):
                continue
            out.append((body_tag, body.size, path.stem))
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


def narrow(found: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """
    @return `found` cut to the `--only START..END` tag range, when one is given.

    A bucket hit names ~40 tags. Re-running over just that range re-buckets it twelve ways, so a
    second launch takes it to three or four - and the range comes straight off the legend, with no
    edit to this file.
    """
    for argument in sys.argv:
        if not argument.startswith("--only"):
            continue
        low, _, high = argument.partition("=")[2].partition("..")
        first, last = int(low, 16), int(high, 16)
        cut = [row for row in found if first <= row[0] <= last]
        if not cut:
            raise SystemExit(f"no textures in 0x{first:08X}..0x{last:08X}")
        print(f"narrowed to 0x{first:08X}..0x{last:08X}: {len(cut)} of {len(found)} textures")
        return cut
    return found


def known_textures() -> set[int]:
    """@return The 55 bodies whose size is exactly a 2048 or 1024x2048 BC7 mip chain."""
    out = set()
    for package_id, path in sorted(INDEX.items()):
        if not any(family in path.stem for family in FAMILIES):
            continue
        for index, entry in enumerate(Package(path).entries):
            if entry.size in TEXTURE_SIZES:
                out.add(TAG_BASE + (package_id << TAG_ENTRY_BITS) + index)
    return out


def main() -> None:
    everything = textures()

    # The type filter is what keeps geometry out. Check it did not also cut textures: every body
    # whose size is exactly a BC7 mip chain is certainly a texture, so all 55 must survive it.
    missing = known_textures() - {tag for tag, _size, _stem in everything}
    if missing:
        raise SystemExit(f"the type filter dropped {len(missing)} known textures, e.g. "
                         + ", ".join(f"0x{tag:08X}" for tag in sorted(missing)[:4]))
    print(f"type filter keeps all {len(known_textures())} known textures")

    found = narrow(everything)
    if not found:
        raise SystemExit("no texture-shaped entry pairs found")
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

    # Never on a dry run. The legend is the only key to the layer that is *installed*, and a dry
    # run of a narrowed range would quietly replace it with a description of a layer nobody has.
    if "--dry-run" not in sys.argv:
        LEGEND.write_text(json.dumps(legend, indent=2), encoding="utf-8")
        print(f"\nlegend -> {LEGEND}")
    else:
        print(f"\nlegend NOT written (dry run); {LEGEND.name} still describes the live layer")
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
