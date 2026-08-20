"""Census every bone index the live Scatterhorn set and dumped globals bodies actually use.

Skinning here is per-mesh. A joint only poses if that draw's vertex palette names it.
This writes objs/skeleton/palette.json — the map inject_scatterhorn.py splits against.

Usage:
    python census_bones.py
"""
from __future__ import annotations

import collections
import json
import struct
from pathlib import Path

import numpy as np

from extract_mesh import dumped, vertex_stride
from parse_models import Model

DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
OUT = Path(__file__).with_name("objs") / "skeleton" / "palette.json"

PLAYABLE = {
    "chest_a": 0x80EFA1CA,
    "chest_b": 0x80EFA1A9,
    "legs_a": 0x80EFA93B,
    "legs_b": 0x80EFA92E,
    "gauntlets_a": 0x80EF981E,
    "gauntlets_b": 0x80EF9809,
    "hood_a": 0x80EFA859,
    "hood_b": 0x80EFA850,
    "class_a": 0x80EFA528,
    "class_b": 0x80EFA51F,
    "robe2_a": 0x80EFA1D4,
    "robe2_b": 0x80EFA1B3,
}

# Character-select / Tower-frame bodies. Not the in-world player. Census only.
GLOBALS = {
    "globals_72k": 0x80B9F962,
    "globals_b9e810": 0x80B9E810,
    "globals_c717ca": 0x80C717CA,
    "globals_f56b13": 0x80F56B13,
}

FILLER = {254, 255}


def load_model(tag: int) -> Model | None:
    path = DUMP / f"tag_{tag:08X}.bin"
    if not path.is_file():
        return None
    return Model(tag, path.read_bytes())


def plausible(bones: list[int]) -> bool:
    if not bones:
        return False
    real = [b for b in bones if b not in FILLER]
    if not real:
        return False
    return max(real) <= 96 and min(real) >= 0


def collect_inline(body: bytes, stride: int) -> tuple[collections.Counter, str]:
    counts: collections.Counter[int] = collections.Counter()
    if stride == 16:
        for at in range(0, len(body) - stride + 1, stride):
            for slot in range(4):
                bone = body[at + 12 + slot]
                weight = body[at + 8 + slot]
                if weight and bone not in FILLER:
                    counts[bone] += 1
        return counts, "stride16 w8-11 b12-15"
    if stride == 12:
        bones_first: collections.Counter[int] = collections.Counter()
        weights_first: collections.Counter[int] = collections.Counter()
        for at in range(0, len(body) - stride + 1, stride):
            p8, p9, p10, p11 = body[at + 8], body[at + 9], body[at + 10], body[at + 11]
            if p10 and p8 not in FILLER:
                bones_first[p8] += 1
            if p11 and p9 not in FILLER:
                bones_first[p9] += 1
            if p8 and p10 not in FILLER:
                weights_first[p10] += 1
            if p9 and p11 not in FILLER:
                weights_first[p11] += 1
        a_ok, b_ok = plausible(list(bones_first)), plausible(list(weights_first))
        if a_ok and (not b_ok or max(bones_first) <= max(weights_first or {0})):
            return bones_first, "stride12 b8-9 w10-11"
        if b_ok:
            return weights_first, "stride12 w8-9 b10-11"
        return collections.Counter(), "stride12 unreadable"
    return collections.Counter(), f"stride{stride} no inline skin"


def collect_weight_buffer(body: bytes) -> tuple[collections.Counter, str]:
    if not body:
        return collections.Counter(), "no weight buffer"
    counts8: collections.Counter[int] = collections.Counter()
    for at in range(0, len(body) - 7, 8):
        for slot in range(4):
            bone = body[at + 4 + slot]
            weight = body[at + slot]
            if weight and bone not in FILLER:
                counts8[bone] += 1
    counts4: collections.Counter[int] = collections.Counter()
    for at in range(0, len(body) - 3, 4):
        b0, b1, w0, w1 = body[at], body[at + 1], body[at + 2], body[at + 3]
        if w0 and b0 not in FILLER:
            counts4[b0] += 1
        if w1 and b1 not in FILLER:
            counts4[b1] += 1
    if plausible(list(counts8)):
        return counts8, "weight buf 8B (w0-3 b4-7)"
    if plausible(list(counts4)):
        return counts4, "weight buf 4B (b0-1 w2-3)"
    return collections.Counter(), f"weight buf {len(body)}B unreadable"


