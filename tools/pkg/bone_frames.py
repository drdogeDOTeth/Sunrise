"""Decide whether bone indices are one global skeleton or a per-piece remap.

`_18` was built on "a draw can only pose the indices its own vertices already name", which
forced a custom body to be split across the chest, legs and gauntlet slots - and that split is
what produced the pillar. The belief was never tested; it was inferred from failures that
changed several things at once.

There is an offline test. Four bone indices appear on more than one armour piece: 1, 3 and 4 on
chest **and** legs, 19 on chest **and** gauntlets. Dequantised model space is character space
(scale/translation put each piece where it sits on the body), so the centroid of the vertices
weighted to bone *b* is comparable across pieces.

- If index spaces are **global**, chest-19 and gauntlet-19 land on the same joint.
- If each piece carries its own **remap**, they land somewhere unrelated.

Usage:
    python bone_frames.py
"""
from __future__ import annotations

import collections
import struct
from pathlib import Path

import bone_probe
from bone_probe import DUMP, FILLER, bones_of_vertex, buffer_of, dumped, meshes_of, stride_of

PACKED_MAX = 32767.0

PIECES = {
    "chest": 0x80EFA1CA,
    "legs": 0x80EFA93B,
    "gauntlets": 0x80EF981E,
    "hood": 0x80EFA859,
    "class": 0x80EFA528,
}


def model_frame(data: bytes) -> tuple[float, tuple[float, float, float]]:
    scale = struct.unpack_from("<4f", data, 0x50)
    translation = struct.unpack_from("<4f", data, 0x60)
    return scale[0], translation[:3]


def centroids(tag: int) -> dict[int, tuple[int, float, float, float]]:
    """@return bone -> (influences, x, y, z) in character space, for one armour piece."""
    data = dumped(tag)
    if data is None:
        return {}
    scale, translation = model_frame(data)
    totals: dict[int, list[float]] = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for mesh in meshes_of(data):
        stride = stride_of(mesh["positions"])
        body = dumped(buffer_of(mesh["positions"]))
        if not body or stride not in (12, 16):
            continue
        for at in range(0, len(body) - stride + 1, stride):
            x, y, z = struct.unpack_from("<3h", body, at)
            point = (x / PACKED_MAX * scale + translation[0],
                     y / PACKED_MAX * scale + translation[1],
                     z / PACKED_MAX * scale + translation[2])
            for bone in bones_of_vertex(body, at, stride):
                slot = totals[bone]
                slot[0] += 1
                slot[1] += point[0]
                slot[2] += point[1]
                slot[3] += point[2]
    return {bone: (int(v[0]), v[1] / v[0], v[2] / v[0], v[3] / v[0])
            for bone, v in totals.items() if v[0]}


def main() -> None:
    per_piece = {name: centroids(tag) for name, tag in PIECES.items()}
    for name, table in per_piece.items():
        print(f"{name:>10}: {len(table)} bones  {sorted(table)}")

    shared = collections.defaultdict(list)
    for name, table in per_piece.items():
        for bone in table:
            shared[bone].append(name)
    overlapping = sorted(b for b, names in shared.items() if len(names) > 1)

    print(f"\nbones used by more than one piece: {overlapping}\n")
    print(f"{'bone':>5} {'piece':>10} {'n':>6} {'x':>8} {'y':>8} {'z':>8}   verdict")
    for bone in overlapping:
        points = []
        for name in shared[bone]:
            n, x, y, z = per_piece[name][bone]
            points.append((name, n, x, y, z))
        spread = max(
            max(abs(a[i] - b[i]) for i in (2, 3, 4))
            for a in points for b in points
        )
        verdict = "SAME JOINT" if spread < 0.15 else f"DISAGREE by {spread:.2f} m"
        for index, (name, n, x, y, z) in enumerate(points):
            tail = f"   {verdict}" if index == 0 else ""
            print(f"{bone:>5} {name:>10} {n:>6} {x:>8.3f} {y:>8.3f} {z:>8.3f}{tail}")

    print("\nMax bone index per piece (what a draw would need to address):")
    for name, table in per_piece.items():
        if table:
            print(f"  {name:>10}: max {max(table):>3}, {len(table)} distinct, "
                  f"gaps {[b for b in range(max(table) + 1) if b not in table]}")


if __name__ == "__main__":
    main()
