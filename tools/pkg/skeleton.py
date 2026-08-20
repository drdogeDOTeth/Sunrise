"""Recover the Guardian bind skeleton from the armour that is skinned to it.

Charm's FK/bind classes are nested inside an entity resource and Shadowkeep has no hardcoded
player base hash, so the rig is not sitting in a package waiting to be read. It does not need
to be: every armour vertex carries the joints it is bound to and the position it holds in the
bind pose, so the skeleton is recoverable from the geometry that hangs off it.

**Joints are estimated at the blend, not at the centroid.** The vertices dominated by one bone
sit along the middle of the limb it drives, so their centroid is a mid-shaft point, not a pivot.
The vertices carrying real weight on *both* a bone and its parent are the ones straddling the
joint, and their centroid lands on it. Bones with no such blend region fall back to their
dominant-weight centroid, and that is recorded per joint in the output.

Writes `objs/skeleton/rig.json` (joints, names, parents, positions) and `objs/skeleton/rig.obj`
(one line segment per bone, viewable or importable), both in character space - the same metres
that `inject_scatterhorn.py` places the custom mesh in.

Usage:
    python skeleton.py
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

from bone_probe import buffer_of, dumped, meshes_of, stride_of

OUT = Path(__file__).with_name("objs") / "skeleton"
PACKED_MAX = 32767.0
FILLER = {254, 255}

# One piece per region. Together they name every joint of the playable rig below the fingers.
PIECES = {
    "chest": 0x80EFA1CA,
    "legs": 0x80EFA93B,
    "gauntlets": 0x80EF981E,
}

# Anatomy read off the recovered positions: side comes from y, height from z, and the chain
# order from which bone's region borders which. Names are ours - Bungie's are not in the data.
HIERARCHY = {
    1: (None, "pelvis"),
    5: (1, "spine_lower"),
    8: (5, "spine_upper"),
    11: (8, "chest"),
    12: (11, "collar_L"),
    14: (11, "collar_R"),
    13: (11, "neck"),
    16: (11, "neck_02"),
    18: (11, "head"),
    27: (11, "shoulder_L"),
    15: (27, "upperarm_L"),
    19: (15, "forearm_L"),
    21: (19, "hand_L"),
    28: (11, "shoulder_R"),
    17: (28, "upperarm_R"),
    20: (17, "forearm_R"),
    22: (20, "hand_R"),
    3: (1, "thigh_L"),
    6: (3, "shin_L"),
    9: (6, "foot_L"),
    25: (9, "toe_L"),
    4: (1, "thigh_R"),
    7: (4, "shin_R"),
    10: (7, "foot_R"),
    26: (10, "toe_R"),
}
# A vertex counts as straddling a joint when both bones carry at least this share of it.
BLEND_FLOOR = 40


def influences(tag: int) -> tuple[np.ndarray, list[list[tuple[int, int]]]]:
    """@return Character-space points, and each point's `(bone, weight)` list."""
    data = dumped(tag)
    if data is None:
        return np.zeros((0, 3)), []
    scale = struct.unpack_from("<4f", data, 0x50)[0]
    translation = struct.unpack_from("<4f", data, 0x60)[:3]
    points: list[tuple[float, float, float]] = []
    skins: list[list[tuple[int, int]]] = []
    for mesh in meshes_of(data):
        stride = stride_of(mesh["positions"])
        body = dumped(buffer_of(mesh["positions"]))
        if not body or stride not in (12, 16):
            continue
        for at in range(0, len(body) - stride + 1, stride):
            x, y, z = struct.unpack_from("<3h", body, at)
            if stride == 16:
                pairs = [(body[at + 12 + s], body[at + 8 + s]) for s in range(4)]
            else:
                pairs = [(body[at + 8], body[at + 10]), (body[at + 9], body[at + 11])]
            pairs = [(bone, weight) for bone, weight in pairs
                     if weight and bone not in FILLER]
            if not pairs:
                continue
            points.append((x / PACKED_MAX * scale + translation[0],
                           y / PACKED_MAX * scale + translation[1],
                           z / PACKED_MAX * scale + translation[2]))
            skins.append(pairs)
    return np.asarray(points, dtype=np.float64), skins


def gather() -> tuple[np.ndarray, list[list[tuple[int, int]]]]:
    clouds, skins = [], []
    for name, tag in PIECES.items():
        piece_points, piece_skins = influences(tag)
        print(f"  {name:>10} 0x{tag:08X}: {len(piece_points):,} skinned verts")
        if len(piece_points):
            clouds.append(piece_points)
            skins.extend(piece_skins)
    return np.concatenate(clouds), skins


def solve(points: np.ndarray, skins: list[list[tuple[int, int]]]) -> dict[int, dict]:
    dominant: dict[int, list[int]] = {}
    blend: dict[tuple[int, int], list[int]] = {}
    for index, pairs in enumerate(skins):
        best = max(pairs, key=lambda pair: pair[1])[0]
        dominant.setdefault(best, []).append(index)
        heavy = {bone for bone, weight in pairs if weight >= BLEND_FLOOR}
        for bone in heavy:
            parent = HIERARCHY.get(bone, (None, ""))[0]
            if parent in heavy:
                blend.setdefault((bone, parent), []).append(index)

    joints: dict[int, dict] = {}
    for bone, (parent, name) in HIERARCHY.items():
        rows = blend.get((bone, parent), [])
        source = "blend with parent"
        if len(rows) < 8:
            rows = dominant.get(bone, [])
            source = "dominant centroid"
        if not rows:
            continue
        centre = points[np.asarray(rows)].mean(0)
        joints[bone] = {
            "bone": bone,
            "name": name,
            "parent": parent,
            "position": [round(float(v), 4) for v in centre],
            "estimator": source,
            "samples": len(rows),
            "dominant_verts": len(dominant.get(bone, [])),
        }
    return joints


