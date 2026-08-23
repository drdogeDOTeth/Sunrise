# Handoff — custom character into Sunrise / Shadowkeep

**Updated:** 2026-08-23 (**emotes shipped on branch `activities`, not yet pressed** — an emote is a plug in the Emote Collection, never equipment; needs the newly built DLL). Cosmetics: **left-hand roll −25° confirmed in game**. Earlier that day (**left-hand roll −25° shipped, not yet seen** — `037c_34` / `037d_32` / `0698_30`). The v24 hand refit under it is **user-confirmed better** in game. FP arms on gauntlets `(0, 16326)` confirmed in-world. Host Intake tool is live. Race-glove blank still unconfirmed. `--fingers` CLOSED on the chest.

**Live / restore:** newest is the **v25 left-hand roll** over the **v24 refit** (`037c_33` / `037d_31` / `0698_29`, confirmed good), both on top of `20260823-141400.json` (100 layers — FP arms + race-glove blanks, v23 hands). Roll too strong or the wrong way? Change `HAND_ROLL` and re-run; the refit underneath is sound. Older floors: `20260822-235602` (92 layers, hands-on-gauntlets confirmed), `20260822-225414` (v23 wrists-only). Tiled `0698_25` stays in `packages\_reverted_uv_tiles\`. Do **not** `--restore` `150046`. **Durable status:** `docs/CUSTOM_CHARACTER.md`. Destiny must be closed to write packages or overwrite the DLL.

**Target look:** the GLB in Blender. Reference: `tools/pkg/objs/textures/_glb_blender_preview.png`. Warlock only.

---

## NEXT AGENT — start here

### The VRM merge is parked, not resolved (`cosmetics` @ `9b887c7`)

PR #1 (Sleepy-Studio, "Add playable VRM injection workflow") merged into `origin/cosmetics` as
`5e2f868` and **the tree did not compile**. `custom_albedo.cpp` was rewritten on both sides since
the merge base `6d0bacb` — 184 lines ours, 973 theirs — and merge `d7e5262` interleaved them,
keeping our `Part`/`PartSpec` helpers next to the profile branch's call sites. `GpuImage`,
`release_com`, `RuntimePart`, `read_file_to_string`, `to_utf8`, `find_json_string` and the new
`load_png`/`upload_image` signatures lost their definitions while every use stayed.

`custom_albedo.cpp`, `custom_albedo.h` and `player_panel.cpp` are now **byte-identical to
`ff351ea`** again. Everything else the PR brought stays: `vrm_injector.py`, `dump_class_bodies.py`,
`game_paths.py`, `docs/characters/README.md`. Only `player_panel.cpp` called the model-profile API,
so nothing else needed reverting; the in-game model picker is gone with it, and no doc describes it.

**Do not "fix the merge" by taking their file.** `043dd75` predates the first-person arm work: it
has **no `hands` part at all** and still carries `mask` at **11574**, not 11580. Both are
user-confirmed in game. Redoing this means porting the arm work forward onto their profile
structure, and it wants a launch to confirm the Warlock still renders.

Two of their tool edits are live and worth knowing: `inject_scatterhorn.py` now falls back to a
spare chest material instead of raising when a source material is unmapped (so a real mapping
mistake is now a warning, not a stop), and `parse_models.py` no longer raises when the dump
directory is empty — downstream tools get an empty `models` list instead of a clear error.

### Emotes: an emote is a plug, not equipment. SHIPPED, awaiting one launch. (branch `activities`)

The worlds being empty and the emotes never playing are separate problems with separate causes.
This is the emote one, and it is settled from the installed data — no launch was needed to find it.

The authored state equipped emote `0x7979AE7D` ("Blowing a Kiss") into the emote equipment slot
with `plugs: []`, and it has been there all along. It resolves, publishes, and does nothing,
because **that is not how the game reaches an emote**:

| item | bucket | equipment slot | ordinary sockets |
|---|---|---|---|
| every one of the 307 emotes, incl. `0x7979AE7D` | 41 | 14 | **0** |
| **Emote Collection `0xBDBB7999`** | 12 | **absent (−1)** | **4** |

Each of the Emote Collection's four lanes accepts **all 307 emotes** — verified against the
extracted plug pools, not guessed:

```powershell
python tools\pkg\build_cache.py --plugs 0xBDBB7999   # lanes 0-3, 307 plug(s) each
python tools\pkg\build_cache.py --slot 14            # 307 items, every one with 0 sockets
```

Four lanes, four `emote_1..4` keys (`up` / `down` / `left` / `right` in
`resources/default_settings.json`). The emote is the **plug**; the Collection is the item.

**The blocker was one line of ours.** `loadout_item_resolver.cpp` required
`*itemDetail.equipmentSlot >= 0`, so an item with no equipment slot could never be carried at all —
the Emote Collection was unreachable from authored state no matter how it was written. Lifted, with
`loadout::kNoEquipmentSlot` (`0xFF`) marking a carried-only row. Nothing can now equip what has no
slot: the equipped path hands that sentinel to `record_equipment_slot`, whose bound check rejects
it, and `character_encoder`'s `valid()` rejects `equipped && kNoEquipmentSlot` outright.

Shipped: an Emote Collection per character in `inventory`, with Cheer / Slow Clap / Point on
up/down/left and the character's own class Sit on right (`0x0AD5589A` Hunter, `0x78380468` Titan,
`0x0203AD93` Warlock — class-matched deliberately, since the pools do not gate by class and a
wrong-class emote may have no animation). Both `resources/default_settings.json` and the live
`C:\Sunrise\bin\x64\Sunrise\settings.json` carry it; the live file needs the **new DLL**, because
the old one rejects the row and fails the whole loadout.

**What one launch decides:** press Up / Down / Left / Right in world. If the log says
`report_failure("loadout")` the row is malformed; if the character screen is normal and the keys do
nothing, the Collection is carried but the client reads its quick slots from somewhere else, and
the next question is whether the game wants it in a *profile* bucket rather than a character one.

**Do not re-derive:** `lookup_item.py` is dead — it hardcoded a record layout, upstream moved the
cache to format 44, and it then answered "not in this install" **for the weapon and helmet the
character is visibly wearing**. Use `build_cache.py`, which derives the layout from `format.h` and
validates it against the file size. Control before concluding: the helmet reports 11 sockets and
the kinetic 12, which is what proves the emotes' 0 is real data and not a broken reader.

### The twisted hand: SHIPPED as `037c_33` / `037d_31` / `0698_29`, awaiting one launch

User 2026-08-23: the hand looks "screwed on at the wrong angle" in **first person and in the
character screen** — both views, every pose. Both views mattering is what proved it was our bind
pose and not the FP viewmodel rig.

**Cause, and it is in the tool's own docstring:** *"this script poses the arms only."*
`ARM_CHAINS` ends at `("Left elbow", 19, 21)`, so the wrist bone was never posed. Worse, each arm
is rotated about the **GLB's** shoulder, which sits ~4.8 cm inboard of rig joint 27 — so the
error is inherited all the way down and **each wrist landed 4.9 cm from the joint that drives
it**. The game rotates our hand about *its* joint, so that gap was a lever arm: every wrist bend
swung the hand ~5 cm wide. With **48% of the hands mesh (1,550 of 3,206 verts) on finger joints
40–71**, that dominated the view.

Fixed in `retarget_mesh.fit_hands()`, two corrections per side and deliberately no more:
re-aim the forearm at the real wrist joint from wherever our elbow actually is and scale it along
its length so the tail lands on that joint (×0.851 / ×0.892); then turn the hand so its frame
matches the donor's.

| measure | v23 (was live) | **v24 (shipped)** |
|---|---:|---:|
| wrist to its joint | 4.89 / 4.93 cm | **0.72 cm** |
| hand axis vs donor | 6.3° / 6.8° | **0.1°** |
| knuckle line vs donor | 28.6° / 25.3° | **0.1°** |
| palm normal vs donor | 29.2° / 26.0° | **0.1°** |

**The change is contained, verified vertex by vertex against the confirmed body:** chest, torso,
legs and shoulders moved **0** vertices, upper arm 30 verts by ≤2.6 mm, weights byte-identical.
Only forearm-down moved. Index counts are unchanged (chest 122,304, hands 16,326), so
`custom_parts.txt` and the hook need no edit. 0 dangling refs across 22 packages.

Two traps this cost, both worth not repeating:

- **Do not fit the hand to knuckle *blend joints*.** A blend region between palm and finger sits
  on the **armoured back** of the glove, so the joint it estimates is biased dorsally; fitting to
  it rolled the hand ~13° the wrong way (audit twist −2.3° → +11.4°). Use weighted centroids,
  which is what `hand_targets.py` now emits and what `audit_hand_bind.py` checks — **fit and
  check must optimise the same quantity.**
- **Do not fit the hand by Procrustes over knuckle positions.** Our knuckles sit 31% closer to
  the wrist and our fingers run 8 cm longer than Destiny's; with radii that different, minimising
  position trades orientation away to buy radial error it can never win. Position-fitting left
  the knuckle line 13.6° out and a second round made it 19.2°. Aligning two *directions*
  (wrist→middle, index→little) leaves 0.1° **and** a lower landmark error (2.41 vs 2.61 cm).

**Still open, by choice:** our knuckles remain ~2.5 cm inboard of Destiny's joints, so fingers
still pivot slightly along their length rather than at the base. Closing it means scaling the
hand ×1.4, which would make the character's already-long fingers 40% longer. Orientation and
mounting are fixed; hand *proportions* are the character's, not a defect.

### Artistic roll: `HAND_ROLL`, left hand at −25° (`037c_34` / `037d_32` / `0698_30`)

User confirmed the refit ("looks better") and then asked for the left palm to roll over —
pronation, the palm-up-to-palm-down motion, **not** a tilt within the palm plane. That is taste,
not fit, so it lives in `retarget_mesh.HAND_ROLL` as degrees about the forearm axis. Left −25,
right untouched. Verified: 24.8° of roll, fingers swung 3.5°, right hand moved **0** vertices,
everything outside the hands 17 verts by ≤3.7 mm, index counts unchanged.

**Pick the value by looking, never by reasoning about the right-hand rule.** `HAND_ROLL`'s sign
matches the preview sheet's panel labels on purpose:

```
python hand_view.py out.png --side back --axis forearm --angles -40,-20,0,20,40
```

Two things that will mislead you here:

- **A preview whose camera comes from the hand's own frame cannot show a roll** — the camera rolls
  with the hand and the render looks untouched. Measure against a *fixed* frame (a previous
  version's) to check a roll landed.
- **Screen-right has to be defined once.** Two scratch scripts defined it with opposite signs and
  produced contradictory readings of the same correct mesh. `cross(palm_normal, fingers)` is the
  convention the preview sheet uses.

Ruled out on the way, do not re-derive:

- **`FINGER_MAP` is correct.** 40→52, 42→53, 43→54 and mirrors are the same digit's next joint
  (1–3° apart from the wrist, ~1 cm further out); thumb 45→56→66 progresses properly. 43 is the
  **pinky** and 44 the **ring** finger, which the GLB has no group for.
- `recover_finger_joints.py` reports 52–60 as missing only because it looks at **dominant**
  vertices; they are never dominant on the glove. Weighted centroids locate them fine.
- **No cross-side weight bleed** — zero verts mix a left and right bone.
- The bind **roll** is not the fault: twist about the forearm is −2.3° / −1.1°.
- An early pass here claimed a 180° palm flip on the right hand. That was an **SVD sign
  ambiguity** in a PCA axis, not real. Build hand frames from named bone chains, never from PCA.

**Hands-on-gauntlets CONFIRMED in-world.** First-person draws the gauntlet mesh — upper arm, forearm, palm and fingers now live on that draw `(0, 16326)` so Ghost/weapon view is a complete arm (chest never draws in FP). Skip-draw is **reverted**. Finger bones 40–71 can still spaghetti on that path. Do not `--fingers` on the chest. Do not AABB-fit. Do not hide the draw.

**Host Intake** (`bring_guardian.ps1` / `tools/pkg/bring_guardian.py`) is how a **caller-supplied** GLB becomes the Warlock: extract → Blender retarget (`--keep-seams --fingers`) → cut hands → `custom_parts.txt` → inject `--hands-on-gauntlets`. The tool does **not** ship a character (no baked-in gas-mask path). Hook reads `C:\Sunrise\bin\x64\Sunrise\custom_parts.txt` (defaults if missing).

Race-default upper-body copies blanked (not yet seen). `--fingers` stays CLOSED.

Live 981E/9809 draw our complete arms `(0, 16326)` (upper arm + forearm + palm + fingers + shoulder cuff). Bind is the chest frame (~1.01), not the glove 0.45 AABB. Arrangement 2292 has no sibling. The leftover leather is the replicated player-body piece with the **7685/7754** part signature:

| tag | package | action |
|---|---|---|
| `0x815B9521` | `w64_globals_06dc_7.pkg` | blanked (was live in the last capture) |
| `0x80FDBE41` | `w64_globals_03ed_6.pkg` | blanked (second globals copy) |
| `0x80EFC649` | `ui_037e` | **left alone** — same mesh 0/1 plus select extras |

Hook miss log now records unique unmatched `(start, count)` (cap 64, no chest-range filter). Look for: leftover leather gone; SkinTats hands; maybe the bald head gone too (same mesh). Leather still there = read `sunrise.log` misses, `--restore` `20260822-235602`. Do not blank `0x80EFC649`. Do not `--fingers`. Do not AABB-fit.

---

## NEXT AGENT — v18 floor (still live)

**Character select is the Blender character** (user 2026-08-22). Green SkinTats, charcoal tank, black mask, striped pants, sneakers. That is done. Do not colour-probe. Do not pack another 512.

**In-world (v12):** near-black silhouette. Sprint/jump flashes the dye quilt (that PS *lights* in destinations). Our replace was killing o1/o2.

**v13:** two-pass (game PS, then RT0 RGB only). **Subset match is closed** — user-confirmed: as soon as the game loaded, character select was a blown-out gray/white/black mess (3 class cards as empty rectangles). `start=0` UI quads counted as the tank; ALWAYS depth painted albedo over the menu. Do not subset-match again.

**v13b:** two-pass, exact match. User: whole body is the green “SEVEN” quilt (dye PS + packed 512, 0–1 UVs). Log: attach ok, all five parts drew. Second draw did not win RT0 (depth LESS after pass 1). Two-pass is **closed**.

**v14:** v12 single-pass restore. Select/menus/inspect = Blender look (user). In destinations the *whole scene* went neon green/cyan (`LTGtextureBuffersTest.mp4`). HUD stayed clean. Not “just a dark coat” — the 3-target replace leaked into world lighting (same index counts on shadow/light passes) and `PSSetShader(previous, nullptr, 0)` dropped class instances so later draws sampled our skin atlas.

**v15 (user, keep):** Sprint/jump quilt **gone**. Io world was fine. Character is **not** solid black — it looks good standing in / near a light, and is just very hard to see when no lamp is on it (missing ambient / IBL, not a crushed albedo). Distance makes that worse.

**v16 TEXCOORD2 is closed** (user): select went dark/flat; Tower became a solid black silhouette. Do not encode `TEXCOORD2` again. Do not add emission.

**v17 (user):** more visible in the dark — AO/fill worked. **Foreign designs** on the body (hex/scale/marble) were Destiny `t2` (Scatterhorn pack) sampled with our 0–1 UVs. Not the GLB. Closed.

**v18 (user, keep):** stop sampling game t2. `o2.x` = GLB metalRough.g (`custom_*_mr.png`). `o2.y` = 0.5 open AO (GLB has no occlusion map). `o2.z` = 0 (`metallicFactor` is 0). Twirl has no MR → 1×1 roughness 0.55. `mode=ours`. User 2026-08-22: looks good in game; first real fully textured custom guardian. World-hopping to confirm more destinations.

Read: this is the floor. Do not sample Destiny t2 again. Do not retry TEXCOORD2 / two-pass / subset-match / emission. Hang = revert DLL. Leftover: stock gloves, bald race head, collapsed necklace UVs.

| artifact | path |
|---|---|
| Status | `docs/CUSTOM_CHARACTER.md` |
| Hook | `Sunrise/src/client/hooks/custom_albedo/` (**v18**, `mode=ours`) |
| Dye PS dump | `tools/pkg/known_good/live_chest_ps.bin` + `.asm` |
| Per-part albedos | `C:\Sunrise\bin\x64\Sunrise\custom_{tank,mask,necklace,skin,twirl}.png` |
| Per-part roughness | `C:\Sunrise\bin\x64\Sunrise\custom_{tank,mask,necklace,skin}_mr.png` |
| Snapshot | `tools/pkg/known_good/20260822-172401.json` |
| Log | `C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log` |

### Live dye PS (`0x81531EE6`) — do not re-derive

`ps_5_0`. Mesh UV is **`TEXCOORD3`**. TBN is `TEXCOORD0..2`. Flat normal = **`TEXCOORD0.xyz`** (`worldN = v0*nz + v1*nx + v2*ny`). `o1.xyz = saturate(n * ~0.375 + 0.5)`, `o1.w` is 0 or ~0.33, `o2.w = TEXCOORD0.w`, `o0.w` fallback ~0.2.

### Hook versions (user-confirmed). Do not repeat a closed one.

| ver | what | result |
|---|---|---|
| v1–v2 | sample / magenta-if-dark | Black |
| **v3** | Bright constants on every SV_Target | **Magenta.** Only early write that showed. |
| v4–v7 | colour on RT0, zeros/wrong extras | Black |
| v8 | 512 B snprintf HLSL | compile fail; cowprint = hook off |
| v9 | game PS then RT0-only | Black (other RTs zeroed) |
| v10 | guessed `(0.5,0.5,1)` G-buffer | Black. Dump 5772 B ok. |
| **v11** | dumped encode + 512 quilt on `TEXCOORD3` | **Right colours, smeared.** Encode solved. |
| **v12** | five GLB albedos + `0698_25` attic’d | **Character select = Blender look.** In-world dark / flicker. |
| **v13** | two-pass + **subset** match | **Smashed character select** (blown-out UI). Closed. |
| **v13b** | two-pass, exact match only | **Green quilt on select** (pass 1 only). Closed. |
| **v14** | v12 single-pass restored | **Select good. World radioactive green.** Closed as installed. |
| **v15** | G-buffer gate + fortress PS restore | **Keep.** Quilt gone. Hard to see off-lamp, good near lights. |
| **v16** | `o1` = TEXCOORD2 geometric | **Closed.** Select dark, world solid black. |
| **v17** | sample game t2 → o2 AO/roughness | **Visible, but Scatterhorn designs.** Closed. |
| **v18** | GLB roughness on t1 + open AO | **Keep.** First in-world fully textured custom guardian. |

### Closed package paths

Paint-what-is-already-bound works and is luma-gated. `1CBE` = white slabs, no green. Look-alike boost = white pants. Off-chest steals vanish four parts. `bind_material_textures.py` hangs select. Do not retry any of those.

Leftover: stock gloves, bald race head, collapsed necklace UVs. Necklace GLB metallic (B) is unused on purpose (`o2.z = 0`).

**Do not lose:** dye PS `tools/pkg/known_good/live_chest_ps.bin` + `.asm`; snapshot `20260822-172401.json`; live PNGs in `C:\Sunrise\bin\x64\Sunrise\custom_*.png` and `custom_*_mr.png`; GLB at `C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb`. Full table in `docs/CUSTOM_CHARACTER.md`.

Build: `.\build.ps1`. Install: `.\install.ps1`. Hang = revert `steam_api64.dll.original`.

---

## NEXT AGENT — archive (pre-v12)

The block below was the v10/v11 briefing. Facts still hold. Do not treat it as the live next step.

Hook v10 attached (`mode=gbuffer`), all five custom parts drew, atlas PNG loaded, and the body is **still black**. That was the last guess. The live dye PS is on disk. **Read it. Match its G-buffer. Then launch once.**

| artifact | path |
|---|---|
| DXBC dump | `Sunrise/tools/pkg/objs/live_chest_ps.bin` (copy of `C:\Sunrise\bin\x64\Sunrise\dump\live_chest_ps.bin`, 5772 B) |
| Disassembly | `Sunrise/tools/pkg/objs/live_chest_ps.asm` |
| Parse signatures | `python tools/pkg/parse_dxbc_osgn.py tools/pkg/objs/live_chest_ps.bin` |
| Disassemble again | `python tools/pkg/disassemble_dxbc.py` |
| Hook | `Sunrise/Sunrise/src/client/hooks/custom_albedo/custom_albedo.cpp` (**v12 installed**) |
| Per-part albedos | `C:\Sunrise\bin\x64\Sunrise\custom_{tank,mask,necklace,skin,twirl}.png` |
| Log | `C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log` (rotates one deep) |

### Live dye PS (`0x81531EE6`) — facts from the dump

`ps_5_0`. Three outputs. **Mesh UV is `TEXCOORD3`, not `TEXCOORD0`.** `TEXCOORD0..2` are the TBN. `TEXCOORD4`, `SV_Position`, `SV_IsFrontFace` are declared and unused.

| semantic | reg | mask | used | meaning |
|---|---|---|---|---|
| TEXCOORD0 | v0 | xyzw | xyzw | TBN tangent; **`.w` is written to `o2.w`** |
| TEXCOORD1 | v1 | xyzw | xyz | TBN bitangent |
| TEXCOORD2 | v2 | xyz | xyz | TBN normal |
| TEXCOORD3 | v3 | xyzw | xyzw | **mesh UV** `.xy` → t0/t1/t2/t3; `.zw` remapped via cb5[0]/cb5[1] → dye t7/t8 |
| TEXCOORD4 | — | xyz | (none) | unused |
| SV_POSITION | — | xyzw | (none) | unused |
| SV_isFrontFace | — | x uint | (none) | unused |

Resources: t0 t1 t2 t3 t7 t8; samplers s1–s6; CB0[8], CB5[25], CB6[14].

**G-buffer encode (do not guess this again):**

```
o0.xyz = dyed albedo
o0.w   = packed id; fallback path writes ~0.2
o1.xyz = saturate(normalize(TBN * n_map) * scale + 0.5)
         TBN multiply is worldN = v0*nz + v1*nx + v2*ny
         flat n_map (0,0,1) → worldN = TEXCOORD0.xyz   // NOT TEXCOORD2
         scale = something * 0.125 + 0.375  (flat → ~0.375)
