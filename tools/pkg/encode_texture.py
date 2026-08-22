"""Encode one of the GLB's atlases to a Destiny texture body: BC7 mode 6 with a mip chain.

The custom model's atlases are **2048x2048**, which is exactly the top level of Destiny's
5,586,944-byte textures, so an atlas goes in at native size with no resampling and the byte count
already matches. What is missing is the encode.

A Destiny texture body is a fixed-size entry: the writer keeps its byte count and the 40-byte
header keeps its width, height, format and mip count. So the encode has to hit an exact size, and
the mip count is not free - it is whatever the target entry already carries. `chain_for()` works
that out from the target size rather than assuming, because 2048x2048 comes to 5,586,944 bytes at
five mips and 5,592,405 at a full chain, and picking wrong is a corrupt texture.

**Mode 6 only.** It is the mode the shipped textures use - every block in the dumped
`0x80EFAD63` has bit 6 set - and the flat-colour probe proved the game renders our mode-6 blocks,
when the weapon turned magenta. One mode keeps this a hundred lines instead of a thousand, at some
cost in quality on blocks with more than two dominant colours.

Usage:
    python encode_texture.py objs/textures/10_image10.png --size 5586944
    python encode_texture.py objs/textures/10_image10.png --tag 0x80B7A6FC
    python encode_texture.py --self-test
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

from bone_probe import entry_of
from paint_textures import unpack_mode6

BC7_BLOCK_BYTES = 16
# The BC7 weight table for 4-bit indices, from the format spec.
WEIGHTS4 = np.array([0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64],
                    dtype=np.int32)


def chain_for(width: int, height: int, target: int) -> int:
    """
    @return How many mip levels of `width` x `height` come to exactly `target` bytes.

    Derived rather than assumed: the same 2048x2048 image is 5,586,944 bytes over five levels and
    5,592,405 over a full chain to 1x1, and the entry size is the only thing that says which.
    """
    total, levels, w, h = 0, 0, width, height
    while w >= 4 and h >= 4:
        total += (w // 4) * (h // 4) * BC7_BLOCK_BYTES
        levels += 1
        if total == target:
            return levels
        if total > target:
            break
        w, h = max(w // 2, 1), max(h // 2, 1)
    raise SystemExit(
        f"no mip chain of {width}x{height} comes to {target:,} bytes "
        f"(1 level is {(width // 4) * (height // 4) * BC7_BLOCK_BYTES:,})")


def encode_blocks(rgba: np.ndarray) -> bytes:
    """
    @return One mip level encoded as BC7 mode-6 blocks.

    Each 4x4 block gets two endpoints from the per-channel min and max of its texels, and each
    texel an index chosen by projecting it onto the line between them. Both p-bits are held at 1 so
    a channel reconstructs as `(value << 1) | 1`, which represents 255 exactly - alpha would
    otherwise come back as 254 and make opaque texels faintly transparent.
    """
    height, width, _ = rgba.shape
    blocks = (rgba.reshape(height // 4, 4, width // 4, 4, 4)
                  .transpose(0, 2, 1, 3, 4)
                  .reshape(-1, 16, 4).astype(np.int32))

    low = blocks.min(axis=1)
    high = blocks.max(axis=1)

    # Project each texel onto the endpoint line and quantise to the 4-bit weight table.
    direction = (high - low).astype(np.float32)
    length = np.maximum((direction * direction).sum(axis=1), 1e-6)
    offset = blocks.astype(np.float32) - low[:, None, :].astype(np.float32)
    projection = (offset * direction[:, None, :]).sum(axis=2) / length[:, None]
    indices = np.clip(np.rint(projection * 15.0), 0, 15).astype(np.int32)

    # Mode 6's first index carries one bit less, so its high bit is implicit and must be zero.
    # Where it is not, swapping the endpoints and inverting every index says the same thing.
    flip = indices[:, 0] > 7
    indices[flip] = 15 - indices[flip]
    low[flip], high[flip] = high[flip].copy(), low[flip].copy()

    # endpoint = (value << 1) | 1, so value = (endpoint - 1) >> 1.
    value0 = np.clip((low - 1) >> 1, 0, 127).astype(np.uint64)
    value1 = np.clip((high - 1) >> 1, 0, 127).astype(np.uint64)

    word_low = np.uint64(64)                                   # mode 6: six zeros then a one
    for slot in range(4):                                      # R0 R1 G0 G1 B0 B1 A0 A1
        word_low = word_low | (value0[:, slot] << np.uint64(7 + slot * 14))
        word_low = word_low | (value1[:, slot] << np.uint64(14 + slot * 14))
    word_low = word_low | (np.uint64(1) << np.uint64(63))       # p0

    word_high = np.uint64(1)                                    # p1, at bit 64
    packed = indices.astype(np.uint64)
    word_high = word_high | (packed[:, 0] << np.uint64(1))      # index 0, three bits
    for texel in range(1, 16):
        word_high = word_high | (packed[:, texel] << np.uint64(4 + (texel - 1) * 4))

    out = np.empty((len(blocks), 2), dtype="<u8")
    out[:, 0] = word_low
    out[:, 1] = word_high
    return out.tobytes()


def encode(path: Path, target: int) -> bytes:
    """@return `target` bytes of BC7 mode 6: the image and as many mips as the size implies."""
    image = Image.open(path).convert("RGBA")
    width, height = image.size
    if width % 4 or height % 4:
        raise SystemExit(f"{path.name} is {width}x{height}; both sides must be multiples of 4")
    levels = chain_for(width, height, target)
    print(f"  {path.name}: {width}x{height}, {levels} mip levels -> {target:,} B")

    out = bytearray()
    for level in range(levels):
        if level:
            image = image.resize((max(width >> level, 4), max(height >> level, 4)),
                                 Image.LANCZOS)
        out += encode_blocks(np.asarray(image, dtype=np.uint8))
    if len(out) != target:
        raise SystemExit(f"encoded {len(out):,} bytes, wanted {target:,}")
    return bytes(out)


def mip01_size(width: int, height: int) -> int:
    """@return Byte count of BC7 mip 0 + mip 1 for `width` x `height`."""
    def level(w: int, h: int) -> int:
        return (max(w, 4) // 4) * (max(h, 4) // 4) * BC7_BLOCK_BYTES
    return level(width, height) + level(max(width // 2, 1), max(height // 2, 1))


def dims_for_mip01(target: int) -> tuple[int, int]:
    """@return `(width, height)` whose mip-0+1 BC7 chain is exactly `target` bytes."""
    found: list[tuple[int, int]] = []
    for width in range(4, 4097, 4):
        for height in (width, width // 2, width * 2):
            if height >= 4 and height % 4 == 0 and mip01_size(width, height) == target:
                found.append((width, height))
    if not found:
        raise SystemExit(f"no mip-0+1 BC7 chain comes to {target:,} bytes")
    # Landscape first — the GLB atlases are square and squash less across the wider side.
    found.sort(key=lambda size: (size[0] < size[1], -size[0] * size[1]))
    return found[0]


def encode_mip01(path: Path, width: int, height: int, target: int) -> bytes:
    """@return `target` bytes of BC7: the image at `width` x `height` plus one mip."""
    image = Image.open(path).convert("RGBA")
    image = image.resize((max(width, 4), max(height, 4)), Image.LANCZOS)
    out = bytearray(encode_blocks(np.asarray(image, dtype=np.uint8)))
    image = image.resize((max(width // 2, 4), max(height // 2, 4)), Image.LANCZOS)
    out += encode_blocks(np.asarray(image, dtype=np.uint8))
    if len(out) != target:
        raise SystemExit(
            f"{path.name}: encoded mip0+1 as {len(out):,} B for {width}x{height}, "
            f"entry is {target:,} B")
    return bytes(out)


def decode_blocks(data: bytes, width: int, height: int) -> np.ndarray:
    """
    @return `data` decoded back to an RGBA image, by the mode-6 rules alone.

    Written from the spec rather than from `encode_blocks`, so agreement between the two is
    evidence and not a tautology. Only what the encoder emits is handled: mode 6, both p-bits set.
    """
    words = np.frombuffer(data, dtype="<u8").reshape(-1, 2)
    low, high = words[:, 0], words[:, 1]
    if np.any((low & np.uint64(0x7F)) != np.uint64(64)):
        raise SystemExit("not every block is mode 6")

    endpoint0 = np.empty((len(words), 4), dtype=np.int32)
    endpoint1 = np.empty((len(words), 4), dtype=np.int32)
    for slot in range(4):
        endpoint0[:, slot] = (((low >> np.uint64(7 + slot * 14)) & np.uint64(0x7F)) << np.uint64(1)
                              | np.uint64(1)).astype(np.int32)
        endpoint1[:, slot] = (((low >> np.uint64(14 + slot * 14)) & np.uint64(0x7F)) << np.uint64(1)
                              | np.uint64(1)).astype(np.int32)

    indices = np.empty((len(words), 16), dtype=np.int32)
    indices[:, 0] = ((high >> np.uint64(1)) & np.uint64(0x7)).astype(np.int32)
    for texel in range(1, 16):
        indices[:, texel] = ((high >> np.uint64(4 + (texel - 1) * 4)) & np.uint64(0xF)).astype(np.int32)

    weight = WEIGHTS4[indices][:, :, None]
    texels = (endpoint0[:, None, :] * (64 - weight) + endpoint1[:, None, :] * weight + 32) >> 6
    return (texels.reshape(height // 4, width // 4, 4, 4, 4)
                  .transpose(0, 2, 1, 3, 4)
                  .reshape(height, width, 4).astype(np.uint8))


def verify(path: Path) -> None:
    """Encodes the top mip, decodes it independently, and reports the error against the source."""
    image = Image.open(path).convert("RGBA")
    original = np.asarray(image, dtype=np.uint8)
    height, width, _ = original.shape
    restored = decode_blocks(encode_blocks(original), width, height)
    error = np.abs(original[:, :, :3].astype(np.int32) - restored[:, :, :3].astype(np.int32))
    print(f"  {path.name}: {width}x{height}")
    print(f"    mean absolute error {error.mean():.2f} / 255, worst channel {error.max()}")
    print(f"    {(error.max(axis=2) <= 2).mean() * 100:.1f}% of pixels within 2")
    print(f"    alpha exactly 255 everywhere: {bool((restored[:, :, 3] == 255).all())}")


def self_test() -> None:
    """Encodes a flat image and checks the blocks decode back to the colour that went in."""
    for colour in ((255, 0, 0), (18, 52, 86), (255, 255, 255)):
        flat = np.zeros((8, 8, 4), dtype=np.uint8)
        flat[:, :, :3] = colour
        flat[:, :, 3] = 255
        block = encode_blocks(flat)[:BC7_BLOCK_BYTES]
        red, green, blue, alpha = unpack_mode6(block)
        if max(abs(red - colour[0]), abs(green - colour[1]), abs(blue - colour[2])) > 1:
            raise SystemExit(f"round trip {colour} -> {(red, green, blue)}")
        if alpha != 255:
            raise SystemExit(f"alpha came back {alpha}, not 255")
        print(f"  flat {colour} -> {(red, green, blue, alpha)}  ok")
    print("self test passed")


def main() -> None:
    if "--self-test" in sys.argv:
        self_test()
        return
    arguments = [value for value in sys.argv[1:] if not value.startswith("--")]
    if not arguments:
        raise SystemExit(__doc__)
    source = Path(arguments[0])
    if "--verify" in sys.argv:
        verify(source)
        return
    if "--tag" in sys.argv:
        tag = int(sys.argv[sys.argv.index("--tag") + 1], 0)
        entry = entry_of(tag)
        if entry is None:
            raise SystemExit(f"0x{tag:08X} is not an installed entry")
        target = entry.size
        print(f"target 0x{tag:08X}: {target:,} B")
    elif "--size" in sys.argv:
        target = int(sys.argv[sys.argv.index("--size") + 1], 0)
    else:
        raise SystemExit("give --tag or --size so the encode hits the entry's exact byte count")

    data = encode(source, target)
    out = source.with_suffix(".bc7")
    out.write_bytes(data)
    print(f"wrote {out} ({len(data):,} B)")


if __name__ == "__main__":
    main()
