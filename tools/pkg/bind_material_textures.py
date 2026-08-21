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

**Nothing here has ever loaded, and four suspects are dead.** Every layer that touches a material
stalls a job fiber in `character:signin` - the Warlock wears this armour, the other two characters
build fine in the same launch. Ruled out one launch per variable:

- **the self-declared length** at `+0x00` - a real bug, fixed, verified in the shipped bytes,
  and the stall stayed;
- **resize** - `--repoint-only` changes three bytes with no resize and stalls;
- **entry size** - a 22-block target and a 1-block target stall identically;
- **package locality** - the binding that *works* is cross-package, and a target in the
  material's own package stalls;
- **what the tag points at** - a resident dye tile of the same size, layout and kind as the
  working binding stalls too.

`--repoint-only [--target=]` is that bisect, kept because each target ruled something out. It
reports what it aims at - size, block count, and the decoded colour when our own sweep painted it -
so any outcome is readable off the screen.

**`--null-control` is what is left.** It writes the material back byte-identical, which has never
been tried: every material layer so far also changed something. Loads -> the write is sound and a
changed tag value is specifically fatal. Stalls -> a material entry cannot be rewritten at all, and
this goes back to `patch.py` rather than to the material format.

Usage:
    python bind_material_textures.py --dry-run
    python bind_material_textures.py --null-control [--dry-run]
    python bind_material_textures.py --repoint-only [--target=0xTAG] [--dry-run]
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
ORIGINAL_BINDING = 0x80C1D3CD   # what slot 3 of 0x80EFA1DC names in shipped Scatterhorn

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


