"""Measure a source model's limb proportions against the recovered Guardian rig.

Answers the question the fitting flags argue about: *by how much, and where,* does this
source differ from the rig it has to skin to? Segment **lengths** are what matter, and
lengths are rotation-invariant, so this reads the GLB node hierarchy directly and never
needs Blender, never needs the facing correction, and never needs an inject.

Run it before deciding between `--fit-proportions` (stretch onto the rig) and the default
(keep the model's shape). A source whose ratios are all near 1.00 needs neither.

Usage:
    python measure_proportions.py --glb path.glb
    python measure_proportions.py            # uses the last ingested model
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from bring_guardian import (BONE_ALIASES, HERE, option, recalled_glb,
                            retarget_tables, vrm_humanoid_roles)
from glb import read_glb

# The chains retarget_mesh.fit_segments walks, in parent->child order. Naming them here rather
# than importing keeps this readable as a report: these are the segments a reader cares about.
CHAINS: dict[str, list[str]] = {
    "spine": ["Hips", "Spine", "Neck", "Head"],
    "arm.L": ["Left shoulder", "Left arm", "Left elbow", "Left wrist"],
    "arm.R": ["Right shoulder", "Right arm", "Right elbow", "Right wrist"],
    "leg.L": ["Left leg", "Left knee", "Left ankle", "Left toe"],
    "leg.R": ["Right leg", "Right knee", "Right ankle", "Right toe"],
}


def identity() -> list[float]:
    return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def multiply(a: list[float], b: list[float]) -> list[float]:
    """Row-major 4x4 product a*b."""
    out = [0.0] * 16
    for row in range(4):
        for col in range(4):
            out[row * 4 + col] = sum(a[row * 4 + k] * b[k * 4 + col] for k in range(4))
    return out


def node_matrix(node: dict) -> list[float]:
    """A glTF node is either an explicit matrix or T*R*S. Both appear in the wild."""
    if "matrix" in node:
        # glTF stores column-major; transpose into the row-major convention used here.
        m = node["matrix"]
        return [m[0], m[4], m[8], m[12],
                m[1], m[5], m[9], m[13],
                m[2], m[6], m[10], m[14],
                m[3], m[7], m[11], m[15]]
    tx, ty, tz = node.get("translation", (0.0, 0.0, 0.0))
    qx, qy, qz, qw = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    sx, sy, sz = node.get("scale", (1.0, 1.0, 1.0))
    rot = [
        1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw), 0.0,
        2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw), 0.0,
        2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy), 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    scale = [sx, 0, 0, 0, 0, sy, 0, 0, 0, 0, sz, 0, 0, 0, 0, 1.0]
    trans = identity()
    trans[3], trans[7], trans[11] = tx, ty, tz
    return multiply(trans, multiply(rot, scale))


def world_positions(document: dict) -> dict[int, tuple[float, float, float]]:
    """World-space translation of every node, walking each scene root down."""
    nodes = document.get("nodes", [])
    world: dict[int, list[float]] = {}

    def walk(index: int, parent: list[float]) -> None:
        here = multiply(parent, node_matrix(nodes[index]))
        world[index] = here
        for child in nodes[index].get("children", []) or []:
            walk(child, here)

    seen: set[int] = set()
    for scene in document.get("scenes", []):
        for root in scene.get("nodes", []):
            walk(root, identity())
            seen.add(root)
    # A file whose skin joints hang outside any scene still measures, rooted where they sit.
    for index in range(len(nodes)):
        if index not in world:
            walk(index, identity())
    return {i: (m[3], m[7], m[11]) for i, m in world.items()}


def canonical_nodes(document: dict) -> dict[str, int]:
    """canonical bone name -> node index, role first and node name second."""
    nodes = document.get("nodes", [])
    roles = vrm_humanoid_roles(document)
    found: dict[str, int] = {}
    for index, role in roles.items():
        bone = BONE_ALIASES.get(role.lower().replace("_", "").replace(" ", ""))
        if bone and bone not in found:
            found[bone] = index
    for index, node in enumerate(nodes):
        key = node.get("name", "").lower().replace("_", "").replace(" ", "").replace(".", "")
        bone = BONE_ALIASES.get(key)
        if bone and bone not in found:
            found[bone] = index
    return found


def distance(a, b) -> float:
    return math.dist(a, b)


def main() -> int:
    glb = Path(option("--glb")) if option("--glb") else recalled_glb()
    if not glb or not glb.is_file():
        print("no model: pass --glb, or ingest one first")
        return 2
    document, _ = read_glb(glb)
    world = world_positions(document)
    found = canonical_nodes(document)

    rig = json.loads((HERE / "objs" / "skeleton" / "rig.json").read_text(encoding="utf-8"))
    joint = {j["bone"]: j["position"] for j in rig["joints"]}
    bone_map, _, _ = retarget_tables()

    print(f"model: {glb.name}")
    print(f"  humanoid bones located: {len(found)}")
    print()
    print(f"  {'segment':22s} {'source':>9s} {'rig':>9s} {'source/rig':>11s}")
    print("  " + "-" * 54)

    ratios: dict[str, list[float]] = {}
    for chain, bones in CHAINS.items():
        for near, far in zip(bones, bones[1:]):
            if near not in found or far not in found:
                print(f"  {chain + ': ' + far:22s} {'-- not in the model --':>32s}")
                continue
            a, b = bone_map.get(near), bone_map.get(far)
            if a not in joint or b not in joint:
                continue
            src = distance(world[found[near]], world[found[far]])
            dst = distance(joint[a], joint[b])
            if dst < 1e-6:
                continue
            ratio = src / dst
            ratios.setdefault(chain, []).append(ratio)
            print(f"  {chain + ': ' + far:22s} {src * 100:8.2f}cm {dst * 100:8.2f}cm {ratio:10.3f}x")

    print()
    print("  chain totals (what a single scale would have to serve):")
    for chain, values in ratios.items():
        span = sum(values) / len(values)
        print(f"    {chain:8s} mean {span:.3f}x   per-segment {[round(v, 2) for v in values]}")
    if ratios:
        flat = [v for values in ratios.values() for v in values]
        print()
        print(f"  spread across all segments: {min(flat):.3f}x .. {max(flat):.3f}x")
        if max(flat) / max(min(flat), 1e-6) > 1.25:
            print("  -> no single scale fits. Either stretch onto the rig (--fit-proportions)")
            print("     or edit the model so its own segments match.")
        else:
            print("  -> close enough to the rig that neither fit is needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
