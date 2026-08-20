"""Paint the Scatterhorn dye *remaining mips* a flat colour, sRGB only.

SK textures split: LargeTextureBuffer (`+0x24`) is mip 0+1, `entry.reference` is the rest.
DyeData tiling is 3.4–4.5, so character select samples the 64px-and-under tail, not the
256/512 top. The first remaining-mip probe painted **normals too** (t4/t6/t8) and went
clay-white. The large-buffer probe wrote verified flat BC7 into mip 0+1 and the Guardian
stayed a grayscale weave — those top mips were not the LOD on screen.

This tool:

1. Deletes the large-buffer patch files (`019b_7` / `01b5_7` / `01bb_7`).
2. Paints only sRGB remaining-mip entries (t3/t5/t7). Normals stay shipped.

Usage:
    python paint_dye_textures.py --dry-run
    python paint_dye_textures.py
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from bone_probe import INDEX, TAG_BASE, TAG_ENTRY_BITS, TAG_ENTRY_MASK, dumped, entry_of
from patch import verify_patch, write_patch_package_multi
from tigerpkg import BLOCK_SIZE, Package

LEGEND = Path(__file__).with_name("dye_texture_legend.json")
RECEIPT = Path(__file__).with_name("inject_receipt.json")
PACKAGES = Path(r"C:\Sunrise\packages")
BC7_BLOCK_BYTES = 16
LARGE_AT = 0x24
BC7_SRGB = 99

# Previous probe: sRGB top mips. Character select did not sample them.
LARGE_MIP_PATCHES = (
    "w64_sandbox_019b_7.pkg",
    "w64_sandbox_01b5_7.pkg",
    "w64_sandbox_01bb_7.pkg",
)

# sRGB remaining-mips only. t4/t6/t8 are BC7_UNORM normals — do not flatten those.
# (channel, slot, header, expected small size, rgb, label)
DIFFUSE_MAPS = (
    ("plate", 3, 0x80BB71AD, 5488, (254, 0, 0), "plate_t3"),
    ("suit", 5, 0x80C1D3CA, 21872, (0, 254, 0), "suit_t5"),
    ("cloth", 7, 0x80C184F9, 21872, (0, 0, 254), "cloth_t7"),
)


def bc7_mode6_flat(red: int, green: int, blue: int) -> bytes:
    """@return One BC7 mode-6 block that decodes to a single opaque colour. Same as paint_textures."""
    for component in (red, green, blue):
        if not 0 <= component <= 255 or component % 2:
            raise ValueError(f"component {component} must be even and in 0..255")
    bits, position = 0, 0

    def put(value: int, width: int) -> None:
        nonlocal bits, position
        bits |= (value & ((1 << width) - 1)) << position
        position += width

    put(0b1000000, 7)
    for component in (red, green, blue, 255):
        value = (component if component != 255 else 254) >> 1
        put(value, 7)
        put(value, 7)
    put(0, 1)
    put(0, 1)
    put(0, 3)
    put(0, 60)
    if position != 128:
        raise AssertionError(f"packed {position} bits, not 128")
    return bits.to_bytes(BC7_BLOCK_BYTES, "little")


def unpack_mode6(block: bytes) -> tuple[int, int, int, int]:
    bits = int.from_bytes(block, "little")

    def take(at: int, width: int) -> int:
        return (bits >> at) & ((1 << width) - 1)

    if take(0, 7) != 0b1000000:
        raise ValueError("not a mode-6 block")
    parity = take(63, 1)
    return tuple((take(7 + slot * 14, 7) << 1) | parity for slot in range(4))


def package_of(tag: int) -> Path:
    package_id = (tag - TAG_BASE) >> TAG_ENTRY_BITS
    path = INDEX.get(package_id)
    if path is None:
        raise SystemExit(f"no package for 0x{tag:08X}")
    return path


def entry_index_of(tag: int) -> int:
    return (tag - TAG_BASE) & TAG_ENTRY_MASK


def restore_patches(names: tuple[str, ...], why: str) -> None:
    """Removes named patch files and drops them from the receipt. Mesh / `_23` stay."""
    print(f"restoring ({why}):")
    removed = []
    for name in names:
        path = PACKAGES / name
        if path.is_file():
            path.unlink()
            removed.append(str(path))
            print(f"  removed {name}")
    if not removed:
        print("  nothing to remove")
        return
    if RECEIPT.is_file():
        done = json.loads(RECEIPT.read_text())["written"]
        RECEIPT.write_text(
            json.dumps({"written": sorted(set(done) - set(removed))}, indent=2))


def write_all(by_package: dict[Path, dict[int, bytes]]) -> None:
    for path, replacements in sorted(by_package.items()):
        write_patch_package_multi(path, replacements)
        opened = Package(path)
        written = path.with_name(f"{opened.stem}_{opened.header.patch_id + 1}.pkg")
        for index, data in replacements.items():
            verify_patch(written, index, data)
        print(f"  wrote and verified {len(replacements)} entries -> {written}")
        done = json.loads(RECEIPT.read_text())["written"] if RECEIPT.is_file() else []
        RECEIPT.write_text(json.dumps({"written": sorted(set(done) | {str(written)})}, indent=2))


def large_of(header_tag: int) -> tuple[int, int, int, int]:
    """@return `(large_tag, data_size, format, width)` from a dumped 40-byte header."""
    body = dumped(header_tag)
    if body is None or len(body) < 40:
        raise SystemExit(f"0x{header_tag:08X} is not dumped; cannot read LargeTextureBuffer")
    data_size, fmt = struct.unpack_from("<II", body, 0)
    width = struct.unpack_from("<H", body, 0x0E)[0]
    (large,) = struct.unpack_from("<I", body, LARGE_AT)
    return large, data_size, fmt, width


def main() -> None:
    by_package: dict[Path, dict[int, bytes]] = {}
    legend = []
    for channel, slot, header, expected, rgb, label in DIFFUSE_MAPS:
        header_body = dumped(header)
        if header_body is None or len(header_body) < 40:
            raise SystemExit(f"0x{header:08X} is not dumped")
        fmt = struct.unpack_from("<I", header_body, 4)[0]
        if fmt != BC7_SRGB:
            raise SystemExit(f"{label} fmt={fmt}, expected BC7_UNORM_SRGB ({BC7_SRGB})")
        header_entry = entry_of(header)
        small = header_entry.reference
        small_entry = entry_of(small)
        if small_entry is None or small_entry.size != expected:
            raise SystemExit(f"{label} remaining-mip 0x{small:08X} size mismatch")
        red, green, blue = rgb
        decoded = unpack_mode6(bc7_mode6_flat(red, green, blue))
        if decoded[:3] != (red, green, blue):
            raise SystemExit(f"BC7 round trip failed for {label}")
        body = bc7_mode6_flat(red, green, blue) * (small_entry.size // BC7_BLOCK_BYTES)
        path = package_of(small)
        by_package.setdefault(path, {})[entry_index_of(small)] = body
        legend.append({
            "label": label,
            "channel": channel,
            "slot": slot,
            "header": f"0x{header:08X}",
            "small": f"0x{small:08X}",
            "package": path.stem,
            "size": small_entry.size,
            "format": fmt,
            "rgb": [red, green, blue],
            "hex": f"#{red:02X}{green:02X}{blue:02X}",
        })
        print(f"  {label:10} t{slot}  0x{small:08X}  {path.stem}  "
              f"{small_entry.size:>6} B  #{red:02X}{green:02X}{blue:02X}")

    print()
    for path, replacements in sorted(by_package.items()):
        written = sum(len(body) for body in replacements.values())
        have = Package(path).header.block_count
        blocks = -(-written // BLOCK_SIZE)
        print(f"  {path.name:<28} {len(replacements)} entries  {written:>7} B  "
              f"blocks {have} + {blocks}")

    LEGEND.write_text(json.dumps(legend, indent=2), encoding="utf-8")
    print(f"\nlegend -> {LEGEND}")

    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return

    restore_patches(LARGE_MIP_PATCHES, "unused top-mip probe")
    write_all(by_package)
    print("Launch once, reach the character screen, quit.")
    print("Expect a red / green / blue weave, not clay and not grayscale zebra.")
    print("Normals are untouched. Suit green is t5, the slot part-10 samples.")


if __name__ == "__main__":
    main()
