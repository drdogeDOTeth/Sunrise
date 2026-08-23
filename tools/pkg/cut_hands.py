"""Split v23 into a chest mesh without distal hands, and a hand-only mesh.

Chest keeps any triangle that is not purely fingers, so the wrist/palm stays on
the body (bones 21/22, ceiling 28). Gauntlets take any triangle that touches a
finger-weighted vertex, plus a ring of wrist triangles so the cut overlaps.

Does not write packages.
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
    print(f"source {len(points):,} verts, {len(faces):,} tris")
    print(f"  finger-primary {int(finger.sum()):,}  wrist-primary {int(wrist.sum()):,}")

    finger_face = finger[faces].any(axis=1)
    all_finger = finger[faces].all(axis=1)
    chest_keep = ~all_finger
    hand_keep = finger_face
    print(f"  chest tris {int(chest_keep.sum()):,}  hand tris {int(hand_keep.sum()):,}  "
          f"overlap {int((chest_keep & hand_keep).sum()):,}")

    extract(points, faces, frame, skins, face_group, chest_keep,
            chest_out,
            "minus purely-finger triangles. Wrist/palm stay on the chest.")
    extract(points, faces, frame, skins, face_group, hand_keep,
            hands_out,
            "triangles that touch a finger-primary vertex. Gauntlet draw only.")


if __name__ == "__main__":
    main()
