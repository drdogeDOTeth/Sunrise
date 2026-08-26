"""Decode a dumped FK skeleton into bone transforms — the real bind pose, not an estimate.

**This is the table `rig.json` has always been a guess at.** `skeleton.py` recovers joints from
armour skin weights because `find_guardian_skeleton.py` could not find a skeleton to read. It can
now: enemy and NPC entities name their FK skeleton inline (class `0x80808545` immediately followed
by its tag), and `docs/ENEMY_MODELS.md` lists the tags.

**Format**, decoded 2026-08-26 and verified three ways:

    32-byte rows:  [scale] [quat x y z w] [pos x y z]

* the middle four floats are a **unit quaternion** — that is the row test, and what locates the
  table: scan every 4-byte alignment and keep the longest run that passes it;
* left and right bones appear as **exact mirrors**, x negated with y and z identical;
* row counts track anatomy — human 64, thrall 48, **shank 15** (a limbless drone).

**The table start is not in the header.** `+0x08`/`+0x10` are constant 96/112 across every
skeleton, and `+0x18` is an offset that does *not* point at the bones. Dividing the region by 48
looked clean and produced NaNs. Scanning alignments is what works; on the human rig it finds 0xA5C.

**Row order is NOT the global bone index** that armour weights use. Testing `row[i]` against
`rig.json` bone `i` matches only the pelvis, and that is chance. Mapping the two index spaces needs
the NodeHierarchy (class `0x80808A08`) or bone-name hashes, and is still open — so this reports
positions and a nearest-neighbour correspondence, and does not rewrite `rig.json`.

Usage:
    python parse_bind_pose.py --tag 80C2321D           # the shared human rig
    python parse_bind_pose.py --tag 80BF8816 --json out.json
    python parse_bind_pose.py --tag 80C2321D --compare  # against the estimated rig.json
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DUMP = Path(os.environ.get("SUNRISE_GAME", r"C:\Sunrise")) / "bin" / "x64" / "Sunrise" / "dump"
RIG = HERE / "objs" / "skeleton" / "rig.json"
ROW = 32


def option(name: str, fallback: str = "") -> str:
    if name in sys.argv:
        at = sys.argv.index(name)
        if at + 1 < len(sys.argv):
            return sys.argv[at + 1]
    return fallback


def read_rows(blob: bytes, start: int) -> list[dict]:
    """Rows from `start` until one stops looking like a bone transform."""
    out: list[dict] = []
    for i in range(4096):
        at = start + i * ROW
        if at + ROW > len(blob):
            break
        f = struct.unpack_from("<8f", blob, at)
        if any(x != x for x in f):
            break
        quat = f[1:5]
        if abs(sum(x * x for x in quat) - 1.0) > 0.02:
            break
        out.append({"index": i, "scale": f[0], "quat": list(quat), "position": list(f[5:8])})
    return out


def locate(blob: bytes) -> tuple[int, list[dict]]:
    """@return (offset, rows) for the longest run of valid bone rows at any 4-byte alignment."""
    best: list[dict] = []
    best_off = 0
    for start in range(0, max(len(blob) - ROW, 0), 4):
        rows = read_rows(blob, start)
        if len(rows) > len(best):
            best, best_off = rows, start
    return best_off, best


def main() -> int:
    tag = option("--tag").replace("0x", "").upper()
    if not tag:
        print("--tag is required, e.g. --tag 80C2321D")
        return 2
    path = DUMP / f"tag_{tag}.bin"
    if not path.is_file():
        print(f"not dumped: {path}\nAdd `tag 0x{tag}` to dump/request.txt and launch to character select.")
        return 2

    blob = path.read_bytes()
    offset, rows = locate(blob)
    print(f"tag {tag}  {len(blob):,} B  declared length {struct.unpack_from('<q', blob, 0)[0]:,}")
    print(f"bone table at 0x{offset:X}: {len(rows)} bones")
    if not rows:
        return 1

    xs = [r["position"][0] for r in rows]
    ys = [r["position"][1] for r in rows]
    zs = [r["position"][2] for r in rows]
    print(f"  x {min(xs):+.3f}..{max(xs):+.3f}   y {min(ys):+.3f}..{max(ys):+.3f}   "
          f"z {min(zs):+.3f}..{max(zs):+.3f}")
    mirrored = sum(1 for r in rows if any(
        abs(r["position"][0] + q["position"][0]) < 1e-4
        and abs(r["position"][1] - q["position"][1]) < 1e-4
        and abs(r["position"][2] - q["position"][2]) < 1e-4
        for q in rows if q is not r))
    print(f"  bones with an exact x-mirrored partner: {mirrored}/{len(rows)}")

    if "--compare" in sys.argv and RIG.is_file():
        rig = json.loads(RIG.read_text(encoding="utf-8"))
        print("\nestimated rig.json -> nearest true bone:")
        worst = []
        for joint in rig["joints"]:
            distance, near = min(
                ((math.dist(joint["position"], r["position"]), r) for r in rows),
                key=lambda t: t[0])
            worst.append((distance, joint["name"]))
            print(f"  {joint['bone']:2d} {joint['name']:12s} {distance * 100:6.2f} cm"
                  f"   true bone {near['index']}")
        mean = sum(d for d, _ in worst) / len(worst)
        print(f"\n  mean {mean * 100:.2f} cm; worst "
              + ", ".join(f"{n} {d*100:.1f}" for d, n in sorted(worst, reverse=True)[:3]))
        print("  Compare against another species to know whether that number means anything:")
        print("    python parse_bind_pose.py --tag 80BF8816 --compare   # thrall, expect far worse")

    out = option("--json")
    if out:
        Path(out).write_text(json.dumps(
            {"tag": tag, "offset": offset, "count": len(rows),
             "note": "32-byte rows [scale, quat xyzw, pos xyz]. Row order is NOT the global "
                     "bone index used by armour weights.",
             "bones": rows}, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
