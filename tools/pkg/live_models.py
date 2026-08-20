"""
Identifies which models the running game actually drew, from an F8 capture.

## The problem this solves

Targeting used to be a search. `w64_investment_*` holds 343 entity models, the install holds 21,240,
and the only feedback available was a blank probe: zero a model's index counts, relaunch, and see if
anything vanished. That is one bit of information per game launch, and it was answered against the
wrong screen for two sessions - the inspect paperdoll and the in-world Guardian are different render
paths, so probes judged by "did the tunic change" never tested the in-world body at all.

## The instrument

`model_class_trace.cpp` reads a bounded window of each live object and logs every dword that falls in
Tiger handle range: `r9tags=` on `stage=lookup`, `paytags=`/`dsttags=` on `stage=resource`. Handles
survive the process, so they can be resolved offline against the entry tables, which are plain even
when the bodies they describe are encrypted.

## What `r9` turned out to be

**Not** the `SEntityModel` blob. Across 878 instance windows in the 2026-08-19 Tower capture,
**zero** named a model handle - but 248 carried **vertex buffer headers**. `r9` is a render-mesh
structure.

That is better than naming the model, because a buffer header belongs to exactly one mesh of exactly
one model. So `--match` inverts it: parse every dumped model's mesh table, collect the header tags it
claims, and intersect against the live set. The result is an **exact identity**, not a ranking. Its
only limit is coverage - a model whose header was never dumped cannot match, so a miss means "not yet
dumped", never "not the target".

## Trust r9tags, distrust paytags

One resource-event `paytags` window held **1,522 handles** - a whole serialized reference table
including orbit gizmos and Tower NPCs. Presence there proves reference, not identity. Only
per-instance `r9` windows are tight enough to act on, which is why `--match` reports them separately.

Usage:
    python live_models.py                    # resolve every captured handle, by class and package
    python live_models.py --match            # which dumped models owned the live buffers
    python live_models.py --log other.log --match
"""
from __future__ import annotations

import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

from parse_models import INDEX, Model, TAG_MAX, TAG_MIN, lookup, option
from tigerpkg import TAG_BASE, TAG_ENTRY_BITS

LOG = Path(r"C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log")
DUMPS = [Path(r"C:\Sunrise\bin\x64\Sunrise\dump"),
         Path(r"C:\Sunrise\bin\x64\Sunrise\dump_models")]

ENTITY_MODEL = 0x808073A5
VERTEX_HEADER_SIZE = 12
INDEX_HEADER_SIZE = 24
# A standing body: feet on the floor, head between 1.5 and 2.3 m. Far sharper than span plus centre
# height, which cannot separate a torso piece from a whole body.
BODY_FLOOR = (-0.25, 0.30)
BODY_TOP = (1.5, 2.3)

FIELD_RE = re.compile(
    r"\b(r9tags|paytags|dsttags)=((?:0x[0-9A-Fa-f]{8})(?:,0x[0-9A-Fa-f]{8})*)")
R9_RE = re.compile(r"\br9=0x([0-9A-Fa-f]+)")

NAME = {pid: path.stem.rsplit("_", 1)[0].replace("w64_", "") for pid, path in INDEX.items()}


def where(tag: int) -> str:
    """@return Short package name owning a tag, without the `w64_` prefix."""
    return NAME.get((tag - TAG_BASE) >> TAG_ENTRY_BITS, f"pkg?{(tag - TAG_BASE) >> TAG_ENTRY_BITS:04X}")


def read_capture(path: Path) -> tuple[dict[str, set[int]], dict[str, Counter]]:
    """@return `(per-instance r9 windows, handles per field name)`."""
    windows: dict[str, set[int]] = defaultdict(set)
    fields: dict[str, Counter] = defaultdict(Counter)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "ev=model_class_trace" not in line:
            continue
        match = R9_RE.search(line)
        key = match.group(1) if match else None
        for field, blob in FIELD_RE.findall(line):
            tags = [int(part, 16) for part in blob.split(",")]
            fields[field].update(tags)
            if field == "r9tags" and key:
                windows[key].update(tags)
    return windows, fields


