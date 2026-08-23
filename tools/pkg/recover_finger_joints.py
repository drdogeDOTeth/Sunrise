"""Recover Destiny finger pivots from Scatterhorn gauntlet weights.

The body rig stops at the wrists (21/22). Fingers live at 34-71 and only the gauntlets
name them. Same estimator as `skeleton.py`: blend-with-parent when we know the parent,
dominant centroid otherwise.

Writes `objs/skeleton/fingers.json`. Offline — does not touch packages.
"""
from __future__ import annotations

import json
from pathlib import Path

from inject_scatterhorn import LEFT_HAND, RIGHT_HAND
from skeleton import BLEND_FLOOR, OUT, influences

GAUNT = 0x80EF981E
WRIST_L, WRIST_R = 21, 22
FINGER_L = sorted(LEFT_HAND - {15, 19, 21})
FINGER_R = sorted(RIGHT_HAND - {17, 20, 22})

# Guessed chains from gauntlet influence counts + L/R pairs in HAND_PAIRS.
# Confirmed or rewritten after this script prints distances from the wrist.
CHAIN_L = {
    "thumb": (34, 40, 66),
    "index": (42, 43, 44),
    "middle": (45, 52, 53),
    "ring": (54, 55),
    "pinky": (56,),
}
CHAIN_R = {
    "thumb": (39, 46, 71),
    "index": (48, 49, 50),
    "middle": (51, 57, 58),
    "ring": (59, 60),
    "pinky": (61,),
}


def main() -> None:
    points, skins = influences(GAUNT)
    print(f"gauntlet 0x{GAUNT:08X}: {len(points):,} skinned verts")

    dominant: dict[int, list[int]] = {}
    for index, pairs in enumerate(skins):
        best = max(pairs, key=lambda pair: pair[1])[0]
        dominant.setdefault(best, []).append(index)

    rows = []
    print(f"\n{'bone':>5} {'side':>5} {'verts':>7} {'x':>8} {'y':>8} {'z':>8} "
          f"{'d_wrist':>8}")
    for bone, side, wrist in (
        *[(b, "L", WRIST_L) for b in FINGER_L],
        *[(b, "R", WRIST_R) for b in FINGER_R],
    ):
        idxs = dominant.get(bone, [])
        if not idxs:
            print(f"{bone:>5} {side:>5} {'0':>7}")
            continue
        centre = points[idxs].mean(0)
        wrist_pts = points[dominant[wrist]] if wrist in dominant else None
        wrist_c = wrist_pts.mean(0) if wrist_pts is not None else centre
        dist = float(((centre - wrist_c) ** 2).sum() ** 0.5)
        rows.append({
            "bone": bone,
            "side": side,
            "position": [round(float(v), 4) for v in centre],
            "dominant_verts": len(idxs),
            "distance_from_wrist": round(dist, 4),
        })
        x, y, z = centre
        print(f"{bone:>5} {side:>5} {len(idxs):>7} {x:>8.3f} {y:>8.3f} {z:>8.3f} "
              f"{dist:>8.3f}")

    print("\nleft, nearest to wrist first:")
    for row in sorted((r for r in rows if r["side"] == "L"),
                      key=lambda r: r["distance_from_wrist"]):
        print(f"  {row['bone']:>3}  d={row['distance_from_wrist']:.3f}  "
              f"n={row['dominant_verts']}")
    print("right, nearest to wrist first:")
    for row in sorted((r for r in rows if r["side"] == "R"),
                      key=lambda r: r["distance_from_wrist"]):
        print(f"  {row['bone']:>3}  d={row['distance_from_wrist']:.3f}  "
              f"n={row['dominant_verts']}")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "fingers.json"
    path.write_text(json.dumps({
        "note": "Gauntlet-dominant centroids, character space. Not blend pivots.",
        "chains_guess": {"L": CHAIN_L, "R": CHAIN_R},
        "joints": rows,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
