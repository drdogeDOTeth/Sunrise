# Handoff — custom character into Sunrise / Shadowkeep

**Updated:** 2026-08-20 (evening). **Status: `_20` written, awaiting a Tower verdict.** The body
is now **posed onto the recovered rig in Blender and carries the GLB's own skin weights**. No
donor transfer, no hand-tuned arm swing. `_19` (whole body, chest draw, joints 1–28) rendered in
the Tower as a recognisable chrome figure; `_20` fixes the arms and hands on top of it.

**Goal:** custom `void_4003GasMask.glb` visibly rendering **and connected** on the playable
Guardian. Inspect / character select / Tower all draw this inject. Race-head inspect is
deprioritized.

Do **not** merge upstream Sunrise 0.3.2.

---

## Read this first (Claude)

Newest files win:

| package | live | previous |
|---|---|---|
| `w64_sandbox_037c` | **`_20`** | `_19` |
| `w64_sandbox_037d` | **`_20`** | `_19` |
| `w64_sandbox_0698` | **`_19`** | `_18` |
| `w64_sandbox_01e2` | `_6` | inert — legs are blanked |
| `w64_sandbox_0699` | `_6` | inert — gauntlets are blanked |

Repo: `C:\Users\Round\OneDrive\Desktop\Destiny2ProjectSunrise\Sunrise` branch `cosmetics`.

**Do not `--undo`.** `inject_mesh.py --undo` deletes *every* receipt patch and returns vanilla
Scatterhorn. Write a **new layer** instead.

Judge in the Tower. Success looks like a connected chrome humanoid whose **legs bend at the
knee and whose feet sit on the ground** — that is the specific thing `_19` changed.

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
| **whole body, chest draw, joints 1-28 (`_19`)** | awaiting verdict | one bind frame, real legs |

Webbing on `_16` was proven: custom mesh median `|y| ≈ 0.07`. **Zero** L/R-spanning
triangles. Laplacian smooth across one "legs" band mixed left shin into right shin.
Crotch `|y|<0.08` is most of this body, not a gusset. Fix was `strip_crossed` + side
labels, **not** splitting onto the legs slot.

Arms yanked back: 55° swing left hands at `|y|≈0.56` vs robe sleeves ≈0.40. 70° matches
robe width, and `_19` keeps 70°. See "the one thing `_19` does not fix" below.

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
  `0698` mesh/position/UV, now also `01e2` / `0699` UV). Unpatched entries in a patched
  package (e.g. `0x81531EE8` / `EE9` in 0698) are still safe.
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

## Ten facts that must survive a compact

1. **Inspect, character select, and Tower all draw this inject.** Tower frames `0x80B9F855` /
   `0x80C23B5D` / `0x80FA2308` are still not the player.
2. **The playable character *is* its armour**, not a globals body. Equipped Scatterhorn is the
   live mesh.
3. **Assignment hop is closed.** Item → arrangement index (`build_data.bin`) → hash table
   `0x81319329` → assignment table `0x81613D23` → parent map `0x80EC3F61` → entity-parent
   (`0x8080744A`, 24 B, SEntity at `+0x10`) → SEntity → resource → `SEntityModel`. Scan the
   resource for class `0x808073A5`; the tag is often at `+0x64C` / `+0x65C`.
4. **Chest arrangement 2293 / hash `0x4A8A34A0` is Scatterhorn Robe.** User-confirmed.
   Models `0x80EFA1CA` / `0x80EFA1A9` in `sandbox_037d`, **not** `investment_0361`.
5. **Full 23k on chest mesh 0 is the shape of the answer**, carrier parts **10 and 32**.
   Original robe ~4,100 verts. Welded GLB 23,512 / 46,068 tris.
6. **Bone indices are one global skeleton, 1–28 is a whole body, and one draw poses all of it.**
   The per-mesh palette never existed. `bone_frames.py` proves it offline in seconds.
7. **A raw nearest-point snap destroys the mesh** (40–50% degenerate triangles → floating
   fragments). Smooth the *displacement*, not the positions. (That was the wrap. Topology
   inject copies skin tails, not positions-from-nearest.)
8. **Write the buffer's package, not the model's.** `package_of(position_buffer)`.
9. **Do not dump rewritten mesh/position/UV tags** in `037c` / `037d` / `0698` / `01e2` /
   `0699`. Exception: **`0x81531EE8` / `0x81531EE9`**.
