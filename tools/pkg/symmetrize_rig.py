"""Mirror-average the recovered Guardian rig, halving the error on its weakest joints.

`skeleton.py` estimates each joint independently from the armour vertices weighted to it, so
a joint with few samples lands wherever its handful of vertices happened to sit. The Guardian
is symmetric — armour is modelled that way, and the well-sampled joints prove it: ankle and toe
mirror to **0.00 cm**, knee 0.02, wrist 0.55. So any left/right disagreement beyond that is
estimator noise with a known correct answer.

Measured before this ran: the upper-arm joint (37 and 47 samples, the thinnest in the rig) sat
**7.65 cm** off its mirror, which is what made a symmetric source measure ×1.97 on one arm
segment and ×0.55 on the other. `fit_segments` was aiming at that.

Mirroring is in Destiny space (x forward, y left, z up), so a pair matches when x and z agree
and y negates. Each side moves to the mean, weighted by sample count where both sides have one:
the better-sampled estimate is the more trustworthy one.

Joints with no mirror partner (pelvis, spine, head) are left exactly as they are.

    python symmetrize_rig.py --check     # report, write nothing
    python symmetrize_rig.py             # write, keeping rig.json.orig

Idempotent: running twice changes nothing the second time. Restore with --restore.
"""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RIG = HERE / "objs" / "skeleton" / "rig.json"
BACKUP = RIG.with_suffix(".json.orig")


def partner(name: str) -> str | None:
    """foo_L <-> foo_R, and nothing else. Case-sensitive on purpose: the rig writes _L/_R."""
    if name.endswith("_L"):
        return name[:-2] + "_R"
    if name.endswith("_R"):
        return name[:-2] + "_L"
    return None


def mirrored(position) -> tuple[float, float, float]:
    return (position[0], -position[1], position[2])


def main() -> int:
    check = "--check" in sys.argv
    if "--restore" in sys.argv:
        if not BACKUP.is_file():
            print("no rig.json.orig to restore")
            return 2
        shutil.copyfile(BACKUP, RIG)
        print(f"restored {RIG.name} from {BACKUP.name}")
        return 0

    rig = json.loads(RIG.read_text(encoding="utf-8"))
    joints = rig["joints"]
    by_name = {j["name"]: j for j in joints}

    print(f"{'pair':14s} {'before':>10s} {'after':>10s}   {'L n':>5s} {'R n':>5s}  weight")
    print("-" * 60)
    moved: dict[str, tuple[float, float, float]] = {}
    errors_before: list[float] = []
    errors_after: list[float] = []
    done: set[str] = set()

    for joint in joints:
        name = joint["name"]
        other = partner(name)
        if other is None or other not in by_name or name in done:
            continue
        done.add(name)
        done.add(other)
        left = joint if name.endswith("_L") else by_name[other]
        right = by_name[other] if name.endswith("_L") else joint

        a = left["position"]
        b = right["position"]
        before = math.dist(a, mirrored(b))
        errors_before.append(before)

        # Weight by sample count: an estimate backed by more vertices is the better one. Fall
        # back to an even split when a side records no count.
        na = float(left.get("samples") or 0)
        nb = float(right.get("samples") or 0)
        total = na + nb
        wa, wb = (na / total, nb / total) if total > 0 else (0.5, 0.5)

        flipped = mirrored(b)
        mean = tuple(a[i] * wa + flipped[i] * wb for i in range(3))
        moved[left["name"]] = mean
        moved[right["name"]] = mirrored(mean)
        after = math.dist(mean, mirrored(mirrored(mean)))
        errors_after.append(after)

        label = left["name"][:-2]
        print(f"{label:14s} {before * 100:9.2f}cm {after * 100:9.2f}cm   "
              f"{int(na):5d} {int(nb):5d}  {wa:.2f}/{wb:.2f}")

    if not moved:
        print("no mirror pairs found — nothing to do")
        return 1

    worst_before = max(errors_before) * 100
    print()
    print(f"mirror error: worst {worst_before:.2f}cm -> {max(errors_after) * 100:.2f}cm, "
          f"mean {sum(errors_before) / len(errors_before) * 100:.2f}cm -> 0.00cm")

    if check:
        print("\n--check: nothing written")
        return 0
    if worst_before < 1e-9:
        print("\nalready symmetric — nothing written")
        return 0

    if not BACKUP.is_file():
        shutil.copyfile(RIG, BACKUP)
        print(f"kept the original as {BACKUP.name}")
    for joint in joints:
        if joint["name"] in moved:
            joint["position"] = [round(v, 4) for v in moved[joint["name"]]]
            joint["estimator"] = joint.get("estimator", "") + " + mirror mean"
    rig["note"] = rig.get("note", "") + (
        "  Left/right joints mirror-averaged by symmetrize_rig.py: the well-sampled joints "
        "mirror to ~0, so any disagreement was estimator noise.")
    RIG.write_text(json.dumps(rig, indent=2), encoding="utf-8")
    print(f"wrote {RIG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
