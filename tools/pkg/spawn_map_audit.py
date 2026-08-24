"""Answer offline which destinations the world-population batch can fill, and why the rest cannot.

`spawn_panel.cpp: step_batch()` fills a destination in two stages, and only its *second* stage is
reported as a count with no names attached:

1. walk the destination's authored placements out of the packages (`skippedNoPlacements` on empty)
2. narrow the package-wide entity roster to combatants this world hosts (`skippedNoRoster` on empty)

Stage 2 is pure string matching over two tables that are already on disk - the scenario rows in
`build_data.bin` and the generated names in `EntityNames.json` - so it can be answered here, with
no launch, for every installed destination at once. Stage 1 needs the block keys and stays in the
game; this tool deliberately does not guess at it, and reports the two stages separately so a
failure is never attributed to the wrong one.

The filters below are transcribed from `spawn_panel.cpp` rather than reinvented. If that file's
tables move, this one is wrong in exactly the way it is meant to catch, so it prints the constants
it used.

Usage:
    python spawn_map_audit.py              # summary plus every destination with no combatants
    python spawn_map_audit.py --tokens     # group the failures by the key they matched on
    python spawn_map_audit.py --roster     # what the named roster actually contains, by faction
    python spawn_map_audit.py --show edz   # the combatants one destination would be filled with
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import build_cache

HERE = Path(__file__).resolve().parent
PANEL = HERE.parent.parent / "Sunrise" / "src" / "server" / "ui" / "spawn" / "spawn_panel.cpp"
NAMES = Path(r"C:\Sunrise\bin\x64\Sunrise\EntityNames.json")


def marker_lists() -> dict[str, object]:
    """@return The panel's own marker tables, parsed out of the source it filters with.

    Transcribing these by hand is how the audit and the game drift apart, so they are read. The
    faction table is a brace-nested initializer, which is why it is pulled apart by hand rather
    than with one expression.
    """
    text = PANEL.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)

    def flat(name: str) -> list[str]:
        match = re.search(rf"{name}\{{(.*?)\}};", text, re.S)
        if not match:
            raise SystemExit(f"{name} is no longer in spawn_panel.cpp - the audit is stale")
        return re.findall(r'"([^"]*)"', match.group(1))

    factions_body = re.search(r"kFactions\{(.*?)\n\};", text, re.S)
    if not factions_body:
        raise SystemExit("kFactions is no longer in spawn_panel.cpp - the audit is stale")
    factions: list[tuple[str, list[str]]] = []
    for entry in re.finditer(r'Faction\{"(\w+)",\s*\{(.*?)\}\}', factions_body.group(1), re.S):
        factions.append((entry.group(1), re.findall(r'"([^"]*)"', entry.group(2))))

    worlds_body = re.search(r"kWorldFactions\{(.*?)\n\};", text, re.S)
    if not worlds_body:
        raise SystemExit("kWorldFactions is no longer in spawn_panel.cpp - the audit is stale")
    worlds: list[tuple[str, list[int]]] = []
    for entry in re.finditer(r'WorldFactions\{"(\w+)",\s*\{([^}]*)\}\}', worlds_body.group(1)):
        indices = [t.strip() for t in entry.group(2).split(",")]
        worlds.append((entry.group(1), [int(t) for t in indices if t.isdigit()]))

    return {
        "factions": factions,
        "worlds": worlds,
        "champions": flat("kChampionMarkers"),
        "bosses": flat("kBossMarkers"),
        "vehicles": flat("kVehicleMarkers"),
    }


def leading_word(name: str) -> str:
    """@return The part before the first underscore, or the whole name when that is under 3."""
    word = name.split("_", 1)[0]
    return word if len(word) >= 3 else name


def scenarios() -> list[tuple[str, str, int]]:
    """@return (name, spawn stem, tag) for every installed destination, from the build cache."""
    data, _version, _header, blocks, consts, arrays = build_cache.layout()
    bodies = build_cache.structs()
    fields = build_cache.field_map("ScenarioRecord", bodies, consts, arrays)
    base, count, size, _ = blocks["scenarios"]
    out = []
    for index in range(count):
        row = base + index * size
        name_at, _e, _s, name_cap = fields["name"]
        stem_at, _e, _s, stem_cap = fields["spawnStem"]
        name_len = build_cache.read_field(data, row, fields["nameLength"])
        stem_len = build_cache.read_field(data, row, fields["spawnStemLength"])
        tag = build_cache.read_field(data, row, fields["tag"])
        name = data[row + name_at: row + name_at + min(name_len, name_cap)].decode(
            "ascii", "replace")
        stem = data[row + stem_at: row + stem_at + min(stem_len, stem_cap)].decode(
            "ascii", "replace")
        out.append((name, stem, tag))
    return out


def roster() -> list[str]:
    """@return Every entity name the generated cache holds, which is all a batch can draw from."""
    if not NAMES.exists():
        raise SystemExit(f"{NAMES} is absent - launch once with the Spawn page open to write it")
    # Localized aliases carry bytes that are not valid UTF-8, so the file is decoded leniently.
    # Only the ASCII dev names matter to the markers below, and those survive intact.
    document = json.loads(NAMES.read_bytes().decode("utf-8", "replace").lstrip("﻿"))
    return [value[0] for value in document["entities"].values() if value]


TABLES = marker_lists()
FACTIONS: list[tuple[str, list[str]]] = TABLES["factions"]
WORLDS: list[tuple[str, list[int]]] = TABLES["worlds"]


def any_marker(name: str, markers: list[str]) -> bool:
    low = name.lower()
    return any(marker and marker in low for marker in markers)


def excluded(name: str) -> bool:
    """The panel's three exclusion checkboxes all default on, so a batch runs with all three."""
    return (any_marker(name, TABLES["vehicles"]) or any_marker(name, TABLES["champions"])
            or any_marker(name, TABLES["bosses"]))


