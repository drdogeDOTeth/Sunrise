"""Measure our injected hand against the Scatterhorn glove the animation was authored for.

Skinning is `M_anim(bone) . B_bone^-1 . v`, and `B` belongs to the game. So any difference
between our **bind** hand and the donor's bind hand is carried into *every* posed frame - it
cannot be animated away. That makes the bind pose the only thing worth measuring offline when
the hand looks wrong in game, and it is measurable with no launch at all.

Four checks, cheapest first:

1. **Per-bone drift.** Our dominant-vertex centroid against the donor's, joint by joint. Also
   names the bones we weight that the donor mesh never does.
2. **The finger map.** `retarget_mesh.FINGER_MAP` sends distal phalanges to 52/53/54 and
   57/58/59. `recover_finger_joints.py` reported those as missing because it only looked at
   *dominant* vertices - they are never dominant on the glove. A **weighted** centroid needs no
   dominance and locates them fine, which is what verifies the chain: each distal joint must sit
   further from the wrist than its proximal joint and in the same direction from it.
3. **Rigid fit.** Umeyama over the shared joints: how much rotation, twist about the forearm,
   and how much residual is real shape difference rather than pose.
4. **Overlay PNG.** Both clouds in three orthographic views, no fitting, `--png <path>`.

A PCA axis was used for the palm normal in the first version of this and reported a phantom
180 deg flip on the right hand - SVD signs are arbitrary. The frame here is built from named
bone chains instead, so both clouds are measured against the same joints.

Usage:
    python audit_hand_bind.py
    python audit_hand_bind.py --png objs/hand_overlay.png
    python audit_hand_bind.py --hands character_hands_v23.obj
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from skeleton import influences

HERE = Path(__file__).resolve().parent
GAUNT = 0x80EF981E
RIG = json.loads((HERE / "objs" / "skeleton" / "rig.json").read_text(encoding="utf-8"))
JOINT = {j["bone"]: np.asarray(j["position"], dtype=np.float64) for j in RIG["joints"]}

# Sides as retarget_mesh.py maps them. `thumb` is the roll-sensitive landmark: it is what
# tells a rolled hand from a correctly oriented one.
SIDES = {
    "LEFT": dict(wrist=21, elbow=19, thumb=(45, 56, 66),
                 chains={"index": (40, 52), "middle": (42, 53), "little": (43, 54)},
                 fingers=(40, 42, 43, 44, 45, 52, 53, 54, 55, 56, 66)),
    "RIGHT": dict(wrist=22, elbow=20, thumb=(51, 61, 71),
                  chains={"index": (46, 57), "middle": (48, 58), "little": (49, 59)},
                  fingers=(46, 48, 49, 50, 51, 57, 58, 59, 60, 61, 71)),
}


def read_obj(path: Path) -> np.ndarray:
    return np.asarray([[float(v) for v in line.split()[1:4]]
                       for line in path.read_text(encoding="ascii").splitlines()
                       if line.startswith("v ")], dtype=np.float64)


def option(name: str, fallback: str) -> str:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else fallback


def dominant(points, skins) -> dict[int, np.ndarray]:
    pts = np.asarray(points, dtype=np.float64)
    out: dict[int, list[int]] = {}
    for index, pairs in enumerate(skins):
        if pairs:
            out.setdefault(int(max(pairs, key=lambda pair: pair[1])[0]), []).append(index)
    return {bone: pts[np.asarray(rows)] for bone, rows in out.items()}


def weighted(points, skins) -> dict[int, tuple[np.ndarray, float, int]]:
    """@return bone -> (weighted centroid, total weight, vertices touching it)."""
    pts = np.asarray(points, dtype=np.float64)
    acc: dict[int, list] = {}
    for index, pairs in enumerate(skins):
        for bone, weight in pairs:
            if weight <= 0:
                continue
            row = acc.setdefault(int(bone), [np.zeros(3), 0.0, 0])
            row[0] = row[0] + weight * pts[index]
            row[1] += weight
            row[2] += 1
    return {bone: (total / mass, mass, count) for bone, (total, mass, count) in acc.items()}


def umeyama(src: np.ndarray, dst: np.ndarray, with_scale: bool):
    source_centre, dest_centre = src.mean(0), dst.mean(0)
    a, b = src - source_centre, dst - dest_centre
    u, singular, vt = np.linalg.svd(b.T @ a / len(src))
    sign = np.diag([1.0, 1.0, float(np.sign(np.linalg.det(u @ vt)))])
    rotation = u @ sign @ vt
    scale = float(singular @ sign.diagonal() * len(src) / (a ** 2).sum()) if with_scale else 1.0
    return rotation, scale, dest_centre - scale * rotation @ source_centre


def axis_angle(rotation: np.ndarray) -> tuple[np.ndarray, float]:
    angle = float(np.degrees(np.arccos(np.clip((np.trace(rotation) - 1) / 2, -1.0, 1.0))))
    axis = np.array([rotation[2, 1] - rotation[1, 2],
                     rotation[0, 2] - rotation[2, 0],
                     rotation[1, 0] - rotation[0, 1]])
    length = np.linalg.norm(axis)
    if length < 1e-9:                       # 180 deg: recover the axis from the symmetric part
        values, vectors = np.linalg.eigh((rotation + np.eye(3)) / 2)
        return vectors[:, int(np.argmax(values))], angle
    return axis / length, angle


def twist_about(rotation: np.ndarray, axis: np.ndarray) -> float:
    """Signed rotation about `axis` after swing-twist decomposition."""
    unit, angle = axis_angle(rotation)
    half = np.radians(angle) / 2
    quaternion = np.concatenate([[np.cos(half)], np.sin(half) * unit])
    projected = float(quaternion[1:] @ axis) * axis
    twist = np.concatenate([[quaternion[0]], projected])
    norm = np.linalg.norm(twist)
    if norm < 1e-9:
        return 180.0
    twist /= norm
    sign = float(np.sign(twist[1:] @ axis)) or 1.0
    return float(np.degrees(2 * np.arctan2(np.linalg.norm(twist[1:]) * sign, twist[0])))


def report_drift(ours, donor) -> None:
    print("\n== per-bone drift (dominant centroids) ==")
    ghost = sorted(set(ours) - set(donor))
    if ghost:
        print(f"  bones we weight that the donor mesh never names: {ghost}")
    print(f"{'bone':>5} {'ours':>6} {'donor':>6}  {'our centroid':>25} "
          f"{'donor centroid':>25} {'drift cm':>9}")
    for bone in sorted(set(ours) | set(donor)):
        o, d = ours.get(bone), donor.get(bone)
        oc = o.mean(0) if o is not None else None
        dc = d.mean(0) if d is not None else None
        show = lambda c: "  ".join(f"{v:7.3f}" for v in c) if c is not None else " " * 25
        drift = (f"{np.linalg.norm(oc - dc) * 100:9.2f}"
                 if oc is not None and dc is not None else f"{'-':>9}")
        print(f"{bone:>5} {0 if o is None else len(o):>6} {0 if d is None else len(d):>6}  "
              f"{show(oc):>25} {show(dc):>25} {drift}")


def report_finger_map(donor_weighted) -> None:
    print("\n== finger map (weighted centroids; dominance is not required) ==")
    for name, side in SIDES.items():
        wrist = donor_weighted[side["wrist"]][0]
        print(f"  {name}: wrist {side['wrist']} at {np.round(wrist, 3)}")
        for finger, (proximal, distal) in side["chains"].items():
            if proximal not in donor_weighted or distal not in donor_weighted:
                print(f"    {finger:<7} {proximal}->{distal}: MISSING from the donor")
                continue
            a = donor_weighted[proximal][0] - wrist
            b = donor_weighted[distal][0] - wrist
            angle = np.degrees(np.arccos(np.clip(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)),
                                                 -1.0, 1.0)))
            ok = "ok" if np.linalg.norm(b) > np.linalg.norm(a) and angle < 15 else "SUSPECT"
            print(f"    {finger:<7} {proximal:>3} -> {distal:>3}: "
                  f"d {np.linalg.norm(a) * 100:5.2f} -> {np.linalg.norm(b) * 100:5.2f} cm, "
                  f"{angle:4.1f} deg apart  {ok}")


def report_fit(ours, donor) -> None:
    print("\n== rigid fit, ours -> donor ==")
    for name, side in SIDES.items():
        bones = [side["wrist"], *side["fingers"]]
        shared = [b for b in bones if b in ours and b in donor]
        src = np.stack([ours[b].mean(0) for b in shared])
        dst = np.stack([donor[b].mean(0) for b in shared])
        before = np.linalg.norm(src - dst, axis=1)
        forearm = JOINT[side["wrist"]] - JOINT[side["elbow"]]
        forearm /= np.linalg.norm(forearm)
        print(f"  {name}: {len(shared)} shared joints, "
              f"error before fit mean {before.mean() * 100:.2f} cm max {before.max() * 100:.2f} cm")
        for label, with_scale in (("rigid      ", False), ("rigid+scale", True)):
            rotation, scale, translation = umeyama(src, dst, with_scale)
            fitted = (scale * (rotation @ src.T).T) + translation
            error = np.linalg.norm(fitted - dst, axis=1)
            _, angle = axis_angle(rotation)
            print(f"    {label}: {angle:5.1f} deg"
                  f"{f', scale x{scale:.3f}' if with_scale else '         '}"
                  f", twist about forearm {twist_about(rotation, forearm):+6.1f} deg,"
                  f" residual mean {error.mean() * 100:5.2f} cm")

        our_reach = np.linalg.norm(np.vstack([ours[b] for b in shared])
                                   - ours[side["wrist"]].mean(0), axis=1)
        their_reach = np.linalg.norm(np.vstack([donor[b] for b in shared])
                                     - donor[side["wrist"]].mean(0), axis=1)
        print(f"    reach from the wrist: ours {our_reach.max() * 100:5.2f} cm, "
              f"donor {their_reach.max() * 100:5.2f} cm")


def overlay(ours_cloud, donor_cloud, path: Path) -> None:
    from PIL import Image, ImageDraw
    cell, pad = 460, 26
    views = (("side  (x,z)", 0, 2), ("front (y,z)", 1, 2), ("top   (x,y)", 0, 1))
    both = np.vstack([donor_cloud, ours_cloud])
    low, high = both.min(0), both.max(0)
    span = float((high - low).max())
    mid = (high + low) / 2
    sheet = Image.new("RGB", (cell * 3 + pad * 4, cell + pad * 2 + 30), (250, 250, 252))
    draw = ImageDraw.Draw(sheet)
    for n, (label, ax, ay) in enumerate(views):
        ox, oy = pad + n * (cell + pad), pad + 24
        draw.rectangle([ox, oy, ox + cell, oy + cell], outline=(200, 200, 210))
        draw.text((ox + 4, pad + 4), f"{label}   donor=grey  ours=green", fill=(40, 40, 50))
        for cloud, colour, radius in ((donor_cloud, (140, 140, 150), 2.2),
                                      (ours_cloud, (30, 170, 70), 1.7)):
            for point in cloud:
                x = (point[ax] - mid[ax]) / span * (cell - 40) + cell / 2 + ox
                y = -(point[ay] - mid[ay]) / span * (cell - 40) + cell / 2 + oy
                draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=colour)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    print(f"\nwrote {path} ({sheet.width}x{sheet.height})")


def main() -> None:
    hands = Path(option("--hands", str(HERE / "character_hands_v23.obj")))
    if not hands.is_file():
        raise SystemExit(f"no hand mesh at {hands}\n  python cut_hands.py")
    donor_points, donor_skins = influences(GAUNT)
    if not len(donor_points):
        raise SystemExit(f"0x{GAUNT:08X} is not dumped; run make_request.py and launch once")
    our_points = read_obj(hands)
    our_skins = json.loads(hands.with_name(hands.stem + "_weights.json")
                           .read_text(encoding="utf-8"))["skins"]
    print(f"donor 0x{GAUNT:08X}: {len(donor_points):,} skinned verts")
    print(f"ours  {hands.name}: {len(our_points):,} verts")

    donor_dom, our_dom = dominant(donor_points, donor_skins), dominant(our_points, our_skins)
    report_drift(our_dom, donor_dom)
    report_finger_map(weighted(donor_points, donor_skins))
    report_fit(our_dom, donor_dom)

    if "--png" in sys.argv:
        left = set(SIDES["LEFT"]["fingers"]) | {SIDES["LEFT"]["wrist"]}
        pick = lambda dom: np.vstack([dom[b] for b in sorted(left) if b in dom])
        overlay(pick(our_dom), pick(donor_dom), Path(option("--png", "hand_overlay.png")))


if __name__ == "__main__":
    main()
