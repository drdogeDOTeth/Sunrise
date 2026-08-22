"""Point all five split parts at the working gas-mask material. No paint.

Two off-chest steals vanished tank/skin/twirl/necklace while the mask stayed:
0691 world VS, then C62/C5B materials that were not on the chest model.

The five-part split already proved that changing a part's material tag to a
chest-native material draws. `0x80EFA1DC` is on the chest AND binds a texture
(the painted mask atlas). This is the control: one atlas on every part.

Expect the custom mask graphic mapped with each part's UVs — wrong layout, but
the body should be visible and textured. Unique atlases stay blocked until we
can bind more buffers on chest-native materials.

Usage:
    python assign_chest_mask.py --dry-run
    python assign_chest_mask.py          # Destiny closed
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from bone_probe import meshes_of
from inject_mesh import PART_STRIDE, write_all
from inject_scatterhorn import CHESTS, put
from verify_bind_layer import locate, newest_of_each

MASK = 0x80EFA1DC
SLOTS = {
    "tank top": 0x80EF98DB,
    "skin, arms": 0x80EFA1F7,
    "gas mask": 0x80EFA1DC,
    "twirl": 0x81532AE0,
    "necklace": 0x81531EF0,
}


def slot_for(data: bytes, material: int) -> int:
    """@return The first LOD-0 part that currently names `material`."""
    for index, (at, _primitive, _offset, count, lod) in enumerate(meshes_of(data)[0]["parts"]):
        (seen,) = struct.unpack_from("<I", data, at)
        if seen == material and lod == 0 and count:
            return index
    raise SystemExit(f"no live LOD-0 part names 0x{material:08X}; the split is not on this model")


def rewrite_model(data: bytes, tag: int) -> bytes:
    """@return The five-part-split model with every part naming the mask material."""
    out = bytearray(data)
    first_at = meshes_of(data)[0]["parts"][0][0]
    for what, finder in SLOTS.items():
        slot = slot_for(data, finder)
        at = first_at + slot * PART_STRIDE
        old = struct.unpack_from("<I", out, at)[0]
        struct.pack_into("<I", out, at, MASK)
        print(f"  0x{tag:08X} {what:22} slot {slot:2}  0x{old:08X} -> 0x{MASK:08X}")
        seen = struct.unpack_from("<I", out, at)[0]
        if seen != MASK:
            raise SystemExit(f"slot {slot} did not take the mask tag")
    return bytes(out)


def main() -> None:
    dry = "--dry-run" in sys.argv
    packages = newest_of_each()
    work: dict[Path, dict[int, bytes]] = {}
    print("=== all five parts -> 0x80EFA1DC (chest-native, already painted) ===")
    for tag in CHESTS:
        pkg, index = locate(tag, packages)
        data = pkg.read_entry(index)
        rewritten = rewrite_model(data, tag)
        if rewritten == data:
            raise SystemExit(f"0x{tag:08X}: rewrite was a no-op")
        put(work, tag, rewritten)
    print(f"\n{sum(len(v) for v in work.values())} entries across {len(work)} packages")
    for path, replacements in sorted(work.items()):
        print(f"  {path.name}: {len(replacements)} entries")
    if dry:
        print("dry-run; nothing written")
        return
    write_all(work, "")
    print("wrote. Next launch: character select. Whole body should show the mask atlas "
          "(wrong UVs). Vanish -> restore 100421.")


if __name__ == "__main__":
    main()
