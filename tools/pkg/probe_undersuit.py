"""
Blanks dumped torso-shaped investment models — the default undersuit chest.

With armour slots null the inspect screen still draws a beige tunic. That is a chest mesh in the
torso cluster (span ~0.6 m at ~1.35 m), not the person-sized bodies we already wrapped. Model
headers are dumped; vertex buffers are not. Blanking only needs the header: zero every part's
index count and the tunic either vanishes or it does not.

Usage:
    python probe_undersuit.py --dry-run
    python probe_undersuit.py
    python inject_mesh.py --undo
"""
from __future__ import annotations

import sys
from pathlib import Path

from inject_mesh import blank_model, entry_index_of, package_of, write_all
from parse_models import REQUEST_DIR, TAG_MAX, TAG_MIN, models


def torso_like(model) -> bool:
    return 0.40 <= model.span <= 0.95 and 1.10 <= model.height <= 1.55 and model.vertices >= 400


def buffer_tags(model) -> list[int]:
    seen: list[int] = []
    if not model.meshes:
        return seen
    mesh = model.meshes[0]
    for tag in (mesh.positions, mesh.position_buffer, mesh.indices, mesh.index_buffer):
        if TAG_MIN <= tag <= TAG_MAX and tag not in seen:
            seen.append(tag)
    return seen


def write_request(chosen: list) -> int:
    lines = [
        "# Torso (undersuit chest) buffers. Do not re-dump model entries through the blank patch.",
        "",
    ]
    requested: set[int] = set()
    for model in chosen:
        lines.append(
            f"# 0x{model.tag:08X}  span {model.span:.3f}  height {model.height:.3f}  "
            f"{model.vertices:,} verts"
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


def main() -> None:
    chosen = [model for model in models if torso_like(model)]
    if not chosen:
        raise SystemExit("no torso-shaped models in dump_models")
    chosen.sort(key=lambda m: -m.vertices)
    by_package: dict[Path, dict[int, bytes]] = {}
    for model in chosen:
        by_package.setdefault(package_of(model.tag), {})[entry_index_of(model.tag)] = (
            blank_model(model)
        )
        print(
            f"blank 0x{model.tag:08X}  span {model.span:.3f}  height {model.height:.3f}  "
            f"{model.vertices:,} verts  {package_of(model.tag).name}"
        )
    requested = write_request(chosen)
    print(f"{len(chosen)} torsos blanked, {requested} buffer tags in request.txt")
    if "--dry-run" in sys.argv:
        return
    write_all(by_package, "")


if __name__ == "__main__":
    main()
