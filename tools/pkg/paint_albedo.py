"""
Put one of the custom model's atlases on the Guardian by painting the texture a material ALREADY
samples - so no texture tag is ever written.

**Why this route.** Changing the tag a material's albedo slot points at is the one write that has
never landed: three targets, three character-select stalls, with entry size, package locality,
record self-length, resizing, residency and the writer's container output all eliminated, and the
byte-identical null control passing. Whatever validates that word is still unknown.

It is also unnecessary. `0x80EFA1DC` (the gas mask material) already names `0x80C1D3CD` in albedo
slot 3. Overwriting the pixels that tag resolves to needs **no tag change at all** - only body
writes at their exact existing sizes, which is the operation the weapon already proved and which
every mesh layer in the working config performs.

**The texture is split**, and both halves must be written or the mips disagree:

    header 0x80C1D3CD (type 32, 40 B)   +0x00 total 349,552   +0x04 format 98 = BC7_UNORM
                                        +0x0E 512 x 512       +0x24 buffer 0x80B3611D
    0x80B3611D  pkg 019b entry  285  type 48  327,680 B   mip 0 (512) + mip 1 (256)
    0x80C1D3CB  pkg 020e entry 5067  type 40   21,872 B   mips 2.. (128 down to 1x1)
                                              total 349,552, exact

Type 48 is not exotic: the `18:05` dye group wrote three of them and is live in the working config.

The header is **not** touched, so width, height, format and mip count all stay as shipped and the
encode simply has to hit them. That is what makes this safe where the flat-colour sweeps were not -
those assumed BC7 for entries that were not BC7. Here the format is read from the header, not
assumed, and refused if it is anything but 98.

Usage:
    python paint_albedo.py --header=0x80C1D3CD --image=objs/textures/04_GasMaskshader_BaseColor.png
    python paint_albedo.py ... --write
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from encode_texture import BC7_BLOCK_BYTES, encode_blocks
from inject_mesh import write_all
from tigerpkg import TAG_BASE, TAG_ENTRY_BITS, TAG_ENTRY_MASK
from verify_bind_layer import locate, newest_of_each

DUMP = Path("C:/Sunrise/bin/x64/Sunrise/dump")
BC7_UNORM = 98
# Offsets inside the 40-byte texture header, decoded 2026-08-22 against 0x80C1D3CD and confirmed by
# arithmetic: the sizes it implies match the two entries exactly, to the byte.
OFF_TOTAL, OFF_FORMAT, OFF_WIDTH, OFF_HEIGHT, OFF_BUFFER = 0x00, 0x04, 0x0E, 0x10, 0x24


def read_header(tag: int) -> dict:
    """@return The decoded 40-byte texture header, from the in-game dump (it is encrypted on disk)."""
    path = DUMP / f"tag_{tag:08X}.bin"
    if not path.is_file():
        raise SystemExit(
            f"{path.name} is not dumped. Texture headers are encrypted and the block keys stay in "
            f"the game, so add 0x{tag:08X} to dump/request.txt (make_request.py) and launch once.")
    data = path.read_bytes()
    if len(data) != 40:
        raise SystemExit(f"{path.name} is {len(data)} bytes, expected a 40-byte header")
    return {
        "total": struct.unpack_from("<I", data, OFF_TOTAL)[0],
        "format": struct.unpack_from("<I", data, OFF_FORMAT)[0],
        "width": struct.unpack_from("<H", data, OFF_WIDTH)[0],
        "height": struct.unpack_from("<H", data, OFF_HEIGHT)[0],
        "buffer": struct.unpack_from("<I", data, OFF_BUFFER)[0],
    }


def mip_chain(image: Image.Image, width: int, height: int, total: int) -> bytes:
    """
    @return `total` bytes of BC7: the image at `width` x `height` then mips, exactly filling it.

    Levels run to 1x1 rather than stopping at 4x4 - a level below one block still occupies a whole
    16-byte block, and including them is what makes 512x512 come to 349,552 rather than 349,520.
    """
    out = bytearray()
    level_w, level_h = width, height
    while len(out) < total:
        # BC7 works on 4x4 blocks, so a level smaller than that is encoded padded up to one block.
        source = image.resize((max(level_w, 4), max(level_h, 4)), Image.LANCZOS)
        out.extend(encode_blocks(np.asarray(source, dtype=np.uint8)))
        if level_w <= 1 and level_h <= 1:
            break
        level_w, level_h = max(level_w // 2, 1), max(level_h // 2, 1)
    if len(out) != total:
        raise SystemExit(
            f"encoded {len(out):,} bytes for {width}x{height}, header says {total:,}. The mip chain "
            "does not match the header; refusing to write a texture the header misdescribes.")
    return bytes(out)


def main() -> None:
    args = sys.argv[1:]
    header_tag = next((int(a.split("=", 1)[1], 0) for a in args if a.startswith("--header=")), None)
    image_path = next((a.split("=", 1)[1] for a in args if a.startswith("--image=")), None)
    if header_tag is None or image_path is None:
        raise SystemExit(__doc__)

    info = read_header(header_tag)
    print(f"header 0x{header_tag:08X}: {info['width']}x{info['height']}, format {info['format']}, "
          f"total {info['total']:,} B, buffer 0x{info['buffer']:08X}")
    if info["format"] != BC7_UNORM:
        raise SystemExit(
            f"format is {info['format']}, not {BC7_UNORM} (BC7_UNORM). This tool only writes BC7; "
            "filling a non-BC7 entry with BC7 blocks is exactly what broke the flat-colour sweeps.")

    packages = newest_of_each()
    buf_pkg, buf_entry = locate(info["buffer"], packages)
    buf_size = buf_pkg.entries[buf_entry].size
    # The remaining mips live in the entry the header pairs with, two indices below it.
    rest_index = ((header_tag - TAG_BASE) & TAG_ENTRY_MASK) - 2
    rest_pkg, _ = locate(header_tag, packages)
    rest_size = rest_pkg.entries[rest_index].size
    print(f"  mip0+1 -> 0x{info['buffer']:08X}  {buf_pkg.stem[-4:]} entry {buf_entry}  "
          f"{buf_size:,} B (type {getattr(buf_pkg.entries[buf_entry], 'entry_type', '?')})")
    print(f"  mips2+ -> {rest_pkg.stem[-4:]} entry {rest_index}  {rest_size:,} B "
          f"(type {getattr(rest_pkg.entries[rest_index], 'entry_type', '?')})")
    if buf_size + rest_size != info["total"]:
        raise SystemExit(
            f"{buf_size:,} + {rest_size:,} = {buf_size + rest_size:,}, but the header says "
            f"{info['total']:,}. The split is not what this tool assumes; stopping.")

    image = Image.open(image_path).convert("RGBA")
    print(f"  source {Path(image_path).name}: {image.size[0]}x{image.size[1]}")
    blob = mip_chain(image, info["width"], info["height"], info["total"])
    print(f"  encoded {len(blob):,} B, splitting at {buf_size:,}")

    if "--write" not in args:
        print("\nNothing written. Re-run with --write.")
        return
    write_all({
        buf_pkg.path: {buf_entry: blob[:buf_size]},
        rest_pkg.path: {rest_index: blob[buf_size:]},
    }, sandbox="")


if __name__ == "__main__":
    main()