def describe_target(header_tag: int) -> str:
    """@return What binding `header_tag` actually asks the streamer for, read from the packages."""
    # Local import: only this path needs the whole package index built.
    from verify_bind_layer import (
        BLOCK_SIZE,
        TAG_BASE,
        TAG_ENTRY_BITS,
        TAG_ENTRY_MASK,
        newest_of_each,
    )

    opened = newest_of_each()
    package = opened.get((header_tag - TAG_BASE) >> TAG_ENTRY_BITS)
    if package is None:
        return "target package is not installed"
    header = package.entries[(header_tag - TAG_BASE) & TAG_ENTRY_MASK]
    body_index = (header.reference - TAG_BASE) & TAG_ENTRY_MASK
    body = package.entries[body_index]
    blocks = max(1, -(-(body.start_offset + body.size) // BLOCK_SIZE))
    plain = package.blocks[body.start_block].flags == 0
    what = (f"body 0x{header.reference:08X}: {body.size:,} B, {blocks} block(s), "
            f"{'painted by us' if plain else 'original/encrypted'}")
    if not plain:
        return what
    data = package.read_entry(body_index)
    unique = {data[i:i + 16] for i in range(0, min(4096, len(data)), 16)}
    return what + (f", flat {flat_colour(next(iter(unique)))}" if len(unique) == 1 else "")


def flat_colour(block: bytes) -> tuple[int, int, int, int]:
    """@return The RGBA a single BC7 block decodes to, so a probe can be read off the screen."""
    import io
    from PIL import Image

    head = b"DDS " + struct.pack("<I", 124) + struct.pack("<I", 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000)
    head += struct.pack("<II", 4, 4) + struct.pack("<I", 16) + struct.pack("<I", 0)
    head += struct.pack("<I", 1) + b"\x00" * 44 + struct.pack("<I", 32) + struct.pack("<I", 0x4)
    head += b"DX10" + b"\x00" * 20 + struct.pack("<I", 0x1000) + b"\x00" * 16
    head += struct.pack("<IIIII", 98, 3, 0, 1, 0)  # DXGI_FORMAT_BC7_UNORM, 2D
    image = Image.open(io.BytesIO(head + block))
    image.load()
    return image.convert("RGBA").getpixel((0, 0))


def null_control() -> None:
    """Write the material back byte-identical. The last untested variable.

    Three repoints have stalled the game - a 22-block target, a 1-block target in the material's own
    package, and a resident dye tile of the same size, layout and kind as the binding that works. So
    it is not what the tag points at. The question left is whether it is the tag at all, or whether
    **rewriting this material entry stalls whatever it says**, which has never been tested because
    every material layer so far also changed something.

    This changes nothing. Same bytes, same length, same entry, through the same writer.

    - loads  -> the write is sound, and a changed tag value is specifically fatal;
    - stalls -> the material entry cannot be rewritten at all, which is a container-level fact and
                sends this back to `patch.py` rather than to the material format.
    """
    group = next(g for g in GROUPS if g.source == "GLSLShader66")
    material = dumped(group.material)
    if material is None:
        raise SystemExit(f"0x{group.material:08X}: material not dumped")

    found = texture_array(material)
    if found is None:
        raise SystemExit(f"0x{group.material:08X}: dumped copy has no texture array, so it is not "
                         "the pristine original this control needs")
    marker, count = found
    slots = dict(struct.unpack_from("<2I", material, marker + ARRAY_HEADER + i * ELEMENT_BYTES)
                 for i in range(count))
    if slots.get(ALBEDO_SLOT) != ORIGINAL_BINDING:
        raise SystemExit(f"0x{group.material:08X}: slot {ALBEDO_SLOT} reads "
                         f"0x{slots.get(ALBEDO_SLOT, 0):08X}, not the original "
                         f"0x{ORIGINAL_BINDING:08X}. The dump is not pristine and this control "
                         "would prove nothing.")
    (declared,) = struct.unpack_from("<I", material, SELF_LENGTH)
    if declared != len(material):
        raise SystemExit(f"dumped material declares {declared:,} B but is {len(material):,} B")

    print(f"null control: 0x{group.material:08X} ({group.what}) written back UNCHANGED")
    print(f"  {len(material):,} B, 1 entry, 0 bytes changed, slot {ALBEDO_SLOT} still "
          f"-> 0x{ORIGINAL_BINDING:08X}")
    print("  the only difference from a working launch is that our writer emitted this entry")

    by_package: dict[Path, dict[int, bytes]] = {}
    put(by_package, group.material, material)
    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return
    write_all(by_package, "")
    print("\nLoads  -> the write is sound; a changed tag value is specifically fatal.\n"
          "Stalls -> a material entry cannot be rewritten at all. Container problem, not format.")


def repoint_only() -> None:
    """Repoint one already-bound slot and change nothing else. See `--repoint-only` above."""
    group = next(g for g in GROUPS if g.source == "GLSLShader66")
    target = group.header
    for argument in sys.argv:
        if argument.startswith("--target="):
            target = int(argument.split("=", 1)[1], 0)
    material = dumped(group.material)
    if material is None:
        raise SystemExit(f"0x{group.material:08X}: material not dumped")
    found = texture_array(material)
    if found is None:
        raise SystemExit(f"0x{group.material:08X} has no pixel-texture array, so there is nothing "
                         "to repoint. This bisect only works on the one material that does.")
    marker, count = found

    patched, how = bind(material, target)
    if len(patched) != len(material):
        raise SystemExit(f"repoint resized the blob {len(material):,} -> {len(patched):,} B; "
                         "that is an append, which is the other half of the bisect")

    # The whole point of this layer is that it touches one tag word and nothing else, so name the
    # bytes allowed to move rather than counting them - the old and new tags may share a byte.
    slot_at = next(marker + ARRAY_HEADER + i * ELEMENT_BYTES for i in range(count)
                   if struct.unpack_from("<I", material, marker + ARRAY_HEADER + i * ELEMENT_BYTES)
                   [0] == ALBEDO_SLOT)
    allowed = range(slot_at + 4, slot_at + 8)
    stray = [i for i, (a, b) in enumerate(zip(material, patched)) if a != b and i not in allowed]
    if stray:
        raise SystemExit(f"bytes changed outside the slot-{ALBEDO_SLOT} tag word at "
                         f"+0x{slot_at + 4:03X}: {[hex(i) for i in stray[:8]]}")
    changed = sum(a != b for a, b in zip(material, patched))
    check(patched, target, group.material)

    print(f"repoint-only: 0x{group.material:08X} ({group.what}) {how}")
    print(f"  -> 0x{target:08X}, {describe_target(target)}")
    print(f"  {len(patched):,} B, 1 entry, {changed} bytes changed at +0x{slot_at + 4:03X}, "
          "no resize, no texture data written")

    by_package: dict[Path, dict[int, bytes]] = {}
    put(by_package, group.material, patched)
    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return
    write_all(by_package, "")
    print("\nGas mask takes the target's colour -> binding works at this size.\n"
          "Loads unchanged -> that pixel shader does not read slot 3.\n"
          "Hangs           -> the stall does not depend on the size of what is bound.")


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
    if "--null-control" in sys.argv:
        null_control()
        return
    if "--repoint-only" in sys.argv:
        repoint_only()
        return
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