def faction_of(name: str) -> str | None:
    for label, markers in FACTIONS:
        if any_marker(name, markers):
            return label
    return None


def is_combatant(name: str) -> bool:
    return faction_of(name) is not None and not excluded(name)


def world_faction(name: str, token: str) -> bool:
    """@return True when this entity belongs to a faction the token's world hosts."""
    found = None
    for stem, indices in WORLDS:
        if stem in token.lower():
            found = indices
            break
    if found is None:
        return True  # An unmapped world accepts every faction, by design.
    low = name.lower()
    return any(marker and marker in low
               for index in found if index < len(FACTIONS)
               for marker in FACTIONS[index][1])


def roster_combatants() -> list[tuple[int, str]]:
    """@return (tag, name) for every named entity that passes the panel's combatant filters."""
    document = json.loads(NAMES.read_bytes().decode("utf-8", "replace").lstrip("﻿"))
    return [(int(tag, 16), value[0])
            for tag, value in document["entities"].items()
            if value and is_combatant(value[0])]


def main() -> None:
    factions = FACTIONS
    worlds = WORLDS
    tables = TABLES
    names = roster()
    combatants = [name for name in names if is_combatant(name)]
    rows = scenarios()

    if "--roster" in sys.argv:
        print(f"{len(names):,} named entities, {len(combatants):,} of them combatants "
              f"after the three exclusions\n")
        for label, _markers in factions:
            held = [n for n in combatants if faction_of(n) == label]
            print(f"{label:<8} {len(held):>4}  {', '.join(sorted(held)[:6])}"
                  f"{' ...' if len(held) > 6 else ''}")
        dropped = [n for n in names if faction_of(n) is not None and excluded(n)]
        print(f"\nexcluded {len(dropped)} named combatants as vehicle/champion/boss")
        return

    if "--show" in sys.argv:
        wanted = sys.argv[sys.argv.index("--show") + 1].lower()
        for name, stem, _tag in rows:
            if wanted not in name.lower():
                continue
            token = leading_word(stem or name)
            fill = [n for n in combatants if world_faction(n, token)]
            print(f"{name}  stem='{stem}'  token='{token}'  -> {len(fill)} combatants")
            for entry in sorted(fill)[:24]:
                print(f"    {entry}")
        return

    filled: list[tuple[str, str, int]] = []
    empty: list[tuple[str, str]] = []
    for name, stem, _tag in rows:
        token = leading_word(stem or name)
        count = sum(1 for n in combatants if world_faction(n, token))
        (filled if count else empty).append(
            (name, token, count) if count else (name, token))

    print(f"{len(names):,} named entities -> {len(combatants):,} combatants after exclusions")
    print(f"{len(rows)} destinations: {len(filled)} would find a roster, "
          f"{len(empty)} would not (`skippedNoRoster`)")
    print("Anything beyond this is stage 1, `skippedNoPlacements`, which needs the block keys.\n")

    if "--tokens" in sys.argv:
        grouped: dict[str, list[str]] = {}
        for name, token in empty:
            grouped.setdefault(token, []).append(name)
        for token, held in sorted(grouped.items(), key=lambda pair: -len(pair[1])):
            mapped = next((stem for stem, _i in worlds if stem in token.lower()), None)
            where = f"matched world '{mapped}'" if mapped else "no world entry - accepts all"
            print(f"{token:<24} {len(held):>4} destinations  ({where})")
            for entry in sorted(held)[:4]:
                print(f"    {entry}")
        return

    for name, token in sorted(empty):
        print(f"  {name:<44} key '{token}'")


if __name__ == "__main__":
    main()
