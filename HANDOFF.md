# Handoff — custom character into Sunrise / Shadowkeep

**Updated:** 2026-08-19 (late evening, arrangement hop). Supersedes all earlier versions.
**Goal:** get the user's custom model visibly rendering in game.

Do **not** merge upstream Sunrise 0.3.2 yet.

Dumps were **not** wiped. `C:\Sunrise\bin\x64\Sunrise\dump\` holds **3,707** `tag_*.bin` files.
`dump_models\` holds 351.

---

## The identification loop that works

`extract_mesh.py` + `render_obj.py` turn any dumped model into a shaded front/side PNG. The user
recognises Destiny content instantly. **Render the candidate, show the user, ask what it is.**

```powershell
python extract_mesh.py 0x80EC2AB4 --dump C:\Sunrise\bin\x64\Sunrise\dump --out-dir objs
python render_obj.py sheet.png --dir objs
```

`investment_01d3` / `investment_0361` render as unmistakable Destiny armour (hooded Warlock robes,
torso + arms). If a render looks like noise, the *stride is wrong*, not the model — see `ui_037e`.

---

## Retractions still standing (visual proof, not inference)

1. **`0x80B9F855` / `0x80C23B5D` / `0x80FA2308` are NOT the player body.** They are **Tower frames**.
2. **`0x815B868B` / `0x815B8697` (globals_06dc) are not body variants.** 5.04 m world objects.
3. **Constructor "owner" tags are materials**, class `0x80807140`.
4. **Investment-root slot 4 (`0x81327CF0`) is NOT the assignment table.** Array count is **1,170**,
   stride ~1256. Arrangement 2293 is past the end.

---

## What is proven

- **The write pipeline is correct end to end.** Position buffer scaled 0.5x dumped back
  byte-for-byte. Entry resizing, multi-package writes, header rewrites, part-table rewrites,
  `--undo`, 0x800 alignment, per-block SHA-1: all good.
- **Armour geometry decodes correctly** and is renderable offline: `investment_01d3` / `0361`.
- **`investment_0361` / `investment_01d3` hold the player's visible geometry** — the armour items.
  In Destiny the character *is* its armour.

---

## The blocker, stated precisely

**The in-world Guardian renders as a permanent dissolve shell**, before any patch and after all of
them. Every in-world visual judgement is void in both directions.

**The character / inspect screen draws armour solidly.** That is the only reliable in-game venue.
Judge there.

---

## The deterministic chain, and exactly where it stops

The user's equipped items are known (`python lookup_item.py`, reads `build_data.bin`):

| slot | definition hash | gearArtIndex | art arrangement | arrangement hash |
|---|---|---:|---:|---|
| helmet | `0xEA042965` | — | **2295** | `0x8FB9E751` |
| gauntlets | `0x188C5834` | — | **2292** | `0x3FBE9128` |
| chest | `0xF8689C4C` | — | **2293** | **`0x4A8A34A0`** |
| legs | `0x083E04B6` | — | **547** | `0xB01AFB7D` |
| class item | `0x99446581` | — | **2294** | `0x0A9977DD` |
| kinetic | `0xE516CF40` | 272 | 2700 | |
| energy | `0x4156C94D` | 205 | 1885 | |
| heavy | `0x6F22FCEC` | 197 | 1846 | |

**Armour carries no `gearArtIndex` — only an art arrangement index. Weapons carry both.**

Charm (Witch Queen): item → arrangement index → assignment table → assignment hash → entity parent
→ `SEntity` → resource → `SEntityModel`.

**The chain stops at: arrangement index / hash → entity-parent tag.** Hash table is solved.
Assignment map is not dumped yet.

## Arrangement hop — proven offline

Arrangement indices in `build_data.bin` run **0..4300** (4,301 slots, 4,028 in use).

Charm WQ classes (`0x808055CE`, `0x808070F2`, `0x80804EA4`, `0x80804F43`) are **zero** in this
install. Shadowkeep equivalents, found by size:

| tag | class | size | what |
|---|---|---:|---|
| `0x81327D22` | `0x80807D84` | 1,912 | investment root, 119 slots. **Dumped.** |
| `0x81319329` | `0x80807546` | 17,252 | **art-arrangement hash table**. `4,301 × 4 + 48`. Monteven `index * 4 + 48`. **Dumped.** |
| `0x81327CCB` | `0x80807BE4` | | item table, slot 48. `15,424 × 24 + 48`. |
| `0x81327CF0` | `0x808076F0` | 1,470,710 | slot 4. Count **1,170**. **Not** the assignment table. **Dumped.** |
| `0x8131914C` | `0x80807C0B` | 56 | 0x38 parent, Charm `A44E8080` shape. Tag64 at `+0x10` / `+0x28` should name the maps. **Not dumped.** |
| `0x81319343` | `0x80807879` | 56 | second 0x38 parent. **Not dumped.** |
| `0x81319135` | `0x80807548` | 24 | sibling of the hash table. **Not dumped.** |
| (16,236 tags) | `0x8080744A` | 24 each | entity-parent stubs. ~3.8 per arrangement. Almost none dumped. |

Other dumped tables that are **not** the assignment map:

- `0x81319348` class `0x808077EE`: 1,425 × 16. Compact hash list, contains our five hashes, **no entity tags**.
- `0x81319320`: 5,867 × 8. Hash → integer index, not a Tiger tag. Our arrangement hashes are absent.
- `0x81319339` class `0x80807567`: 2,242 nested DynamicArrays. Wrong table.
- Exact-size hunt (`--census`): `4301×8+56` hits in `investment_0361` (`0x80EC342D`, `0x80EC3827`)
  are **float transforms** (`1.0f` / `-1.0f` in the prefix). `0x80EC259F` is a uint16 remap.
  `0x81A2694C` is class `0x80809A8A` (57,745 generic buffers, including audio). Size coincidence.

The assignment table is therefore **nested or named by a pointer** — Charm's WQ `A44E8080` parent
(Tag64 at `+0x28`) is the next dump. D1/WQ row-size formulas (`4301×24+40`, `4301×32+40`) have
**zero** investment hits.

`tools/pkg/lookup_arrangement.py` walks the chain once the dumps exist. It skips tags already on
disk, so `--request` only asks for the missing 0x38 parents and large undumped tables.

```powershell
python lookup_arrangement.py --census     # offline size hunt
python lookup_arrangement.py --request    # writes dump\request.txt (skips already-dumped)
# launch Destiny once, no patch installed; dump runs at investment refresh
python lookup_arrangement.py              # equipped loadout → model tags
```

---

## New tools (all in `tools/pkg/`)

| tool | what it answers |
|---|---|
| `lookup_arrangement.py` | **arrangement index → model tag.** `--request` / `--census` / no args. |
| `render_obj.py` | shaded front/side PNG contact sheet from OBJ — **the identification loop** |
| `renderable.py` | which models have every buffer on disk; `--missing` prints a dump request |
| `body_survey.py` | body-shaped models per package |
| `live_entities.py` | `SEntityModel` handles a capture named outright |
| `trace_timeline.py` | buckets a capture by `t=` |

---

## Facts worth not rediscovering

- **`ui_037e` uses stride 48** and is *not* int16-packed. Garbage render ≠ garbage model.
- `sandbox_0691` has 31 body-shaped models, **0 renderable** — no buffers dumped.
- Body-shaped counts: `investment_0361` 38, `investment_01d3` 36, `sandbox_0691` 31,
  `npcs_0360` 29, `sandbox_037a` 24, `npcs_03e6` 19, `ui_037e` 18, `globals_03ab` 13.
- Upstream is **39 ahead / 42 behind**. Do not merge 0.3.2.
- `state.cosmetics.ornament_replaces_arrangement` is **false** in the live settings.
- `resolve.py` **runs CLI on import**. Do not `from resolve import ...`.
- Tags are **additive**: `0x80800000 + (package_id << 13) + entry`. Never OR.
- Shadowkeep classes: `SEntity` `0x80809C0F`, resource `0x80809C36`, `SEntityModel` `0x808073A5`,
  mesh `0x80807378`.
- Dump via `dump\request.txt` (`tag 0x...` / `class 0x...`), cap 1024, runs at investment refresh.
  No patch installed when dumping.

---

## Recommended next steps, in order

1. **Launch once against `lookup_arrangement.py --request`.** No patch. The 0x38 parents should
   name the assignment-map tag (Charm `A44E8080`: Tag64 at `+0x28`).
2. **`python lookup_arrangement.py`** — chest arrangement 2293 / hash `0x4A8A34A0` → entity-parent
   `0x8080744A` → `SEntity` → `SEntityModel`.
3. **Dump that model's buffers, `extract_mesh.py` + `render_obj.py`, confirm it is the chest.**
4. **Inject the custom wrap into that tag** and judge on the **character screen**, never in-world.

### Do not

- Judge anything by the in-world Guardian. It is a dissolve shell.
- Trust a `--match` negative without checking the package histogram.
- Dump while a patch is installed unless you *intend* to read through it.
- Re-probe the Tower frames, `globals_06dc`, or `ui_037e` on the assumption its render is meaningful.
- Treat slot 4 as the assignment table.

---

## Environment

| what | where |
|---|---|
| Game | `C:\Sunrise`, Shadowkeep `86657.20.08.23` |
| Our DLL | `C:\Sunrise\bin\x64\steam_api64.dll` |
| Dumps | `C:\Sunrise\bin\x64\Sunrise\dump\` (3,707 files), `dump_models\` (351) |
| Logs | `...\Sunrise\logs\sunrise.log` — **rotates one deep, archive immediately** |
| Archived captures | `reference/captures/` (6 logs) |
| Build data cache | `C:\Sunrise\bin\x64\Sunrise\cache\build_data.bin` |
| Custom character | `C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb` |

Warlock `soid 0x9EAA300100100103`, race 0, gender 0, class 2, armour **equipped**.
`settings.json.bak_nullarmor` holds the null-armour state. No patches installed.

**Docs:** `Sunrise/docs/TOOLS.md`, `PACKAGES.md`, `GEOMETRY.md`, `CLAUDE.md`.
