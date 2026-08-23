"""Preview the hand the way the player sees it, at a range of rotations. No launch needed.

The camera is built from the hand's **own** frame — square at the palm, fingers up — which is the
Ghost-summon view and is pose-independent, so it works on a bind-pose mesh. Two axes:

    --axis view      tilt within the palm plane: fingers swing left/right on screen
    --axis forearm   roll (pronation): the palm turns over, palm-up to palm-down

**`retarget_mesh.HAND_ROLL` uses the same sign as the panel labels here**, deliberately. Pick a
value by looking at the sheet; do not reason about the right-hand rule and find out a launch
later. Both signs were needed in practice — the arithmetic said one thing, the user's eye on this
sheet said the other, and the eye is what ships.

**This cannot verify a roll that is already baked into the mesh.** The camera comes from the hand,
so it rolls with it and the render looks untouched. To check a shipped roll, measure the palm
normal against a *fixed* frame — a previous version's — not against the mesh's own.

    python hand_view.py out.png
    python hand_view.py out.png --axis forearm --angles -40,-20,0,20,40
    python hand_view.py out.png --side palm --mesh character_hands_v25.obj
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
WRIST, FOREARM = 21, 19
KNUCKLE = {"index": 40, "middle": 42, "little": 43, "thumb": 45}
HAND_BONES = {21, 40, 42, 43, 45, 52, 53, 54, 56, 66}
CELL = 400
LIGHT = np.array([0.3, 0.25, 1.0])
LIGHT /= np.linalg.norm(LIGHT)


def option(name: str, fallback: str) -> str:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else fallback


def read_obj(path: Path):
    points, faces = [], []
    for line in path.read_text(encoding="ascii").splitlines():
        if line.startswith("v "):
            points.append([float(v) for v in line.split()[1:4]])
        elif line.startswith("f "):
            faces.append([int(chunk.split("/")[0]) - 1 for chunk in line.split()[1:4]])
    return np.asarray(points), np.asarray(faces, dtype=np.int64)


def rotation_about(axis, degrees: float):
    axis = axis / np.linalg.norm(axis)
    turn = np.radians(degrees)
    c, s = np.cos(turn), np.sin(turn)
    x, y, z = axis
    return np.array([[c + x*x*(1-c), x*y*(1-c) - z*s, x*z*(1-c) + y*s],
                     [y*x*(1-c) + z*s, c + y*y*(1-c), y*z*(1-c) - x*s],
                     [z*x*(1-c) - y*s, z*y*(1-c) + x*s, c + z*z*(1-c)]])


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
        raise SystemExit(__doc__)
    out = Path(sys.argv[1])
    source = HERE / option("--mesh", "character_hands_v25.obj")
    if not source.is_file():
        raise SystemExit(f"no hand mesh at {source}\n  python cut_hands.py")

    points, faces = read_obj(source)
    skins = json.loads(source.with_name(source.stem + "_weights.json")
                       .read_text(encoding="ascii"))["skins"]
    primary = np.array([max(s, key=lambda pair: pair[1])[0] if s else 1 for s in skins])

    def centroid(bone: int):
        weight = np.array([dict((int(b), w) for b, w in s).get(bone, 0) for s in skins], float)
        return (points * weight[:, None]).sum(0) / weight.sum()

    palm = centroid(WRIST)
    knuckles = {name: centroid(bone) for name, bone in KNUCKLE.items()}
    up = knuckles["middle"] - palm
    up /= np.linalg.norm(up)
    across = knuckles["index"] - knuckles["little"]
    across -= up * (across @ up)
    across /= np.linalg.norm(across)
    normal = np.cross(up, across)

    side = option("--side", "back")
    view = -normal if side == "back" else normal
    right = np.cross(view, up)
    right /= np.linalg.norm(right)

    if option("--axis", "view") == "view":
        spin = view
    else:
        rig = json.loads((HERE / "objs" / "skeleton" / "rig.json").read_text(encoding="utf-8"))
        joint = {j["bone"]: np.asarray(j["position"], float) for j in rig["joints"]}
        spin = joint[WRIST] - joint[FOREARM]

    keep_vertex = np.isin(primary, list(HAND_BONES | {FOREARM}))
    keep_face = keep_vertex[faces].all(axis=1)
    used, inverse = np.unique(faces[keep_face].ravel(), return_inverse=True)
    hand = points[used]
    tris = inverse.reshape(-1, 3)
    rolls = np.isin(primary[used], list(HAND_BONES))

    angles = [float(a) for a in option("--angles", "-20,-10,0,10,20").split(",")]
    sheet = Image.new("RGB", (CELL * len(angles) + 18 * (len(angles) + 1), CELL + 62),
                      (248, 248, 250))
    draw = ImageDraw.Draw(sheet)

    for n, angle in enumerate(angles):
        moved = hand.copy()
        if angle:
            turn = rotation_about(spin, -angle)
            moved[rolls] = (turn @ (hand[rolls] - palm).T).T + palm
        ox, oy = 18 + n * (CELL + 18), 48
        draw.rectangle([ox, oy, ox + CELL, oy + CELL], outline=(205, 205, 215), fill=(255,) * 3)
        draw.text((ox + 8, 20), "AS IS (0)" if angle == 0 else f"{angle:+.0f} deg",
                  fill=(20, 20, 30))

        flat = np.stack([(moved - palm) @ right, (moved - palm) @ up], axis=1)
        depth = (moved - palm) @ view
        span = float((flat.max(0) - flat.min(0)).max()) * 1.2
        mid = (flat.max(0) + flat.min(0)) / 2

        def place(p):
            return (float((p[0] - mid[0]) / span * CELL + CELL / 2 + ox),
                    float(-(p[1] - mid[1]) / span * CELL + CELL / 2 + oy))

        for index in np.argsort(-depth[tris].mean(axis=1)):
            tri = tris[index]
            a, b, c = moved[tri]
            face = np.cross(b - a, c - a)
            length = np.linalg.norm(face)
            if length < 1e-12:
                continue
            camera = np.array([face @ right, face @ up, face @ view]) / length
            shade = abs(float(camera @ LIGHT))
            draw.polygon([place(flat[tri[0]]), place(flat[tri[1]]), place(flat[tri[2]])],
                         fill=(int(35 + 140 * shade), int(115 + 125 * shade), int(45 + 105 * shade)))

        if angle == 0:
            spot = place(np.array([(knuckles["thumb"] - palm) @ right,
                                   (knuckles["thumb"] - palm) @ up]))
            draw.ellipse([spot[0] - 7, spot[1] - 7, spot[0] + 7, spot[1] + 7],
                         outline=(200, 40, 40), width=3)
            draw.text((spot[0] + 11, spot[1] - 7), "thumb", fill=(200, 40, 40))

    sheet.save(out)
    print(f"wrote {out} ({sheet.width}x{sheet.height}), {side} of the hand, "
          f"spinning about the {option('--axis', 'view')} axis")


if __name__ == "__main__":
    main()