o1.w   = 0x3ea8f5c3 (~0.33) if o0.w > 0.5 else 0     // NOT 1.0
o2.x   = roughness-ish (must stay > 0.05 or o0.w takes the 0.2 path)
o2.y   = 0.5 * sat(t2.x)
o2.z   = metallic-ish * dye mask
o2.w   = v0.w                                        // NOT 1.0
```

v10 wrote `RT1=(0.5,0.5,1,1)` and `RT2=(0.5,0.5,0.5,1)` with **only `SV_Position`**. Lighting decodes `o1` as `(c-0.5)/~0.375` and uses `o2.w = TEXCOORD0.w`. That encode is why v10 is black even with non-zero fills. An earlier v11 draft that encoded `TEXCOORD2` as the normal was wrong — flat dye reconstruct uses **v0**.

### Hook versions (user-confirmed). Do not repeat a closed one.

| ver | what | result |
|---|---|---|
| v1 | Sample t3 + game s0, full interpolator struct | Black. Hook fired. |
| v2 | Our sampler, SampleLevel t0–t6, magenta if dark | Black, not magenta. |
| **v3** | Bright constants on **every** SV_Target, `SV_Position` only, skip depth-only | **Magenta. Only write that ever showed.** Colour is RT0. |
| v4 | Packed 512 + TEXCOORD0, yellow if UV zero | Black. Atlas bound. Yellow never showed. |
| v5 | PrimitiveID + ByteAddressBuffer IA copies | Black. Mesh copy **ok**: slot0 stride 16, slot1 stride 24, index R16, UV `0.946/0.376`. |
| v6 | Screen-space atlas (`xy/512`) | Black. |
| v7 | Per-part constants, RT2=0 | Black. |
| v8 | Constants on 8 targets; HLSL via **512 B snprintf** | **`compile result=fail` — no hook.** Cowprint = dye PS + packed 512. |
| v9 | Game PS then RT0-only second pass, others write-mask 0 | Black. Unwritten bound RTs come back zero. |
| **v10** | Atlas on RT0 + guessed RT1/RT2, dump live PS | **Still black.** Dump **ok** 5772 B. |
| **v11** | Dumped TBN encode + packed 512 on `TEXCOORD3` | **Right GLB colours, smeared/tiled.** Quilt on 0–1 UVs. Encode is solved. |

v3 worked because every target was a **bright constant** (smashed G-buffer, but visible). v4–v7 wrote zeros on RT2 → lit black. v9 left RT1/RT2 undefined. v10 filled all three with the **wrong encode**.

v8 cowprint (white / black / purple SMPTE on the mask) = **hook off**. Distinct from hook-black.

### Hook v11 — CONFIRMED (user 2026-08-22)

Right colours: green SkinTats, black tank, colour blocks from the other quilt tiles on the legs (magenta/cyan/white, red). Weapon/UI vanilla. **TEXCOORD3 is 0–1 mesh UV** even with `0698_25` live — the VS is not outputting the tiled buffer. Smear = one 512 quilt sampled with those 0–1 UVs. G-buffer encode is done. Do not reopen it.

### Hook v12 (installed, not launched)

Same encode. Five GLB albedos bound by `(StartIndex, IndexCount)`: tank/mask/necklace/skin 2048², twirl 512². Attach log `mode=parts`. `0698_25` attic’d to `packages\_reverted_uv_tiles\` (live UV layer = `0698_24`).

Read:
- **Blender look** = this is the character. Snapshot when they say so. Leftover stock gloves + bald race head are separate.
- **Still smeared / quilt blocks** = TEXCOORD3 is not the 0–1 albedo UV; dump the VS or try `1-v` / `.zw`. Do not pack another 512.
- **Necklace is a flat speck** = known collapsed UVs on that part (`u` 0.817–0.859, `v` ~0.410). Mesh UV bug, not the hook.
- Hang = revert DLL. Put `0698_25` back with `revert_layer.ps1 -Attic _reverted_uv_tiles -Restore` only if the attic move is the hang (it should not be).

Build: `Sunrise/build.ps1` (v143 fallback). Install: `Sunrise/install.ps1`. Destiny closed.

---

**What is actually known (packages):** paint-what-is-already-bound works. Unique RGB via another same-family package PS is closed. Packed 512 tiles remap offline (skin mean RGB ~38,99,60) and get luma-gated in-game. Do not pack another 512. Do not retry `1CBE`. Do not steal.

**`1CBE` result:** character select loaded, body did not vanish. Custom mesh is flat **white slabs** (chest, belly, fronts of legs), a **yellow waist band**, gold/tan mask, leftover purple gauntlets. **No green.** `0x81531CBE` is a cloth/metal palette shader (same dye tile as `0x81531EE6`), not an albedo sample. Do not retry `1CBE`. Do not pack another 512. Do not steal.

Offline `dump_if_requested` does **not** fire at character select (it runs on investment refresh). Request tags can stay in `dump/request.txt`. The live dye PS is already dumped by the draw hook — do not wait on another investment dump.

**Look-alike probe (`019b_10` / `0698_26`) user-confirmed fail.** Saturation boost + 384 skin tile. Screenshot: black mask, charcoal vest, black arms, **white pants**. Luma gate, not albedo. Green never appeared. Restored. Do not boost again. Do not steal. Do not pack another 512.

---

## 0691 VANISH (2026-08-22, user-confirmed) — world VS on a skinned chest

`assign_split_materials.py` pointed tank/skin/twirl/necklace at `sandbox_0691` materials that already bind a unique slot-3 texture (same *layout* as the working mask: ~1408 B, one PS bind at slot 3). Character select **loaded**. The gas mask stayed. The rest of the custom body **vanished** (floating mask + leftover stock glove + weapon).

Cause, from the dumped materials, not a guess:

| | chest / mask | 0691 steal |
|---|---|---|
| vertex shader | **`0x81532C62`** (armor) | **`0x80FEBAC3`** (world; 136 of 1038 dumps) |
| pixel shader | `0x81531EE6` | four different 0691 PS tags |
| package | `037c` / `037d` / `0698` / `0699` | `sandbox_0691_6` |

Layout matching the mask is not enough. **Armor VS is not enough either.** `assign_armor_vs.py` pointed tank/skin/twirl/necklace at C62/C5B slot-3 binders (037c/0698/01fd) and painted those BC7 buffers. Character select loaded; mask stayed; the other four vanished — same screenshot as 0691. Restored `100421` (`moved 4 out`). The shared fact: those materials were **not on the chest model**. The five-part split only ever reused materials already on the chest, and that drew.

`assign_armor_vs.py` now exits on purpose. `assign_chest_mask.py` is the control: every split part names `0x80EFA1DC` (chest-native, already painted). Expect the mask atlas on the whole body (wrong UVs). Unique atlases stay blocked until a chest-native material binds a second unique buffer — do not steal off-chest tags.

Among 3,081 dumped materials, **492** use the armor VS. **113** of those have a texture array (the previous "7" was a split-size sampling hole: 1664–1728 B in 01fd/037c/0698 are full of slot-3 binders).

**The five slot-5/6 headers we dumped are not unique GLB atlases:**

| header | size | format | buffer | package |
|---|---|---|---|---|
| `0x80C70DCD` | 512² | BC1_SRGB | `0x80B34068` 163,840 B | `globals_019a` (dye-like) |
| `0x80C70F3F` | 512² | BC1_SRGB | `0x80B34021` 163,840 B | `globals_019a` |
| `0x80C1D9EB` | 512² | BC7 | `0x80B36018` 327,680 B | `sandbox_019b_8` (same family as the mask) |
| `0x80C0EDD2` | 256² | BC7_SRGB | `0x80B361B3` 81,920 B | `019b_8` |
| `0x80BC8F21` | 512² | BC1_SRGB | `0x80B6A115` 163,840 B | `01b5_8` |

Do not steal those onto the chest for unique art. `0x80C1D3CA` (slot 4 next to the mask) is already `suit_t5` in the dye legend.

**Gold is closed for off-chest steals.** Those four unique BC7 buffers exist, but pointing chest parts at their materials vanished the body. Do not re-run `assign_armor_vs.py`. Control is `assign_chest_mask.py` (live).

- `assign_split_materials.py` / any steal of `0x80FEBAC3` (or other non-armor VS) onto chest parts.
- `assign_armor_vs.py` / any steal of a material **not already on the chest model**, even C62/C5B.
- `bind_material_textures.py` — changing a texture *tag* or appending a PSTextures array hangs `character:signin`.
- Dye tiles t3/t5/t7. Dyes cannot carry the GLB's unique atlases.
- Overwriting `019b_8` / `0x80B3611D` with a flat colour.
- Hunting more 1408 B twins in 037c/037d/0698/0699/01fd — that set is empty.
- Stealing the slot-5/6 BC1 globals tiles (`0x80C70DCD` / `0x80C70F3F` / `0x80BC8F21`) as unique atlases.
- Stealing 256² / BC1 slot-3 (`0x80C1D358`, `0x80C6AA3E`, most of the 16) as unique atlases.

Next: in-world lighting / LOD miss (see top). Character select is done. Do not pack another 512. Hang = revert DLL.

The earlier "3,965 records, one texture array" count was **models**. After the bindable-material launch: **5,001 dumps, 1,038 materials, 846 with a texture array.** Chest materials that bind: only `0x80EFA1DC` and sibling `0x80EFA1E2`, both naming `0x80C1D3CD`. The 0691 "gold family" is a layout match with the **wrong vertex shader** — retired, see above.

`0x80C1D3CD` is 512². Do not reopen geometry.

---

> "HAND, LEGS, ARMS, ALL OF IT. it looks good like this in the tower, character screen, and in
> world (i went to mercury) no stretching or anything." — on `_20`
>
> "AHHH YEAH THATS WHAT IM TALKING ABOUT." — on `_22`

The custom `void_4003GasMask.glb` renders on the playable Guardian in **every view** — character
select, character screen, the Tower, Mercury, the EDZ — with correct proportions, articulated
hands, arms and legs, **no stretching**, and proper shading: defined gloves, sneakers, ribbed
fabric, real lighting response.

**Geometry, skinning and shading are all solved. Do not re-open them.** No re-tuning weights,
bone palettes, arm angles or the tangent frame.

Three things remain, none of them geometry:

1. **Four of five split materials bind no texture, and off-chest steals vanish.**
   Control confirmed. Unique colour is live as a 512 pack + UV tiles (`pack_chest_atlas.py`).
   Soft. Necklace UVs are collapsed (`v` constant). Do not `--save` until the user likes it.
2. ~~**The custom mesh has five texture atlases and we draw through two parts.**~~ **Done** —
   see "The five-part split" below. Five parts, five materials, one per atlas.
3. **Gloves still draw over the custom hands**, and the bald race head still draws above the gas
   mask. Both cosmetic leftovers. See "the leftover gloves".

Upstream Sunrise **is merged** (`cosmetics` = `upstream/master` as of 2026-08-22). Do not merge it again until the next upstream cut. Do **not** `--undo` the mesh.

---

## TEXTURES: the paint path WORKS. The problem is that four materials bind nothing. (2026-08-22)

**Breakthrough: pixels we wrote reached the Guardian, with no texture tag written anywhere.**
Painting `0x80B3611D` — the buffer named at `+0x24` of `0x80C1D3CD`, which is the *only* texture
`0x80EFA1DC` (the gas mask material) binds — put a clearly visible magenta patch on the mask.
User-confirmed in the character screen. **This retires the whole "we must repoint the albedo tag"
problem**: we do not need the write that has never landed
([[changing-a-texture-tag-never-landed]] is now a curiosity, not a blocker).

### The 40-byte texture header, decoded

    +0x00  u32  total size of the whole texture, all mips
    +0x04  u32  DXGI format enum   98 = BC7_UNORM, 99 = BC7_UNORM_SRGB
    +0x0C  u16  0xCAFE magic
    +0x0E  u16  width          +0x10  u16  height
    +0x24  u32  TAG of the buffer holding the top mips (type 48)

Confirmed by arithmetic: `0x80C1D3CD` says 512x512 BC7, total 349,552; its buffer is 327,680
(mip 0 + mip 1) and the remainder is exactly 21,872. Headers are **encrypted**, so they must come
from an in-game dump (`make_request.py`), not from disk.

**The header names only ONE tag.** The entry holding the small mips is *not* named and has **no
fixed offset** — in pkg 020e the headers cluster at 5065/5066/5069/5070 and the bodies at
5063/5064/5067/5068/5071. Guessing "header − 2" overwrote `0x80C1D3CB`, which the dye legend shows
belongs to a *different* texture (suit_t5), and corrupted it. `paint_albedo.py` now writes only the
buffer and requires an explicit `--small=` for anything else. Leaving the small mips alone is
visually free: the buffer holds mip 0 and 1, which is what renders at any normal distance.

### What blocked the other four parts (still blocked; 0691 steal vanished them)

Those four materials still have a **NULL** texture-array field. Giving them an array hangs.
Pointing them at `sandbox_0691` slot-3 binders loaded, then hid the body: world VS `0x80FEBAC3`
on a skinned chest. Steal only armor VS `0x81532C62` / `0x81532C5B`. See "0691 VANISH" at the top.

### Dyes are RULED OUT as the body's visible colour (one launch, user-confirmed)

`plate_t3` painted red, `suit_t5` green, `cloth_t7` blue — all three buffers verified in-package,
clean launch to orbit with 0 hitches and 0 errors — **no visible change anywhere on the body.**
The dye textures named in `dye_texture_legend.json` do not drive what you see. Do not re-probe them.

### Where to go next, in order of cost

1. Unique RGB on the bound dye tile is **closed**. Look-alike went white/black. Restored; live is `150046`.
2. Do not boost, steal, append a texture array, or paint `0x80B3611D` flat. Do not pack another 512.
3. Unique RGB is the **draw hook** matching `live_chest_ps.asm` (see top). Not another package PS.

## GET BACK TO A WORKING GAME — read this before touching packages

The working set is recorded as **data**, not prose. If anything is broken, or you do not know which
layers are live:

```powershell
python Sunrise\tools\pkg\known_good.py --check      # is the live set the good one?
python Sunrise\tools\pkg\known_good.py --restore    # make it so (moves files, never deletes)
python Sunrise\tools\pkg\known_good.py --list       # every saved snapshot
python Sunrise\tools\pkg\known_good.py --save "note"   # record the current set as good
```

Snapshots live in `tools/pkg/known_good/` and are committed with the repo. `20260822-092232.json`
is the 96-layer set below: **custom mesh, five-part split, orbit clean, Tower clean.** Negative
tested — pull a live layer and push a quarantined one back in, and `--check` names both and
`--restore` fixes both.

**Before every launch, also check for dangling references.** A layer removed from the middle of a
stack spins the game at character select forever, with no error anywhere:

```python
miss = [q for q in {b.patch_id for b in p.blocks} if p.patch_path(q).name not in live]
```

Run it over **our** packages only - shipped `w64_video_*` use `patch_id` values that are not file
indices and report thousands of false dangles.

## SOLVED: the Tower failure is `w64_sandbox_0699_7.pkg` (2026-08-22)

**One file.** With `0699` truncated to `_6`, the Tower loads in 5,055 ms with **zero** hitch asserts,
zero maydays, `ENUM(3)` complete in 3,307 ms, through to `activity:in_world`.

| config | Tower |
|---|---|
| vanilla | PASS 5,042 ms |
| 51 (`13:25`) | PASS 5,042 ms |
| 55 (`14:39`) | FAIL |
| **54 = 55 minus `0699_7`** | **PASS 5,055 ms** |
| 98 + all 417 rows reverted | FAIL |
| 55 with the `14:39` rows reverted | FAIL |

**It is NOT the painted content, and the section below is wrong about the mechanism.** Two
independent results kill it:

1. Reverting **all 417** painted rows across 30 packages left the Tower broken (orbit was fine).
2. A 55-layer config whose *content is byte-identical* to the passing 51-layer config — every
   painted row restored to its original block — **still failed**.

Paint layers change **only** the 8-byte `blockInfo` of an entry row (verified across all four
`14:39` layers: rows differing in the first 8 bytes = 0), so the revert is complete. Content is
therefore exonerated, and what remains is structural.

**And `0699_7` is not malformed.** Correct SHA-1 on every one of its 533 blocks, no nulls, all full
`0x40000` with one partial at the end, `flags=0` — formally identical to its three siblings from the
same write (`037c_23`, `037d_23`, `0698_22`), which stay live in the passing config and cause
nothing. The only things that distinguish it: it is **3–10x larger** (533 blocks vs 150/171/54), and
`0699` is the one package of ours the Tower's resource trace touches. **The mechanism is unknown.**
Do not invent one; five theories died already.

**We do not need the mechanism, because we do not need the file.** `0699_7` and `0699_8` are both
flat-colour paint sweeps. `0699_6` (the `10:12` group) is mixed mesh work and must stay. Per-package
truncation is legal — block tables never cross packages.

**The focus confound is dead.** Two failures (`no2103`, `64layers`) happened with the window focused
for the whole load. Backgrounding does not cause this.

## Superseded: "the paint sweeps are the bug" — mechanism disproven, kept for the data

**Vanilla loads the Tower in 5.0 s with zero hitch asserts.** That control had never been run, and
it settles that the Tower failure is ours. Bisected to a clean bracket by time cutoff:

| layers | cutoff | Tower |
|---:|---|---|
| 0 | vanilla | **PASS** — 5.0 s, `activity:in_world` |
| 51 | `13:25` | **PASS** — 5.0 s, ENUM(3) done in 3,254 ms |
| 55 | `14:39` | FAIL — 42.8 s |
| 58, 64, 84, 98 | later | FAIL — 36–50 s |

**The whole failure is one task, `ENUM(3)`.** It takes ~3.2 s in a passing run and never completes
in a failing one; `ENUM(8,9,10)` are gated behind it and never start. `ENUM(7)` **never completes
even in a passing run** — the old note that "tasks 3,7,8,9,10 are stuck" was reading normal
behaviour as pathology. The bail is a *deadline* failure, not a deadlock: the resourcerer blocks the
mainloop ~20 s (`hitch detected ... 'resourcerer process events'`, repeat counts to 4,608), the
network pump starves (`40657 ms since the last network update`), and the client times out **its own
in-process server** (`Lost connection to activity host ... > (20000 + 500) ms`) and maydays.

**What the `14:39` group is: 55 painted texture bodies, no mesh at all.** Classifying every layer by
the entry types it writes separates the two populations exactly — every group ≤ `13:25` writes mesh
records plus paired `40`+`32`; every failing group writes `{40: N}` alone.

**The mechanism is format, not pairing.** `paint_textures.py` fills an entry with one 16-byte BC7
mode-6 block repeated, on the stated assumption that a flat colour needs no knowledge of width,
height or mip count. That holds only if the entry really is BC7. It is not always:

- `5,586,944` = BC7 2048² → 128². Fill is valid.
- `2,793,472` = **ambiguous** — exactly BC1 2048² → 128² *and* exactly BC7 2048×1024 → 128×64.
- `1,115,200` (the `ui_01a3_7` orbit killer) matches **no** BC7 or BC1 chain at any dimension tried.

Entries whose real format is not BC7 get a byte pattern that is not a valid block stream. The
headers are **encrypted** (`blockflags=3`), so the format cannot be decoded offline and the
ambiguity cannot be resolved without either the in-game dump path or a launch.

### Three theories that died tonight — do not revive them

1. **"Cumulative volume."** Removing the 332 MB `17:37` group made the failure *worse* (58 layers:
   50.1 s / 4,608 repeats, vs 64 layers: 36.5 s / 2,048). Byte count is not the driver.
2. **"Paint sweeps skip the type-32 header, so the header is stale."** Refuted: painting preserves
   entry size and type *exactly* (verified across all four `14:39` layers, 0 differences vs the
   layer beneath), and the header is untouched by design. The `40`-without-`32` correlation was an
   artefact of which tool wrote which group.
3. **"The bind targets were malformed, so the tag write actually landed."** Refuted: all five bind
   target bodies are `5,586,944` B holding a valid repeated BC7 mode-6 block (`c0 ff 1f 00 …`,
   read directly — our blocks are `flags=0`, plain). They are well-formed. **The character-select
   hang on a changed tag value is still unexplained.**

### Layers cannot be removed from the middle — only truncated from the top

Moving the paint sweeps aside and keeping the mesh work **breaks the game** (character select spins
forever). Block tables are cumulative and later layers interleave their blocks with earlier ones:
`037c_28` needs 150 blocks from `037c_23` and 177 from `037c_26`. Pull a middle layer and every
layer above it dangles. `vanilla_mode.ps1 -Restore -UpTo <cutoff>` is safe because it truncates;
`-Hold` on a middle layer is not. **Check for dangling references before every launch:**

```python
# for each package we ever patched: every block.patch_id must have its file installed
miss = [q for q in {b.patch_id for b in p.blocks} if p.patch_path(q).name not in live]
```

(Do **not** run that check across shipped packages — `w64_video_*` use `patch_id` values that are
not file indices at all, and it reports thousands of false dangles.)

### The fix, and why it needs no keys

A paint layer **appends** blocks and repoints entry rows; it never destroys the originals. In
`037c_23` the block table grew 463 → 613 and every original block survives at an identical
`(patch_id, offset)`. So a **new top layer** that rewrites the painted entry rows back to their
values in the layer beneath restores Bungie's original encrypted content — no block keys, no format
decode, nothing removed, and the five-part split kept.

| entry | original row | painted row |
|---:|---|---|
| 2060 | `(269, 38096, 2793472)` | `(463, 0, 2793472)` |
| 2062 | `(279, 214224, 5586944)` | `(473, 172032, 5586944)` |

That writer does not exist yet. It is the next thing to build.

### Tooling that lied, and is now fixed

`tower_watch.ps1` produced **five** false verdicts in one evening. Root causes worth remembering:
PowerShell ranges **reverse** when start > end (`$a[$n..($n-1)]` returns elements, not empty), and
`Total time spent … state: [36]` is emitted on **every** exit from that state — a 5 s success prints
it exactly as a 39 s bail does, so testing for it as a failure signature reports vanilla as broken.
Judge by progression to `activity:physics_join` / `activity:in_world`, and **replay any verdict
logic against a known pass and a known fail before spending a launch on it** (`-Log <file> -Live`).
Captures are in `tools/pkg/captures/`.

---

## Read this first (Claude)

The briefing at the **top of this file** is current. This section used to list live layers;
those rows below are **historical** and will send you backwards. Do not treat them as live.

**Live / restore = `20260822-172401.json`.** Newest files that matter:

| package | live | note |
|---|---|---|
| `w64_sandbox_037c` | **`_28`** | five-part split geometry (byte-identical verts to `_22`) |
| `w64_sandbox_037d` | **`_26`** | all five parts name chest-native `0x80EFA1DC` |
| `w64_sandbox_019b` | **`_9`** | packed 512 on `0x80B3611D` (dye-gated; leave it) |
| `w64_sandbox_0698` | **`_24`** | 0–1 UVs. `_25` (tiles) is in `_reverted_uv_tiles\` |
| `w64_sandbox_0699` | **`_6`** | Tower-safe; do not bring `_7` back |

`037d_27` (1CBE swap) and the 14× 21:03 F8 color-probe pkgs are in `packages\_vanilla_test\` / the attic. Do not `--save` them back. Do not `--undo` geometry.

**Package indices are per-package, not a version number.** Do not read `_28` / `_26` / `_25` as one number.

Repo: `C:\Users\Round\OneDrive\Desktop\Destiny2ProjectSunrise\Sunrise` branch `cosmetics`. Sunrise DLL is hook **v12** (per-part GLB albedos). Snapshot `20260822-172401`. `0698_25` attic’d.

**Do not `--undo`.** `inject_mesh.py --undo` deletes *every* receipt patch and returns vanilla
Scatterhorn. Do not write a new mesh layer. Geometry is closed.

**The recipe that produced the working body** (do not re-run unless restoring from zero), from `tools/pkg`, Destiny closed:

```
blender --background --python retarget_mesh.py -- 23512 character_body.obj
python inject_scatterhorn.py --dry-run
python inject_scatterhorn.py
```

No flags. `--no-retarget` / `--no-authored` / `--no-uvs` / `--one-part` are bisect-only.

---

## The five-part split

**A Destiny part is a range of the index buffer plus one material, and a material holds one
texture set.** Everything we injected went through a single part, so the body could wear exactly
one texture set no matter how many atlases we encoded. This was the structural blocker under the
whole texture effort, and no amount of correct BC7 would have moved it.

The chest model `0x80EFA1CA` has **58 parts, 29 at LOD 0, and those 29 are three passes over the
same seven index ranges** — Destiny's three ordered material stages:

| pass | slots | materials |
|---|---|---|
| 1 | 0–20 | nine distinct: `0x81531EF0`, `0x81532AE0`, `0x80BFB5E5`, `0x80EF98DB`, `0x80EFA1F7`, `0x80EFA1DB`, `0x80EFA1DC`, `0x81531EEF`, `0x81531EEE` |
| 2 | 22–42 | almost all `0x80EF938B` |
| 3 | 44–56 | all `0x80EFAD53` |

`carrier_slots()` picked the largest range — offset 3,465, count 3,087 — and that range appears in
**both slot 10 and slot 32**. The "two carrier parts" this file used to describe were never two
parts: they were **one range drawn twice, in two stages**.

`retarget_mesh.py` already writes `character_body_groups.json` — per-triangle source material in
the OBJ's face order. `inject_scatterhorn.py` now sorts triangles by that group so each source
material is one contiguous run, then gives each run its own pass-1 part:

| source material | tris | Destiny material | slot |
|---|---|---|---|
| `GLSLShader85` BlackTankTop | 24,786 | `0x80EF98DB` | 10 |
| `GLSLShader13` SkinTats | 14,222 | `0x80EFA1F7` | 12 |
| `GLSLShader66` GasMask | 3,858 | `0x80EFA1DC` | 16 |
| `GLSLShader22` Twirl | 2,012 | `0x81532AE0` | 2 |
| `GLSLShader60` Silver_Necklace | 1,190 | `0x81531EF0` | 0 |

Sorting permutes **triangles only** — the vertex buffer, and with it the weights and the tangent
frame, is byte-identical. Geometry, skinning and shading cannot regress from this change.

Three things make it verifiable rather than hopeful:

- **`GROUP_MATERIALS` keys on material tag, not slot number.** A slot number means nothing outside
  the one model it was read from, and this mapping is applied to *both* chest models. They happen
  to lay pass 1 out identically; `slot_for_material()` checks that instead of assuming it.
- **Every other part is zeroed, including passes 2 and 3** over the ranges we keep. Otherwise the
  same triangles draw again under a material we did not choose.
- **`check_parts()` re-parses the bytes it is about to ship** and compares the drawn parts against
  the plan. Written against the parser, not the plan, so agreement is evidence. Negative-tested:
  feed it a mangled plan and it raises.

**Confirmed in game, first launch after the split.** The body stopped reading as one uniform white
surface: black arms and hands separate from a white-and-grey tank, a grey hood with the gas mask
reading clearly, and the **necklace chain visible at the collar** — the 1,190-triangle group
drawing on its own material. Nothing on the body came up in probe colours, only the weapon, so
none of the five materials sample anything the sweeps painted.

---

## The albedo is bound, not found

**Four of the five carrier materials name no texture at all**, and the fifth names one 21,872-byte
texture. That is not a search failure — it is what the material says. Of all fourteen materials on
the two chest models, exactly **one** (`0x80EFA1DC`, our GasMask carrier) has a pixel-texture
array. So the albedo was never going to be discoverable; it has to be **written**.

`bind_material_textures.py` writes it. What had to be decoded, all offline from dumped materials:

- A material field is `{int64 count, int64 offset}`, and the array's **count word** sits at
  `field + 8 + offset` — the marker is four bytes before it. This convention resolves **every**
  array in **every** one of the five materials, which is why it is trusted rather than assumed.
- An inline array is `[0x80809FBD][count][0][element class][0]` — twenty bytes — then elements.
- The pixel-texture element class is `0x80807211`, eight bytes: `{u32 sampler slot, u32 texture
  header tag}`. `0x80EFA1DC` has exactly one, at **slot 3**.
- The field slots are at fixed offsets: `+0x048` vertex shader, `+0x050` its textures (null in all
  five), `+0x2C8` pixel shader, **`+0x2D0` its textures**, `+0x2E8` bytecode, `+0x308` samplers,
  `+0x2F8` / `+0x318` float4 constants.

Because `+0x2D0` is a **relative** offset, the new array is appended past every byte the game
already knows about and the field aimed at it. **No existing byte moves** — verified: exactly three
bytes change inside the original span, all of them in the `{count, offset}` field itself. That is
what makes this safe to do to a structure whose other fields are not fully decoded.

| group | material | action | texture written |
|---|---|---|---|
| BlackTankTop | `0x80EF98DB` | appended array | `0x80EF880E` body / `0x80EF8811` header |
| SkinTats | `0x80EFA1F7` | appended array | `0x80EF881F` / `0x80EF881D` |
| GasMask | `0x80EFA1DC` | **repointed** slot 3 from `0x80C1D3CD` | `0x80EF89F3` / `0x80EF89F1` |
| Twirl | `0x81532AE0` | appended array | `0x80EFAD63` / `0x80EFAD60` |
| Silver_Necklace | `0x81531EF0` | appended array | `0x80EFB7B9` / `0x80EFB76B` |

**Slot 3 for all five, on two independent grounds:** the one material that already binds a texture
binds it at slot 3, and the dye system's plate channel carries its albedo at slot 3. The five pixel
shaders are *not* all the same (`0x81532B03`, `0x81531EE3`, `0x81531EE6`, `0x81532DAA` ×2), so a
group that stays Scatterhorn-coloured is reading a different slot — and only that group needs a
sweep, not the body.

**The atlases go in over existing textures, not into new entries.** All five targets are exactly
**5,586,944 B**, which is the full five-level BC7 chain for 2048×2048 — so an atlas lands at native
size with no resampling and no entry resize. Headers are rewritten from `0x80EFAD60`, a dumped and
verified header for exactly that shape. Twirl ships at 512² and is upscaled to 2048² so every group
shares one target size.

**If it crashes or renders wrong:** `.\revert_layer.ps1 -Confirm` on the four packages drops back to
the split. Never `--undo`.

---

## The hang: a record declares its own length

**A structured record carries its own byte length at `+0x00`, and the loader believes it over the
entry size.** `bind_material_textures.py` appended a 48-byte array and left `+0x00` at the old
value, so on the tank-top material the loader read `0x650` while the new array sat at `0x65C` —
**outside the region it had**. The texture-array field then pointed at bytes the loader never
loaded, and character select hung.

Measured, not assumed: across every dumped blob, **2,715 of 2,718 type-8 records** have `+0x00`
equal to their own byte length. Texture headers (32) and bodies (40) never do — they are raw data,
not records — which is why nothing before this ever tripped over it.

The fix is one line, plus a guard in `check()` that refuses to ship a blob whose declared length
disagrees with its actual length. Negative-tested: stale the length and it raises.

**This generalises.** Any future resize of a structured entry — material, dye body, entity record —
must update `+0x00`. The writer will happily produce a file the registrar accepts and the loader
hangs on, exactly as the null block SHA-1 once did.

## The hang, round two — the fix landed and the hang stayed

The corrected layer was launched 2026-08-20 23:48 and **hung identically**. Everything below was
established from that log and from the installed packages, with **no further launches**.

**The game names the failure**, in a third launch left running long enough for the assert to fire
(`trace_archive/sunrise-20260821-1441-hang-atlas-fixed-with-assert.log`):

```
ev=assert stage=hit text=hitch detected: mainloop world controller state character:signin
    job fiber:{{!0x00834002!}} phase:{{!-1!}} iteration:0x0 stalled     (x512)
