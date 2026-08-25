# World population

Upstream PR #58 fills a destination with combatants from the client. The client binary carries the
whole combat loop, so a spawned enemy tracks the player, shoots back, takes damage, deals damage and
drops ammo with nothing else wired up — confirmed in game 2026-08-23.

The populator has two modes:

| mode | where entities appear |
|---|---|
| roaming ring | in a band around the player, wherever they go |
| **map** (`use_map`) | at the destination's own authored positions, from a saved map file |

Map mode is the one worth having, and it needs a map file per destination. This document covers how
those get made — including the offline route, which is faster, covers more destinations and needs no
launch.

---

## Configuration

`C:\Sunrise\bin\x64\Sunrise\population.json`. Written by the Spawn page, and hand-editable; the
reader layers the file over the built-in defaults, so a missing or malformed key keeps its default
rather than stopping the module. Twelve keys, exactly as `population_settings_store.cpp` spells
them:

```json
{
  "enabled": true,
  "auto_on_load": true,
  "use_map": true,
  "snap_to_ground": true,
  "target": 12,
  "interval_ms": 600,
  "respawn_delay_ms": 45000,
  "minimum_radius": 18.000,
  "maximum_radius": 55.000,
  "forget_radius": 140.000,
  "lift": 0.500,
  "scale": 1.000
}
```

`auto_on_load` loads the arriving destination's map once per arrival and arms the populator from it.
It arms `enabled` from whether that map had any points, so **a destination with no map file stays
empty**: with `use_map` on and no maps written, nothing spawns anywhere. That is the whole trap in
this feature, and it looks identical to the mod being broken.

Write the file without a BOM. `Set-Content -Encoding UTF8` adds one, and the reader's key search
then misses the first key.

---

## Where a map comes from

A map file is `spawn_map_<destination>.txt` in the artifact directory, one point per line:

```
80BC8E3F 583.318 -336.875 -18.171
```

A tag and three floats — `%08X %.3f %.3f %.3f`, read back by `sscanf` as `%x %f %f %f`. Anything
past 2048 lines (`kMapCapacity`) is dropped on read. Z is up: `place_from_map` grounds a point by
`ground[2]` and applies `lift` to the same lane.

The tag on a line is **not** the entity that was authored there. Both routes below discard the
authored tags and round-robin the world's own combatants across the positions instead, because the
point of the map is where a body can stand, not what stood there.

### The tags are a pool, not an assignment

**A map is authored where nothing can know which entities a destination streams.** Entity
definitions live in shared packages, so neither the package id carried in a tag nor the
destination's own package list settles it — measured, zero of the 191 named combatants live in any
destination's package list. Upstream answered this with `kWorldFactions`, a hardcoded world-to-
faction table, which is a guess and was wrong for Nessus.

The game answers it for free: `is_tag_resident` is one resolver lookup, and a map names about a
hundred distinct tags, so the whole set sweeps for less than the placement it precedes. So the tags
in a map file are read as a **pool of candidate bodies**. When a point's authored body is not
streamed in this world, the point fills with one that is, rather than standing empty. Swept at most
every 15 s (a bubble change alters what streams) and never at load, when the world is still
streaming in.

The panel reports the outcome directly:

```
Bodies: 47 of the map's 115 entities stream in this world
```

Measured in game, 2026-08-24, with the faction-matched pools that shipped first:

| world | destination | pool | streams |
|---|---|---|---|
| Dreaming City | `dreaming_city_freeroam` | 82 | 23 |
| Nessus | `planet_x_freeroam` | 115 | 23 |
| EDZ | `edz_freeroam` | 73 | 20 |
| Tangled Shore | `tangled_shore_freeroam` | 44 | 16 |
| Mercury | `mercury_freeroam` | 42 | 5 |
| `polaris_freeroam` | `polaris_freeroam` | 51 | **0** |

`polaris_freeroam` is why the faction guess had to go, and why **every map now carries the full 191-combatant
pool**: faction-matched tags still take most of the points, so a correct guess still shapes how a
world feels, and every tag it excluded is spread through the remainder as a wildcard. A tag the
world cannot produce costs nothing; one the guess omitted was the difference between a populated
world and an empty one.

**The stems are codenames and the mapping is NOT established.** `planet_x` is Nessus, confirmed by
a player standing in it. Every other stem is unverified: `polaris` was called Mars in one session
and the same player later reported Io loading and the Moon never had, which the destination list
cannot both satisfy. **`kWorldFactions` is not evidence** — it is the guess that was wrong twice.
Do not infer a planet from a stem, and do not write one into a table.