def write_obj(joints: dict[int, dict], path: Path) -> None:
    order = sorted(joints)
    row_of = {bone: index + 1 for index, bone in enumerate(order)}
    lines = ["# Guardian bind skeleton, recovered from Scatterhorn armour weights",
             "o guardian_rig"]
    lines += [f"v {joints[bone]['position'][0]:.6f} {joints[bone]['position'][1]:.6f} "
              f"{joints[bone]['position'][2]:.6f}" for bone in order]
    for bone in order:
        parent = joints[bone]["parent"]
        if parent in row_of:
            lines.append(f"l {row_of[parent]} {row_of[bone]}")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_svg(joints: dict[int, dict], path: Path) -> None:
    """Front and side projections, so the rig can be checked without Blender.

    The project's own rule: render it and look. A rig that reads as a person is right; a rig
    with a knee at hip height is the `_18` failure in a form you can see in one glance.
    """
    pad, height, gap = 46, 520, 60
    floor = min(joint["position"][2] for joint in joints.values())
    top = max(joint["position"][2] for joint in joints.values())
    metres = max(top - floor, 1e-6) * 1.12
    per_metre = height / metres

    def project(joint: dict, view: str, origin: float) -> tuple[float, float]:
        x, y, z = joint["position"]
        across = -y if view == "front" else x
        return origin + across * per_metre, pad + (top - z) * per_metre + 20

    width = pad * 2 + gap + 2 * 0.9 * per_metre
    origins = {"front": pad + 0.45 * per_metre,
               "side": pad + 0.9 * per_metre + gap + 0.45 * per_metre}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} '
           f'{height + pad * 2 + 40:.0f}" font-family="ui-sans-serif,system-ui,sans-serif">',
           f'<rect width="100%" height="100%" fill="#12141a"/>']
    for view, origin in origins.items():
        label = "front (looking at the chest)" if view == "front" else "side (facing right)"
        out.append(f'<text x="{origin:.1f}" y="{pad - 6:.0f}" fill="#8a93a8" font-size="13" '
                   f'text-anchor="middle">{label}</text>')
        base = pad + (top - floor) * per_metre + 20
        out.append(f'<line x1="{origin - 0.42 * per_metre:.1f}" y1="{base:.1f}" '
                   f'x2="{origin + 0.42 * per_metre:.1f}" y2="{base:.1f}" '
                   f'stroke="#2b3040" stroke-width="1.5"/>')
        for bone in sorted(joints):
            parent = joints[bone]["parent"]
            if parent not in joints:
                continue
            ax, ay = project(joints[parent], view, origin)
            bx, by = project(joints[bone], view, origin)
            out.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
                       f'stroke="#4d7dd6" stroke-width="2.5" stroke-linecap="round"/>')
        for bone in sorted(joints):
            cx, cy = project(joints[bone], view, origin)
            name = joints[bone]["name"]
            side = name.endswith("_R")
            colour = "#e8b04b" if side else "#6fd3a8" if name.endswith("_L") else "#dfe4ee"
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.4" fill="{colour}"/>')
            if view == "front" and not side:
                out.append(f'<text x="{cx + 7:.1f}" y="{cy + 3.5:.1f}" fill="#8a93a8" '
                           f'font-size="10">{bone} {name.removesuffix("_L")}</text>')
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")


def main() -> None:
    print("reading skinned armour:")
    points, skins = gather()
    joints = solve(points, skins)
    OUT.mkdir(parents=True, exist_ok=True)

    print(f"\n{len(joints)} joints recovered\n")
    print(f"{'bone':>5} {'name':>13} {'parent':>7} {'x':>8} {'y':>8} {'z':>8} "
          f"{'verts':>7}  estimator")
    for bone in sorted(joints):
        joint = joints[bone]
        x, y, z = joint["position"]
        parent = "-" if joint["parent"] is None else str(joint["parent"])
        print(f"{bone:>5} {joint['name']:>13} {parent:>7} {x:>8.3f} {y:>8.3f} {z:>8.3f} "
              f"{joint['dominant_verts']:>7}  {joint['estimator']}")

    missing = [bone for bone in HIERARCHY if bone not in joints]
    if missing:
        print(f"\nno geometry bound to: {missing} (fingers and unused joints live above 28)")

    rig = OUT / "rig.json"
    rig.write_text(json.dumps({
        "note": ("Character space, metres, Destiny Z-up. Joints estimated from armour skin "
                 "weights; positions are pivots where a parent blend existed, mid-shaft "
                 "otherwise. Bone numbers are the global index space every armour piece "
                 "shares - see bone_frames.py."),
        "joints": [joints[bone] for bone in sorted(joints)],
    }, indent=2), encoding="utf-8")
    write_obj(joints, OUT / "rig.obj")
    write_svg(joints, OUT / "rig.svg")
    print(f"\nwrote {rig}\nwrote {OUT / 'rig.obj'}\nwrote {OUT / 'rig.svg'}")


if __name__ == "__main__":
    main()
