"""Recover the Guardian's wrist and knuckle **joints** from the Scatterhorn gauntlet.

`rig.json` stops at the wrist, and `fingers.json` holds dominant-weight *centroids* — mid-shaft
points, not pivots. Posing a hand onto them lands it wrong, and mixing them with an armature's
bone heads compares two different quantities.

This uses `skeleton.py`'s estimator instead: a vertex carrying real weight on **both** a bone and
its parent straddles the pivot between them, so that set's centroid *is* the joint. Wrist =
blend(forearm, hand); each knuckle = blend(hand, that finger's proximal bone).

Written as plain JSON so `retarget_mesh.py` can read it inside Blender, where the package
readers are not importable.

Usage:
    python hand_targets.py
"""
from __future__ import annotations

import json

import numpy as np

from skeleton import BLEND_FLOOR, OUT, influences

GAUNT = 0x80EF981E
SIDES = {
    "L": dict(forearm=19, wrist=21, knuckles={"index": 40, "middle": 42, "little": 43, "thumb": 45}),
    "R": dict(forearm=20, wrist=22, knuckles={"index": 46, "middle": 48, "little": 49, "thumb": 51}),
}


def centroid_of(points, skins, bone: int):
    """@return Weight-averaged position of every vertex carrying weight on `bone`, and the count.

    The orientation fit uses these rather than the blend joints below. A blend region between the
    palm and a finger sits on the **armoured back** of the glove, so the joint it estimates is
    biased dorsally - fitting our knuckles to it rolled the hand ~13 deg the wrong way (measured
    2026-08-23, `audit_hand_bind.py` twist went -2.3 -> +11.4 deg). A weighted centroid over the
    whole cloud has no such bias, and it is exactly what the audit compares, so the fix and the
    check now optimise the same quantity.
    """
    pts = np.asarray(points, dtype=np.float64)
    total = np.zeros(3)
    mass = 0.0
    count = 0
    for index, pairs in enumerate(skins):
        for other, weight in pairs:
            if int(other) == bone and weight > 0:
                total += weight * pts[index]
                mass += weight
                count += 1
    return (total / mass, count) if mass else (None, 0)


def blend_joint(points, skins, parent: int, child: int):
    """@return Centroid of the vertices straddling `parent`/`child`, and how many there were."""
    rows = []
    for index, pairs in enumerate(skins):
        weights = {int(bone): weight for bone, weight in pairs}
        if weights.get(parent, 0) >= BLEND_FLOOR and weights.get(child, 0) >= BLEND_FLOOR:
            rows.append(index)
    if not rows:
        return None, 0
    return np.asarray(points, dtype=np.float64)[np.asarray(rows)].mean(0), len(rows)


def main() -> None:
    points, skins = influences(GAUNT)
    if not len(points):
        raise SystemExit(f"0x{GAUNT:08X} is not dumped; run make_request.py and launch once")
    print(f"gauntlet 0x{GAUNT:08X}: {len(points):,} skinned verts")

    out = {"note": ("Blend-estimated joints from the Scatterhorn gauntlet, character space, "
                    "metres. Pivots, not centroids - see hand_targets.py."),
           "source": f"0x{GAUNT:08X}", "blend_floor": BLEND_FLOOR, "sides": {}}
    for side, spec in SIDES.items():
        wrist, count = blend_joint(points, skins, spec["forearm"], spec["wrist"])
        if wrist is None:
            raise SystemExit(f"no {side} wrist blend region; cannot place the hand")
        row = {"wrist_bone": spec["wrist"], "forearm_bone": spec["forearm"],
               "wrist": [round(float(v), 5) for v in wrist], "wrist_verts": count,
               "knuckles": {}}
        print(f"  {side} wrist {spec['wrist']}: {np.round(wrist, 3)} (n={count})")
        for name, bone in spec["knuckles"].items():
            joint, n = blend_joint(points, skins, spec["wrist"], bone)
            if joint is None:
                print(f"    {name} ({bone}): no blend region, skipped")
                continue
            row["knuckles"][name] = {"bone": bone,
                                     "position": [round(float(v), 5) for v in joint],
                                     "verts": n,
                                     "from_wrist": round(float(np.linalg.norm(joint - wrist)), 4)}
            print(f"    {name:<7} {bone:>3}: {np.round(joint, 3)} (n={n}, "
                  f"{np.linalg.norm(joint - wrist) * 100:.2f} cm from the wrist)")

        row["centroids"] = {}
        for name, bone in spec["knuckles"].items():
            centre, n = centroid_of(points, skins, bone)
            if centre is None:
                continue
            row["centroids"][name] = {"bone": bone,
                                      "position": [round(float(v), 5) for v in centre],
                                      "verts": n}
        centre, n = centroid_of(points, skins, spec["wrist"])
        if centre is not None:
            row["centroids"]["palm"] = {"bone": spec["wrist"],
                                        "position": [round(float(v), 5) for v in centre],
                                        "verts": n}
        print(f"    centroids: " + ", ".join(
            f"{key} {np.round(value['position'], 3)}" for key, value in row["centroids"].items()))
        out["sides"][side] = row

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "hand_targets.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
