"""Bisect the patch layers to find the one that breaks a world, a stack at a time.

**Why the unit is a stack, not a file.** Layers truncate only: pulling one out of the middle of a
package's stack dangles everything above it and the game spins at character select with no error.
But different package ids never reference each other, so a whole id's stack is safe to include or
exclude as a unit. The 106 layers of v25 are 22 such stacks, three of them very deep (037c has 28,
037d 27, 0698 25 - the custom mesh) and the rest one to three.

**What this does.** Writes a snapshot holding exactly the stacks asked for, then `known_good.py`
moves the files and verifies the result. Snapshots are written under a 1970 date so they always
sort before the real ones and a bare `--restore` still means the newest real snapshot.

Confirmed 2026-08-24: with all 106 layers atticked the Moon, and two more worlds, load in 6.0s.
With them live the Moon spends 33-39s in the load and is killed by the client's own activity-host
timeout. So one of these stacks is the cause. Fourteen of the 22 are read during the Moon load,
which is a prior and not a proof - a layer the load never reads still joins the package's patch
table at registration.

Usage:
    python bisect_layers.py --list              # the stacks, and which the Moon reads
    python bisect_layers.py --live-read-set     # the 14 stacks that load reads
    python bisect_layers.py --live-none
    python bisect_layers.py --live-all
    python bisect_layers.py --live w64_sandbox_037d,w64_globals_0211
    python bisect_layers.py --live-except w64_sandbox_037c,w64_sandbox_0698

Then:
    python known_good.py --restore --snapshot=19700102-bisect.json
    python known_good.py --restore              # back to v25 when finished
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

STORE = Path(__file__).with_name("known_good")
OUTPUT = STORE / "19700102-bisect.json"
NAME = re.compile(r"^(w64_.+?)_([0-9a-f]{4})_(\d+)\.pkg$")
# Package files the Moon load was measured reading, from the F8 capture of 2026-08-24.
TRACE = Path(__file__).with_name("captures") / "moon_trace_files.txt"


def newest_real() -> Path:
    """@return The newest snapshot that is not one of this tool's own."""
    snaps = sorted(p for p in STORE.glob("*.json") if not p.name.startswith("1970"))
    if not snaps:
        raise SystemExit(f"no real snapshots in {STORE}")
    return snaps[-1]


def layers(snapshot: Path) -> list[dict]:
    """@return Every layer entry in a snapshot, whatever shape it nests them in."""
    found: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("name"), str) and node["name"].endswith(".pkg"):
                found.append(node)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(snapshot.read_text(encoding="utf-8")))
    return found


def stacks(entries: list[dict]) -> dict[str, list[dict]]:
    """@return Layer entries grouped by `family_packageid`, each sorted bottom-up."""
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for entry in entries:
        match = NAME.match(os.path.basename(entry["name"].replace("\\", "/")))
        if match is None:
            raise SystemExit(f"cannot parse layer name: {entry['name']}")
        grouped[f"{match.group(1)}_{match.group(2)}"].append(entry)
    for key, items in grouped.items():
        items.sort(key=lambda e: int(NAME.match(os.path.basename(e["name"])).group(3)))
    return dict(sorted(grouped.items()))


def read_during_load() -> set[str]:
    """@return Layer file names the traced Moon load read, empty when no capture is stored."""
    if not TRACE.exists():
        return set()
    return {line.strip() for line in TRACE.read_text(encoding="utf-8").splitlines()
            if line.strip()}


def write(chosen: dict[str, list[dict]], note: str) -> None:
    live = [entry for items in chosen.values() for entry in items]
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "saved": datetime(1970, 1, 2).isoformat(),
        "note": note,
        "count": len(live),
        "live": sorted(live, key=lambda e: e["name"]),
    }, indent=2), encoding="utf-8")
    print(f"{OUTPUT.name}: {len(live)} layer(s) across {len(chosen)} stack(s)")
    for key, items in chosen.items():
        print(f"  {key}: {len(items)}")
    print(f"\n  python tools/pkg/known_good.py --restore --snapshot={OUTPUT.name}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    source = newest_real()
    grouped = stacks(layers(source))
    touched = read_during_load()

    if "--list" in args:
        print(f"{sum(len(v) for v in grouped.values())} layers, "
              f"{len(grouped)} stacks, from {source.name}")
        if not touched:
            print(f"(no read capture at {TRACE}, so the read column is blank)")
        for key, items in grouped.items():
            names = {os.path.basename(e["name"]) for e in items}
            mark = "READ" if names & touched else "    "
            patches = ",".join(NAME.match(os.path.basename(e["name"])).group(3) for e in items)
            print(f"  {mark}  {key:<22} {len(items):>2} layer(s)  [patch {patches}]")
        return

    def pick(keys: set[str], note: str) -> None:
        unknown = keys - set(grouped)
        if unknown:
            raise SystemExit(f"unknown stack(s): {sorted(unknown)}")
        write({k: v for k, v in grouped.items() if k in keys}, note)

    if "--live-all" in args:
        pick(set(grouped), f"bisect: all {len(grouped)} stacks live (equals {source.name})")
    elif "--live-none" in args:
        pick(set(), "bisect: no layers live (stock control)")
    elif "--live-read-set" in args:
        if not touched:
            raise SystemExit(f"no read capture stored at {TRACE}")
        keys = {k for k, v in grouped.items()
                if {os.path.basename(e["name"]) for e in v} & touched}
        pick(keys, f"bisect: the {len(keys)} stacks the Moon load reads")
    else:
        for flag, invert in (("--live", False), ("--live-except", True)):
            if flag in args:
                index = args.index(flag)
                if index + 1 >= len(args):
                    raise SystemExit(f"{flag} needs a comma-separated list of stacks")
                named = {s.strip() for s in args[index + 1].split(",") if s.strip()}
                keys = set(grouped) - named if invert else named
                pick(keys, f"bisect: {flag} {','.join(sorted(named))}")
                return
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