**A zero in that line is why a world stays empty.** If it happens, `Fill positions with filtered
combatants` then `Save map` re-tags that destination from what the game has streamed in — it
prefers the world's factions and falls back to every resident combatant, so it cannot come back
empty in a world that has enemies in it.

`place_from_map` also re-grounds every point at placement time, so an authored height that drifted
costs a snap rather than a body in the floor.

### Route 1 — in game, from the authored placement chain

Spawn page → **Extract every destination**. Walks each destination's scenario, slice sets,
registries and placed handles out of the packages, keeps the transforms, fills them with combatants
and saves. Needs the block keys, so it only runs in the process, and it is slow enough that it steps
one destination per frame to keep the game's own session alive.

> **This button overwrites every map file**, the offline ones included, and its output is the weaker
> of the two: measured against them it lost a third of the EDZ's points and over half of Io's.
> `Populate this destination` overwrites one the same way. Recover with
> `python tools/pkg/spawn_maps_build.py --clean` followed by `--write`.

**`Public areas only` throws away every social space.** Measured in the Tower 2026-08-24:
`ev=placement_extract placements=0 kept=0 absent=0 private=6` — all six of its slice sets are
private, so the filter rejected the destination before reading one placement record, and publishing
the empty result wiped the live map (the saved file survives; `Load map` restores it). A social
space needs that checkbox **off**, and `Combatants only` off as well if the point is to reach its
NPCs. This is also a large part of the "175 of 466 fail" count below.

Its two failure buckets are reported as bare counts:

- `skippedNoPlacements` — the walk found nothing, or `extract` refused the destination
- `skippedNoRoster` — no named entity passed the world's faction filter

### Route 2 — offline, from the build cache

`tools/pkg/spawn_maps_build.py`. The build cache already holds the game's **own** spawn sets: 8,892
authored positions grouped by map stem, with the package rules that decide which destination may
load each one. That is a different source from the placement chain — these are the positions the
game itself spawns at, rather than where props and encounters were placed — and it is entirely on
disk, so every destination can be written at once with no launch.

```bash
python tools/pkg/spawn_maps_build.py            # dry run
python tools/pkg/spawn_maps_build.py --write    # write them
python tools/pkg/spawn_maps_build.py --clean    # remove every map it wrote
```

Current install: **423 maps, 105,382 points at 15-unit squad spacing.** Every free-roam destination is
covered, the Moon (`luna_freeroam`) among them.

**Thin by set, never by point.** A spawn set is a squad — three or four bodies a couple of metres
apart, authored that way, and it reads as a squad in play. Thinning points breaks that up and costs
most of the population to fix a problem that was never about the bodies inside a squad; it is about
two squads standing on the same ground. Across Nessus, the EDZ and Io the median set is 34–47 units
from its nearest neighbour, but the closest tenth are within one or two. So a whole set is kept or
dropped on the distance between set centres: `--spacing`, default 15 units, `0` to keep every set.
Nessus keeps 112 of its 131 sets.

Underneath that, at a different scale, any point within `BODY_CLEARANCE` (1.5 units) of one already
kept is dropped, because two bodies there occupy the same ground. Squads sit about two units apart
and survive untouched. It runs across the whole destination rather than set by set, since some of
those duplicates are two *different* squads sharing a coordinate. Every free-roam map measures a
closest pair of exactly 1.50 and a median nearest neighbour of 2.0.

Spread is real rather than clustered — before thinning, the Moon's points cover roughly 3 km × 4 km
across 172 distinct 50 m cells, and thinning removes duplicates rather than area.

Social spaces, the Tower and cutscenes are skipped by default (`--all` includes them). Nothing in
the Tower expects a combatant and it is the most fragile world in this install.

The set-to-destination rule is the game's own, from
`activity_destination_spawn_binding.cpp: loads_package`: a set in the map package belongs to every
destination of that stem, otherwise the destination must name one of the activity packages that
declares it.

---

## What the "175 of 466 fail" note actually means

PR #58 ships an unexplained note that placement extraction fails on 175 of 466 destinations, the
Moon among them. `tools/pkg/spawn_map_audit.py` answers half of that offline, and the answer changes
where to look:

