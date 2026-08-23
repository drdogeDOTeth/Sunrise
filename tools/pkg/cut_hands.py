"""Split the retargeted body into a chest mesh and a complete arm+hand mesh.

First-person never draws the chest, so anything left on the body is invisible
on Ghost and weapons. Gauntlets take every triangle whose verts are only
upper-arm / forearm / wrist / finger, plus a cuff of arm-shoulder faces so
the cut overlaps. The chest keeps the stump (not those arm-only faces).

Shoulder bones 27/28 stay on the chest: they carry tank-strap tris, and the
gauntlet draw is one skin atlas.

Does not write packages. Does not --fingers on the chest.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from inject_scatterhorn import read_obj

HERE = Path(__file__).resolve().parent
SRC = HERE / "character_body_v23.obj"


def _option(name: str, fallback: Path) -> Path:
    if name not in sys.argv:
        return fallback
    at = sys.argv.index(name)
    if at + 1 >= len(sys.argv):
        raise SystemExit(f"{name} needs a path")
    return Path(sys.argv[at + 1])
FINGER_BONES = frozenset({
    40, 42, 43, 44, 45, 46, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 66, 71,
})
WRISTS = frozenset({21, 22})
FOREARMS = frozenset({19, 20})
UPPER_ARMS = frozenset({15, 17})
SHOULDERS = frozenset({27, 28})
ARM_BONES = FINGER_BONES | WRISTS | FOREARMS | UPPER_ARMS


def load_sidecars(stem: Path):
    weights = json.loads(stem.with_name(stem.stem + "_weights.json").read_text(encoding="utf-8"))
    groups = json.loads(stem.with_name(stem.stem + "_groups.json").read_text(encoding="utf-8"))
    frame = np.fromfile(stem.with_name(stem.stem + "_frame.bin"), dtype=np.float32).reshape(-1, 9)
    return weights, frame, np.asarray(groups["face_material"], dtype=np.int64), groups["slots"]


def primary(skin: list) -> int:
    return int(skin[0][0]) if skin else 1


def extract(points, faces, frame, skins, face_group, keep_faces: np.ndarray, dest: Path, note: str):
    used, inverse = np.unique(faces[keep_faces].ravel(), return_inverse=True)
    new_faces = inverse.reshape(-1, 3)
    new_points = points[used]
    new_frame = frame[used]
    new_skins = [skins[int(i)] for i in used]
    new_group = face_group[keep_faces]
    dest.write_text(
        "".join(f"v {x:.6f} {y:.6f} {z:.6f}\n" for x, y, z in new_points)
        + "".join(f"f {a + 1} {b + 1} {c + 1}\n" for a, b, c in new_faces),
        encoding="ascii",
    )
    new_frame.astype(np.float32).tofile(dest.with_name(dest.stem + "_frame.bin"))
    dest.with_name(dest.stem + "_weights.json").write_text(json.dumps({
        "note": note,
        "retargeted": True,
        "keep_seams": True,
        "fingers": True,
        "vertices": len(new_skins),
        "skins": new_skins,
    }), encoding="utf-8")
    dest.with_name(dest.stem + "_groups.json").write_text(json.dumps({
        "note": note,
        "slots": ["GLSLShader85", "GLSLShader66", "GLSLShader60", "GLSLShader13", "GLSLShader22"],
        "triangles": int(len(new_faces)),
        "face_material": [int(g) for g in new_group],
    }), encoding="utf-8")
    print(f"\n{dest.name}: {len(new_points):,} verts, {len(new_faces):,} tris")
    bones = Counter(primary(s) for s in new_skins)
    print("  primary", dict(sorted(bones.items())))
    for index, name in enumerate(["tank", "mask", "necklace", "skin", "twirl"]):
        tris = int((new_group == index).sum())
        print(f"  {name:10} {tris:6,} tris  {tris * 3:6,} indices")
    return new_faces.size


def main() -> None:
    source = _option("--mesh", SRC)
    chest_out = _option("--chest-out", HERE / "character_body_v23_nohands.obj")
    hands_out = _option("--hands-out", HERE / "character_hands_v23.obj")
    points, faces = read_obj(source)
    weights, frame, face_group, slots = load_sidecars(source)
    skins = weights["skins"]
    prim = np.array([primary(s) for s in skins], dtype=np.int32)
    finger = np.isin(prim, list(FINGER_BONES))
    wrist = np.isin(prim, list(WRISTS))
    forearm = np.isin(prim, list(FOREARMS))
    upper = np.isin(prim, list(UPPER_ARMS))
    print(f"source {len(points):,} verts, {len(faces):,} tris")
    print(f"  finger-primary {int(finger.sum()):,}  wrist-primary {int(wrist.sum()):,}  "
          f"forearm-primary {int(forearm.sum()):,}  upper-primary {int(upper.sum()):,}")

    arm = np.isin(prim, list(ARM_BONES))
    shoulder = np.isin(prim, list(SHOULDERS))
    arm_only = arm[faces].all(axis=1)
    cuff = arm[faces].any(axis=1) & shoulder[faces].any(axis=1)
    chest_keep = ~arm_only
    hand_keep = arm_only | cuff
    print(f"  chest tris {int(chest_keep.sum()):,}  arm tris {int(hand_keep.sum()):,}  "
          f"overlap {int((chest_keep & hand_keep).sum()):,}")

    extract(points, faces, frame, skins, face_group, chest_keep,
            chest_out,
            "minus arm-only triangles. Upper arm, forearm, palm and fingers live on the "
            "gauntlet draw so first-person has a complete arm. Cuff overlaps the chest stump.")
    extract(points, faces, frame, skins, face_group, hand_keep,
            hands_out,
            "upper-arm + forearm + wrist + finger triangles plus a shoulder cuff. "
            "Gauntlet draw only.")


if __name__ == "__main__":
    main()
