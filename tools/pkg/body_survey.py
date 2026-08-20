"""
Surveys every dumped `SEntityModel` for body-shaped geometry, grouped by package.

A package holding one body-shaped model among hundreds of props is a world package. A package
holding a *family* of body-shaped models is a character package, and that difference is what
separates the Guardian from Tower scenery without a single game launch.

Usage:
    python body_survey.py                     # packages ranked by body-shaped models
    python body_survey.py --package sandbox_0691
    python body_survey.py --min-bytes 20000
"""
from __future__ import annotations

import struct
from collections import defaultdict
from pathlib import Path

from parse_models import INDEX, Model, lookup, option
from tigerpkg import TAG_BASE, TAG_ENTRY_BITS

ENTITY_MODEL = 0x808073A5
DUMPS = [Path(r"C:\Sunrise\bin\x64\Sunrise\dump"),
         Path(r"C:\Sunrise\bin\x64\Sunrise\dump_models")]
BODY_FLOOR = (-1.00, 0.30)
BODY_TOP = (1.4, 2.4)

NAME = {pid: path.stem.rsplit("_", 1)[0].replace("w64_", "") for pid, path in INDEX.items()}


def where(tag: int) -> str:
    return NAME.get((tag - TAG_BASE) >> TAG_ENTRY_BITS, f"pkg?{(tag - TAG_BASE) >> TAG_ENTRY_BITS:04X}")


def main() -> None:
    want = option("--package", "")
    floor_bytes = int(option("--min-bytes", "4000"))

    per_package: dict[str, list[tuple]] = defaultdict(list)
    total = 0
    for folder in DUMPS:
        for path in folder.glob("tag_*.bin"):
            try:
                tag = int(path.stem[4:], 16)
            except ValueError:
                continue
            entry = lookup(tag)
            if entry is None or entry.reference != ENTITY_MODEL:
                continue
            data = path.read_bytes()
            if len(data) < 0xA0:
                continue
            (declared,) = struct.unpack_from("<q", data, 0)
            if declared != len(data):
                continue
            model = Model(tag, data)
            if not model.meshes or not model.scale:
                continue
            total += 1
            half = max(abs(value) for value in model.scale[:3])
            floor = model.translation[2] - half
            top = model.translation[2] + half
            positions = sum(mesh.position_bytes for mesh in model.meshes)
            if not (BODY_FLOOR[0] <= floor <= BODY_FLOOR[1] and BODY_TOP[0] <= top <= BODY_TOP[1]):
                continue
            if positions < floor_bytes:
                continue
            per_package[where(tag)].append((tag, len(model.meshes), floor, top, positions))

    print(f"{total:,} dumped SEntityModels parsed; "
          f"{sum(len(v) for v in per_package.values())} body-shaped with >= {floor_bytes:,} B\n")

    if want:
        rows = sorted(per_package.get(want, []), key=lambda row: -row[4])
        print(f"--- {want}: {len(rows)} body-shaped ---")
        print(f"{'tag':>10} {'meshes':>6} {'floor':>7} {'top':>7} {'pos B':>10}")
        for tag, meshes, floor, top, positions in rows:
            print(f"0x{tag:08X} {meshes:6} {floor:7.3f} {top:7.3f} {positions:10,}")
        return

    print(f"{'bodies':>6}  package")
    for package, rows in sorted(per_package.items(), key=lambda kv: -len(kv[1])):
        biggest = max(row[4] for row in rows)
        print(f"{len(rows):6}  {package:<24} largest {biggest:,} B  "
              f"e.g. 0x{max(rows, key=lambda row: row[4])[0]:08X}")


if __name__ == "__main__":
    main()