```

A **job fiber deadlocks in `character:signin`** — a stall waiting on something that never arrives,
not a crash and not a bad dereference. Leave the next hang running past t≈87 s; the first two
launches were closed at ~45 s and the assert never fired.

**The fix is genuinely in the shipped bytes.** `verify_bind_layer.py` opens the newest installed
patch of every package, pulls all seventeen entries out of the block stream and checks what we know
how to state: all five materials declare their own length at `+0x00` correctly, all five resolve a
pixel-texture array with slot 3 pointing at the intended header, every block is plain with a
matching SHA-1 and 0x800 alignment, and all four packages pass `Package.check()` with zero
complaints. `diff_entry_rows.py` adds the half `verify_bind_layer.py` structurally cannot see — the
entry-table **rows** — and every one preserves its original `reference` (the class id the loader
dispatches on) and `type_info`. Only placement and, for the four appended materials, size moved.

**Entry size: what is actually proven, and what was over-claimed.** This file briefly said size was
"disproven". It is not. What the evidence supports is narrower:

- A 22-block, 5,586,944 B plain entry of ours can sit in a layer that **loads**. The five-part split
  the user confirmed working already contains three (`0x80EF880E`, `0x80EF881F`, `0x80EF89F3` in
  `037c_26` / `037d_24`, the 228-texture sweep).
- The game's **reader** returns one byte-exact — `0x80EFAD63` was dumped at full size under `_23`.

Neither of those is a 22-block entry being **bound to a drawing material and streamed as a
texture**, which is a different code path, and it is the path the repoint test exercised. Present
and readable is not the same as sampled. Size is a live suspect again; see "Warlock only".

**Retracted: "none of our seventeen tags appear at all".** They do not appear in a **healthy**
launch either — checked against `trace_archive/sunrise-20260820-205929.log`, zero hits for all
seventeen. It is a property of what `model_class_trace` logs, not evidence about this layer. The
discriminator that *does* work is volume past character select: a healthy launch logs **3,690**
lines after `ev=bootflow stage=character_select result=held`, both hangs log **~270** and stop.

**What is left is the material surgery**, the only thing both hung layers share and the only kind of
write this pipeline has never landed:

| write | ever shipped and loaded? |
|---|---|
| type-40 texture body, 5,586,944 B / 22 blocks | **yes** — `_26`/`_24` sweep |
| type-32 header rewrite | yes |
| type-8 record rewritten **at the same size** | **yes** — the chest models, five-part split |
| type-8 record **resized** | **never** |
| a texture binding **repointed** inside a material | **never** |

Those last two are both untested and both present in both hung layers. `--bind-only` cannot
separate them — it does both at once.

### `--repoint-only` is the bisect that separates them

`0x80EFA1DC` (GasMask) is the one carrier material that already owns a pixel-texture array, so
binding it costs **four bytes and no resize**: slot 3 moves from the suit dye tile `0x80C1D3CD` to
`0x80EF89F1`, the gas mask's own header, which is still the **original encrypted Scatterhorn
header** and describes its original shape. Its body `0x80EF89F3` is already flat **red** —
`(254, 0, 0)`, decoded from the BC7 block — left there by the `_26` sweep. So one entry, 1,408 B,
three bytes actually different, and the test reports itself on screen:

| outcome | conclusion |
|---|---|
| **gas mask turns red** | binding works; the fault is in the append/resize path |
| loads, gas mask unchanged | that pixel shader does not read slot 3; no hang cause here |
| hangs | repointing alone hangs the loader, and the append is innocent |

Guards, both negative-tested: the edit is refused if it resizes the blob, or if any byte outside the
slot-3 tag word moves.

## Warlock only — the hang is the bind, and it is per character

**Result of the repoint launch: the Hunter loaded and rendered. Clicking the Warlock stalled.**
Same assert, same fiber `0x00834002`, 5,120 repeats. That is the single most useful fact this
problem has produced:

- **Three bytes are enough.** One repointed texture tag, no resize, no new bytes, stalls the game.
  The append/resize half of the surgery is not needed to explain anything.
- **It is our content, not the container.** A character without our patched material builds fine in
  the same launch. Every "the game never reaches our stuff" reading of the earlier logs is dead.
- **It is a demand-load stall**, not a bad dereference — a fiber waiting on something that never
  arrives, while the rest of the process keeps logging.

The last model build before the stall (seq 2075, t=71,281) resolves `0x80EFA1CC`–`0x80EFA1D3`, the
chest model's own buffers at entries 460–467, immediately around the model at 458. Then nothing.

**Package locality is not the discriminator** — the binding that *works* (`0x80C1D3CD`) is in
`sandbox_020e`, a different package from the material, exactly like the one that stalls. Both are
type 32/sub 1, 40 bytes. The difference between them is what they point at:

| | body | size | blocks |
|---|---|---:|---:|
| worked | `0x80C1D3CC` dye tile, split | ~350 KB total | 1 |
| stalled | `0x80EF89F3` | **5,586,944 B** | **22** |

### Size is out too. What is left is residency.

The size control **stalled identically** — Titan and Hunter loaded, Warlock froze. So a 174,592 B
single-block target in the *same package as the material* fails exactly like a 5,586,944 B
twenty-two-block one in another package. That closes the whole "what we point at, physically" axis:

| axis | status |
|---|---|
| entry size / block count | **ruled out** — 22 blocks and 1 block stall identically |
| package locality | **ruled out** — the working binding is cross-package; a same-package target stalls |
| self-declared length | ruled out — unchanged, and verified correct in the shipped bytes |
| resize / append | ruled out — three bytes with no resize is enough |
| our writer's container output | ruled out — Hunter and Titan build from the same files |

**The material references the tag exactly once.** Mapped every tag-like word in the blob: the only
occurrence of `0x80C1D3CD` is at `+0x424`, the word we change. There is no second table to keep in
sync. The other three arrays in the material are samplers (`0x808073F3`, six elements pointing at
`0x80C70B8E` / `0x80C6B566`), an int array and a float4 array — none name a texture.

**Two hypotheses survive:**

- **(A) Residency.** A material may only bind a texture something already loads. Every texture we
  ever saw *sampled* was one already referenced by the thing sampling it — the `_26` sweep turned
  the weapon magenta by painting a texture the weapon's own material already named. Pointing at a
  texture nothing requests makes the loader demand-load it, and the fiber waits forever.
- **(B) Materials cannot be rewritten at all.** Never once landed. Note what *has*: type-8 **model**
  records (the five-part split), texture bodies and headers, geometry buffers, dye bodies. And note
  what has never landed anywhere: **changing a texture *tag*.** The dye work only ever changed slot
  *numbers*, never which texture a slot points at.

### Residency is dead too. The null control is all that is left.

The resident-dye repoint **stalled**. That target was the same *kind* of object as the binding that
works — a dye tile this Warlock loads to render the robe, same 21,872 B in one block, same split
layout, same package family — differing only in identity. So it is not residency, and it is not
anything about what the tag points at. **Three targets, three stalls, nothing in common but the
act of changing the word.**

Which leaves the one thing never tried: **does rewriting this material stall regardless of what it
says?** Every material layer so far also changed something, so "the write itself" has never been
isolated.

**The dumped material is pristine**, which this control depends on: `tag_80EFA1DC.bin` was written
2026-08-20 14:08, and the first patch that ever touched a material entry was written at 22:39 that
evening. At 14:08 the live layers were the `_22` geometry inject, which touches buffers and models
and no materials. `--null-control` re-checks it anyway — it refuses unless slot 3 still reads the
shipped `0x80C1D3CD` and the self-length agrees.

**Also decoded and set aside:** the 208-byte tail after `037c`'s block table is
`[0x80809FBD][22][0][0x80809A13][0]` followed by 22 pairs mapping **type-16** entries in `037c` to
type-8 records in `w64_manifest_068f`. Our material is type 8 in `037d`, and `037d`'s tail is
**empty**. Not the mechanism. `patch.py` already carries the tail through unchanged.

## The null control PASSED. Materials are writable.

**`037d_29` rendered the Warlock at character select and went on to enter `cleanup`** — the only
launch in this whole sequence to complete `character:signin` at all. All three repoints died inside
it. So:

- **Rewriting a material entry is sound.** The write, the block placement, the plain-block
  substitution, our whole container path for a type-8 material — all fine.
- **A changed tag value is specifically fatal**, and it is fatal to the *character-select preview*.
  Whatever validates that word lives outside the material and outside anything we have decoded.

**Correction, because it nearly sent this the wrong way:** `character:signin` logged
`Total time spent: [75680] ms` and that is **not** a stall. `character:signin` is the state the game
holds in *while the character select screen is up waiting for a click*. Seventy-five seconds is how
long the user looked at the screen. Never read that number as performance.

### The `cleanup` stall is a different animal, and may not be a stall

Selecting the Warlock enters `cleanup`, and there the assert is a different job entirely —
`job name: 'resourcerer process events', owner: 'system owner'`, not the anonymous
`job fiber:0x00834002` of the preview stalls. And **work was still completing when the game was
closed**:

```
Completed task 'ENUM(22)' after '25745ms'
Completed task 'ENUM(55)' after '25851ms'
state:cleanup: After 25851ms, status: '2', tasks active: '0x0020000800000000',
               pending: '0x0000000000000000', requested: '0x00e0010c30fbdf3c'
