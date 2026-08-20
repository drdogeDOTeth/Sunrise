"""
Blanks every entity model in w64_ui_037e, the inspect-screen character package.

Gear and globals person-bodies did not draw this beige undersuit. ui_037e is full of
person-sized models (span ~1.85 m, height ~0.93 m), including two 46,448-byte 6-mesh
headers. Orbit models are kilometre-scale gizmos and are left alone.

Usage:
    python probe_ui.py --dry-run
    python probe_ui.py
    python inject_mesh.py --undo
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from inject_mesh import blank_model, entry_index_of, package_of, write_all
from parse_models import Model, REQUEST_DIR, TAG_MAX, TAG_MIN
from tigerpkg import TAG_BASE, TAG_ENTRY_BITS

DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
UI_PACKAGE = 0x037E


def load() -> list[Model]:
    out: list[Model] = []
    for path in sorted(DUMP.glob("tag_*.bin")):
        data = path.read_bytes()
        if len(data) < 0xA0:
            continue
        (declared,) = struct.unpack_from("<q", data, 0)
        if declared != len(data):
            continue
        tag = int(path.stem[4:], 16)
        if ((tag - TAG_BASE) >> TAG_ENTRY_BITS) != UI_PACKAGE:
            continue
        model = Model(tag, data)
        if model.meshes:
            out.append(model)
    return out


def buffer_tags(model: Model) -> list[int]:
    seen: list[int] = []
    mesh = model.meshes[0]
    for tag in (mesh.positions, mesh.position_buffer, mesh.indices, mesh.index_buffer):
        if TAG_MIN <= tag <= TAG_MAX and tag not in seen:
            seen.append(tag)
    return seen


def write_request(chosen: list[Model]) -> int:
    lines = [
        "# UI inspect-screen character buffers. Do not re-dump blanked model entries.",
        "",
    ]
    requested: set[int] = set()
    for model in chosen:
        lines.append(
            f"# 0x{model.tag:08X}  span {model.span:.3f}  height {model.height:.3f}  "
            f"{len(model.meshes)} meshes"
        )
        for tag in buffer_tags(model):
            if tag in requested:
                continue
            requested.add(tag)
            lines.append(f"tag 0x{tag:08X}")
        lines.append("")
    (REQUEST_DIR / "request.txt").write_text("\r\n".join(lines), encoding="utf-8")
    return len(requested)


def main() -> None:
    chosen = load()
    if not chosen:
        raise SystemExit("no ui_037e entity models dumped")
    chosen.sort(key=lambda m: -m.vertices)
    by_package: dict[Path, dict[int, bytes]] = {}
    for model in chosen:
        by_package.setdefault(package_of(model.tag), {})[entry_index_of(model.tag)] = (
            blank_model(model)
        )
        print(
            f"blank 0x{model.tag:08X}  {len(model.meshes)} meshes  "
            f"span {model.span:.3f}  height {model.height:.3f}  {len(model.data):,} B"
        )
    requested = write_request(chosen)
    print(f"{len(chosen)} UI models, {requested} buffer tags")
    if "--dry-run" in sys.argv:
        return
    write_all(by_package, "")


if __name__ == "__main__":
    main()