def census_tag(name: str, tag: int) -> dict:
    header = DUMP / f"tag_{tag:08X}.bin"
    row = {"name": name, "tag": f"0x{tag:08X}", "dumped": header.is_file(), "meshes": []}
    model = load_model(tag)
    if model is None:
        return row
    for index, mesh in enumerate(model.meshes):
        stride = vertex_stride(mesh.positions)
        body = dumped(mesh.position_buffer)
        weights = dumped(mesh.weight_buffer)
        entry: dict = {
            "mesh": index,
            "stride": stride,
            "verts": (len(body) // stride) if body and stride else 0,
            "position_buffer": f"0x{mesh.position_buffer:08X}",
            "weight_buffer": f"0x{mesh.weight_buffer:08X}",
            "inline_bones": [],
            "weight_bones": [],
            "layout": "",
        }
        if body and stride:
            inline, layout = collect_inline(body, stride)
            entry["layout"] = layout
            entry["inline_bones"] = sorted(inline)
            entry["inline_max"] = max(inline) if inline else None
            entry["inline_counts"] = {str(k): int(v) for k, v in inline.most_common()}
        if weights:
            wcounts, wlayout = collect_weight_buffer(weights)
            entry["weight_layout"] = wlayout
            entry["weight_bones"] = sorted(wcounts)
            entry["weight_max"] = max(wcounts) if wcounts else None
        row["meshes"].append(entry)
        print(
            f"  {name} mesh {index}: stride {stride} verts {entry['verts']:,}  "
            f"inline {entry['inline_bones'] or '-'}  "
            f"wbuf {entry.get('weight_bones') or '-'}"
        )
    return row


def union_of(rows: list[dict], key: str) -> list[int]:
    bones: set[int] = set()
    for row in rows:
        if not row["name"].startswith(key):
            continue
        for mesh in row["meshes"]:
            bones.update(mesh.get("inline_bones") or [])
            bones.update(mesh.get("weight_bones") or [])
    return sorted(bones)


def main() -> None:
    rows = []
    print("=== playable Scatterhorn ===")
    for name, tag in PLAYABLE.items():
        rows.append(census_tag(name, tag))
    print("=== globals (not the in-world player) ===")
    for name, tag in GLOBALS.items():
        rows.append(census_tag(name, tag))

    playable_rows = [r for r in rows if r["name"] in PLAYABLE]
    chest = union_of(playable_rows, "chest")
    legs = union_of(playable_rows, "legs")
    gaunt = union_of(playable_rows, "gauntlets")
    hood = union_of(playable_rows, "hood")
    klass = union_of(playable_rows, "class")
    robe2 = union_of(playable_rows, "robe2")
    playable_union = sorted(set(chest) | set(legs) | set(gaunt) | set(hood) | set(klass) | set(robe2))
    globals_union = sorted(
        {
            b
            for r in rows
            if r["name"].startswith("globals")
            for m in r["meshes"]
            for b in (m.get("inline_bones") or []) + (m.get("weight_bones") or [])
        }
    )
    missing_from_playable = [b for b in globals_union if b not in playable_union]

    palette = {
        "note": (
            "Per-mesh palettes from dumped buffers. A bone only poses on the draw that names it. "
            "Do not write gauntlet indices into the chest mesh."
        ),
        "playable": {
            "chest": chest,
            "legs": legs,
            "gauntlets": gaunt,
            "hood": hood,
            "class_item": klass,
            "second_robe": robe2,
            "union": playable_union,
        },
        "globals_union": globals_union,
        "in_globals_not_playable_armor": missing_from_playable,
        "models": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(palette, indent=2), encoding="utf-8")
    print("\nplayable union:", playable_union)
    print("chest:", chest)
    print("legs:", legs)
    print("gauntlets:", gaunt)
    print("hood:", hood)
    print("class:", klass)
    print("robe2:", robe2)
    print("globals extra (not on playable armor):", missing_from_playable)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
