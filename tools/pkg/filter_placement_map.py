"""Cut a saved placement map down to the entities worth standing up, by name.

**Why this exists.** The Spawn page's *Extract authored placements* reads a destination's real
placement chain - authored tags at authored transforms - and for the Tower that is 1762 records.
But with *Combatants only* unchecked it keeps everything the chain names: crates, doors, lights,
terminals, decorative geometry. All of that is already in the world, so handing the whole set to
the populator spends its live budget respawning scenery and the vendors never come up. Filtering
in game would need a rebuild and a launch to learn what the tags even are; the saved map is plain
text and `EntityNames.json` names the tags offline, so the filter belongs here.

**What it reports.** Every distinct tag in the map with its count and its name, so the authored
cast of a destination can be read without guessing - which is the thing no tool has said yet.

**What it writes.** The same file, in the same `%08X x y z` format `spawn_keybind_store.cpp`
reads, holding only the rows whose entity name matches `--keep` (default: the `ai_` actors plus
ghosts and frames). The full extract is kept beside it as `.bak-full` so nothing is lost, and
duplicate placements of one tag at one spot collapse to a single row.

Usage:
    python filter_placement_map.py                       # report only
    python filter_placement_map.py --keep '^ai_'         # a different selection
    python filter_placement_map.py --write
    python filter_placement_map.py --restore             # put the full extract back
"""
from __future__ import annotations

import io
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from game_paths import artifact_dir
from spawn_maps_build import safe_name

ARTIFACTS = artifact_dir()
DESTINATION = "city_tower_social_d2"
NAMES = ARTIFACTS / "EntityNames.json"

# The default selection. `ai_*` is what the extracted table calls an actor; ghosts and frames are
# the Tower's ambient population and are wanted for the same reason.
DEFAULT_KEEP = r"^(ai_|.*_ghost|.*ghost_|.*frame.*|.*sweeper.*)"
# Two placements closer than this are the same authored spot named twice by overlapping slices.
MERGE_DISTANCE = 0.5


def entity_names() -> dict[int, list[str]]:
    """@return Every name the extracted table gives each tag. Localized aliases carry bad UTF-8."""
    doc = json.loads(NAMES.read_bytes().decode("utf-8", "replace"))
    table = doc.get("entities", {})
    named: dict[int, list[str]] = {}
    for tag, names in table.items():
        if not isinstance(names, list):
            names = [names]
        named[int(tag, 16)] = [n for n in names if isinstance(n, str)]
    return named


def read_map(path: Path) -> list[tuple[int, float, float, float]]:
    """@return One row per line, in file order. Malformed lines are skipped rather than fatal."""
    rows: list[tuple[int, float, float, float]] = []
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        try:
            rows.append((int(parts[0], 16), float(parts[1]), float(parts[2]), float(parts[3])))
        except ValueError:
            continue
    return rows


def best_name(names: list[str]) -> str:
    """@return The most useful of a tag's names: an `ai_` actor first, else the shortest ASCII."""
    actors = [n for n in names if n.startswith("ai_")]
    if actors:
        return min(actors, key=len)
    ascii_names = [n for n in names if n.isascii()]
    return min(ascii_names, key=len) if ascii_names else (names[0] if names else "")


def declutter(rows: list[tuple[int, float, float, float]]) -> list[tuple[int, float, float, float]]:
    """@return The rows with per-tag duplicates at one spot collapsed, keeping file order."""
    seen: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    kept: list[tuple[int, float, float, float]] = []
    for tag, x, y, z in rows:
        near = any((x - a) ** 2 + (y - b) ** 2 + (z - c) ** 2 < MERGE_DISTANCE ** 2
                   for a, b, c in seen[tag])
        if near:
            continue
        seen[tag].append((x, y, z))
        kept.append((tag, x, y, z))
    return kept


def main() -> None:
    target = ARTIFACTS / f"spawn_map_{safe_name(DESTINATION)}.txt"
    backup = target.with_suffix(".txt.bak-full")

    if "--restore" in sys.argv:
        if not backup.is_file():
            raise SystemExit(f"no {backup.name} to restore from")
        target.write_bytes(backup.read_bytes())
        print(f"restored {len(read_map(target))} rows from {backup.name}")
        return

    if not target.is_file():
        raise SystemExit(f"{target} is not there - extract and Save map first")

    # Once the full extract is banked, later runs filter from it rather than from a filtered file.
    source = backup if backup.is_file() else target
    rows = read_map(source)
    print(f"{source.name}: {len(rows)} rows")
    if not rows:
        raise SystemExit("nothing to filter")

    names = entity_names()
    counts = Counter(tag for tag, *_ in rows)
    named = sum(1 for tag in counts if tag in names)
    print(f"{len(counts)} distinct tags, {named} of them named by EntityNames.json\n")

    keep = re.compile(sys.argv[sys.argv.index("--keep") + 1] if "--keep" in sys.argv
                      else DEFAULT_KEEP)

    print("=== every distinct tag, most placed first ===")
    matched: set[int] = set()
    for tag, count in counts.most_common():
        label = best_name(names.get(tag, []))
        hit = bool(label) and keep.match(label) is not None
        if hit:
            matched.add(tag)
        print(f"  {'KEEP' if hit else '    '}  {tag:08X}  x{count:<5}  {label or '(unnamed)'}")

    selected = [row for row in rows if row[0] in matched]
    merged = declutter(selected)
    print(f"\nkeep pattern {keep.pattern!r} matches {len(matched)} tags, "
          f"{len(selected)} rows, {len(merged)} after merging duplicates")
    for line in merged[:12]:
        print(f"    {line[0]:08X} {line[1]:.3f} {line[2]:.3f} {line[3]:.3f}"
              f"   {best_name(names.get(line[0], []))}")
    if len(merged) > 12:
        print(f"    ... {len(merged) - 12} more")

    if "--write" not in sys.argv:
        print("\ndry run - pass --write")
        return
    if not merged:
        raise SystemExit("nothing matched; refusing to write an empty map")
    if not backup.is_file():
        backup.write_bytes(target.read_bytes())
        print(f"\nbanked the full extract as {backup.name}")
    body = "".join(f"{t:08X} {x:.3f} {y:.3f} {z:.3f}\n" for t, x, y, z in merged)
    target.write_text(body, encoding="ascii")
    print(f"wrote {len(merged)} rows to {target}")


if __name__ == "__main__":
    main()