```

Two twenty-five-second tasks **finished**, in the last logged moment, with nothing pending. That is
grinding, not deadlock. It may simply need minutes.

**And its provenance is unknown**, which is the real gap: *this stack has never been taken to orbit
without a material patch on it.* The `_22` inject reached the Tower and Mercury, but that predates
the 228-texture sweep — `0699_8` alone is 186 MB. So `cleanup` being slow may be the baseline.

## The orbit stall IS a deadlock, and it does no I/O

**Correction to the section below: the orbit stall is real.** The "closed too early" reading was
wrong — it explained the *first two* reports, but a launch left running for two minutes settles it.
From t=72,812 to t=162,844 the state prints the identical line every fifteen seconds:

```
state:cleanup: After 116067ms, status: '2', tasks active: '0x0020000800000000',
               pending: '0x0000000000000000', requested: '0x00e0010c30fbdf3c'
```

**Tasks 35 and 53 start and never complete** — 50 started, 48 completed, and the stuck mask
`0x0020000800000000` is exactly bits 35 and 53. Nothing pending, nothing changing.

### The F8 capture: no package I/O at all during the stall

Armed at character select (t=44,140), `cleanup` entered at t=46,781, first hitch at t=67,625,
capture stopped at t=162,719. **203 reads in the whole window**, and the *last* one is at
**t=47,437** — twenty seconds before the stall is even reported, and ninety before it ends.

| file | reads |
|---|---:|
| `w64_video_01e5_0.pkg` | 99 |
| `w64_investment_globals_client_*` | ~50 across ten packages |
| `w64_ui_01a3_7.pkg` | 5 |
| `w64_shared_manifest_070d_6.pkg` | last read, t=47,437 |

**The game reads nothing while it is stuck**, so this was never a streaming or package problem, and
none of our patched content is implicated by it. The hitch names `resourcerer process events`, but
the resourcerer has no I/O outstanding — it is blocked on something else.

This also means the raised capture limit was not needed: 203 reads never approached even the old
8,192. The raise is harmless and stays, because a capture that *does* run long should not truncate.

### RETRACTED: "a different character" is not a control

A non-Warlock character stalls identically — same tasks 35 and 53, same mask, same 48-of-50. That
was briefly written up here as proof the orbit deadlock is not ours. **It is not proof, and the
claim is withdrawn.**

**Character select previews every character, so our patched content is resident whichever one is
picked.** Measured on the non-Warlock run itself: `0x80EFA1CC`–`0x80EFA1D3`, the Scatterhorn chest
buffers, are resolved *during `cleanup`*, and `sandbox_037c` / `037d` are among the packages the
cleanup phase touches. The test proves the stall is not **specific to selecting the Warlock**. It
says nothing about whether our layers cause it.

The real control is removing the layers, and `revert_layer.ps1` cannot do it — our work is 99 layers
across 32 packages, stacked up to 22 deep. `vanilla_mode.ps1` moves all of them aside at once and
`-Restore` puts them back; nothing is deleted and the layers underneath are untouched shipped files.

### The server side, which cleared itself

With `server` and `middleware` at debug, the in-process server is demonstrably **healthy and idle**:
a burst of real BAP traffic ends at t=42,500 (`svc=23 rsp=24`, `queuez stage=translate
result=paired`, all `result=ok`), and after that the only traffic for the next 130 seconds is a
28-byte heartbeat `svc=250 rsp=251` every five seconds, answered every time.

**The client stops asking.** It is not waiting on the server, and the server has nothing
outstanding. Combined with zero package I/O, the client is blocked on something internal while
`resourcerer process events` never finishes. The one warning in the whole launch,
`ev=queuez stage=notification result=skipped reason=null_payload`, fires at t=31,281 — during
bootflow, eleven seconds *before* `cleanup` — so it is not the stall, though it is unexplained.

## ANSWERED: our layers cause the orbit deadlock

**Vanilla reaches orbit.** With all 99 layers moved aside, the state machine goes
`cleanup -> setup:orbit` and **50 tasks complete** — the two that hang everywhere else, 35 and 53,
finish here. So the deadlock is caused by something we wrote, and the earlier
"a different character stalls too, so it is not ours" reading was exactly backwards.

### Bisecting it — step 1 PASSED

**Everything through 17:37 reaches orbit.** User-confirmed with the Warlock, custom mesh on it, and
the log agrees independently: `ENUM(55)` completed in **3,001 ms** instead of ~25,000, `cleanup`
took 3 s total, then `setup:orbit`, `bootflow stage=orbit_handoff result=released`, and
`slice_set_transition_manager: Stopping transition ... due to completed`. 50 tasks completed.

So the culprit is in the **last four groups**: `18:05`, `19:09`, `21:03`, `22:04`.

**Watch out for a false negative from the watcher.** `Get-Content -Raw` intermittently returns
nothing while the game holds the log open, and the watcher reused stale text and reported "still in
`bootflow:start`" for the launch that had actually reached orbit. Both `orbit_status.ps1` and the
watcher now open the log with a `FileStream` using `ReadWrite` sharing and treat a failed read as
*unknown* rather than as no progress. **A tool saying "no progress" is not evidence until it has
proven it can read the file.**

## SOLVED: `w64_ui_01a3_7.pkg` is the orbit deadlock

**Held it back, orbit reached — 50 tasks, clean.** Two painted entries in the UI package hang tasks
35 and 53 forever in `cleanup`:

| tag | type | size | header |
|---|---|---:|---|
| `0x80B46182` | 40/1 | 1,115,200 | `0x80B46183` |
| `0x80B46187` | 40/1 | 1,115,200 | `0x80B46186` |

Both pass every structural check — correct `entry_type`, correct paired header, whole number of BC7
blocks. The fault is not in the container; it is that **painting these two particular UI textures
makes the resource system never finish a task.** They arrived in the 21:03 sweep, the group that was
never launched, and sat under every test for the rest of the evening.

**Quarantined to `packages\_bad_ui_01a3\`** — a separate attic so `vanilla_mode.ps1 -Restore` can
never put it back. Nothing is lost: it was one of 150 speculative paints and the custom character
does not depend on it. `paint_textures.py` should skip `ui_*` packages if that sweep is ever rerun.

**Everything else restored — all 98 remaining layers, including the five-part split — and
confirmed.** The full set reaches orbit with 50 tasks, so the split is positively cleared rather
than merely "not necessary for the bug". **This is the good configuration: keep it.**

### Bug #2 is still open

The Tower still fails — `activity:initial_slice_set_loading`, lookups stop after ~3 s, mayday at
~35 s. Its culprit is in `{14:39, 16:17, 16:27, 16:37, 17:37}`, since step 1 already showed it.
Bisect that the same way once orbit is confirmed.

## How the orbit deadlock was found

**Step 3 failed** — restoring the `21:03` group brought the deadlock straight back: tasks 35 and 53
stuck, `0x0020000800000000`, nine hitch asserts. Only the five-part split was held back, so the
split is **innocent** and the culprit is one of the fifteen packages written at `2026-08-20 21:03`:
the "top 150 textures the F8 capture recorded being read" sweep — **the one group that had never
been launched even once.**

Everything at or before `19:09` reaches orbit. That is 81 layers including all the geometry work,
the five-part split's predecessors, the 228-texture sweep and every dye layer.

**Nothing is wrong with them structurally.** All **150** painted entries across the fifteen packages
are `entry_type` 40 / subtype 1 — a texture body, without exception, which is the filter
`paint_textures.py` applies. Checked offline; there is no mistyped entry to find. Per package:

| package | painted | | package | painted |
|---|---:|---|---|---:|
| `sandbox_01b5` | 28 | | `sandbox_01d5` | 9 |
| `sandbox_01bb` | 13 | | `sandbox_01d6` | 13 |
| `sandbox_01bc` | 19 | | `sandbox_01d7` | 1 |
| `sandbox_01bd` | 12 | | `sandbox_01d8` | 2 |
| `sandbox_01be` | 3 | | `sandbox_01d9` | 3 |
| `sandbox_01c0` | 18 | | `sandbox_01db` | 10 |
| `sandbox_01dc` | 2 | | `sandbox_01dd` | 15 |
| **`ui_01a3`** | **2** | | | |

`vanilla_mode.ps1 -Hold <name|wildcard>` moves named live layers into the attic, which is how to
bisect *within* a group whose members all share a write minute.

**Live test: `ui_01a3_7` held back, the other fourteen live.** A prior-driven shot rather than a
half-split — it is the only non-sandbox package in the group and the deadlock is in the transition
*to orbit*, which is UI-heavy. It is 2 of 150 entries, so a pass ends this immediately and a fail
costs one launch before falling back to binary search over the remaining fourteen.

### TWO bugs, not one

Step 2 (through 19:09) **reached orbit** — 50 tasks — and then **failed the Tower**. Both failures
are real and they are not the same failure.

| | orbit deadlock | Tower load failure |
|---|---|---|
| state | `cleanup` | `activity:initial_slice_set_loading` |
| ends? | **never** — identical printout for 90 s+ | **bails at ~35 s**, back to `cleanup` |
| stuck tasks | 35 and 53, forever | none; it times out |
| model lookups | none at all | ~1,980, then **stop after 3 s** |
| hitch asserts | thousands | none |
| tell-tale | `tasks active: 0x0020000800000000` | `Sending mayday`, `Total time spent: [34896] ms` |
| culprit group | `21:03` or `22:04` | already present at `17:37` |

**Retracted, same day:** an earlier reading here said the Tower report was a load still in progress
closed too early. It was not. In both runs the model lookups **stop about three seconds in** and
nothing follows for the next thirty; the game then sends a networking mayday and gives up. Eight
seconds was indeed too early to watch it *bail*, but the load had already stalled. The user's
"it froze" was correct in substance.

The lesson that keeps recurring: **"still logging" is not "still progressing".** Check whether the
*interesting* events are still arriving — here, model lookups — not whether the file is growing.
Heartbeats, roster pushes and keepalives continue right through a dead load.

### Orbit and the Tower: telling a live load from a dead one

Step 1 reached orbit, then **the Tower load was reported frozen**. Measured, it was almost certainly
not: it had been in `activity:initial_slice_set_loading` for **8 seconds**, the last task completed
**5 seconds** before the close, and **1,980 model lookups** landed in the preceding 3.2 s, with
tasks finishing at 21 / 187 / 787 / 3,227 ms. The server was pushing `dest=city_tower_social_d2`
rosters every second, all `result=ok`. Everything says mid-load.

**Know the three signatures apart:**

| | orbit deadlock | world load in progress |
|---|---|---|
| state | `cleanup`, never advances | `activity:initial_slice_set_loading` |
| printout | identical every 15 s for 90 s+ | tasks completing, models resolving |
| stuck tasks | 35 and 53, forever | none |
| hitch asserts | thousands | **none** |
| model lookups | none | ~2,000 in 3 s |

A Tower load is far heavier than orbit — **allow two minutes**, not eight seconds.

`orbit_status.ps1` was part of the problem and is fixed: it used to declare **DONE** the moment
anything past `cleanup` appeared, which is true the instant `setup:orbit` arrives and therefore
useless for a Tower test. It now judges by **progress** — how long the log has been quiet and how
long the current state has held — reports models resolved, flags `activity:` states as heavy, and
says **GAME NOT RUNNING** instead of STALLED when reading a finished launch's log.

**Note the old Mercury signature is NOT this.** `docs/PACKAGES.md` records
`activity:initial_slice_set_loading` followed by *nothing but networking heartbeats forever* as the
null-block-SHA-1 hang. That one has no task completions and no model lookups. This had thousands.

### The mechanics

The layers are additive in time, so restoring every layer written before a cutoff reproduces a
coherent historical state. `vanilla_mode.ps1 -Groups` lists the cutoffs; `-Restore -UpTo <time>`
reproduces one. The interval is bounded at both ends:

- **known good to orbit:** `2026-08-20 13:25` — the `_22` inject reached the Tower, Mercury, the EDZ
- **known bad:** everything

That leaves **nine groups** to search: 14:39, 16:17, 16:27, 16:37, 17:37, 18:05, 19:09, 21:03,
22:04. Binary search isolates it in about three launches.

**Step 2 is staged:** restored through **19:09**, holding back `21:03` (15 pkgs) and `22:04` (3).

| outcome | next |
|---|---|
| **reaches orbit** | culprit is in {21:03, 22:04} — one more launch splits it |
| **deadlocks** | culprit is in {18:05, 19:09} — one more launch splits it |

Priors worth holding lightly: **21:03 is the only group never launched at all** (15 packages
including `ui_01a3`), and 17:37 is the 228-texture sweep that put 186 MB into `0699_8`.

### Superseded: the vanilla control

**All 99 layers moved to `packages\_vanilla_test`.** The install is now exactly as shipped —
verified, nothing newer than the install date remains, and the Scatterhorn packages are back on
`037c_6` / `037d_5` / `0698_5` / `0699_5`. **The custom character is gone for this test.**

| outcome | conclusion |
|---|---|
| **vanilla reaches orbit** | our layers cause the deadlock. Restore and bisect by group — the 08-20 21:03 sweep (15 packages, never once launched) is the first suspect |
| **vanilla still deadlocks** | genuinely nothing to do with the package work — it is the mod or the install, and *that* is the thing to fix |

Put everything back with:

```powershell
.anilla_mode.ps1 -Restore
```

Expect the first vanilla launch to be slower: the content manifest cache will rebuild.

### Superseded: the control never run

**A character with none of our armour work stalls identically** — same two tasks (35 and 53), same
`0x0020000800000000` mask, same 48-of-50 completions, same `cleanup`. User-confirmed 2026-08-21.

So **none of the cosmetics work is implicated in it**: not the geometry, not the five-part split, not
the material rewrites, not any texture sweep. Combined with "no package I/O during the stall", the
orbit bug is mod-wide and independent of everything in this file. It does not block the cosmetics
thread conceptually — but it does mean **the character cannot be taken into the world right now**,
so cosmetic results have to be judged at character select until it is fixed.

**Where the cosmetics work actually stands, unaffected:** materials are writable (the null control
rendered the Warlock), and a *changed texture tag value* is what kills the character-select preview.
Size, package locality, residency, resize, self-length and the writer are all ruled out. That is
still the open question.

### The orbit bug: ask the component that is silent

`core/logging/levels` had `server` and `middleware` at **`warn`**, so the in-process server has been
mute this whole time. A deadlock with **zero I/O** in a mod whose server runs in-process is exactly
what those channels exist to explain. Raised to `debug` (and `core` to `info`) in
`C:\Sunriseind\Sunrise\settings.json`; original saved as `settings.json.bak-preserverlog`.
No rebuild needed — settings are read at startup.

**Write that file without a BOM.** `Set-Content -Encoding UTF8` adds one and the loader then rejects
the file with an error that blames the JSON rather than the encoding.

This is the same move that F8 was: the instrument was already there and switched off.

### Superseded: the control never run

Every orbit attempt in this file has used the **Warlock**. The Hunter and Titan reach character
select fine but have never been taken further. That splits the whole question in one launch:

| outcome | conclusion |
|---|---|
| **Hunter reaches orbit** | the stall is Warlock-specific, so it *is* our armour work, and the search narrows to what the Warlock loads that the others do not |
| **Hunter stalls the same** | nothing to do with any of this work — it is the mod or the install, and the cosmetics thread is unblocked the moment it is fixed |

No F8 needed; I/O is already ruled out. `orbit_status.ps1` gives the verdict without watching the
window.

## Superseded: "the orbit hang was closed too early"

The baseline — **no material patch at all** — stalled in `cleanup` identically: same job
(`resourcerer process events`), same ~25 s `ENUM(55)`. So the `cleanup` behaviour is **baseline**,
nothing to do with materials, and **the null control was a clean pass end to end.**

Then the timings, which settle what "hang" meant:

| run | `ENUM(55)` completed | game closed | gap |
|---|---:|---:|---:|
| null control `037d_29` | t=141,360 | t=141,360 | **0 s** |
| baseline `037d_25` | t=115,890 | t=123,390 | **7.5 s** |

Both were closed within seconds of the long task **finishing**, with `pending: 0x0000000000000000`
and two tasks still active. Nothing was deadlocked. Going to orbit blocks the mainloop for tens of
seconds at a stretch, so Windows greys the window and it reads as a hang — and orbit demonstrably
works on this install: the `_22` inject reached the Tower, Mercury and the EDZ.

**Judge a load by the log, not by the window.** `watch_load.ps1` follows it in a second terminal and
prints state changes, task completions and hitches as they happen. It waits for the log to rotate
first, so it follows the launch you are about to start rather than the last one.

```powershell
.\watch_load.ps1
```

**Give orbit five minutes** before calling it. Every "hang" recorded in this file was called after
45-125 seconds.

### The baseline (superseded — it answered)

All four material layers moved to `_reverted_material`. Live is the **clean five-part split** with
**no material patch at all**: `037c_28` / **`037d_25`** / `0698_24` / `0699_8`. Verified — entry 476
is back on its original block (patch 3, flags `0x0003`, compressed + encrypted), the chest model at
458 is still ours, zero complaints.

| outcome | conclusion |
|---|---|
| **reaches orbit** | `cleanup` is ours. The material rewrite has a second, later cost — and now there is a timing baseline to compare against |
| **same 25 s grind, then orbit** | `cleanup` is **baseline**, unrelated to materials, and the null control is a clean pass end to end |
| **never reaches orbit** | the split layer itself cannot reach orbit, which is a bigger and much older problem than the binding, and it moves to the front |

**Give it several minutes before judging.** Every "hang" in this log so far was called after 45–125
seconds, and the one launch that was allowed to run longer moved on to a new state. Note roughly how
long orbit takes.

### The superseded null control

Shipped as `037d_29`, byte-identical to the pristine dump. **Passed** — see above. Kept because it
is what proved the write sound.

| outcome | conclusion |
|---|---|
| **loads** | the write is sound. A *changed tag value* is specifically fatal — the tag is validated somewhere outside the material, and finding that validator is the whole problem |
| **stalls** | **a material entry cannot be rewritten at all.** Container-level, and it goes back to `patch.py`, not the material format — the chest model at entry 458 rewrites fine in this same package, so compare those two writes byte for byte |

Nothing visual to look for this time — it either reaches the character screen or it does not.

### The superseded residency test

Same three bytes, same slot, same material. Slot 3 now points at **`0x80C184F9`** — the cloth `t7`
dye texture. It is the tightest possible control: the *same kind of object* as the binding that
works (a dye texture this Warlock loads to render the Scatterhorn Robe), the same 21,872 B in one
block, the same split layout — differing only in **identity**. And our own dye probe already painted
it flat **blue `(0, 0, 254)`**, so it reports itself.

Shipped as `037d_28`. **Stalled.** Kept because it is what ruled residency out.

| outcome | conclusion |
|---|---|
| ~~gas mask turns blue~~ | **(B) dead, (A) confirmed.** Rewriting materials is fine; the target must be resident. Route: get our atlas into a texture the character already loads |
| loads, gas mask unchanged | **(B) dead** — slot 3 is not what that shader samples. Sweep slots for this group |
| **stalls again** | **(A) dead.** Rewriting a material stalls whatever it says. Next is the null control: write the material back byte-identical |

Either of the first two outcomes kills (B) without spending a launch on the null control, which is
why this goes first.

### The superseded size control

Same three bytes, same slot, same material — **only the size of the target changes**. Slot 3 now
points at `0x80EFB535`, whose body `0x80EFB534` is **174,592 B in one block**, in the same package
as the material, painted flat **yellow-green `(128, 254, 0)`** by our own sweep. 32× smaller, one
block instead of twenty-two.

Shipped as `037d_27`. **Stalled.** Superseded by the residency test above; kept because it is what
ruled size out.

```powershell
.\revert_layer.ps1 -Attic _reverted_atlas2 -Confirm   # back to the working split
python bind_material_textures.py --repoint-only --dry-run
python bind_material_textures.py --repoint-only
```

`revert_layer.ps1` now takes `-Attic`. Each reverted set needs its own, because `-Restore` moves
back everything it finds and a shared attic restores a layer that never existed.

**Reverting frees an index, and the next layer written takes the same filename.** The first repoint layer
is `w64_sandbox_037d_26.pkg` — the same name as the atlas layer now sitting in `_reverted_atlas2`.
`-Restore` refuses on that collision rather than overwriting the newer layer; move the live file
aside first if you really mean to put the atlas back. Guard negative-tested.

**Live now:** `037c_28` / **`037d_25` (clean split, no material patch)** / `0698_24` / `0699_8`.
The other four materials read back encrypted and original at their pre-append sizes — unpatched,
as intended. `0x80EF89F3` is still one flat red block, 5,586,944 B.

### How the self-length bug was found, and the two launches it cost

The first atlas layer changed three things at once: five material blobs resized, five texture
headers rewritten, and 27.9 MB of texture bodies written. `--bind-only` split that — the same five
material patches, **7,696 B**, pointing at a texture that already existed and writing no texture
data. It hung too, which cleared the atlases and put the fault squarely on the material surgery.
Then the invariant fell out of one offline scan.

The lesson is the bisect, not the byte: three changes in one layer cost a launch to separate, and
the separating layer was three orders of magnitude smaller than the one it replaced.

### What was ruled out on the way

The atlas layer **hangs the game at character select**, reproduced three times. It is a *hang*, not
a crash: no error, no dialog, the window stops responding and the log simply stops. Moved aside to
`packages\_reverted_atlas` (a separate attic from `_reverted` and `_reverted_26`, so `-Restore`
cannot put back a mixed set). The split underneath is live and good.

**What the logs say.** `model_class_trace` stops dead mid-resolution — last event at t=33,719 on the
fresh capture — then nothing but `application_state: Suspend` as the frozen window is clicked at,
and no `ev=shutdown`. ~~Across the whole launch none of our seventeen tags appear at all.~~
**Retracted** — they appear in no launch, healthy ones included; see "The hang, round two". Note the
log rotates **one deep on launch**, so two launches destroy a capture; archive it first.

**Ruled out, offline, against the reverted layer:**

- **Container malformation.** It reads back clean: 8,192 entries, 858 blocks, and every one of our
  entries has the right size, the right `entry_type` (40 body / 32 header / 8 material) and intact
  mutual references.
- **The null SHA-1**, which is this project's one recorded cause of a *hang* rather than a crash.
  `patch.py` writes `sha1(chunk)` per block record and has since the Mercury work.
- **`blockInfo` overflow.** `encode_block_info` bounds-checks the two 14-bit fields, and size gets
  36 bits; 5,586,944 round-trips correctly, which the read-back proves.

**Entry size was the leading suspect and was wrong.** Each atlas is 5,586,944 B ≈ 21 blocks, against
a previous verified maximum of 798 KB across four, and `docs/PACKAGES.md` records oversized Mercury
buffers hanging the loader. It fit the evidence and it was not the cause — `--bind-only` wrote
7,696 bytes and hung identically. **Live again** after the repoint result: the working split layer
carries three of our own 22-block entries, but none of them was ever *bound and sampled*. See
"Warlock only".

---

## The per-mesh bone palette was never real

`_18` was built on "each armour draw poses only the indices in that mesh's own vertex palette",
so a whole body had to be split across chest, legs and gauntlets. **That premise is false**, and
two offline tools now show it (`bone_frames.py`, `bone_probe.py`, both need no launch):

- **Eight bone indices appear on more than one piece** — 1, 3, 4, 5 on chest *and* legs; 15, 17,
  19, 20 on chest *and* gauntlets. Every one lands on the **same joint and the same side** in
  every piece that uses it. Bone 3 is the left thigh whether the chest or the legs names it.
  A per-piece remap would scramble sides; 8 of 8 agree.
- **Parts within one mesh share indices heavily** rather than partitioning them, so there is no
  per-part palette either. Bone 1 appears in nine separate chest parts.
- **The character-select body `0x80B9F962` poses 51 joints spanning 1..63 from one mesh.** One
  draw addressing the whole skeleton is normal here.
- The **chest's own mesh 1 already uses bone 20**, the very index `_18` treated as illegal there.

So bone indices are **one global skeleton index space** shared by every piece on the pawn.
A draw is not restricted to the joints its original vertices happened to name.

What actually bounds a write is the index *range*, and the shipped chest already writes up to
**28**. Joints 1–28 are a complete humanoid; everything above 28 is fingers. Staying at or below
28 is safe even under the pessimistic reading (a matrix array sized from the mesh's own highest
index), which is why `BODY_BONE_CEILING = 28`.

**Corroborating evidence that was already in this file:** the old "height-band + gauntlet bones
on the chest" run reported **"legs OK"**. Leg bones posed correctly on the chest draw then. Only
the arms failed, and those were the 34–71 finger indices, above the ceiling.

### What `_18` actually got wrong

Not the bone indices — the **frames**. It placed the mesh once, then packed three pieces with
three different model translations/scales, and **AABB-fit** custom limbs onto donor clouds:
thighs were rescaled onto Scatterhorn boot clouds, so most of the lower body took foot bones
(`(9, 2994), (10, 2915)`), and foot bones sit on the ground. That is the pillar. The claw hand
was the same mistake on gloves, which are wrist-and-finger shaped, not arm shaped.

**`fit_queries` / `nearest_fitted` are the dangerous functions.** The body path does not use them.

Earlier failures that already proved the same geometry:

| layer | what | Tower result |
|---|---|---|
| rigid bone 1, full 23k on chest | visible shape, spine-locked | connected-ish statue |
| unconstrained nearest robe+legs on chest | legs walked, waist tore | |
| height-band + **gauntlet bones on the chest** | legs OK, **arm needles** | unposed 20–71 |
| chest-legal palette, full 23k, gauntlets blanked | **best connected chrome body** | |
| **shoulder-cut** whole arms onto gauntlets | floating full-arm blobs, pillar torso | gloves are not arms |
| hand-only cut + glove bind frame (`_15`) | **sliver** + floaters | do not shrink into glove AABB |
| restore full chest, blank gauntlets (`_16`) | arms yanked back, **sheet between legs**, chrome noise | skinny mesh + L/R weight blend |
| side-lock + 70° swing (`_17`) | written to fix `_16`; user went to look, then demanded all bones | **no clear "this is good"** before `_18` |
| **3-slot all-bones (`_18`)** | pillar + shards | **worse.** AABB-fit, not bone indices |
| **whole body, chest draw, joints 1-28 (`_19`)** | connected, real legs | one bind frame |
| **retargeted + authored weights (`_20`)** | **WORKS** — hands, arms, legs | user-confirmed |
| tangent frame, stride bug (`_21`) | shards everywhere | position header shipped stride 24 |
| **tangent frame, fixed (`_22`)** | **WORKS** — properly shaded | user-confirmed |

Webbing on `_16` was proven: custom mesh median `|y| ≈ 0.07`. **Zero** L/R-spanning
triangles. Laplacian smooth across one "legs" band mixed left shin into right shin.
Crotch `|y|<0.08` is most of this body, not a gusset. Fix was `strip_crossed` + side
labels, **not** splitting onto the legs slot.

Arms yanked back: 55° swing left hands at `|y|≈0.56` vs robe sleeves ≈0.40. All of that
hand-tuning is **obsolete** — `_20` replaced the swing with a real retarget against `rig.json`.
See "The retarget — what `_20` changed".

### Hard rules

- Do **not** split a welded body across armour slots. It buys nothing — one draw poses the
  whole rig — and three model frames is what broke `_18`.
- Do **not** AABB-fit a limb onto a donor cloud (that is how thighs became feet).
  `fit_queries` / `nearest_fitted` exist only for the abandoned per-slot path.
- Do **not** write bone indices **above 28** into an armour mesh. 34–71 are fingers and are
  the only indices ever observed to misbehave. 1–28 are the body and are fine.
- Do **not** put hands on gauntlets. Gloves are wrist-and-finger shaped, not arm shaped;
  `_15` slivered and `_18` floated a claw.
- Do **not** wrap globals `0x80B9F962` / `0x80B9E810` / `0x80C717CA` / `0x80F56B13`.
- Do **not** copy robe weights by **height rank**.
- Do **not** inject sash jacket `0x80EC2AB4`.
- Do **not** dump patched sandbox tags whose **bodies we rewrote** (`037c` / `037d` /
  `0698` mesh/position/UV, `01e2` / `0699` UV, dye remaining-mip sRGB in `01db` /
  `020c` / `020e`, dye bodies `0x80EF9662` / `666` / `6AA`, plate/cloth DyeInfo
  sidecars `0x80EF9661` / `0x80EF969D` in `037c_25`). Safe: `0x81531EE8` / `EE9`,
  40-byte dye texture headers, DyeInfo **16-byte** headers, suit sidecar `0x80EF9663`.
- Do **not** `from resolve import ...` (CLI on import).
- Do **not** run `wrap_player_body.py` / `inject_player_body.py` (Tower frames).
- Tags: `0x80800000 + (package_id << 13) + entry`. Never OR.
- `parse_models.py` / `lookup_arrangement.describe()` scans every package on import —
  can hang ~90s. Prefer `Path.exists` on dump files.
- Dump is **once per process**. Destiny must be **closed** to write packages.

---

## The rig — recovered, named, and on disk

Charm's FK/bind classes `0x80808545` / `0x80808546` are nested inside an entity resource, not
package-entry types, and Shadowkeep has no hardcoded player base hash (Witch Queen's is
`0000670F342E9595`). Armour does **not** carry a bind-pose table; the runtime pawn owns the rig.
**That no longer blocks anything** — the rig is recoverable from the armour bound to it.

`python skeleton.py` → `objs/skeleton/rig.json` + `rig.obj` (line segments, importable).

Joints are estimated **at the blend, not the centroid**: vertices carrying ≥40/255 weight on
both a bone and its parent straddle the pivot, so their centroid *is* the joint. A dominant-weight
centroid, which is what `bones.json` holds, lands mid-shaft instead and reads ~0.2 m off.

25 joints, character space, metres, Z-up:

| bone | name | parent | x | y | z |
|---:|---|---:|---:|---:|---:|
| 1 | pelvis | — | 0.027 | 0.005 | 1.060 |
| 5 | spine_lower | 1 | 0.030 | −0.120 | 1.141 |
| 8 | spine_upper | 5 | 0.036 | 0.013 | 1.235 |
| 11 | chest | 8 | 0.021 | 0.014 | 1.311 |
| 12 / 14 | collar L/R | 11 | −0.012 / −0.025 | ±0.137 | 1.501 |
| 13 / 16 | neck, neck_02 | 11 | −0.075 / −0.106 | ~0.02 | 1.63 / 1.65 |
| 18 | head | 11 | −0.019 | 0.000 | 1.606 |
| 27 / 28 | shoulder L/R | 11 | −0.047 / −0.026 | ±0.189 | 1.420 |
| 15 / 17 | upperarm L/R | 27 / 28 | 0.036 / −0.013 | ±0.26 | 1.335 / 1.385 |
| 19 / 20 | forearm L/R (elbow) | 15 / 17 | 0.078 / 0.077 | ±0.315 | 1.279 |
| 21 / 22 | hand L/R (wrist) | 19 / 20 | 0.296 / 0.295 | ±0.392 | 1.150 |
| 3 / 4 | thigh L/R (hip) | 1 | 0.034 / 0.008 | ±0.14 | 0.976 |
| 6 / 7 | shin L/R (knee) | 3 / 4 | 0.073 | ±0.145 | 0.548 |
| 9 / 10 | foot L/R (ankle) | 6 / 7 | −0.031 | ±0.173 | 0.148 |
| 25 / 26 | toe L/R | 9 / 10 | 0.100 | ±0.199 | 0.036 |

Proportions check out: thigh 0.43 m, shin 0.40 m, standing height ~1.87 m. Bone 5 sits at
y −0.120, so "spine_lower" is a right-of-centre torso bone rather than a true spine — the only
name in the table that is a guess rather than a reading.

Bones 2, 23, 24 are never bound by armour. 34–71 are fingers (gauntlets only). The
character-select body `0x80B9F962` adds `2, 30–33, 35–38, 41, 47, 62, 63` — same skeleton, more
joints. Do not wrap it; it spins select cards.

`python census_bones.py` still writes the per-piece sets to `objs/skeleton/palette.json`. Read
those as **where to take a weight from**, never as what a draw is allowed to pose.

Do not dump EE8 children (`0x80C70B90`, `0x80C70BB7`/`BB8`); they are 48–88 B type tables.
Dumped chest extras `0x81531EE8` / `EE9` are `0x80803EB6` / `0x80803EB7`, not FK/bind.

`MAX_BONE = 80` is the packer's dense-weight array width, unrelated to what a draw can pose.
`BODY_BONE_CEILING = 28` is the rule that matters.

## The retarget — what `_20` changed

`retarget_mesh.py` replaces `prepare_mesh.py`. It runs in Blender, and it does three things the
old script could not.

**1. It poses the arms onto the rig.** The GLB is a T-pose; Destiny's bind pose is an A-pose with
the arms angled **forward**. Each arm chain is rotated about its own joint so the custom
shoulder→elbow and elbow→wrist directions match the rig's: upper arms turn 55°, elbows a further
19–21°. Only the arms are touched — the GLB's legs and torso already land within a few
centimetres of the rig (knee 0.529 vs 0.548, hips 1.031 vs pelvis 1.060, head 1.624 vs 1.606).

**Never retarget the torso.** Rig bone 5 sits at y −0.120, so pelvis→spine points sideways, and
aligning to it tilts the whole body.

**2. It exports the GLB's own weights.** Every vertex group maps to a rig bone index, so each
vertex keeps the weights its artist gave it. Fingers fold onto the wrist (21/22) because the
rig's finger joints are above the ceiling of 28. This replaces nearest-donor transfer entirely —
a transfer can only ask "which Scatterhorn vertex is nearest", and that is wrong wherever the two
bodies are different shapes.

**3. It drops unrigged objects.** The GLB carries an `Icosphere` — 42 verts, no vertex groups, a
2 m sphere on the origin. `prepare_mesh.py` welded it into the body. (It was *not* in the
`character_chest.obj` that shipped in `_16`–`_19`, so it never reached the game — but the script
the old recipe told you to run would have put it there.)

A retargeted mesh is placed **absolutely**, in rig space, not re-centred on the chest model's
bounding box. Re-centring lifted the whole body 10.6 cm and left the feet hovering above the
joints that drive them.

### What the audit says

Per-joint drift, custom vertices against the donor vertices on the same joint:

| joint | `_19` (donor weights, 70° swing) | `_20` (authored weights, retargeted) |
|---|---:|---:|
| 3 / 4 thigh | 0.215 / 0.242 | **0.065 / 0.061** |
| 6 / 7 shin | 0.135 / 0.141 | **0.097 / 0.103** |
| 9 / 10 foot | 0.072 / 0.071 | **0.003 / 0.003** |
| 15 / 17 upper arm | 0.207 / 0.155 | **0.014 / 0.025** |
| 19 / 20 forearm | 0.292 / 0.294 | **0.011 / 0.035** |
| 21 / 22 hand | **no geometry at all** | **843 verts each**, 0.064 / 0.081 |
| 25 / 26 toe | **no geometry at all** | **918 / 915 verts**, 0.001 |

Worst drift fell from 0.294 m to 0.103 m, and every joint in the rig now carries geometry.

Rebuild with:
```
blender --background --python retarget_mesh.py -- 23512 character_body.obj
```
`--no-retarget` leaves the T-pose, and `inject_scatterhorn.py --no-authored` falls back to donor
weights — those two flags are how to bisect if `_20` looks wrong.

---

## Facts that must survive a compact

**The state of the project**

1. **The custom character works in game.** `_22`, user-confirmed in every view. Geometry,
   skinning and shading are done. Only textures and two cosmetic leftovers remain.
2. **The playable character *is* its armour**, not a globals body. Equipped Scatterhorn is the
   live mesh, and inspect / character select / Tower / in-world all draw this one inject.
3. **The whole body rides on the chest draw alone**, carrier parts **10 and 32** of model
   `0x80EFA1CA` / `0x80EFA1A9` in `sandbox_037d`. Legs, gauntlets, hood, second robe and class
   item are blanked. Original robe ~4,100 verts; the custom body is 23,512 / 46,068 tris.

**The three things that took longest to learn**

4. **Bone indices are one global skeleton. 1–28 is a whole body, and one draw poses all of it.**
   The per-mesh palette never existed. `bone_frames.py` proves it offline in seconds. Never
   split a welded body across armour slots, and never AABB-fit a limb onto a donor cloud.
5. **The rig is recoverable from the armour bound to it** — `skeleton.py`, 25 named joints, at
   the *parent blend* not the bone centroid. The custom mesh is posed onto it and carries its
   own weights; nearest-donor transfer is gone and should not come back.
6. **The second vertex buffer is a tangent frame, and getting it wrong looks like liquid chrome
   long before a wrong albedo does.** Layout in `docs/GEOMETRY.md`; `decode_texcoords.py`
   re-verifies it against the geometry on demand.

**The two invariants that break silently**

7. **Diff your own patch layers when something breaks.** Anything we write is plain, so reading
   two layers back and comparing entry by entry needs no launch. `_21`'s shards were found that
   way in seconds: `positions` SAME but `pos_hdr` DIFFERENT, which is impossible if the pipeline
   is sane. `check_buffer_headers()` now guards that specific class of bug.
8. **Write the buffer's package, not the model's.** `package_of(position_buffer)`. Buffer
   *headers* live in `0698`; position buffers live in `037d`. They are different packages.
9. **Do not dump rewritten mesh/position/UV tags** in `037c` / `037d` / `0698` / `01e2` /
   `0699`. We **do** write dye bodies (`0x808071F3`) and DyeInfo sidecars now; do not dump
   those either. Exception: **`0x81531EE8` / `0x81531EE9`**, materials, 40-byte dye
   texture headers, DyeInfo 16-byte headers, suit sidecar `0x80EF9663`.

**Version facts**

10. **0.3.2+ is merged.** Shadowkeep classes still: `SEntity` `0x80809C0F`, resource `0x80809C36`,
    `SEntityModel` `0x808073A5`, entity-parent `0x8080744A`, material `0x808071E8`.
    Charm WQ classes are **zero**.
11. **Assignment hop is closed.** Item → arrangement index (`build_data.bin`) → hash table
    `0x81319329` → assignment table `0x81613D23` → parent map `0x80EC3F61` → entity-parent
    (`0x8080744A`, 24 B, SEntity at `+0x10`) → SEntity → resource → `SEntityModel`. Scan the
    resource for class `0x808073A5`; the tag is often at `+0x64C` / `+0x65C`.
    Chest arrangement 2293 / hash `0x4A8A34A0` is Scatterhorn Robe, user-confirmed.

---

## Retractions still standing (visual proof, not inference)

1. **`0x80B9F855` / `0x80C23B5D` / `0x80FA2308` are NOT the player body.** Tower frames.
2. **`0x815B868B` / `0x815B8697` (globals_06dc) are not body variants.** 5.04 m world objects.
3. **Constructor "owner" tags are not owners.** They are class `0x80807140`, the 208-byte
   technique that *references* a material. The material itself is class `0x808071E8`.
4. **Investment-root slot 4 (`0x81327CF0`) is NOT the assignment table.** Count **1,170**.
5. **Slot 19 (`0x81327CDE`) is NOT the assignment table.** Count **5,181**.
6. **`0x80EC2012` is legs/boots**, first cluster in `investment_0361`, not the equipped chest.
7. **Sash jacket `0x80EC2AB4` / `0x80BA7661` is a different item.** Do not inject.
8. **`0x80BC5220` / `0x80BC5205` are entity *resources*, not models.** Real leg models:
   `0x80EFA93B` / `0x80EFA92E`.

---

## Equipped loadout

Warlock `soid 0x9EAA300100100103`, settings `race 0, gender 0, class 2`, Scatterhorn
**equipped**. `python lookup_item.py` reads `build_data.bin`.

| slot | item hash | arr | arrangement hash | models A / B |
|---|---|---:|---|---|
| helmet | `0xEA042965` Scatterhorn Hood | **2295** | `0x8FB9E751` | `0x80EFA859` / `0x80EFA850` |
| gauntlets | `0x188C5834` | **2292** | `0x3FBE9128` | `0x80EF981E` / `0x80EF9809` |
| chest | `0xF8689C4C` Scatterhorn Robe | **2293** | **`0x4A8A34A0`** | `0x80EFA1CA` / `0x80EFA1A9` |
| legs | `0x083E04B6` | **547** | `0xB01AFB7D` | `0x80EFA93B` / `0x80EFA92E` |
| class item | `0x99446581` | **2294** | `0x0A9977DD` | `0x80EFA528` / `0x80EFA51F` |

`helmet_mode` in `settings.json` is what the game receives on sign-in. **0 = Helmet Always
On.** Hood is blanked so the gas mask can read. `state.cosmetics.ornament_replaces_arrangement`
is **false**.

Gender 0 → A vs B is **unproven**. Write both.

Chest SEntities: `0x80EFA1D8` / `0x80EFA1B7`. Primitive 3 triangle list. UV stride 24.
Second robe on the same SEntity: `0x80EFA1D4` / `0x80EFA1B3` (stride 48) — keep blanked.

### Assignment hop (closed)

```
hash 0x4A8A34A0
  → 0x81613D23  assign 0xCB405903 / 0x90273609
  → 0x80EC3F61  parent 0x80EEA8B7 / 0x80EEA5D6   (investment_0375)
  → SEntity     0x80EFA1D8 / 0x80EFA1B7           (sandbox_037d)
  → model       0x80EFA1CA / 0x80EFA1A9
