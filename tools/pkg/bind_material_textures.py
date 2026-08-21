"""Bind the custom model's own atlases to the five carrier materials.

The body draws as five parts with five materials (see `inject_scatterhorn.py`), but four of those
five materials name **no texture at all** and the fifth names one small one. That is not a search
failure - it is what the material says. A material's pixel-texture array is the field at `+0x2D0`,
and in four of the five it is `{count 0, offset 0}`.

So the albedo is not found, it is **bound**. This writes the binding.

What was decoded to get here, all offline from dumped material bodies:

- A material field is `{int64 count, int64 offset}` and the array's *count word* sits at
  `field + 8 + offset` - so the marker is four bytes before that. This convention resolves every
  array in every one of the five materials, which is why it is trusted.
- An inline array is `[0x80809FBD][count][0][element class][0]` - twenty bytes - then the elements.
- The pixel-texture element class is `0x80807211`, eight bytes: `{u32 sampler slot, u32 texture
  header tag}`. `0x80EFA1DC` (GasMask) has exactly one, at slot 3.

Because the field is a *relative offset*, a new array can be appended to the end of the blob and
the field aimed at it. **No existing byte moves**, which is the whole reason this is safe to do to
a structure whose other fields are not fully decoded.

Slot 3 is used for all five. Two independent sources agree on it: the one material that already
binds a texture binds it at slot 3, and the dye system's plate channel carries its albedo at slot
3 (`paint_dye_slots.py`). If a group does not take colour, its pixel shader reads a different
slot and the next round sweeps slots for that group alone.

The atlases are written over five existing 2048x2048 texture bodies rather than into new entries.
All five are exactly 5,586,944 B, which is the full five-level BC7 chain for 2048x2048, so the
encode lands at native size with no resampling and no entry resize. Their headers are rewritten
from `0x80EFAD60`, a dumped and verified header for a texture of exactly those dimensions.

**`--bind-only` is the bisect**, and it exists because the first attempt hung the game at character
select. That layer changed three things at once: it resized five material blobs, rewrote five
texture headers, and wrote five 5,586,944-byte texture bodies - 27.9 MB, where the largest write
this pipeline had ever verified was 798 KB across four blocks. `docs/PACKAGES.md` already records
oversized buffers hanging the loader while single-block ones load fine, so the big write is the
first suspect and the material surgery is the second.

`--bind-only` separates them: it patches the five materials and writes **no texture data at all**,
pointing every one of them at a texture that already exists and was never touched. About 8 KB.
If the game loads, the material surgery is sound and the 27.9 MB write is what hangs it. If it
hangs anyway, the material surgery is at fault and the atlases are innocent. Either answer costs
one launch, and it reports itself on screen - all five regions wear the same real texture.

Usage:
    python bind_material_textures.py --dry-run
    python bind_material_textures.py --bind-only [--dry-run]
    python bind_material_textures.py            # Destiny closed
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from bone_probe import dumped
from encode_texture import encode
from inject_scatterhorn import entry_index_of, package_of, put, write_all

ARRAY_CLASS = 0x80809FBD
TEXTURE_BINDING = 0x80807211
TEXTURE_ARRAY_FIELD = 0x2D0
SELF_LENGTH = 0x00        # a structured record's own byte length; the loader trusts it over the entry
ARRAY_HEADER = 0x14
ELEMENT_BYTES = 8
ALBEDO_SLOT = 3

# A dumped, verified 40-byte header for a 2048x2048 BC7_UNORM texture with five mips and no split
# buffer. Every body below is the same shape, so the same forty bytes describe all of them.
HEADER_TEMPLATE = 0x80EFAD60
ATLAS_BYTES = 5_586_944
ATLAS_SIDE = 2048

TEXTURES = Path(__file__).with_name("objs") / "textures"


@dataclass(frozen=True)
class Group:
    source: str        # the GLB material this atlas belongs to
    what: str          # what it covers, for the log
    image: str         # base colour atlas, from glb_textures.py
    material: int      # the Destiny material the part draws with
    body: int          # texture body we overwrite
    header: int        # its 40-byte header


GROUPS = [
    Group("GLSLShader85", "tank top", "01_BlackTankTopshader_BaseColor.png",
          0x80EF98DB, 0x80EF880E, 0x80EF8811),
    Group("GLSLShader13", "skin, arms", "10_SkinTats_BaseColor.png",
          0x80EFA1F7, 0x80EF881F, 0x80EF881D),
    Group("GLSLShader66", "gas mask", "04_GasMaskshader_BaseColor.png",
          0x80EFA1DC, 0x80EF89F3, 0x80EF89F1),
    Group("GLSLShader22", "twirl", "13_Twirlshader_BaseColor.png",
          0x81532AE0, 0x80EFAD63, 0x80EFAD60),
    Group("GLSLShader60", "necklace", "07_Plancha_BaseColor.png",
          0x81531EF0, 0x80EFB7B9, 0x80EFB76B),
]


def texture_array(data: bytes) -> tuple[int, int] | None:
    """@return `(marker offset, count)` of the pixel-texture array, or None when the field is null."""
    count, offset = struct.unpack_from("<qq", data, TEXTURE_ARRAY_FIELD)
    if count <= 0:
        return None
    marker = TEXTURE_ARRAY_FIELD + 8 + offset - 4
    if not 0 <= marker <= len(data) - ARRAY_HEADER:
        raise SystemExit(f"texture array offset {offset} lands outside a {len(data):,} B material")
    (seen,) = struct.unpack_from("<I", data, marker)
    its_count, _, element, _ = struct.unpack_from("<4I", data, marker + 4)
    if seen != ARRAY_CLASS or element != TEXTURE_BINDING or its_count != count:
        raise SystemExit(f"+0x{marker:03X} is not the pixel-texture array it claims to be: "
                         f"marker 0x{seen:08X}, elem 0x{element:08X}, count {its_count} vs {count}")
    return marker, count


def bind(data: bytes, header_tag: int) -> tuple[bytes, str]:
    """@return The material with `header_tag` bound at the albedo slot, and what had to be done."""
    existing = texture_array(data)
    if existing is not None:
        marker, count = existing
        for index in range(count):
            at = marker + ARRAY_HEADER + index * ELEMENT_BYTES
            slot, old = struct.unpack_from("<2I", data, at)
            if slot == ALBEDO_SLOT:
                out = bytearray(data)
                struct.pack_into("<I", out, at + 4, header_tag)
                return bytes(out), f"repointed slot {slot} from 0x{old:08X}"
        raise SystemExit(f"material has {count} textures but none at slot {ALBEDO_SLOT}; "
                         "growing an existing array is not implemented")

    # Append. The field is a relative offset, so the array can live past every byte the game
    # already knows about and nothing in front of it shifts. Existing arrays all place their count
    # word on a 16-byte boundary, so this one does too.
    out = bytearray(data)
    while (len(out) + 4) % 16:
        out.append(0)
    marker = len(out)
    out += struct.pack("<5I", ARRAY_CLASS, 1, 0, TEXTURE_BINDING, 0)
    out += struct.pack("<2I", ALBEDO_SLOT, header_tag)
    while len(out) % 16:
        out.append(0)
    struct.pack_into("<qq", out, TEXTURE_ARRAY_FIELD, 1, marker + 4 - TEXTURE_ARRAY_FIELD - 8)
    # A structured record carries its own byte length at +0x00, and the loader believes that over
    # the entry size. Leaving it at the old value is what hung the game at character select: the
    # appended array sat past the end the blob declared, so the field pointed outside the region
    # the loader had. 2,715 of 2,718 dumped type-8 records declare their length here.
    struct.pack_into("<I", out, SELF_LENGTH, len(out))
    return bytes(out), f"appended array at +0x{marker:03X}, {len(data):,} -> {len(out):,} B"


def check(data: bytes, header_tag: int, material: int) -> None:
    """Read the binding back out of the bytes about to ship, by the same rules the game uses."""
    (declared,) = struct.unpack_from("<I", data, SELF_LENGTH)
    if declared != len(data):
        raise SystemExit(f"0x{material:08X}: blob declares {declared:,} B at +0x00 but is "
                         f"{len(data):,} B. This is the bug that hung character select - the "
                         "loader believes the declared length, so anything past it is outside "
                         "the region it has.")
    found = texture_array(data)
    if found is None:
        raise SystemExit(f"0x{material:08X}: texture array reads back as null")
    marker, count = found
    bound = {slot: tag for slot, tag in
             (struct.unpack_from("<2I", data, marker + ARRAY_HEADER + i * ELEMENT_BYTES)
              for i in range(count))}
    if bound.get(ALBEDO_SLOT) != header_tag:
        raise SystemExit(f"0x{material:08X}: slot {ALBEDO_SLOT} reads back as "
                         f"0x{bound.get(ALBEDO_SLOT, 0):08X}, wanted 0x{header_tag:08X}")


def atlas(group: Group) -> bytes:
    """@return `ATLAS_BYTES` of BC7, encoded once and cached beside the source image."""
    source = TEXTURES / group.image
    if not source.is_file():
        raise SystemExit(f"missing atlas {source}\nExtract them: python glb_textures.py")
    cache = source.with_suffix(".bc7")
    if cache.is_file() and cache.stat().st_size == ATLAS_BYTES:
        print(f"  {group.image}: cached {ATLAS_BYTES:,} B")
        return cache.read_bytes()
    with Image.open(source) as image:
        width, height = image.size
        if (width, height) != (ATLAS_SIDE, ATLAS_SIDE):
            # Twirl ships at 512x512. Upscaling to the common size costs disc space and no quality
            # - the sampler was going to magnify it anyway - and keeps every group on one target
            # size, so no group needs its own hijack entry of a different shape.
            print(f"  {group.image}: {width}x{height} -> {ATLAS_SIDE}x{ATLAS_SIDE}")
            source = TEXTURES / f"_upscaled_{group.image}"
            image.convert("RGBA").resize((ATLAS_SIDE, ATLAS_SIDE), Image.LANCZOS).save(source)
            cache = source.with_suffix(".bc7")
    data = encode(source, ATLAS_BYTES)
    cache.write_bytes(data)
    return data


def bind_only() -> None:
    """Patch the five materials and write no texture data. See `--bind-only` above."""
    print(f"bind-only: every material -> existing texture 0x{HEADER_TEMPLATE:08X}, "
          "no texture bytes written")
    by_package: dict[Path, dict[int, bytes]] = {}
    for group in GROUPS:
        material = dumped(group.material)
        if material is None:
            raise SystemExit(f"0x{group.material:08X}: material not dumped")
        patched, how = bind(material, HEADER_TEMPLATE)
        check(patched, HEADER_TEMPLATE, group.material)
        print(f"  {group.source:<14} 0x{group.material:08X}: {how}")
        put(by_package, group.material, patched)
    total = sum(len(b) for entries in by_package.values() for b in entries.values())
    print(f"\n{sum(len(v) for v in by_package.values())} entries, {total:,} B, "
          f"{len(by_package)} packages")
    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return
    write_all(by_package, "")
    print("\nIf this loads, the material surgery is sound and the 27.9 MB texture write is what "
          "hangs it. If it hangs, the surgery is at fault.")


def main() -> None:
    if "--bind-only" in sys.argv:
        bind_only()
        return
    template = dumped(HEADER_TEMPLATE)
    if template is None or len(template) != 40:
        raise SystemExit(f"0x{HEADER_TEMPLATE:08X}: need the 40-byte reference header dumped")
    width, height = struct.unpack_from("<HH", template, 0x0E)
    if (width, height) != (ATLAS_SIDE, ATLAS_SIDE):
        raise SystemExit(f"template header is {width}x{height}, not {ATLAS_SIDE} square")
    print(f"header template 0x{HEADER_TEMPLATE:08X}: {width}x{height}, "
          f"format {struct.unpack_from('<I', template, 4)[0]}, {template[0x17]} mips")

    by_package: dict[Path, dict[int, bytes]] = {}
    for group in GROUPS:
        material = dumped(group.material)
        if material is None:
            raise SystemExit(f"0x{group.material:08X}: material not dumped; "
                             "python material_probe.py --request, then launch once")
        print(f"\n=== {group.source} ({group.what}) ===")
        data = atlas(group)
        patched, how = bind(material, group.header)
        check(patched, group.header, group.material)
        print(f"  material 0x{group.material:08X}: {how}")
        print(f"  texture  0x{group.body:08X} body + 0x{group.header:08X} header, "
              f"{len(data):,} B")
        put(by_package, group.body, data)
        put(by_package, group.header, template)
        put(by_package, group.material, patched)

    total = sum(len(blob) for entries in by_package.values() for blob in entries.values())
    print(f"\n{sum(len(v) for v in by_package.values())} entries, {total / 1e6:.1f} MB, "
          f"across {len(by_package)} packages")
    for path, entries in sorted(by_package.items()):
        print(f"  {path.name}: {len(entries)} entries")
    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return
    write_all(by_package, "")
    print("\nFive atlases bound at slot 3. A group that stays Scatterhorn-coloured reads a "
          "different slot; sweep slots for that group alone.")


if __name__ == "__main__":
    main()
