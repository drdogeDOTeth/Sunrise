"""Write every destination's spawn map offline, from `build_data.bin` alone.

The in-game batch (`spawn_panel.cpp: step_batch`) builds these files by walking the authored
placement chain out of the packages, which needs the block keys and therefore the running game. It
also reports its failures as two bare counts. This tool takes the other road: the build cache
already holds the game's **own spawn sets** - 8,892 authored positions grouped by map stem, with
the package rules that say which destination may load each one - so the same map files can be
written here, for every destination at once, with no launch.

Two sources, one output. The batch's placement walk finds where props and encounters were placed;
the spawn-set catalog is where the game itself spawns things. The catalog is the better source and
the only one available offline, and both end up as the same file: a tag and a position per line,
read back by `spawn_keybind_store.cpp: read_map`.

The tag on each line is not the authored entity - the batch discards those too. It is a combatant
this world hosts, chosen from the named roster by the same faction rules the panel applies, and
round-robined across the points by the batch's own expression.

Those tags are a **pool**, not an assignment, and nothing here can do better: entity definitions
live in shared packages, so neither a tag's package id nor the destination's package list says
which entities a world streams. `place_from_map` sweeps the pool at runtime and fills a point whose
authored body is absent with one the world does produce. So a wrong guess here costs variety, not
an empty world - and the panel's `Bodies: N of M` line reports how much of a pool survived.

Usage:
    python spawn_maps_build.py                     # dry run: what would be written, and why not
    python spawn_maps_build.py --write             # write them into the artifact directory
    python spawn_maps_build.py --write --only luna # just the destinations whose name matches
    python spawn_maps_build.py --write --spacing 0 # keep every point, clusters and all
    python spawn_maps_build.py --clean             # remove every map this tool wrote
"""
from __future__ import annotations

import sys
from pathlib import Path

import build_cache
import spawn_map_audit as audit

ARTIFACTS = Path(r"C:\Sunrise\bin\x64\Sunrise")
# `spawn_keybind_store.h: kMapCapacity`. A longer file is simply truncated on read, so the cap is
# applied here instead of shipping points the game will silently drop.
MAP_CAPACITY = 2048

# Destinations left without a map unless `--all` is passed. Every other world is somewhere the
# player is already fighting; these are the ones a hostile spawn turns from a feature into a
# broken load. The Tower is the specific worry - it is the most fragile world in this install and
# nothing in it expects a combatant.
SOCIAL_MARKERS = ("social", "city_tower", "cine_")

# Minimum distance between kept points, in world units. See `thin`. Six leaves the distinct
# locations intact while collapsing the squad clusters that otherwise spawn as a heap.
DEFAULT_SPACING = 6.0


def cache_tables():
    """@return Everything one pass needs out of the build cache, decoded once."""
    data, _version, _header, blocks, consts, arrays = build_cache.layout()
    bodies = build_cache.structs()

    def read(record: str, key: str):
        fields = build_cache.field_map(record, bodies, consts, arrays)
        base, count, size, _ = blocks[key]
        return fields, base, count, size

    def text(row: int, fields, member: str, length_member: str) -> str:
        at, _element, _size, capacity = fields[member]
        length = build_cache.read_field(data, row, fields[length_member])
        return data[row + at: row + at + min(length, capacity)].decode("ascii", "replace")

    sf, sb, sc, ss = read("SpawnStemRecord", "spawnStems")
    stems = {}
    for index in range(sc):
        row = sb + index * ss
        stems[text(row, sf, "name", "nameLength")] = index

    hf, hb, hc, hs = read("SpawnNameHashRecord", "spawnNameHashes")
    hashes = []
    for index in range(hc):
        row = hb + index * hs
        count = build_cache.read_field(data, row, hf["activityPackageCount"])
        packages = build_cache.read_field(data, row, hf["activityPackages"])
        mask_bytes = build_cache.read_field(data, row, hf["bubbleMask"])
        mask = int.from_bytes(bytes(mask_bytes), "little")
        hashes.append({
            "value": build_cache.read_field(data, row, hf["value"]),
            "stem": build_cache.read_field(data, row, hf["stemIndex"]),
            "inMap": build_cache.read_field(data, row, hf["inMapPackage"]),
            "unbound": build_cache.read_field(data, row, hf["unbound"]),
            "packages": set(packages[:count]),
            "mask": mask,
        })

    pf, pb, pc, ps = read("SpawnPointRecord", "spawnPoints")
    points: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    for index in range(pc):
        row = pb + index * ps
        key = (build_cache.read_field(data, row, pf["stemIndex"]),
               build_cache.read_field(data, row, pf["nameHash"]))
        points.setdefault(key, []).append(build_cache.read_field(data, row, pf["position"]))

    df, db, dc, ds = read("ScenarioRecord", "scenarios")
    destinations = []
    for index in range(dc):
        row = db + index * ds
        count = build_cache.read_field(data, row, df["packageCount"])
        packages = build_cache.read_field(data, row, df["packages"])
        bubbles = build_cache.read_field(data, row, df["bubbleCount"])
        indices = build_cache.read_field(data, row, df["bubbleMapIndices"])
        destinations.append({
            "name": text(row, df, "name", "nameLength"),
            "stem": text(row, df, "spawnStem", "spawnStemLength"),
            "packages": set(packages[:count]),
            "bubbles": set(indices[:bubbles]),
        })
    return stems, hashes, points, destinations


