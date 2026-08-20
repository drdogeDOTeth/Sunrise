"""
Reports which dumped models have every buffer they need to be turned into geometry.

A model entry is only a table of pointers. Rendering it needs the vertex buffer header (for the
stride), the position buffer and the index buffer, each a separate dumped entry. This says which
models are already complete on disk and, for the rest, exactly which tags a dump request must ask
for - so one launch collects everything instead of discovering a gap after the game has closed.

Usage:
    python renderable.py --package sandbox_0691
    python renderable.py --tags 0x8152217E,0x80EFC299
    python renderable.py --package sandbox_0691 --missing   # tags to request
"""
from __future__ import annotations

import struct
from pathlib import Path

from parse_models import INDEX, Model, lookup, option
from tigerpkg import TAG_BASE, TAG_ENTRY_BITS

ENTITY_MODEL = 0x808073A5
GEOMETRY = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
DUMPS = [GEOMETRY, Path(r"C:\Sunrise\bin\x64\Sunrise\dump_models")]

NAME = {pid: path.stem.rsplit("_", 1)[0].replace("w64_", "") for pid, path in INDEX.items()}


def where(tag: int) -> str:
    return NAME.get((tag - TAG_BASE) >> TAG_ENTRY_BITS, f"pkg?{(tag - TAG_BASE) >> TAG_ENTRY_BITS:04X}")


def have(tag: int) -> bool:
    return (GEOMETRY / f"tag_{tag:08X}.bin").is_file()


def load(tag: int) -> Model | None:
    for folder in DUMPS:
        path = folder / f"tag_{tag:08X}.bin"
        if not path.is_file():
            continue
        data = path.read_bytes()
        if len(data) < 0xA0:
            return None
        (declared,) = struct.unpack_from("<q", data, 0)
        if declared != len(data):
            return None
        model = Model(tag, data)
        return model if model.meshes and model.scale else None
    return None


def needs(model: Model) -> list[int]:
    """@return Buffer tags this model needs that are not on disk, in request order."""
    wanted: list[int] = []
    for mesh in model.meshes:
        for tag in (mesh.positions, mesh.position_buffer, mesh.indices, mesh.index_buffer):
            if tag and not have(tag) and tag not in wanted:
                wanted.append(tag)
    return wanted


def main() -> None:
    package = option("--package", "")
    tag_list = option("--tags", "")
    show_missing = "--missing" in __import__("sys").argv

    tags: list[int] = []
    if tag_list:
        tags = [int(part, 0) for part in tag_list.split(",") if part.strip()]
    else:
        for folder in DUMPS:
            for path in folder.glob("tag_*.bin"):
                try:
                    tag = int(path.stem[4:], 16)
                except ValueError:
                    continue
                entry = lookup(tag)
                if entry is None or entry.reference != ENTITY_MODEL:
                    continue
                if package and where(tag) != package:
                    continue
                tags.append(tag)

    complete: list[tuple[int, int]] = []
    gaps: list[tuple[int, list[int]]] = []
    for tag in sorted(set(tags)):
        model = load(tag)
        if model is None:
            continue
        missing = needs(model)
        positions = sum(mesh.position_bytes for mesh in model.meshes)
        if missing:
            gaps.append((tag, missing))
        else:
            complete.append((tag, positions))

    if show_missing:
        wanted: list[int] = []
        for _, missing in gaps:
            for tag in missing:
                if tag not in wanted:
                    wanted.append(tag)
        for tag in wanted:
            print(f"tag 0x{tag:08X}")
        return

    print(f"{len(complete)} renderable now, {len(gaps)} missing buffers")
    for tag, positions in sorted(complete, key=lambda row: -row[1])[:40]:
        print(f"  READY  0x{tag:08X} {where(tag):<20} {positions:10,} B")
    for tag, missing in gaps[:20]:
        print(f"  needs  0x{tag:08X} {where(tag):<20} {len(missing)} buffers")


if __name__ == "__main__":
    main()