10. **Do not merge 0.3.2.** Shadowkeep: `SEntity` `0x80809C0F`, resource `0x80809C36`,
    `SEntityModel` `0x808073A5`, entity-parent `0x8080744A`. Charm WQ classes are **zero**.

---

## Retractions still standing (visual proof, not inference)

1. **`0x80B9F855` / `0x80C23B5D` / `0x80FA2308` are NOT the player body.** Tower frames.
2. **`0x815B868B` / `0x815B8697` (globals_06dc) are not body variants.** 5.04 m world objects.
3. **Constructor "owner" tags are materials**, class `0x80807140`.
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
  align; per-block SHA-1).
- Armour geometry decodes and renders offline.
- Equipped Scatterhorn lives in **sandbox** `037c` / `037d` / `0698`, not investment lookalikes.
- Chest-only 23k inject **can** look like a connected custom body (pre-`_18`).
- **Bone indices are one global skeleton.** Proven offline, three independent ways.
- **The rig is recovered** — 25 named joints with pivots, `objs/skeleton/rig.json`.

---

## What `_19` is

Destiny closed, from `Sunrise/tools/pkg`, `python inject_scatterhorn.py`:

- One placed mesh, all 23,512 verts, on **chest mesh 0 of both `0x80EFA1CA` and `0x80EFA1A9`**,
  packed with the chest's own translation and a scale grown 0.700 → 0.904 so the body fits.
- Weights from a **single donor cloud spanning chest + legs + gauntlets** (both meshes each),
  plain nearest neighbour, no AABB fit, clamped to `BODY_BONES` (joints ≤ 28).
- `strip_crossed` + side labels + `ARM_SWING = 70` + `LEG_Z = 0.98` + `SIDE_EPS = 0.04`,
  unchanged from `_17`.
- Legs, gauntlets, hood, second robe, class item **blanked** — the one mesh covers all of them.
- Patches written and verified: `037c_19` (2 entries), `037d_19` (14), `0698_18` (10).

The dry run now prints a **per-joint drift audit** — where the custom vertices on a joint sit
against where the donor vertices on the same joint sit. `_18`'s pillar was thighs 0.6 m from
their donors, and the old histogram could not show that. Nothing in `_19` exceeds 0.30 m.

Custom mesh: `Sunrise/tools/pkg/character_chest.obj` (already built).
Blender: `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`
```
blender --background --python prepare_mesh.py -- 23512 character_chest.obj
```

---

## Next: UVs and textures

Geometry and skinning are done. The body still **wears Scatterhorn's chrome** because
`clone_uvs` only *resizes* the original stride-24 texcoord buffer to the new vertex count — it
tiles garbage UVs rather than writing the mesh's own. That is the whole reason everything looks
like polished metal.

What that needs, in order:

1. **Export the GLB's UVs** alongside positions and weights in `retarget_mesh.py`. The loop is
   already there; the mesh has a UV layer per object and they survive the join and the decimate.
2. **Work out the stride-24 texcoord layout.** 24 bytes per vertex holds more than a UV pair —
   normals and tangents live there too, and writing a UV without the normal will flat-shade or
   invert the lighting. Dump `0x80EFA1C8`-family texcoord buffers and decode before writing.
   `GEOMETRY.md` has the position layout; this one is not decoded yet.
3. **Then textures.** Materials are class `0x80807140` (208 B, references `0x808071E8` +
   `0x808071DC`) — that is the material/technique resource identified back when the "owner tags"
   were retracted. A texture swap is a separate, easier problem than geometry and the writer
   already handles arbitrary entry resizing.

Mesh 1 (stride 12; chest bone 20, legs 25/26) is a separate packer. `rewrite_chest` zeros
non-mesh-0 parts. Do not un-zero original extras.

---

## Tools (`Sunrise/tools/pkg/`)

| tool | what |
|---|---|
| `retarget_mesh.py` | **Current mesh build.** Blender: drops unrigged objects, poses the arms onto `rig.json`, welds/decimates, exports OBJ + real per-vertex weights on rig bone indices. |
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

## Suggested first message back to the user

`_20` is installed. Launch, go to the Tower, and look at the **arms and hands** — that is what
this layer changed. Do not re-derive the bone census, re-argue the palette, or re-tune an arm
swing angle; all three are settled and written down above. If the body looks right, the next
work is UVs, not geometry.
