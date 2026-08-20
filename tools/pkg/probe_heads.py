"""
Blanks the three globals player-head candidates and requests their buffers.

The inspect screen with helmet_mode=1 draws the race/gender head, not a helmet item. Shotgunning
investment armour never moved that head. These three are the only globals models that sit at head
height with a ~0.31 m box and a real part table.

Blanking is the probe: if the Warlock's face disappears, that package is the player head. The
request file dumps the buffers the follow-up wrap needs, in the same launch.

Usage:
    python probe_heads.py --dry-run
    python probe_heads.py
    python inject_mesh.py --undo
"""
from __future__ import annotations

import shutil
import struct
import sys
from pathlib import Path

from inject_mesh import blank_model, entry_index_of, package_of, write_all
from parse_models import DUMP as MODELS_DIR, Model, REQUEST_DIR, TAG_MAX, TAG_MIN

GEOMETRY = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
HEADS = {0x80C71828, 0x80FA3678, 0x80B9F9B7}
# Person-sized globals bodies in the same packages, plus the 37k-vert cluster.
BODIES = {0x80B9F962, 0x80C717CA, 0x80B9E810, 0x80F56B13, 0x80FA2DF2}


def load_model(tag: int) -> Model:
    path = GEOMETRY / f"tag_{tag:08X}.bin"
    if not path.is_file():
        raise SystemExit(f"0x{tag:08X} is not in {GEOMETRY}")
    data = path.read_bytes()
    (declared,) = struct.unpack_from("<q", data, 0)
    if declared != len(data):
        raise SystemExit(f"0x{tag:08X} dump is not an entity model")
    model = Model(tag, data)
    if not model.meshes:
        raise SystemExit(f"0x{tag:08X} has no meshes")
    return model


def buffer_tags(model: Model) -> list[int]:
    seen: list[int] = []
    mesh = model.meshes[0]
    for tag in (mesh.positions, mesh.position_buffer, mesh.texcoords, mesh.texcoord_buffer,
                mesh.indices, mesh.index_buffer):
        if TAG_MIN <= tag <= TAG_MAX and tag not in seen:
            seen.append(tag)
    return seen


def write_request(models: list[Model]) -> int:
    lines = [
        "# Globals player-head and body buffers. Headers first so stride is known.",
        "# Model entries are already dumped; do not re-dump them through the blank patch.",
        "",
    ]
    requested: set[int] = set()
    for model in models:
        mesh = model.meshes[0]
        lines.append(
            f"# 0x{model.tag:08X}  span {model.span:.3f}  height {model.height:.3f}  "
            f"{mesh.position_bytes:,}B positions"
        )
        for tag in buffer_tags(model):
            if tag in requested:
                continue
            requested.add(tag)
            lines.append(f"tag 0x{tag:08X}")
        lines.append("")
    request = REQUEST_DIR / "request.txt"
    request.write_text("\r\n".join(lines), encoding="utf-8")
    return len(requested)


def archive_models(tags: set[int]) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for tag in tags:
        src = GEOMETRY / f"tag_{tag:08X}.bin"
        dst = MODELS_DIR / src.name
        if src.is_file():
            shutil.copy2(src, dst)


def main() -> None:
    heads = [load_model(tag) for tag in sorted(HEADS)]
    bodies = [load_model(tag) for tag in sorted(BODIES)]
    archive_models(HEADS | BODIES)

    by_package: dict[Path, dict[int, bytes]] = {}
    for model in heads:
        by_package.setdefault(package_of(model.tag), {})[entry_index_of(model.tag)] = (
            blank_model(model)
        )
        print(
            f"blank 0x{model.tag:08X}  {package_of(model.tag).name}  "
            f"span {model.span:.3f}  height {model.height:.3f}  "
            f"{len(model.meshes)} meshes"
        )

    requested = write_request(heads + bodies)
    print(f"request.txt: {requested} buffer tags for {len(heads)} heads + {len(bodies)} bodies")

    if "--dry-run" in sys.argv:
        return
    write_all(by_package, "")


if __name__ == "__main__":
    main()