```

| tag | class | package | what |
|---|---|---|---|
| `0x81319329` | `0x80807546` | 058c | arrangement hash table, Monteven `index * 4 + 48` |
| `0x81613D23` | `0x80805DF5` | 0709 | hash → two assignment hashes, 32 B records |
| `0x80EC3F61` | `0x808056EA` | 0361 | `{assignmentHash, entityParent}` |
| `0x8080744A` | | 0375 for Scatterhorn | entity-parent, SEntity at **`+0x10`** |

---

## Identification loop

```powershell
python extract_mesh.py 0x80EFA1CA --dump C:\Sunrise\bin\x64\Sunrise\dump --out-dir objs\scatterhorn
python render_obj.py objs\scatterhorn\model_80EFA1CA.png objs\scatterhorn\model_80EFA1CA.obj
```

If a render looks like noise, the **stride is wrong**. `ui_037e` is stride 48.

---

## What is proven

- Write pipeline is correct (0.5x position patch dumped back byte-for-byte; `--undo`; 0x800
  align; per-block SHA-1; arbitrary entry resizing).
- Armour geometry decodes and renders offline, and so does the second vertex buffer.
- Equipped Scatterhorn lives in **sandbox** `037c` / `037d` / `0698`, not investment lookalikes.
- **Bone indices are one global skeleton.** Proven offline, three independent ways.
- **The rig is recovered** — 25 named joints with pivots, `objs/skeleton/rig.json`.
- **The whole custom character renders correctly in game**, posed and shaded. `_22`.

---

## What `_22` is, exactly

Destiny closed, from `Sunrise/tools/pkg`:

```
blender --background --python retarget_mesh.py -- 23512 character_body.obj
python inject_scatterhorn.py
```

- One placed mesh, all **23,512 verts / 46,068 tris**, on **chest mesh 0 of both `0x80EFA1CA`
  and `0x80EFA1A9`**, carrier parts **10 and 32**, every other part's index count zeroed.
- Placed **absolutely in rig space** (`z -0.008..1.765`), not re-centred; scale grown 0.700 →
  1.013 so the body packs.
- **Arms posed onto `rig.json`** — upper arms 55°, elbows 19–21°. Torso and legs untouched.
- **Weights are the GLB's own**, mapped to rig bone indices, clamped to joints ≤ 28.
- **Tangent frame is the GLB's own** — UV, normal, tangent, handedness — with the model header's
  texcoord scale/translation set to `0.5 / 0.5`.
- Legs, gauntlets, hood, second robe, class item **blanked** — the one mesh covers all of them.
- Patches: `037c_22` (2 entries), `037d_22` (14), `0698_21` (10).

The dry run prints a **per-joint drift audit** — where custom vertices on a joint sit against
the donor vertices on the same joint. `_18`'s pillar was thighs 0.6 m from their donors and the
old bone histogram could not show that. Nothing in `_22` exceeds 0.103 m, and every joint in the
rig carries geometry.

Blender: `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`

Build outputs, all gitignored and all regenerated by that one Blender command:
`character_body.obj`, `_weights.json`, `_frame.bin`, `_groups.json`.

---

## The leftover gloves, and the bald head

Two cosmetic leftovers, both low-risk and neither geometry.

**The bald race head draws above the gas mask.** Visible in the character screen: a smooth grey
head sits above the custom mask. `helmet_mode` in `settings.json` is `0` (Helmet Always On) and
the hood model is blanked, so the head being drawn is the **race/gender head**, not an item — the
same default mesh the old `probe_heads.py` work could never find in `globals`. It is drawn by the
character path, not by armour, which is why blanking armour never removed it. Options: find and
blank that head, or accept it and design the custom head around it.

**A glove still draws over the custom hands.** `0x80EF981E` / `0x80EF9809` now *are* our
hand mesh. Arrangement 2292 has no sibling. The leftover is the replicated default upper
body (7685/7754 signature). Probe 2026-08-23 blanked `0x815B9521` and `0x80FDBE41`.
Do **not** blank the UI copy `0x80EFC649`. Do not `--fingers`.

Separate: the GLB has no `RingFinger*` groups, so Destiny ring bones 44/55 have zero verts.
That is the stiff digit, not a leftover mesh. Index/middle/thumb/pinky are weighted.

## `_21` shipped a stride bug. `_22` is the fix.

`_21` exploded the body into huge angular shards in every view. The cause was **one shadowed
local variable** in `inject_mesh0`: `header` held the *position* buffer header, and the new
texcoord branch reassigned `header` to the *texcoord* header before the position header was
written. So the position buffer's 12-byte header shipped with **stride 24 instead of 16**, and
the game read 23,512 stride-16 vertices at stride-24 offsets.

Nothing objected, because **every size was right** — only the stride was wrong, and the writer
never rewrites a stride, so it had no reason to look at one.

Diagnosis took no launch. Our own patch files are plain, so reading `_20` and `_21` back and
diffing entry by entry showed `positions` SAME but `pos_hdr` DIFFERENT — which is impossible if
the pipeline is sane, since the same buffer at the same size must produce the same header.

`check_buffer_headers()` now refuses to ship any buffer header whose stride does not match the
buffer it describes, or whose size is not a whole number of vertices at that stride. **Diff your
own patch layers when something breaks** — the writer is offline-readable and it is the fastest
instrument in the toolbox.

## `_22` — the stride-24 buffer is decoded, and we now write it

The "chrome" was never a texture problem. `clone_uvs` only *resized* the shipped second vertex
buffer, so every **normal and tangent** in it was interpolated nonsense, and a broken tangent
frame reads as reflective noise long before a wrong albedo does.

The layout is in [docs/GEOMETRY.md](docs/GEOMETRY.md) and `decode_texcoords.py` re-verifies it on
demand: UV pair, unit normal, zero pad, unit tangent, `+/-32767` handedness, secondary UV.

`_22` writes a real one. `retarget_mesh.py` exports `character_body_frame.bin` (9 float32 per
vertex: u, v, normal xyz, tangent xyz, handedness) and `inject_scatterhorn.py` packs it, setting
`texcoord_scale` / `texcoord_translation` to `0.5 / 0.5` so the int16 range covers exactly `0..1`.

The buffer we write passes every check the shipped one does — unit lengths at std 0.00001, pad
`{0}`, sign `{±32767}`, normal ⟂ tangent at mean `|dot|` 0.0000, UV round-trip error 8e-6 — and
carries the original's near-constant `uv2` (0.782) across unchanged.

**Expectation for `_22`:** the lighting should stop being liquid metal and start reading as a
shaded character. The **albedo is still Scatterhorn's**, now sampled through the custom model's
own UV layout, so it will look like robe texture laid over the body — different, not yet right.

Two known limits, neither worth acting on before the Tower verdict:

- **UV seams are welded shut.** 7,785 of 138,204 face corners disagreed with their vertex's UV.
  Destiny stores one UV per vertex, so welding to 23,512 forces a pick. The fix, if seam smear
  shows: inject the GLB **unwelded** at 61,908 vertices, which still fits under the 65,535 index
  ceiling.
- **The v-flip is the one unverified bit.** Blender's UV origin is bottom-left and Destiny is
  assumed top-left, so `retarget_mesh.py` writes `1 - v`. Geometry cannot confirm this. If the
  texture lands upside down, that line is why.

## Then: textures — the shape of the problem

**Correction:** materials are class **`0x808071E8`**, 1,032–1,616 B. `0x80807140` (208 B) is the
*technique/owner* that references them; the earlier note had the two the wrong way round.

`material_probe.py` reads a part's first four bytes as its material tag, offline. The chest model
has **14 distinct materials over 58 parts**, and the two carrier parts we write draw through:

| carrier part | material | size | package |
|---|---|---:|---|
| 10 | `0x80EF98DB` | 1,616 B | `sandbox_037c` |
| 32 | `0x80EF8C3C` | 1,184 B | `sandbox_037c` |

**The structural problem: the custom model has five materials, each with its own UV atlas.**

| group | source mesh | triangles | maps |
|---|---|---:|---|
| GLSLShader85 | BlackTankTop | 24,786 | base colour, normal, roughness |
| GLSLShader13 | SkinTats (the body, incl. arms) | 14,222 | base colour, normal, roughness |
| GLSLShader66 | GasMask | 3,858 | base colour, normal, roughness |
| GLSLShader22 | Twirl | 2,012 | base colour, normal |
| GLSLShader60 | Silver_Necklace | 1,190 | base colour, normal, roughness |

A Destiny material samples **one** texture set, so two carrier parts can only ever wear two of
those five. `retarget_mesh.py` now writes `character_body_groups.json` — the source material of
every triangle, in the OBJ's face order — so the injector can sort faces into **five contiguous
per-material index ranges** and give each its own part. The model has 58 parts to spend; we use 2.

**Reuse existing material entries, do not invent new ones.** Pick five of the 14, rewrite each to
point at textures we upload, and each carrier part keeps a material tag the game already trusts.

### A texture is two entries, and the 40-byte header is fully decoded

**A texture is a 40-byte header entry and a data entry that reference each other.** True for all 55
in the Scatterhorn packages — `0x80EFAD60` (40 B) ↔ `0x80EFAD63` (5,586,944 B). Data is
**5,586,944 B** or **2,793,472 B**, its half. The header, dumped and read:

| offset | value | field |
|---|---:|---|
| `+0x00` | 5,586,944 | data size, exactly the data entry's |
| `+0x04` | 98 | **raw DXGI format enum** — `DXGI_FORMAT_BC7_UNORM` |
| `+0x0C` | `0xCAFE` | magic |
| `+0x0E` | 2048 | width |
| `+0x10` | 2048 | height |
| `+0x12` | 1 | depth |
| `+0x14` | 1 | array size |
| `+0x16` | `08 05` | ?, **mip count** |

Five mips is why the size is 5,586,944 and not 5,592,405: the chain stops at 128×128, and
2048→128 sums to that byte for byte. The body confirms the format independently — every block
begins with bit 6 set, which is BC7 **mode 6**.

### A material does **not** name its textures

Two earlier readings here were wrong and are corrected. The entries at `+0x048`/`+0x2C8` are not
texture headers; they are 40-byte **shader** headers, and their bodies begin `44 58 42 43` —
**DXBC**, with `ISGN` input signatures and `TEXCOORD` semantics. The `+0x4D0` 52-byte entries are
not fallbacks; each is a **`D3D11_SAMPLER_DESC`**, field for field and exactly 52 bytes: filter
`0x55` = ANISOTROPIC, address 3 = CLAMP ×3, MipLODBias `0xBF000000` = −0.5, MaxAnisotropy 4,
border 0,0,0,0, MinLOD 0, MaxLOD `0x7F7FFFFF` = FLT_MAX.

Fully decomposed, `0x80EF98DB` accounts for all 1,616 of its bytes. Arrays are
`[0x80809FBD][count, 0, elem_class, 0][data]`:

| offset | element class | count | what |
|---|---|---:|---|
| `+0x048`, `+0x2C8` | — | 2 | vertex and pixel shader (640 B apart: two stage blocks) |
| `+0x420` | `0x80800009` | 70 B | binding bytecode, 4-byte opcodes |
| `+0x480` | `0x80800090` | 3 | float4 constants |
| `+0x4D0` | `0x808073F3` | **5** | `{u32 tag, u32 pad, u64 hash64}` resource refs |
| `+0x540` | `0x80800090` | 17 | float4 constants |

All five resource refs are **samplers** (`0x80C70B8E` ×2, `0x80C6B566` ×3). So a material names
shaders, samplers and constants — **and no textures at all**. Two independent checks agree:
searching all 12 dumped materials against every one of the game's 181,141 forty-byte entries
returns only the two shaders, and the one 64-bit field that could hold a texture (`+0x60`) is
`0xCBF29CE484222325`, the FNV-1a offset basis — the hash of nothing.

The binding therefore lives *above* the material: the appearance system's **dye / shader** path.

### `_23` swatch probe: loaded, sampled by nobody

User launched, closed, reported no swatch colour on the Guardian. That is a real negative, not a
missed install.

Proof the patches were live: this same launch dumped `0x80EFAD63` (one of the 55) at 5,586,944 B,
and that dump is byte-for-byte the flat `#447A98` block we wrote into `037d_23`. Unique 16-byte
blocks in the first kilobyte: **1**. Offline `Package.read_entry` of the same tag is plain,
`patch_id=23`, flags 0. If the Guardian had sampled it, it would have been a solid slate blue.

