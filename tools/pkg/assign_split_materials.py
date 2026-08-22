"""FAILED 2026-08-22. Do not run.

Pointed tank/skin/twirl/necklace at sandbox_0691 slot-3 binders. Character select loaded.
Mask stayed (chest VS 0x81532C62). The other four parts vanished — 0691 uses world VS
0x80FEBAC3. Restored to known-good 100421.

Steal only materials that already use 0x81532C62 / 0x81532C5B. Layout matching the mask
(1408 B, slot 3) is not enough.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from bone_probe import meshes_of
from encode_texture import dims_for_mip01, encode_mip01
from inject_mesh import PART_MATERIAL, PART_STRIDE, entry_index_of, package_of, write_all
from inject_scatterhorn import CHESTS, put
from verify_bind_layer import locate, newest_of_each

TEXTURES = Path(__file__).with_name("objs") / "textures"

# Slot-finder tags are the ones the five-part split already parked on the chest.
# Draw tags are sandbox_0691 materials that already bind a unique texture at slot 3.
# Gas mask stays on 0x80EFA1DC — 019b_8 already holds its atlas.
ASSIGN = {
    "GLSLShader85": {
        "slot_tag": 0x80EF98DB,
        "draw_tag": 0x815230C1,
        "buffer": 0x80B5A17A,
        "image": "01_BlackTankTopshader_BaseColor.png",
        "what": "tank top",
    },
    "GLSLShader13": {
        "slot_tag": 0x80EFA1F7,
        "draw_tag": 0x81523662,
        "buffer": 0x80B6AD17,
        "image": "10_SkinTats_BaseColor.png",
        "what": "skin, arms",
    },
    "GLSLShader66": {
        "slot_tag": 0x80EFA1DC,
        "draw_tag": 0x80EFA1DC,
        "buffer": None,
        "image": None,
        "what": "gas mask (already painted)",
    },
    "GLSLShader22": {
        "slot_tag": 0x81532AE0,
        "draw_tag": 0x8152311E,
        "buffer": 0x80B6A9D2,
        "image": "13_Twirlshader_BaseColor.png",
        "what": "twirl",
    },
    "GLSLShader60": {
        "slot_tag": 0x81531EF0,
        "draw_tag": 0x81522AD1,
        "buffer": 0x80B6A0D0,
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
    """@return The five-part-split model with the four empty parts pointed at bindable materials."""
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
        "assign_split_materials.py is retired: 0691 world VS (0x80FEBAC3) made four parts "
        "vanish. known_good 100421 is restored. Steal only VS 0x81532C62 / 0x81532C5B."
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

    print("\n=== paint already-bound buffers ===")
    for spec in ASSIGN.values():
        if spec["buffer"] is None:
            print(f"  {spec['what']}: skipped")
            continue
        pkg, index = locate(spec["buffer"], packages)
        size = pkg.entries[index].size
        width, height = dims_for_mip01(size)
        image = TEXTURES / spec["image"]
        if not image.is_file():
            raise SystemExit(f"missing {image}; python glb_textures.py --extract")
        print(f"  {spec['what']:22} 0x{spec['buffer']:08X}  {pkg.path.name} entry {index}  "
              f"{size:,} B -> {width}x{height}  {image.name}")
        blob = encode_mip01(image, width, height, size)
        work.setdefault(pkg.path, {})[index] = blob

    print(f"\n{sum(len(v) for v in work.values())} entries across {len(work)} packages")
    for path, replacements in sorted(work.items()):
        print(f"  {path.name}: {len(replacements)} entries")
    if dry:
        print("dry-run; nothing written")
        return
    write_all(work, "")
    print("wrote. Next launch: character screen. Mask stays 019b_8; the other four should show "
          "the custom atlases (soft — the stolen slots are smaller than 2048²).")


if __name__ == "__main__":
    main()
