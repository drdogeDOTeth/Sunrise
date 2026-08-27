"""
Tests which bone-index space a skinned mesh uses, against the read skeleton.

## What this settles

`rig_true_human.json` is the real skeleton, read from FK tag `0x80C2321D` and decoded to 64 rows of
quaternion plus position. Its own note said **"Row order is NOT the global bone index used by armour
weights"**, and that blocked replacing the estimated `rig.json` with exact values.

Zavala's body mesh disproves the general form of that. His mesh 0 is a complete humanoid, 6,147
vertices, **every one of them weighted**, weight sums all exactly 255, across 54 bones in range
1..63 — and its coordinate frame is the skeleton's own (both about 1.68 m tall, x and y within a few
cm). Comparing each bone's weighted vertex centroid to the row at the *same index* gives:

    identity, bone index == row index:   median 2.6 cm, mean 6.4 cm

A centroid is not a joint — it sits mid-limb — so a few centimetres is what agreement looks like.
**For Zavala's mesh, the row order is the bone index.**

## The correction this forces

`rig.json`'s "4.53 cm from truth" was a **set-to-set** figure: nearest row to each estimate, ignoring
which bone it claimed to be. This tool reproduces it (mean 4.53 cm) and then measures the thing that
actually matters:

    rig.json bone i vs true row i:       median 37.8 cm, mean 49.2 cm

So `rig.json` is a good *shape* and a bad *labelling*. It was never per-joint accurate, and reading
the 4.53 cm as per-joint accuracy overstates it.

## What is not settled

Scatterhorn armour does **not** match row order by identity (chest 35.8 cm, legs 37.0, gauntlets
61.5). That is consistent with armour using a different index space — but it is also what partial
coverage produces, because a chest piece's vertices weighted to a leg bone are a biased sample where
a whole body's are not. Zavala is the trustworthy instrument here; the armour numbers are a flag,
not a verdict.

Usage:
    python map_bone_space.py                       # Zavala, the armour, and rig.json
    python map_bone_space.py 0x80C714B2            # any dumped model with a stride-16 mesh
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TRUE_RIG = HERE / "objs" / "skeleton" / "rig_true_human.json"
ESTIMATE = HERE / "objs" / "skeleton" / "rig.json"

#: Stride 16 is the skinned vertex layout: packed position, then 4 weights and 4 bone indices.
SKINNED_STRIDE = 16
WEIGHTS_AT = 8
BONES_AT = 12

DEFAULTS = [
    (0x80C714B2, "Zavala body"),
    (0x80EFA1CA, "Scatterhorn chest"),
    (0x80EFA93B, "Scatterhorn legs"),
    (0x80EF981E, "Scatterhorn gauntlets"),
]


def true_rows() -> np.ndarray:
    """@return The read skeleton's joint positions, in row order."""
    return np.array([row["position"] for row in json.loads(TRUE_RIG.read_text())["bones"]])


def centroids(model) -> dict[int, np.ndarray]:
    """
    @param model A parsed SEntityModel.
    @return Bone index -> weighted centroid of the vertices bound to it, over every stride-16 mesh.

    Weighting by the vertex's own weight matters: an unweighted centroid lets a vertex barely
    influenced by a bone pull that bone's estimate as hard as one rigidly bound to it.
    """
    import extract_mesh as em

    accumulated: dict[int, list] = {}
    for mesh in model.meshes:
        stride = em.vertex_stride(mesh.positions)
        body = em.dumped(mesh.position_buffer)
        if stride != SKINNED_STRIDE or body is None:
            continue
        points = em.read_positions(body, stride, model.scale[0], model.translation[:3])
        for index, point in enumerate(points):
            at = index * stride
            weights = body[at + WEIGHTS_AT:at + WEIGHTS_AT + 4]
            bones = body[at + BONES_AT:at + BONES_AT + 4]
            for weight, bone in zip(weights, bones):
                if not weight:
                    continue
                slot = accumulated.setdefault(bone, [np.zeros(3), 0.0])
                slot[0] += np.asarray(point) * weight
                slot[1] += weight
    return {bone: total / mass for bone, (total, mass) in accumulated.items()}


def report(label: str, cent: dict[int, np.ndarray], rows: np.ndarray) -> None:
    """Prints how well this mesh's bone indices line up with the skeleton's row order."""
    usable = [b for b in sorted(cent) if b < len(rows)]
    if not usable:
        print(f"  {label:24} no bone index lands inside {len(rows)} rows")
        return
    distances = np.array([np.linalg.norm(cent[b] - rows[b]) for b in usable])
    verdict = "SAME SPACE" if np.median(distances) < 0.15 else "does not match"
    print(f"  {label:24} {len(usable):>3} bones   median {np.median(distances) * 100:5.1f} cm   "
          f"mean {np.mean(distances) * 100:5.1f} cm   {verdict}")


def main() -> int:
    if not TRUE_RIG.is_file():
        print(f"missing {TRUE_RIG}")
        return 1
    rows = true_rows()
    # Read the arguments BEFORE pointing parse_models at the dump directory, because that is done by
    # rewriting sys.argv - reading after it silently discarded every tag the caller asked for and
    # reported the defaults instead, which looks like a working run.
    wanted = [(int(a, 0), f"0x{int(a, 0):08X}") for a in sys.argv[1:] if a.startswith("0x")]
    sys.argv = [sys.argv[0], "--dump", r"C:\Sunrise\bin\x64\Sunrise\dump"]
    from parse_models import models

    by_tag = {m.tag: m for m in models}

    print(f"skeleton {TRUE_RIG.name}: {len(rows)} rows\n")
    print("Does this mesh's bone index equal the skeleton's row index?")
    for tag, label in (wanted or DEFAULTS):
        model = by_tag.get(tag)
        if model is None:
            print(f"  {label:24} not among the {len(models)} dumped models")
            continue
        cent = centroids(model)
        if not cent:
            print(f"  {label:24} no stride-16 skinned mesh")
            continue
        report(label, cent, rows)

    if ESTIMATE.is_file():
        estimate = json.loads(ESTIMATE.read_text())["joints"]
        indexed = np.array([np.linalg.norm(np.asarray(j["position"]) - rows[j["bone"]])
                            for j in estimate if j["bone"] < len(rows)])
        nearest = np.array([min(np.linalg.norm(np.asarray(j["position"]) - r) for r in rows)
                            for j in estimate])
        print(f"\n{ESTIMATE.name}, {len(estimate)} estimated joints:")
        print(f"  by index (bone i vs row i)   median {np.median(indexed) * 100:5.1f} cm   "
              f"mean {np.mean(indexed) * 100:5.1f} cm   <- the labelling")
        print(f"  set-to-set (nearest row)     median {np.median(nearest) * 100:5.1f} cm   "
              f"mean {np.mean(nearest) * 100:5.1f} cm   <- the shape, and what '4.53 cm' meant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