What the "slot tables" actually were: `0x81532C61` / `0x81532B04` / `0x81532693` / `0x81532C5F`
are **DXBC shader bodies**, paired with 40-byte shader headers the same way textures pair. They
begin `44 58 42 43`. Charm's Shadowkeep material layout matches the dump: VS at `+0x48`, PS at
`+0x2C8`, `VSTextures` at `+0x50` and `PSTextures` at `+0x2D0` — both **count 0, rel 0** on the
two carrier materials.

The part-10 pixel shader (`0x81532B04`) still *declares* the slots it will sample. SHEX
`dcl_resource_texture2d` (opcode `0x58`) names **t0, t1, t2, t5, t6**; `dcl_sampler` (opcode
`0x5A`) names s1–s5, which is why the material's five resource refs are samplers. The GPU binds
those t-slots at draw time from dyes, not from anything in the material.

### A texture is `entry_type` 40 with a type-32 header. Nothing else.

**Entry pairing alone does not identify a texture, and believing it cost a GPU crash.** The first
sweep painted every 40-byte header paired mutually with a body — 527 entries — and the game died
at character select with *"Graphics runtime detected a crash or loss of device"*, error code
`broccoli`, with no characters drawn. **Geometry buffers pair a header with a body exactly the
same way**, so 356 of the 527 were type-41 buffers, and the GPU read flat colour as vertex data.

