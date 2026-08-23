"""Blank the two globals copies of the default upper-body mesh (head through hands).

The leftover stock gloves are not on arrangement 2292. Live 981E/9809 only draw our
7902-index hands. The last capture's live headers named this replicated player-body
piece instead: same 7685/7754 part signature in three places.

    0x815B9521  globals_06dc  live in the hands-on-gauntlets session
    0x80FDBE41  globals_03ed  same box, second globals copy
    0x80EFC649  ui_037e       same mesh 0/1 plus select extras — do not touch

Blanking is the probe. If the leather on the hands (and maybe the bald head) vanishes,
this is the race-path default. If they stay, restore 20260822-235602.

Does not touch the UI copy. Does not --fingers. Does not AABB-fit.

Usage:
    python blank_race_gloves.py --dry-run
    python blank_race_gloves.py
    python known_good.py --restore --snapshot=20260822-235602.json
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from inject_mesh import blank_model, entry_index_of, package_of, write_all
from parse_models import Model

DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
# UI copy 0x80EFC649 stays. Wrapping ui_037e spun character-select cards.
TARGETS = (0x815B9521, 0x80FDBE41)


def load_model(tag: int) -> Model:
    path = DUMP / f"tag_{tag:08X}.bin"
    if not path.is_file():
        raise SystemExit(f"0x{tag:08X} is not dumped")
    data = path.read_bytes()
    (declared,) = struct.unpack_from("<q", data, 0)
    if declared != len(data):
        raise SystemExit(f"0x{tag:08X} dump is not an entity model")
    model = Model(tag, data)
    if not model.meshes:
        raise SystemExit(f"0x{tag:08X} has no meshes")
    return model


def main() -> None:
    by_package: dict[Path, dict[int, bytes]] = {}
    for tag in TARGETS:
        model = load_model(tag)
        drawn = sum(1 for mesh in model.meshes for part in mesh.parts if part[3])
        by_package.setdefault(package_of(tag), {})[entry_index_of(tag)] = blank_model(model)
        print(f"blank 0x{tag:08X}  z={model.height:.3f} span={model.span:.3f}  "
              f"{len(model.meshes)} meshes  {drawn} drawn parts  {package_of(tag).name}")
    print(f"{len(TARGETS)} models across {len(by_package)} packages")
    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return
    write_all(by_package, "")


if __name__ == "__main__":
    main()
