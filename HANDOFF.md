# Handoff — custom character into Sunrise / Shadowkeep

**Updated:** 2026-08-20 (hot-package sweep). **Status: `_22` geometry WORKS. `_26` painted all 228 textures in the four gear packages and the WEAPON CHANGED COLOUR — paint reaches the GPU and shows. The body did not, and `_27`'s t0 rebind on all three dye channels also showed nothing. Current layer paints the 384 textures in `globals_0238`, both investment packages and `sandbox_0378`–`037b`.**

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

1. **Albedo is still Scatterhorn's, and it does not come from the 55 sandbox textures we
   painted.** Those patches loaded; the Guardian never samples them. See "Then: textures".
2. **The custom mesh has five texture atlases and we draw through two parts.** Structural, and
   it gates textures. Same section.
3. **Gloves still draw over the custom hands**, and the bald race head still draws above the gas
   mask. Both cosmetic leftovers. See "the leftover gloves".

Do **not** merge upstream Sunrise 0.3.2.

---

## Read this first (Claude)

Newest files win:

| package | live | geometry | note |
|---|---|---|---|
| `w64_sandbox_037c` | **`_26`** | `_22` | `_26` = full texture sweep; `_25` = t0 remap + sidecars; `_24` = inert tints |
| `w64_sandbox_037d` | **`_24`** | `_22` | `_24` = full texture sweep; `_23` = the 55-swatch probe |
| `w64_sandbox_0698` | **`_23`** | `_21` | `_23` = full texture sweep; `_22` = the 55-swatch probe |
| `w64_sandbox_0699` | **`_8`** | — | `_8` = full texture sweep; `_7` = the 55-swatch probe |
| `w64_sandbox_01e2` | `_6` | — | inert; legs are blanked |
| `w64_sandbox_01db` | **`_6`** | — | dye paint plate t3 remaining mips |
| `w64_sandbox_020c` | **`_6`** | — | dye paint cloth t7 remaining mips |
| `w64_sandbox_020e` | **`_7`** | — | dye paint suit t5 remaining mips |

**Package indices are per-package, not a version number** — the injector writes each package its
own next index. "The `_22` inject" is `037c_22` + `037d_22` + `0698_21`; the texture probe on top
of it is `037c_23` + `037d_23` + `0698_22` + `0699_7`. Do not read them as one number.

**Geometry lives in the `_22` layer and the probe does not touch it** — the probe rewrites only
texture *bodies*, at their original sizes. `_22` is still the state to branch geometry from. `_21`
shipped a stride bug and exploded; `_20` was the last good layer before it.

Repo: `C:\Users\Round\OneDrive\Desktop\Destiny2ProjectSunrise\Sunrise` branch `cosmetics`.

**Do not `--undo`.** `inject_mesh.py --undo` deletes *every* receipt patch and returns vanilla
Scatterhorn. Write a **new layer** instead.

**The recipe that produces the working body**, from `tools/pkg`, Destiny closed:

```
blender --background --python retarget_mesh.py -- 23512 character_body.obj
python inject_scatterhorn.py --dry-run
python inject_scatterhorn.py
```

No flags. `--no-retarget` (T-pose mesh), `--no-authored` (donor weights) and `--no-uvs` (resized
Scatterhorn texcoords) each disable one half of the pipeline and exist only to bisect a
regression.

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

10. **Do not merge 0.3.2.** Shadowkeep: `SEntity` `0x80809C0F`, resource `0x80809C36`,
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

**A glove still draws over the custom hands.** The gauntlet models we blank are `0x80EF981E` /
`0x80EF9809`, from arrangement 2292 — so something else is drawing. Candidates, cheapest first:

1. **The gauntlet arrangement carries more than two models.** `lookup_arrangement.py` resolves an
   arrangement hash to a pair; re-check whether 2292 yields further entity models beyond A/B, the
   way the chest SEntity also carries the second robe `0x80EFA1D4` / `0x80EFA1B3`.
2. **A sibling model on the same SEntity**, exactly like the second robe. Scan the gauntlet
   SEntity's resource for every `0x808073A5` rather than taking the first.
3. **Mesh 1 of the gauntlet models.** `blank_model` zeroes every part's index count across all
   meshes, so this is unlikely, but worth confirming from the receipt.