`entry_type` comes from the plain entry table, so it costs no launch and no key:

| | `entry_type` | `entry_subtype` |
|---|---:|---:|
| texture body | **40** | 1 |
| texture header (40 B) | **32** | 1 |
| geometry buffer | 41 | 0, 1, 2, 6 |

All 55 textures found by exact mip-chain size are `(40, 1)` with a `(32, 1)` header, without
exception. With that filter the four packages hold **255 textures**, not the 1,638 that pairing
suggested — 228 of them a whole number of BC7 blocks and paintable.

Two further traps, both caught by asserting the 55 known textures survive the filter:

- **A pair may straddle packages.** `0x80EFADA6`'s body is in `037d` with its header in `037c`.
- **A header may live outside the family entirely.** `0x80EFB8FC`'s header is in `03c1`.

So walk **bodies**, not headers, and resolve the header globally. Only the body's location decides
whether we want it. Iterating headers within one package silently dropped four textures, then two.

**`revert_layer.ps1` is the recovery.** Patches are additive and the loader takes the newest file,
so moving the top layer aside restores the one beneath exactly. Moving, not deleting — a bad
layer's original bytes cannot be regenerated, since they were encrypted and never dumped.

### Correction: the 55 were a fraction of the textures, not all of them

`paint_textures.py` originally found textures by matching **two exact byte sizes** — 5,586,944 and
2,793,472, the totals a 2048² and a 1024×2048 BC7 mip chain happen to come to. That is not what a
texture is.

Found **structurally** instead — a 40-byte header entry and a body that reference each other, no
size assumption — those four packages hold **1,638 textures**, sizes 184 B to 5,586,944 B:

| package | textures | of those, painted in `_23` |
|---|---:|---:|
| `sandbox_037c` | 406 | 9 |
| `sandbox_037d` | 429 | 10 |
| `sandbox_0698` | 175 | 4 |
| `sandbox_0699` | 628 | 31 |

A texture is also allowed to be **split**: `+0x24` of the header names a buffer holding mip 0+1,
and the entry paired with the header holds the remaining mips. Size-matching sees neither half.
That is the same layout the dye textures use, and it is why sizes like 5,488 and 21,872 are
normal rather than suspicious.

So "those textures never bind to the Guardian" is only true of the 55. The sweep in `_26` paints
the other **527** (the rest of the 1,638 are not a whole number of BC7 blocks and are left alone),
in 12 contiguous colour buckets of ~40 tags each.

`ours()` keeps the sweep off our own work: everything the writer emits is a **plain** block, and
plain blocks are the only thing in a shipped destination package that is neither encrypted nor
compressed. That excludes the injected vertex, index and UV buffers — which pair a header with a
body exactly like a texture and would otherwise be repainted — and the dye bodies and sidecars
written in `_25`.

### How a Destiny shader actually paints the mesh

A cosmetic shader is not composited onto a texture file. It is a **plug** whose art block
publishes material pairs `(key, dyeIndex)`. `character_appearance_render.cpp` folds the base item
then every plug, stage by stage, and only the first **six** distinct keys reach the appearance
record. The gear renderer looks those six pairs up and binds each dye's textures onto the mesh
material's empty t-slots.

Scatterhorn Robe `0xF8689C4C` default pairs, from `build_data.bin`:

| key | dye | channel (Charm names) |
|---:|---:|---|
| 0 | **6714** | ArmorPlate |
| 1 | **6715** | ArmorSuit |
| 2 | **6716** | ArmorCloth |

Initial shader plug `0x491C30A4` (bucket 14, 30 override rows) maps the same three keys to dyes
7101/7103/7102, and those three resolve to the **same entity-parents** as 6714/6715/6716. One
dump covers both.

### Dye hop (bodies dumped 2026-08-20; hop is closed)

`0x81613D24` is the art-dye table, 12,506 × 8 B (`artDyeHash`, `dyeManifestHash`).
`0x80EC3F60` maps a `dyeManifestHash` onto an entity-parent. Class `0x8080744A`, 24 B, child at
`+0x10`, package `investment_0375`. For **armour** that child is an SEntity. For **dyes** it is
not: WQ's `0x80806FA3` is zero here; SK uses class **`0x808071CD`**, 24 B, 4,966 of them, in
`sandbox_0207`.

`0x808071CD` layout (proven from the dump, not from Charm): FileSize at `+0x00`, channel index
at **`+0x08`** (0 plate / 1 suit / 2 cloth), dye-body tag at **`+0x0C`**, `0xFFFFFFFF` at
`+0x10`.

| dye | entity-parent | stub (`0x808071CD`) | body (`0x808071F3`, 1,515 B) |
|---:|---|---|---|
| 6714 / 7101 | `0x80EEACFC` | `0x80C0F9A3` channel 0 | **`0x80EF9662`** `gear_dye_0` |
| 6715 / 7103 | `0x80EEAD33` | `0x80C0F9A7` channel 1 | **`0x80EF9666`** `gear_dye_1` |
| 6716 / 7102 | `0x80EEADF8` | `0x80C0F9DB` channel 2 | **`0x80EF96AA`** `gear_dye_2` |

SK dye body, dumped, Charm `SDye` analogue:

| offset | field |
|---|---|
| `+0x00` | FileSize 1515 |
| `+0x08` | StringPointer → `gear_dye_N` |
| `+0x40` | DyeTextures DynamicArray, element `0x80807211`, `{uint32 slot, FileHash header}` |
| `+0x88` | DyeData DynamicArray, 27 × float4, element `0x80800090` |
| `+0xB8` | uint32 (7 plate / 6 suit / 5 cloth) |
| `+0xBC` | FileHash → 16-byte DyeInfo header; `entry.reference` is the **432 B sidecar** |

| channel | slots | header | format | large (`+0x24`) | large size | remaining mips |
|---|---|---|---|---|---:|---:|
| plate | t3 | `0x80BB71AD` | BC7_UNORM_SRGB 256² | **`0x80B6A11D`** | 81,920 | 5,488 |
| plate | t4 | `0x80B7B43E` | BC7_UNORM 256² | `0x80B361D2` | 81,920 | 5,488 |
| suit | t5 | `0x80C1D3CA` | BC7_UNORM_SRGB 512² | **`0x80B3611C`** | 327,680 | 21,872 |
| suit | t6 | `0x80C1D3CD` | BC7_UNORM 512² | `0x80B3611D` | 327,680 | 21,872 |
| cloth | t7 | `0x80C184F9` | BC7_UNORM_SRGB 512² | **`0x80B763E8`** | 327,680 | 21,872 |
| cloth | t8 | `0x80BB3BFD` | BC7_UNORM 512² | `0x80B6A137` | 327,680 | 21,872 |

Charm `STextureHeader.LargeTextureBuffer` at `+0x24` holds mip 0+1. `entry.reference` holds the
rest (64px-and-under, or 128px-and-under). Header `DataSize` is the sum.

