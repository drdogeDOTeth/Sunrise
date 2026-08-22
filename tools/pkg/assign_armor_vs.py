"""FAILED 2026-08-22. Do not run.

Off-chest C62/C5B slot-3 binders vanished tank/skin/twirl/necklace the same way 0691
did. Armor VS is not enough; the material has to already live on the chest model.
Restored to known-good 100421. Control is assign_chest_mask.py.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from bone_probe import dumped, meshes_of
from encode_texture import encode_mip01
from inject_mesh import PART_STRIDE, write_all
from inject_scatterhorn import CHESTS, put
from paint_albedo import BC7_FORMATS, OFF_BUFFER, OFF_FORMAT, OFF_HEIGHT, OFF_WIDTH, read_header
from verify_bind_layer import locate, newest_of_each

TEXTURES = Path(__file__).with_name("objs") / "textures"
MASK_BUFFER = 0x80B3611D
ARMOR_VS = {0x81532C62, 0x81532C5B}

ASSIGN = {
    "GLSLShader85": {
        "slot_tag": 0x80EF98DB,
        "draw_tag": 0x81530032,
        "header": 0x80C1D2D4,
        "image": "01_BlackTankTopshader_BaseColor.png",
        "what": "tank top",
    },
    "GLSLShader13": {
        "slot_tag": 0x80EFA1F7,
        "draw_tag": 0x80EF812C,
        "header": 0x80BDF93E,
        "image": "10_SkinTats_BaseColor.png",
        "what": "skin, arms",
    },
    "GLSLShader66": {
        "slot_tag": 0x80EFA1DC,
        "draw_tag": 0x80EFA1DC,
        "header": None,
        "image": None,
        "what": "gas mask (already painted)",
    },
    "GLSLShader22": {
        "slot_tag": 0x81532AE0,
        "draw_tag": 0x81531983,
        "header": 0x80EF9BA7,
        "image": "13_Twirlshader_BaseColor.png",
        "what": "twirl",
    },
    "GLSLShader60": {
        "slot_tag": 0x81531EF0,
        "draw_tag": 0x80BFA114,
        "header": 0x80EFB6D8,
        "image": "07_Plancha_BaseColor.png",
        "what": "necklace",
    },
}


def slot_for(data: bytes, material: int) -> int:
    """@return The first LOD-0 part that currently names `material`."""
    for index, (at, _primitive, _offset, count, lod) in enumerate(meshes_of(data)[0]["parts"]):
        (seen,) = struct.unpack_from("<I", data, at)
        if seen == material and lod == 0 and count:
            return index
    raise SystemExit(f"no live LOD-0 part names 0x{material:08X}; the split is not on this model")


def rewrite_model(data: bytes, tag: int) -> bytes:
    """@return The five-part-split model with the four empty parts pointed at armor-VS binders."""
    out = bytearray(data)
    first_at = meshes_of(data)[0]["parts"][0][0]
    for spec in ASSIGN.values():
        slot = slot_for(data, spec["slot_tag"])
        at = first_at + slot * PART_STRIDE
        old = struct.unpack_from("<I", out, at)[0]
        struct.pack_into("<I", out, at, spec["draw_tag"])
        print(f"  0x{tag:08X} {spec['what']:22} slot {slot:2}  "
              f"0x{old:08X} -> 0x{spec['draw_tag']:08X}")
    for spec in ASSIGN.values():
        slot_for(bytes(out), spec["draw_tag"])
    return bytes(out)


def main() -> None:
    raise SystemExit(
        "assign_armor_vs.py is retired: off-chest C62/C5B binders vanished four parts "
        "the same way 0691 did. Constraint is chest-native materials, not just armor VS. "
        "Restored 100421. Use assign_chest_mask.py for the on-chest control."
    )
    dry = "--dry-run" in sys.argv
    packages = newest_of_each()
    work: dict[Path, dict[int, bytes]] = {}

    print("=== part materials ===")
    for tag in CHESTS:
        pkg, index = locate(tag, packages)
        data = pkg.read_entry(index)
        rewritten = rewrite_model(data, tag)
        if rewritten == data:
            raise SystemExit(f"0x{tag:08X}: rewrite was a no-op")
        put(work, tag, rewritten)

    print("\n=== paint already-bound slot-3 buffers ===")
    painted: list[int] = []
    for spec in ASSIGN.values():
        if spec["header"] is None:
            print(f"  {spec['what']}: skipped")
            continue
        info = read_header(spec["header"])
        if info["format"] not in BC7_FORMATS:
            raise SystemExit(
                f"{spec['what']}: header 0x{spec['header']:08X} format {info['format']}, not BC7")
        if info["buffer"] == MASK_BUFFER:
            raise SystemExit(f"{spec['what']}: refused to paint the working mask buffer")
        vs_body = dumped(spec["draw_tag"])
        if vs_body is None or len(vs_body) < 0x04C:
            raise SystemExit(f"{spec['what']}: draw material 0x{spec['draw_tag']:08X} not dumped")
        vs = struct.unpack_from("<I", vs_body, 0x048)[0]
        if vs not in ARMOR_VS:
            raise SystemExit(
                f"{spec['what']}: draw VS 0x{vs:08X} is not armor "
                f"(0x81532C62 / 0x81532C5B) — that is the 0691 vanish")
        pkg, index = locate(info["buffer"], packages)
        size = pkg.entries[index].size
        image = TEXTURES / spec["image"]
        if not image.is_file():
            raise SystemExit(f"missing {image}; python glb_textures.py --extract")
        print(f"  {spec['what']:22} 0x{info['buffer']:08X}  {pkg.path.name} entry {index}  "
              f"{size:,} B -> {info['width']}x{info['height']} fmt={info['format']}  {image.name}")
        blob = encode_mip01(image, info["width"], info["height"], size)
        work.setdefault(pkg.path, {})[index] = blob
        painted.append(info["buffer"])

    if MASK_BUFFER in painted:
        raise SystemExit("mask buffer snuck into the paint set")
    print(f"\n{sum(len(v) for v in work.values())} entries across {len(work)} packages")
    for path, replacements in sorted(work.items()):
        print(f"  {path.name}: {len(replacements)} entries")
    if dry:
        print("dry-run; nothing written")
        return
    write_all(work, "")
    print("wrote. Next launch: character select. Mask stays 019b_8. "
          "Tank/skin/twirl/necklace should show the GLB atlases (soft 512/1024).")


if __name__ == "__main__":
    main()