This is cosmetic and low-risk. Do not fix it in the same layer as a UV change.

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
| 55× 2048/1024 sandbox BC7 swatches | `037c_23` / `037d_23` / `0698_22` / `0699_7` | **nothing**; dump of `0x80EFAD63` was our paint | those **55** never bind — but see the correction below; this ruled out 3.4%, not the packages |
| 527× everything that *paired*, 12 buckets | reverted, in `packages\_reverted` | **GPU device loss** at character select, no characters drawn | 356 of them were type-41 **geometry buffers**; pairing does not identify a texture |
| 228× every `entry_type` 40 texture, 12 buckets | `037c_26` / `037d_24` / `0698_23` / `0699_8` | **the weapon turned magenta. The body did not change at all.** | **paint reaches the GPU and shows** — first positive result. The body's albedo is not in these four packages |
| all three dye channels' albedo tile rebound to t0, both mip halves painted | `037c_27` + six dye packages | **nothing** | t0 is not the albedo slot for any of the three channels. The dye tiles are inert, so any colour seen from here is not them |
| **384× every texture in the character-select hot packages**, 12 buckets | **`globals_0238_7`, `globals_03ed_6`, `investment_01d3_7`, `investment_0361_7`, `sandbox_0378_6` / `0379_6` / `037a_7` / `037b_6` (current)** | **awaiting launch** | a colour names a bucket of ~10–48; nothing sends this to the package trace |
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
| `paint_dye_slots.py` | **Current texture step.** Rebinds all three dye channels' albedo tile to one t-slot (`--slot`, default 0) and paints each channel's texture a distinct colour in **both** mip halves, so the channel reports itself and the mip level cannot confound it. |
| `revert_layer.ps1` | **Recovery.** Moves each package's newest layer aside, reverting to the one beneath. `-Confirm` to act, `-Restore` to put it back. Refuses while destiny2 is running. |
| `paint_dye_textures.py` | sRGB remaining-mips t3/t5/t7. **Done.** On disk, no visible colour. Leave installed for `_25`. |
| `paint_dye_tints.py` | Inline DyeData tints. **`037c_24`. Failed visually. Do not rerun.** |
| `paint_dye_bind.py` | **Current texture step.** `037c_25`: restore dye bodies, suit slot 5→0, plate/cloth DyeInfo sidecars. |
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

**The character works. Do not re-open geometry, skinning or the tangent frame.** Do not flatten
dye normals. Do not rerun `paint_dye_tints.py`. Do not `--undo`.

`_25` came back **no colour** — so neither the t0 remap nor the DyeInfo sidecars are the albedo
path. Its dump did land: `0x80EF9663` (432 B suit sidecar) and the three 16-byte DyeInfo headers
are on disk and still undecoded.

**The body is white because nothing is bound to its albedo slot — not because the right texture
has not been found.** That is now the reading the evidence supports, and it retires the whole
"find and repaint" line of attack that `_23`, `_24` and `_26` all belonged to:

- The part-10 pixel shader declares `t0, t1, t2, t5, t6`. DyeTextures only name `t3`–`t8`.
- Painting the dye **normals** (t4/t6/t8) visibly changed the body — dye textures do reach it.
- Painting the dye **sRGB albedo** tiles (t3/t5/t7) did not — the shader is not reading them.
- `_26` painted all 228 textures in the four packages: **the weapon changed, the body did not.**

So the visible albedo is `t0`/`t1`/`t2`, which nothing supplies. **Bind, do not find.**

`_27` rebound all three channels to **t0** and showed nothing, so t0 is not the albedo slot for
any channel. `--slot 1` and `--slot 2` remain untried and are cheap, but the weapon result argues
for finishing the search first: **find-and-repaint demonstrably works when the texture is in a
package we painted**, so "the slot is empty" may simply be "the texture is somewhere we have never
touched". Two facts point the same way:

- The four gear packages hold only **257** textures between them, which is far too few to dress
  the whole Scatterhorn set.
- The dye textures the body *does* sample live in **`01db` / `01b5`**, not in the gear package —
  Destiny keeps gear textures in shared texture packages and only *names* them from the gear.

1. Current: **the hot-package sweep**, `python paint_textures.py --hot`. 384 textures across
   `globals_0238` (the top package in the character-select live-handle histogram), both
   **investment** packages (the armour), and the `sandbox_0378`–`037b` run. 667.8 MB, all block
   ceilings clear. **One** character-screen look, then quit:
   - **any bucket colour on the body** — read the hex off the sheet, then
     `python paint_textures.py --hot --only=0xAAAA..0xBBBB` to bisect that bucket.
   - **nothing** — the search is out of cheap guesses and the next move is the **package trace**:
     `live_models.py` with no `--match` gives a live-handle histogram needing no dumps. Painting
     all 97,609 type-40 textures install-wide is not an option.
   - **a crash** — `.\revert_layer.ps1 -Confirm -Stems <the eight above>` and narrow first.

   Revert for this layer:
   `.\revert_layer.ps1 -Stems w64_globals_0238,w64_globals_03ed,w64_investment_01d3,w64_investment_0361,w64_sandbox_0378,w64_sandbox_0379,w64_sandbox_037a,w64_sandbox_037b -Confirm`
   - **nothing** — the albedo is genuinely not in these four packages, and the next move is the
     package trace, not another paint. `live_models.py` with **no `--match`** gives a package
     histogram from a capture that needs no dumps; `globals_0238` led it at 1,702 live handles
     last time and has never been painted or dumped.
2. Geometry stays `_22`. Remaining-mip sRGB paint stays.
3. Floating hands / bald race head are still independent cosmetics.

If the user wants a quick win instead, the leftover glove and the bald race head are both
cosmetic and independent of the texture work.
