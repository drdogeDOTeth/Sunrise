"""
Writes a named, exact rig in **body** bone space, for retargeting onto an NPC.

## Why a second rig file

`objs/skeleton/rig.json` is estimated from Guardian **armour** skin weights. It is a good shape and
a bad labelling: set-to-set it sits 4.5 cm from the real skeleton, but bone *i* against the real
row *i* is 37.8 cm (`map_bone_space.py`). Its bone numbers belong to the armour's index space.

A body uses a different space, and that one is exact. Zavala's mesh 0 - 6,147 vertices, every one
weighted, sums all 255 - puts each bone's weighted vertex centroid **2.6 cm** from the row at the
same index in `rig_true_human.json`. So for a body, row index *is* bone index, and the positions
are read rather than estimated.

This file joins the two halves: exact positions from `rig_true_human.json`, names from the anatomy
those positions describe.

## Where the names come from

Read off the geometry, not guessed. Every joint below was identified from three things that agree:
the true rig's own position, the number of Zavala vertices bound to it, and the mirror structure
(`+y` is Left, confirmed against `rig.json`'s own `thigh_L` at `y +0.134` and `thigh_R` at
`y -0.147`).

    idx  z       y        verts   read as
      1  1.034   0.000    1730    pelvis - mid, heaviest, at hip height
    2/3  1.004  +/-0.112   466    thigh - mirror pair just below pelvis
      4  1.091   0.000    1205    spine_lower - mid, above pelvis
    5/6  0.553  +/-0.147   433    knee - mirror pair at half height
      7  1.191   0.000    1135    spine_upper
    8/9  0.126  +/-0.173   137    ankle - mirror pair near the floor
     10  1.289   0.000    1590    chest
  11/13  1.505  +/-0.042   202    collar - mirror pair, narrow
     12  1.581   0.000     202    neck
  14/16  1.485  +/-0.186   309    shoulder
     15  1.629   0.000     172    head - the highest mid joint
  18/19  1.274  +/-0.324   540    elbow
  20/21  1.156  +/-0.392   459    wrist
  28/29  0.031  +/-0.198    96    toe - lowest pair of all
  30/31  1.484  +/-0.186   164    shoulder twist - co-located with 14/16
  34-63  ~1.03-1.16        ~100   fingers, three joints x five x two hands

**The armour space disagrees joint for joint**, which is the whole reason this file exists: armour
puts knees at 6/7 and ankles at 9/10 where a body puts them at 5/6 and 8/9, and armour's 18 is the
head where a body's 18 is an elbow. Feeding the armour map to an NPC retarget would weight the
character to the wrong limbs.

Usage:
    python make_body_rig.py                 # write both files
    python make_body_rig.py --check         # verify against Zavala without writing
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKELETON = HERE / "objs" / "skeleton" / "rig_true_human.json"
RIG_OUT = HERE / "objs" / "skeleton" / "rig_body_space.json"
MAP_OUT = HERE / "objs" / "skeleton" / "bone_map_body_space.json"

#: Row index -> joint name, read off the geometry (see the table in the module docstring).
NAMES = {
    1: "pelvis",
    2: "thigh_L", 3: "thigh_R",
    4: "spine_lower",
    5: "knee_L", 6: "knee_R",
    7: "spine_upper",
    8: "ankle_L", 9: "ankle_R",
    10: "chest",
    11: "collar_L", 13: "collar_R",
    12: "neck",
    14: "shoulder_L", 16: "shoulder_R",
    15: "head",
    18: "elbow_L", 19: "elbow_R",
    20: "wrist_L", 21: "wrist_R",
    28: "toe_L", 29: "toe_R",
    30: "upperarm_L", 31: "upperarm_R",
}

#: GLB armature bone -> body-space index, the same keys `retarget_mesh.BONE_MAP` uses so it can be
#: passed straight to `--bone-map`. Compare against that map to see how far the two spaces differ.
BONE_MAP = {
    "Hips": 1, "Spine": 4, "Neck": 12, "Head": 15,
    "Jaw": 15, "LeftEye": 15, "RightEye": 15,
    "Left shoulder": 14, "Left arm": 30, "Left elbow": 18, "Left wrist": 20,
    "Right shoulder": 16, "Right arm": 31, "Right elbow": 19, "Right wrist": 21,
    "Left leg": 2, "Left knee": 5, "Left ankle": 8, "Left toe": 28,
    "Right leg": 3, "Right knee": 6, "Right ankle": 9, "Right toe": 29,
}


def rows() -> list[list[float]]:
    """@return Exact joint positions from the read skeleton, in row order."""
    return [row["position"] for row in json.loads(SKELETON.read_text())["bones"]]


def check(positions: list[list[float]]) -> int:
    """
    Verifies each name against the geometry that produced it, so a typo cannot pass silently.

    @return Number of disagreements.
    """
    problems = 0
    for index, name in sorted(NAMES.items()):
        x, y, z = positions[index]
        side = name.rsplit("_", 1)[-1] if name.endswith(("_L", "_R")) else "mid"
        expected = {"L": y > 0.01, "R": y < -0.01, "mid": abs(y) <= 0.01}[side]
        if not expected:
            print(f"  {name} (row {index}) has y {y:+.3f}, which is not {side}")
            problems += 1
    # Anatomy that must hold for any human skeleton, stated as assertions rather than assumed.
    order = [("toe_L", "ankle_L"), ("ankle_L", "knee_L"), ("knee_L", "thigh_L"),
             ("thigh_L", "chest"), ("chest", "neck"), ("neck", "head")]
    by_name = {name: positions[index][2] for index, name in NAMES.items()}
    for lower, upper in order:
        if by_name[lower] >= by_name[upper]:
            print(f"  {lower} ({by_name[lower]:.3f}) is not below {upper} ({by_name[upper]:.3f})")
            problems += 1
    return problems


def main() -> int:
    if not SKELETON.is_file():
        print(f"missing {SKELETON}")
        return 1
    positions = rows()
    print(f"{SKELETON.name}: {len(positions)} rows, {len(NAMES)} named\n")
    problems = check(positions)
    if problems:
        print(f"\n{problems} disagreement(s) between the names and the geometry")
        return 1
    print("  every name agrees with its side and the head-to-toe ordering")

    if "--check" in sys.argv:
        return 0

    joints = [{"bone": index, "name": name, "position": positions[index],
               "source": "read from FK 0x80C2321D; row index is the body bone index"}
              for index, name in sorted(NAMES.items())]
    RIG_OUT.write_text(json.dumps({
        "note": "Exact joints in BODY bone space. Row index of rig_true_human.json IS the bone "
                "index a body mesh weights to - proven on Zavala's mesh 0 at 2.6 cm median. "
                "Not interchangeable with rig.json, which is armour space and estimated.",
        "skeleton": "0x80C2321D",
        "joints": joints,
    }, indent=2), encoding="utf-8")
    MAP_OUT.write_text(json.dumps(BONE_MAP, indent=2), encoding="utf-8")
    print(f"\nwrote {RIG_OUT.name} ({len(joints)} joints)")
    print(f"wrote {MAP_OUT.name} ({len(BONE_MAP)} armature bones)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
