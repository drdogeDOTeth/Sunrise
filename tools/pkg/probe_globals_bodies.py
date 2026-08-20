"""
Blanks remaining person-sized globals bodies, not the character-select preview.

Investment has been ruled out: silencing all 343 gear models left the beige undersuit in place.
Empty armour slots make the client draw a race/gender/class default. Character-select uses
0x80B9F962 (72k, wrapping it spun the select cards). In-game inspect uses something else in
globals. These candidates skip that preview and the three bodies already xyz-wrapped with
armour off (no inspect change).

Usage:
    python probe_globals_bodies.py --dry-run
    python probe_globals_bodies.py
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
GLOBALS = {0x01CF, 0x01FE, 0x0211, 0x0238, 0x03AB, 0x03D1, 0x03ED, 0x03F5, 0x06DC}
# Select preview, plus in-game wraps that already failed with armour off.
SKIP = {0x80B9F962, 0x80C717CA, 0x80B9E810, 0x80F56B13}


def load_globals_models() -> list[Model]:
    out: list[Model] = []
    for path in sorted(DUMP.glob("tag_*.bin")):
        data = path.read_bytes()
        if len(data) < 0xA0:
            continue
        (declared,) = struct.unpack_from("<q", data, 0)
        if declared != len(data):
            continue
        model = Model(int(path.stem[4:], 16), data)
        if model.meshes:
            out.append(model)
    return out


def person_like(model: Model) -> bool:
    verts = model.meshes[0].position_bytes // 8
    return 1.40 <= model.span <= 2.30 and -0.2 <= model.height <= 1.25 and verts >= 1500


def buffer_tags(model: Model) -> list[int]:
    seen: list[int] = []
    mesh = model.meshes[0]
    for tag in (mesh.positions, mesh.position_buffer, mesh.indices, mesh.index_buffer):
        if TAG_MIN <= tag <= TAG_MAX and tag not in seen:
            seen.append(tag)
    return seen


def write_request(chosen: list[Model]) -> int:
    lines = [
        "# Globals person-sized bodies (not the 72k select preview). Buffers only.",
        "",
    ]
    requested: set[int] = set()
    for model in chosen:
        lines.append(
            f"# 0x{model.tag:08X}  span {model.span:.3f}  height {model.height:.3f}"
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
    chosen = []
    for model in load_globals_models():
        package_id = (model.tag - TAG_BASE) >> TAG_ENTRY_BITS
        if package_id not in GLOBALS or model.tag in SKIP or not person_like(model):
            continue
        chosen.append(model)
    if not chosen:
        raise SystemExit("no remaining globals person-sized bodies")
    chosen.sort(key=lambda m: -m.vertices)
    by_package: dict[Path, dict[int, bytes]] = {}
    for model in chosen:
        by_package.setdefault(package_of(model.tag), {})[entry_index_of(model.tag)] = (
            blank_model(model)
        )
        print(
            f"blank 0x{model.tag:08X}  span {model.span:.3f}  height {model.height:.3f}  "
            f"{package_of(model.tag).name}"
        )
    requested = write_request(chosen)
    print(f"{len(chosen)} bodies, {requested} buffer tags, {len(by_package)} packages")
    if "--dry-run" in sys.argv:
        return
    write_all(by_package, "")


if __name__ == "__main__":
    main()