def thin(positions: list, separation: float) -> list:
    """Drops points that sit on top of a point already kept.

    The game's spawn sets are squad positions: three or four bodies within a couple of metres, meant
    to be used one set at a time. The populator fills every free point in its band at once, so a
    whole cluster becomes a heap. Measured on Nessus, 651 points collapse to 191 at eight metres and
    176 at twelve - so roughly seven in ten points are a duplicate of a neighbour, and the distinct
    locations survive almost untouched.

    @param positions Points in map order.
    @param separation Minimum distance between kept points, in world units. Zero keeps everything.
    """
    if separation <= 0.0:
        return positions
    limit = separation * separation
    kept: list = []
    for point in positions:
        close = False
        for held in kept:
            delta = ((point[0] - held[0]) ** 2 + (point[1] - held[1]) ** 2
                     + (point[2] - held[2]) ** 2)
            if delta < limit:
                close = True
                break
        if not close:
            kept.append(point)
    return kept


def loads_package(destination, row) -> bool:
    """The game's own rule, from `activity_destination_spawn_binding.cpp: loads_package`.

    A set in the map package belongs to every destination of that stem; otherwise the destination
    must name one of the activity packages that declares it.
    """
    if row["inMap"]:
        return True
    return bool(row["packages"] & destination["packages"])


def safe_name(destination: str) -> str:
    """The same substitution `spawn_keybind_store.cpp: map_path` applies, so the names line up."""
    return "".join(
        c if (c.isascii() and (c.isalnum() or c in "_-")) else "_" for c in destination[:95])


def main() -> None:
    write = "--write" in sys.argv
    separation = (float(sys.argv[sys.argv.index("--spacing") + 1])
                  if "--spacing" in sys.argv else DEFAULT_SPACING)
    only = sys.argv[sys.argv.index("--only") + 1].lower() if "--only" in sys.argv else ""

    if "--clean" in sys.argv:
        removed = 0
        for path in ARTIFACTS.glob("spawn_map_*.txt"):
            path.unlink()
            removed += 1
        print(f"removed {removed} map files from {ARTIFACTS}")
        return

    stems, hashes, points, destinations = cache_tables()
    combatants = audit.roster_combatants()

    written = 0
    total_points = 0
    no_stem: list[str] = []
    no_sets: list[str] = []
    no_roster: list[str] = []
    social: list[str] = []
    report: list[tuple[str, int, int]] = []

    for destination in destinations:
        name = destination["name"]
        if only and only not in name.lower():
            continue
        if "--all" not in sys.argv and any(mark in name for mark in SOCIAL_MARKERS):
            social.append(name)
            continue
        stem = destination["stem"]
        if not stem or stem not in stems:
            no_stem.append(name)
            continue
        stem_index = stems[stem]

        placed: list[tuple[float, float, float]] = []
        sets = 0
        for row in hashes:
            if row["stem"] != stem_index or not loads_package(destination, row):
                continue
            held = points.get((stem_index, row["value"]))
            if not held:
                continue
            sets += 1
            placed.extend(held)
        if not placed:
            no_sets.append(name)
            continue
        placed = thin(placed, separation)

        token = audit.leading_word(stem or name)
        fill = [tag for tag, entity in combatants if audit.world_faction(entity, token)]
        if not fill:
            no_roster.append(name)
            continue

        placed = placed[:MAP_CAPACITY]
        lines = []
        for index, position in enumerate(placed):
            # The batch's own round robin, kept so an offline map has the same character as one
            # the game would have written.
            tag = fill[(index * 7 + index // len(fill)) % len(fill)]
            lines.append(f"{tag:08X} {position[0]:.3f} {position[1]:.3f} {position[2]:.3f}")
        if write:
            (ARTIFACTS / f"spawn_map_{safe_name(name)}.txt").write_text(
                "\n".join(lines) + "\n", encoding="ascii", newline="\n")
        written += 1
        total_points += len(placed)
        report.append((name, sets, len(placed)))

    verb = "wrote" if write else "would write"
    print(f"{verb} {written} maps, {total_points:,} points, {separation:g}-unit spacing"
          f"{'' if write else '  (dry run - pass --write)'}")
    print(f"skipped: {len(social)} social spaces and cutscenes (pass --all to include), "
          f"{len(no_stem)} with no map stem, {len(no_sets)} whose stem offers them no set, "
          f"{len(no_roster)} with no combatant roster\n")
    for name, sets, count in sorted(report, key=lambda entry: -entry[2])[:20]:
        print(f"  {name:<44} {sets:>3} sets  {count:>5} points")
    if no_sets:
        print(f"\nno set loaded by the destination ({len(no_sets)}):")
        for name in sorted(no_sets)[:12]:
            print(f"  {name}")


if __name__ == "__main__":
    main()
