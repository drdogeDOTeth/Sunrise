"""Stand the Tower's cast up at its authored spawn points, using only what already ships.

**What this is for.** The Tower loads but is dead: no Zavala, no Banshee, nothing to walk up to, so
no vendor interaction is ever even attempted - the client never sends opcode 901 because there is
nobody to send it about. Everything downstream - dialogue, inventories, purchases - needs a body in
the world first, and this is the cheapest way to find out whether a body is enough.

**Why it needs no new code.** The populator already places arbitrary entity tags at authored
positions from a map file, and the build cache already holds 5 spawn sets / 48 points for
`city_tower_social_d2`. So the whole probe is a map file whose tags are NPCs rather than
combatants. `spawn_maps_build.py` skips social spaces deliberately - nothing in the Tower expects a
combatant - which is exactly why the Tower has no map today and why writing one by hand is safe:
it adds a file that did not exist rather than overwriting one that did.

**What it will tell us**, in one launch, from the Spawn page's own status line:

  * whether these tags stream in the Tower at all (`Bodies: N of the map's M entities`),
  * whether an NPC entity renders as its character rather than a placeholder,
  * whether walking up to one offers any interaction.

A tag the world cannot produce costs nothing: the populator substitutes a resident one, so a zero
in that line is the finding, not a crash.

**Both flavours are included.** The table names an `ai_*` actor and a bare model for most of the
cast, and which one the game treats as the interactable NPC is not established - so both go in and
the status line reports which stream.

Usage:
    python tower_npc_map.py            # dry run: what it would write
    python tower_npc_map.py --write
    python tower_npc_map.py --clean    # remove just this map
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from game_paths import artifact_dir
from spawn_maps_build import cache_tables, declutter, loads_package, safe_name

ARTIFACTS = artifact_dir()
DESTINATION = "city_tower_social_d2"
NAMES = ARTIFACTS / "EntityNames.json"

# The Tower cast, by the names the extracted entity table uses. Ordered so the first few are the
# ones a player walks past first, because the populator fills points in order.
CAST = (
    "ai_zavala", "zavala",
    "ai_gunsmith", "gunsmith",
    "ai_cryptarch", "cryptarch",
    "ai_ikora_rey", "ikora_rey",
    "ai_shaxx_pvp_crucible_vendor", "shaxx",
    "ai_kadi_55_30_postmaster",
    "ai_tess_everis", "tess_everis",
    "ai_amanda_holliday",
    "tower_ghost", "tower_ghost_plaza",
)


def entity_tags() -> dict[str, int]:
    """@return Tag by exact entity name, for the cast this map places."""
    doc = json.loads(NAMES.read_bytes().decode("utf-8", "replace"))
    table = doc.get("entities", {})
    found: dict[str, int] = {}
    for tag, names in table.items():
        if not isinstance(names, list):
            names = [names]
        for name in names:
            if isinstance(name, str) and name in CAST:
                found.setdefault(name, int(tag, 16))
    return found


def tower_points() -> list[tuple[float, float, float]]:
    """@return Every authored spawn position this destination may load, deduplicated."""
    stems, hashes, points, destinations = cache_tables()
    destination = next((d for d in destinations if d["name"] == DESTINATION), None)
    if destination is None:
        raise SystemExit(f"{DESTINATION} is not in the build cache")
    stem = stems.get(destination["stem"])
    if stem is None:
        raise SystemExit(f"{DESTINATION} has no map stem ({destination['stem']!r})")
    gathered: list[tuple[float, float, float]] = []
    for row in hashes:
        if row["stem"] != stem or not loads_package(destination, row):
            continue
        gathered.extend(points.get((stem, row["value"]), []))
    return declutter(gathered)


def main() -> None:
    target = ARTIFACTS / f"spawn_map_{safe_name(DESTINATION)}.txt"
    if "--clean" in sys.argv:
        if target.is_file():
            target.unlink()
            print(f"removed {target.name}")
        else:
            print(f"{target.name} is not there")
        return

    tags = entity_tags()
    missing = [name for name in CAST if name not in tags]
    print(f"cast: {len(tags)} of {len(CAST)} named entities found")
    for name in CAST:
        if name in tags:
            print(f"  {tags[name]:08X}  {name}")
    if missing:
        print(f"  not in this install: {', '.join(missing)}")
    if not tags:
        raise SystemExit("no cast entity resolved; nothing to write")

    positions = tower_points()
    print(f"\n{DESTINATION}: {len(positions)} authored positions")
    if not positions:
        raise SystemExit("no positions; nothing to write")

    ordered = [tags[name] for name in CAST if name in tags]
    lines = [
        f"{ordered[index % len(ordered)]:08X} {p[0]:.3f} {p[1]:.3f} {p[2]:.3f}"
        for index, p in enumerate(positions)
    ]
    print(f"would write {len(lines)} rows, cycling {len(ordered)} entities")
    print("  first rows:")
    for line in lines[:6]:
        print(f"    {line}")

    if "--write" not in sys.argv:
        print("\ndry run - pass --write")
        return
    if target.exists():
        raise SystemExit(f"{target.name} already exists; --clean it first rather than overwrite")
    target.write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"\nwrote {target}")


if __name__ == "__main__":
    main()