DyeInfo sidecars (not dumped before `_25`; Charm comments "Bungie stopped using the DyeInfo
file" and copies from inline DyeData instead):

| channel | 16-byte header | 432 B body (`entry.reference` of the header) |
|---|---|---|
| plate | `0x80EF9660` | `0x80EF9661` |
| suit | `0x80EF9664` | `0x80EF9663` (still shipped; dump this one) |
| cloth | `0x80EF96A9` | `0x80EF969D` |

432 B = 27 × float4, same count as SK DyeData. WQ Charm `DyeInfo` is only 21 vec4s.

### Probe log (do not repeat a row)

| probe | layer | visual | conclusion |
|---|---|---|---|
| atlas bind, self-length stale | `_reverted_atlas` | **hang at character select** | the `+0x00` bug; real, fixed |
| bind-only, self-length stale | `_reverted_bindonly` | **hang** | cleared the atlases, not the surgery |
| atlas bind, self-length fixed | `037c_29` / `037d_26` / `0698_25` / `0699_9` | **hang** | fix verified present in the shipped bytes; surgery is what is left |
| repoint only, 3 B, 22-block target | `037d_26` | **Hunter loaded; clicking Warlock stalled** | three bytes are enough; the append is innocent; it is our content, not the container |
| same repoint, 1-block target | `037d_27` | **Titan + Hunter loaded; Warlock stalled** | size and package locality both ruled out |
| same repoint, resident dye target | `037d_28` | **Warlock stalled** | residency ruled out; nothing about the target explains it |
| null control, material byte-identical | `037d_29` | **Warlock rendered; reached `cleanup`; ground there** | **materials ARE writable**; a changed tag value is what kills the preview |
| baseline: no material patch at all | `037d_25` | **same `cleanup` grind** | `cleanup` is **baseline**, not ours; the null control passed end to end |
| same baseline, allowed to run + F8 | `037d_25` | **genuine deadlock: tasks 35 and 53 never complete, zero package I/O for 90 s** | not streaming, not our patches being read |
| Hunter/Titan to orbit | `037d_25` | **stalled identically** | not *Warlock-specific* — but our content is resident anyway, so this exonerates nothing |
| orbit with server logging at debug | `037d_25` | **server healthy and idle; client stops asking** | not a server problem; client blocked internally |
| vanilla: all 99 layers removed | shipped packages | **REACHED ORBIT, 50 tasks completed** | our layers cause the deadlock |
| bisect step 1: restored through 17:37 | 64 of 99 layers | **REACHED ORBIT** (cleanup 3,001 ms, not 25,000); Tower closed at 8 s, inconclusive | culprit is in {18:05, 19:09, 21:03, 22:04} |
| bisect step 2: through 19:09 | 81 of 99 layers | **orbit OK**; Tower failed | orbit culprit is {21:03, 22:04} |
| bisect step 3: through 21:03 | 96 of 99 layers | **DEADLOCKED** | the split is innocent; culprit is in the **21:03 sweep** |
| `ui_01a3_7` held back, other 14 live | 95 of 99 layers | **REACHED ORBIT** | **`ui_01a3_7` is the orbit deadlock** |
| all 98 layers, `ui_01a3_7` quarantined | **current** | **REACHED ORBIT, 50 tasks** | confirmed: full custom character + orbit. The split is positively cleared |
| **Tower on the full set** | current | — | bug #2: expected to fail; culprit in `{14:39 … 17:37}` |
| 55× 2048/1024 sandbox BC7 swatches | `037c_23` / `037d_23` / `0698_22` / `0699_7` | **nothing**; dump of `0x80EFAD63` was our paint | those **55** never bind — but see the correction below; this ruled out 3.4%, not the packages |
| 527× everything that *paired*, 12 buckets | reverted, in `packages\_reverted` | **GPU device loss** at character select, no characters drawn | 356 of them were type-41 **geometry buffers**; pairing does not identify a texture |
| 228× every `entry_type` 40 texture, 12 buckets | `037c_26` / `037d_24` / `0698_23` / `0699_8` | **the weapon turned magenta. The body did not change at all.** | **paint reaches the GPU and shows** — first positive result. The body's albedo is not in these four packages |
| all three dye channels' albedo tile rebound to t0, both mip halves painted | `037c_27` + six dye packages | **nothing** | t0 is not the albedo slot for any of the three channels. The dye tiles are inert, so any colour seen from here is not them |
| 384× every texture in the "hot" packages, 12 buckets | **reverted, never launched** | — | a guess about *where* to look, replaced by the measurement below before it cost a launch |
| 36× the textures named in model lookups, one colour per package | `fx_019d_6`, `globals_0211_6`, `sandbox_01e4_6` / `01ef_6` / `0207_7` / `020c_8` / `0378_6` / `037b_6` / `0692_6` / `0695_7` | **nothing** | the tag graph never names the body's texture — see below |
| **top 150 of the 1,237 textures the F8 capture recorded being READ**, 12 buckets | **`sandbox_01b5_8` / `01bb_8` / `01bc_7` / `01bd_7` / `01be_6` / `01c0_6` / `01d5_7` / `01d6_4` / `01d7_5` / `01d8_6` / `01d9_7` / `01db_8` / `01dc_6` / `01dd_6`, `ui_01a3_7` (current)** | **awaiting launch** | 73.4 MB. A colour names ~12 tags, then `--only` bisects |
| remaining-mips **incl. normals** t4/t6/t8 | deleted | **clay-white + black splotches** | dye remaining-mips **are** sampled; do not flatten normals |
| sRGB **top mips** t3/t5/t7 | deleted | grayscale zebra weave | character select is not sampling mip 0+1 |
| sRGB **remaining-mips only** t3 red / t5 green / t7 blue | `01db_6` / `020c_6` / `020e_7` | **no colour** | those sRGB tiles are not visible albedo |
| inline DyeData albedo vec4s | `037c_24` | **no colour** | Charm's inline copy is not the live colour path |
| **t0 remap + DyeInfo sidecars** | **`037c_25` (current)** | **awaiting launch** | green = t0; red/blue = sidecar; nothing = neither |

t0/t1/t2 are still unnamed by the dye. Part-10 PS declares t0, t1, t2, t5, t6. DyeTextures only
name t3–t8. Material float4s on `0x80EF98DB` look like roughness knobs, not Scatterhorn
grey-green albedo — do not patch those until `_25` returns.

Leave `_23` installed. Leave remaining-mip sRGB paint installed (needed for the t0 test).
Do not `--undo`. Geometry still `_22`.

Mesh 1 (stride 12; chest bone 20, legs 25/26) is a separate packer. `rewrite_chest` zeros
non-mesh-0 parts. Do not un-zero original extras.

---

## Tools (`Sunrise/tools/pkg/`)

| tool | what |
|---|---|
| `retarget_mesh.py` | **Current mesh build.** Blender: drops unrigged objects, poses the arms onto `rig.json`, welds/decimates, exports OBJ + per-vertex weights on rig bone indices + `_frame.bin` (tangent frame) + `_groups.json` (source material per triangle). |
| `material_probe.py` | Reads each part's material tag offline, and writes the dump request for the material bodies. |
| `texture_probe.py` | Finds every texture pair offline (40-byte header ↔ data) and decodes the header. |
| `paint_textures.py` | **Current texture step.** Flat-colour probe of every `entry_type` 40 texture in 12 colour buckets, `--only=A..B` to bisect one. `ours()` skips plain blocks so it never repaints our own buffers; `already_painted()` readmits our own flat paint so a bucket *can* be bisected. Asserts the 55 size-matched textures survive the filter. |
| `glb_textures.py` | Pulls the custom model's own atlases out of the GLB with no Blender and no glTF library, reporting dimensions and which material wears each. `--extract` writes them to `objs/textures/`. |
| `encode_texture.py` | Encodes an atlas to a Destiny texture body: BC7 mode 6 with a mip chain, sized to an exact entry (`--tag` or `--size`). `--verify` decodes it back with independently written code and reports the error; `--self-test` checks flat colours round-trip. |
| `trace_model_tags.py` | **Ask, do not guess.** Reads `model_class_trace`'s `r9tags` out of `sunrise.log` — every tag the game resolved per model, logged from process start with no F8 — and reports the textures among them. Writes `traced_textures.json`. |
| `trace_textures.py` | The F8 package-read capture, mapped through the block table to entries and filtered to textures. Archives the log first, because it rotates one deep. Use when a model lookup names no texture. |
| `paint_dye_slots.py` | Dye rebind step. Rebinds all three dye channels' albedo tile to one t-slot (`--slot`, default 0) and paints each channel's texture a distinct colour in **both** mip halves, so the channel reports itself and the mip level cannot confound it. |
| `revert_layer.ps1` | **Recovery.** Moves each package's newest layer aside, reverting to the one beneath. `-Confirm` to act, `-Restore` to put it back, `-Attic` to give a set its own directory. Refuses while destiny2 is running. |
| `paint_albedo.py` | Paint an image into the buffer a material *already* samples. No tag change. Under the pack, `019b_9` holds the 512 atlas on `0x80B3611D`. |
| `pack_chest_atlas.py` | **Current texture write.** Packs five GLB albedos into `0x80B3611D` and scales each part's UVs into a tile. Texcoord U/V only. |
| `request_bindable_materials.py` | Fills `dump/request.txt` with **armour-family** materials (037c/037d/0698/0699/01fd). Not 0691. |
| `scan_material_textures.py` | After a material dump: binds + VS/PS. Keep only armor VS `0x81532C62` / `0x81532C5B`. |
| `assign_split_materials.py` | **Do not run.** 0691 world VS vanished four parts. Restored 100421. |
| `assign_armor_vs.py` | **Do not run.** Off-chest C62/C5B vanished four parts. Same as 0691. |
| `assign_chest_mask.py` | All five split parts name `0x80EFA1DC`. Live as `037d_26`. |
| `known_good.py` | Snapshot / restore the live layer set. `--check` before every launch. |
| `bind_material_textures.py` | **Do not run.** Tag rewrite and array append hang `character:signin`. |
| `verify_bind_layer.py` | Reads the shipped layer back out of the installed packages as the game would — entry sizes, `entry_type`, self-length, texture-array resolution, per-block SHA-1 and alignment. Offline; run it before every launch. |
| `diff_entry_rows.py` | Diffs an entry's **table row** (`reference`, `type_info`, placement) against the layer beneath. The half `verify_bind_layer.py` cannot see, because it reads bodies *through* those rows. |
| `paint_dye_textures.py` | sRGB remaining-mips t3/t5/t7. **Done.** On disk, no visible colour. Leave installed for `_25`. |
| `paint_dye_tints.py` | Inline DyeData tints. **`037c_24`. Failed visually. Do not rerun.** |
| `paint_dye_bind.py` | Dye t0 remap + DyeInfo sidecars. **Failed visually. Do not rerun.** |
| `dye_probe.py` | Dye index → entity-parent → `0x808071CD` stub (`+0x0C`) → `0x808071F3` body. Hop closed. |
| `decode_texcoords.py` | Decodes and re-verifies the stride-24 second vertex buffer against the geometry. |
| `prepare_mesh.py` | Superseded. Joins every object including the unrigged icosphere, and throws the armature away. |
| `inject_scatterhorn.py` | **Current injector.** Whole body, chest draw, joints 1–28, authored weights when a `_weights.json` sits beside the mesh. |
| `bone_frames.py` | Proves the index space is global. Offline, seconds. |
| `bone_probe.py` | Raw part records + per-part bone sets. Standalone, no `parse_models`. |
| `skeleton.py` | Recovers the rig → `objs/skeleton/rig.json` + `rig.obj` |
| `census_bones.py` | Per-piece bone sets → `objs/skeleton/palette.json` |
| `inject_mesh.py --undo` | Deletes **all** receipt patches. Do not use to "go back one." |
| `prepare_mesh.py` | Blender weld/decimate to vertex budget |
| `extract_mesh.py` + `render_obj.py` | Identification |
| `parse_skeleton.py` | EE8/EE9 — not FK |
| `lookup_arrangement.py` / `lookup_item.py` | loadout hop |
| `check_dumps.py` | dumps poisoned by an installed patch |

`wrap_player_body.py` / `inject_player_body.py` = Tower frames. Do not run.

---

## Environment

| what | where |
|---|---|
| Game | `C:\Sunrise`, Shadowkeep `86657.20.08.23` |
| DLL | `C:\Sunrise\bin\x64\steam_api64.dll` |
| Dumps | `C:\Sunrise\bin\x64\Sunrise\dump\` (~3,889 files), `dump_models\` (351) |
| Logs | `...\Sunrise\logs\sunrise.log` — **rotates one deep, archive immediately** |
| Custom GLB | `C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb` |
| Patch receipt | `Sunrise/tools/pkg/inject_receipt.json` |
| Packages | `C:\Sunrise\packages\` |

**Docs:** `Sunrise/docs/TOOLS.md`, `PACKAGES.md`, `GEOMETRY.md`, `COSMETICS.md`, `CLAUDE.md`.

---

## Where to pick up

**Read the top of this file and `docs/CUSTOM_CHARACTER.md`.** v12 is confirmed on character select. Snapshot `20260822-172401`. In-world lighting is next. Do not `--restore` 150046.

Do not run `assign_split_materials.py` or `assign_armor_vs.py`. Do not steal off-chest materials. Do not run `bind_material_textures.py`. Do not paint `0x80B3611D` with a flat colour. Do not pack another 512. Do not retry `1CBE`. Do not saturation-boost.

**The character works. Do not re-open geometry, skinning or the tangent frame.** Do not flatten dye normals. Do not rerun `paint_dye_tints.py`. Do not `--undo`.

The Tower is already solved (`0699_7` out). Ignore the superseded Tower-bisect paragraph that used to live here.

The section below ("Stop sweeping") is **historical**. Do not paint traced packages. Do not run another F8 sweep. Albedo is the bound dye tile plus a draw-time hook, not a missing type-40 tag.

## Stop sweeping. The log already says which textures the game loads.

**`model_class_trace` logs an `r9tags=` list on every `SEntityModel` lookup — every tag the game
resolved while building that model — from process start, with no F8 window needed.** It has been
writing that to `sunrise.log` the whole time. Four sweeps painted well over a thousand textures
across twelve packages to find the albedo by elimination, and every one was a guess about *where*
to look, when the answer was already on disk.

`trace_model_tags.py` reads it. From the `_27` launch: **1,470 model lookups, 39 distinct
textures**, in ten packages — and **`sandbox_0692` holds 14 of them**, a package no sweep had ever
considered.

| package | textures |
|---|---:|
| `sandbox_0692` | 14 |
| `globals_0211` | 6 |
| `sandbox_01e4` | 4 |
| `sandbox_037b` | 4 |
| `sandbox_0378` | 3 |
| `fx_019d`, `sandbox_0207`, `020c` | 2 each |
| `sandbox_01ef`, `0695` | 1 each |

`paint_textures.py --traced` paints exactly those, one colour per package: **1.0 MB**, against
667.8 MB for the sweep it replaced. Three are skipped as 4-byte-but-not-8 sizes (87,380 / 508 /
21,844) and are not authored rather than written ragged.

**Not every texture is BC7.** The traced set contains 43,704 and 87,408 — the same 5,463 blocks at
8 and at 16 bytes — so `bc1_flat()` handles the 8-byte families (BC1, BC4) that a 16-byte-only
painter silently skipped.

### The art side is ready and waiting on the slot

Nothing extracted from Destiny has ever been a texture — only geometry — and the material
dependency set says why. But the custom model's own atlases were never pulled out either, and
they are what actually goes on the body. `glb_textures.py` gets them with no Blender and no glTF
library: a GLB is a header then a JSON chunk then a BIN chunk, images sit at a `bufferView`, and
PNG/JPEG headers give dimensions without an image library.

| material | mesh group | base colour | normal | roughness |
|---|---|---|---|---|
| GLSLShader85 | BlackTankTop | 2048² | 2048² | 2048² |
| GLSLShader13 | SkinTats (body + arms) | 2048² | 2048² | 2048² |
| GLSLShader66 | GasMask | 2048² | 2048² | 2048² |
| GLSLShader60 | Silver_Necklace | 2048² | 2048² | 2048² |
| GLSLShader22 | Twirl | 512² | 512² | — |

**2048×2048 is exactly the top level of Destiny's 5,586,944-byte textures**, so an atlas goes in
at native size with no resampling and the byte count already matches.

`encode_texture.py` does the encode: BC7 mode 6 with a mip chain, hitting an exact entry size.
Mode 6 because every block of the dumped `0x80EFAD63` is mode 6 and the flat-colour probe proved
the game renders ours. The mip count is **derived from the target size, not assumed** — 2048²
comes to 5,586,944 bytes at five levels and 5,592,405 at a full chain, and guessing wrong is a
corrupt texture.

Verified against a decoder written separately from the encoder, so agreement is evidence rather
than tautology: **mean absolute error 0.72–0.79 of 255, 93–99% of pixels within 2, alpha exactly
255 everywhere.** Worst-case blocks are those needing three or more distinct colours, which a
single-line mode cannot fit.

### The tag graph never names the body's texture. The read trace does.

Parsing **every** tag field settled it — `stage=lookup` carries `r9tags`, `stage=resource` carries
`tag=` and `paytags=`, and matching only the first shape read a third of the events. With all
three, exactly one event names a carrier material, and this is its whole dependency set:

| tag | type | what |
|---|---:|---|
| `0x81532694`, `0x80EFAD59`, `0x815B9224` | 33 | three shader headers (the last in `globals_06dc`) |
| `0x80C6B566` | 34 | sampler |
| `0x80EF8C3C` | 8 | the material itself |

**No texture, of any type.** More entry types, for the record: material **8**, shader header
**33**, sampler **34**, texture body **40**, texture header **32**, geometry buffer **41**.

So no tag-graph search can ever find it, which is why four sweeps and the model trace all came
back empty. The **F8 package-read capture** can, because it records what the game *reads* rather
than what it *names*: 1,323 reads mapping to **1,237 texture entries**.

A read covers a whole block and every entry sharing that block is credited, so the read count is a
ranking rather than a proof — hence `--top`. The body is a large surface whose maps are read
repeatedly, so it should rank high; the top 150 come to 73.4 MB against ~1 GB for all 1,237.

1. Current: **the top 150 by read count**. **One** character-screen look, then quit.
   - **a bucket colour on the body** — `python paint_textures.py --traced --only=0xAAAA..0xBBBB`
     bisects those ~12 tags to one or two.
   - **nothing** — re-run `trace_textures.py <archived log> --top 400` and paint the next slice;
     the capture is already on disk in `tools/pkg/trace_archive/`, so this costs no launch to
     prepare. The full set is ~1 GB and writable if it comes to that.
2. `_27` rebound all three dye channels to **t0** and showed nothing, so t0 is not the albedo slot.
   `--slot 1` / `--slot 2` stay untried, and are only worth it once the read trace is exhausted.

Revert for this layer:
`.\revert_layer.ps1 -Confirm -Stems w64_sandbox_01b5,w64_sandbox_01bb,w64_sandbox_01bc,w64_sandbox_01bd,w64_sandbox_01be,w64_sandbox_01c0,w64_sandbox_01d5,w64_sandbox_01d6,w64_sandbox_01d7,w64_sandbox_01d8,w64_sandbox_01d9,w64_sandbox_01db,w64_sandbox_01dc,w64_sandbox_01dd,w64_ui_01a3`
   - **nothing** — the albedo is genuinely not in these four packages, and the next move is the
     package trace, not another paint. `live_models.py` with **no `--match`** gives a package
     histogram from a capture that needs no dumps; `globals_0238` led it at 1,702 live handles
     last time and has never been painted or dumped.
2. Geometry stays `_22`. Remaining-mip sRGB paint stays.
3. Floating hands / bald race head are still independent cosmetics.

If the user wants a quick win instead, the leftover glove and the bald race head are both
cosmetic and independent of the texture work.