def dumped_models() -> dict[int, dict]:
    """@return Parsed mesh tables for every dumped `SEntityModel`, keyed by tag."""
    out: dict[int, dict] = {}
    for folder in DUMPS:
        for path in folder.glob("tag_*.bin"):
            try:
                tag = int(path.stem[4:], 16)
            except ValueError:
                continue
            if tag in out:
                continue
            entry = lookup(tag)
            if entry is None or entry.reference != ENTITY_MODEL:
                continue
            data = path.read_bytes()
            if len(data) < 0xA0:
                continue
            (declared,) = struct.unpack_from("<q", data, 0)
            if declared != len(data):
                continue
            model = Model(tag, data)
            if not model.meshes or not model.scale:
                continue
            headers = {value
                       for mesh in model.meshes
                       for value in (mesh.positions, mesh.texcoords, mesh.weights, mesh.indices)
                       if TAG_MIN <= value <= TAG_MAX}
            half = max(abs(value) for value in model.scale[:3])
            out[tag] = {
                "headers": headers,
                "floor": model.translation[2] - half,
                "top": model.translation[2] + half,
                "meshes": len(model.meshes),
                "positions": sum(mesh.position_bytes for mesh in model.meshes),
            }
    return out


def is_body(info: dict) -> bool:
    return (BODY_FLOOR[0] <= info["floor"] <= BODY_FLOOR[1]
            and BODY_TOP[0] <= info["top"] <= BODY_TOP[1])


def main() -> None:
    log = Path(option("--log", str(LOG)))
    if not log.is_file():
        raise SystemExit(f"no capture log at {log}")
    windows, fields = read_capture(log)
    if not fields:
        raise SystemExit(
            f"no model_class_trace tag windows in {log}\n"
            "Capture one: launch, F8 in orbit, travel, F8 off, close the game.")

    live_r9 = {tag for tags in windows.values() for tag in tags}
    live_all = {tag for counter in fields.values() for tag in counter}
    print(f"{log.name}: {len(windows):,} instance windows, "
          f"{len(live_r9):,} handles from r9, {len(live_all):,} from all fields")
    for field, counter in fields.items():
        print(f"  {field:<8} {sum(counter.values()):7,} handles, {len(counter):5,} distinct")

    if "--match" not in sys.argv:
        classes: Counter[int] = Counter()
        packages: Counter[str] = Counter()
        unresolved = 0
        for counter in fields.values():
            for tag, count in counter.items():
                entry = lookup(tag)
                if entry is None:
                    unresolved += count
                    continue
                classes[entry.reference] += count
                packages[where(tag)] += count
        print("\n--- classes resolved ---")
        for cls, count in classes.most_common(15):
            print(f"{count:8,}  0x{cls:08X}")
        print(f"{unresolved:8,}  unresolved (in range but not a live entry)")
        print("\n--- packages ---")
        for package, count in packages.most_common(15):
            print(f"{count:8,}  {package}")
        print("\nRun with --match to name the models that owned these buffers.")
        return

    models = dumped_models()
    print(f"\n{len(models):,} dumped models with parsed mesh tables")
    rows = []
    for tag, info in models.items():
        hit_all = info["headers"] & live_all
        if not hit_all:
            continue
        rows.append((len(info["headers"] & live_r9),
                     len(hit_all) / max(1, len(info["headers"])),
                     len(hit_all), len(info["headers"]), tag, info))
    rows.sort(reverse=True)

    print("\n--- models whose buffer headers were live ---")
    print(f"{'r9':>4} {'cov':>6} {'hit':>4}/{'own':<4} {'tag':>10} {'package':<20} "
          f"{'meshes':>6} {'floor':>7} {'top':>7} {'pos B':>10}  body?")
    for hits, coverage, hit, owned, tag, info in rows[:50]:
        print(f"{hits:4} {coverage:6.0%} {hit:4}/{owned:<4} 0x{tag:08X} {where(tag):<20} "
              f"{info['meshes']:6} {info['floor']:7.3f} {info['top']:7.3f} "
              f"{info['positions']:10,}  {'<<< BODY' if is_body(info) else ''}")
    bodies = [row for row in rows if is_body(row[5])]
    print(f"\n{len(rows)} models had live buffers; {len(bodies)} are body-shaped")
    print("Byte-identical geometry repeated across several globals packages is the player-body "
          "signature;\na set of differently-sized humanoids in one package is an NPC crowd.")


if __name__ == "__main__":
    main()