```bash
python tools/pkg/spawn_map_audit.py           # per-destination roster verdict
python tools/pkg/spawn_map_audit.py --tokens  # failures grouped by the key they matched on
python tools/pkg/spawn_map_audit.py --roster  # what the named roster holds, by faction
```

On this install: **466 of 466 destinations find a roster. `skippedNoRoster` cannot be the cause of
any failure here.** Every failure is `skippedNoPlacements`, in the package walk.

And most of that is correct behaviour rather than a bug. Of the 466 rows, 86 are `pvp_*` and 23 are
`gambit_*` — Crucible and Gambit maps, which have no authored combatant placements — plus the
`cine_*` cutscenes and the `mission_*` and `strike_*` spaces whose bubbles are all private, which
the batch skips deliberately because it passes `publicOnly`. A number near 175 is what those add up
to.

The audit transcribes nothing by hand: it parses `kFactions`, `kWorldFactions`, `kChampionMarkers`,
`kBossMarkers` and `kVehicleMarkers` out of `spawn_panel.cpp`, so it fails loudly if those tables
move rather than quietly disagreeing with the game.

One wart it exposes, in the game's filter rather than in the audit: the faction markers are plain
substring tests, so `ai_pvp_vendor_progression` is classed as Hive because "pr**ogre**ssion"
contains "ogre". It costs one junk tag out of 191 and a skipped placement attempt.

---

## Standing a social space's own cast up

The Tower is the case the roaming populator was never built for. Two findings settle how to do it.

**The authored placements are there, and `Public areas only` was hiding all of them.** For
`city_tower_social_d2`:

| filter | result |
|---|---|
| `Public areas only` **on** | `placements=0 kept=0 absent=0 private=6` |
| `Public areas only` **off** | `placements=1762 kept=1762 absent=66 private=0` |

Every one of the Tower's six slice states is private. That is not a mission-versus-free-roam split
here - a social space has no public bubble at all - so the flag that protects a strike's map from
being read as free roam removes the entire Tower. Turn it off for social spaces.

**But the Tower's authored placements do not include its vendors.** Measured on the full 1762-row
extract: of the 28 `ai_*` cast tags `EntityNames.json` knows, **zero appear in it**. Only 9 of the
205 distinct tags are entities at all - `combat_snowball_dispenser`, `lantern_cloud`,
`fotc_stationary_sweeping`, `pot_frame_waving_batons` - and the other 196 are unnamed props and
geometry. The scenario placement chain dresses the set; something else stands the cast on it.

So the placement walk is the wrong table for a social space, and no filter over it can help. It is
still worth running once per destination, because the report says what a world authors:

```bash
python tools/pkg/filter_placement_map.py            # report every distinct tag, named
python tools/pkg/filter_placement_map.py --write    # narrow to the names that match --keep
python tools/pkg/filter_placement_map.py --restore  # put the full extract back
```

### Authoring a social space by hand

Since nothing in the packages says where Zavala stands, the recorder is the instrument. **Record
point here** stamps the entity selected in the main spawner at the player's feet and republishes
immediately, so a vendor appears the moment its point is recorded - the map is authored and
verified in the same walk.

1. **Clear map** first, or the props stay in it and eat the live budget.
2. Pick the actor in the main spawner - `ai_zavala`, not the bare `zavala` model. The `ai_*` tag
   opens the vendor screen; the model just stands there.
3. Walk to where that character belongs and press **Record point here**.
4. Repeat for the rest of the cast, then **Save map**.

`Populate this destination` and `Extract every destination` both write map files, so neither is
safe to press while a hand-made map is being kept.

---

## Reference

| file | what |
|---|---|
| `src/client/hooks/spawn/spawn_runtime.cpp` | the populator, `service_auto_load`, `place_from_map` |
| `src/client/spawn/population_settings_store.cpp` | `population.json` reader and writer |
| `src/client/spawn/spawn_keybind_store.cpp` | the map file format, `save_map` / `load_map` |
| `src/client/content/placements/placement_extract.cpp` | the in-game placement walk |
| `src/server/ui/spawn/spawn_panel.cpp` | the Spawn page, the roster, `step_batch` |
| `src/state/activity/destination/activity_destination_spawn_binding.cpp` | the spawn-set rule |
| `tools/pkg/spawn_maps_build.py` | offline map writer |
| `tools/pkg/spawn_map_audit.py` | offline roster audit |
| `tools/pkg/filter_placement_map.py` | narrow a saved map to named entities |
| `tools/pkg/tower_npc_map.py` | the Tower cast probe map |
