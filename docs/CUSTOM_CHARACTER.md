# Custom character — what is in, and what is next

Live memory and launch discipline: [`HANDOFF.md`](../HANDOFF.md).
Cursor rule: `.cursor/rules/sunrise-handoff.mdc`.
Root `HANDOFF.md` is stale (`_18`); ignore it.

**Target:** `C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb` on the playable Warlock,
looking like `tools/pkg/objs/textures/_glb_blender_preview.png` — green SkinTats graffiti, charcoal
tank, black gas mask, twirl, teal necklace. Not a dye tint.

**Live / restore:** newest is **SchizoAxe at its own proportions** (`037c_38` / `037d_36` /
`0698_34`) under snapshot `20260826-103621.json` — 118 layers, facing-corrected, no proportion fit
and no hand fit. Prior SchizoAxe stretched-to-rig: `20260826-093816`. The **v25 body**, the
long-confirmed character, is `20260825-132926`; worlds-working baseline is `20260824-200131`.
Snapshot `20260822-235602.json` is **hands-on-gauntlets confirmed** (no needles, sword grip).
`--fingers` is closed on the chest. Destiny must be closed to write packages or overwrite the DLL.

Paint and the part table are **not** in the snapshot — they live in
`bin\x64\Sunrise\characters\<name>\` and swap live from the Character page. Only geometry needs a
relaunch.

**The hand is retargeted as of 2026-08-23.** `retarget_mesh.fit_hands()` puts each wrist on the
joint that drives it (was 4.9 cm off, now 0.72) and turns the hand onto the donor's frame (palm
normal was 29° out, now 0.1°). **User-confirmed better in game.** Verify offline, no launch, with
`python audit_hand_bind.py --png objs/hand_overlay.png`. Full account and the two fitting traps:
[`HANDOFF.md`](../HANDOFF.md).

**Two ways to bring a source in, and the choice is a real tradeoff.** Both were measured on
SchizoAxe (a 1.574 m chibi against a ~1.75 m rig):

| | `--fit-proportions` (stretch to rig) | default (as authored) |
|---|---|---|
| joints | land **on** their rig joints (wrist 0.00 cm off, forearm ×1.000) | land wherever the model puts them (wrist **~75 cm** off, and `--no-fit-hands` leaves it there) |
| shape | stilt legs — hips→feet needs ×2.35 where arms need ×1.33 | the model's own silhouette, unaltered |
| standing | grounded, because the legs are fitted | grounded here (0.000 → 1.574 m) only because the VRM was authored feet-on-floor |
| animation | bends correctly | bends about joints far from the geometry — hands swing on a long radius |

`--no-fit-hands` exists because `fit_hands` is the one thing still reaching for the rig when the
proportion fit is off; on a short-armed source it blows the forearm out (**×5.3**) while still
leaving the wrist 60 cm short — the worst of both. Leave the proportion fit **off** for a source
authored to the rig (the v25 body), and turn both off together for a source you want kept as-is.

**What makes a chibi a chibi is the head, and that was measured wrong before.** Against the
corrected rig the chains need **arms ×1.34, spine ×1.64, legs ×1.70** — closer together than the
old note claimed (×1.33 / ×1.22 / ×2.35), because those were read off an asymmetric rig. The real
mismatch is elsewhere: **64.7% of SchizoAxe's vertices are head-dominant**, and that mass reaches
**0.784 m above the head joint**. So seating the skeleton at human height gives a **2.39 m**
character — that is what "all stretched out" was. Lengthening the legs never addressed it.

`--head-scale` is the knob, scaling head-weighted vertices about the head joint, faded by weight so
the neck does not tear. Measured: **×1.00 → 2.36 m, ×0.55 → 2.01 m, ×0.30 → 1.81 m** (a Guardian is
~1.80 m). Render the choice rather than guessing — `python render_obj.py sheet.png a.obj b.obj`.

**The rig itself was asymmetric, and it is fixed.** `skeleton.py` estimates each joint
independently, so thinly-sampled joints landed wherever their few vertices sat. The upper-arm joint
(37 and 47 samples, the thinnest in the rig) was **7.65 cm** off its mirror, which made a perfectly
symmetric source measure ×1.97 on one arm segment and ×0.55 on the other — and `fit_segments` was
aiming at that. `python symmetrize_rig.py` mirror-averages every pair, weighted by sample count;
worst error **7.65 cm → 0.00**, mean 1.72 → 0.00. Reversible with `--restore`.

Still wrong, and symmetry cannot see it: the rig reads a **12.20 cm humerus against a 26.44 cm
forearm**, which is backwards for a human. Mirroring only removes the *antisymmetric* half of an
error, so a joint misplaced the same way on both sides survives it.

**`--limb-girth` stops the stilts.** The fit moves joints apart and the armature stretches the mesh
between them — lengthwise only, not one millimetre wider. Girth repeats the stretch radially about
the bone axis. Two things it deliberately does not do, both because measuring said so: it uses the
**whole-limb** factor rather than per-segment (per-segment drove the upper arm to ×0.627 against a
×1.748 forearm — Popeye, straight out of that bad humerus), and it skips feet, hips, neck and spine
(the foot scored ×2.251, since ankle→toe is not the foot's shape). Result on SchizoAxe: arms
**×1.117**, legs **×1.286**, uniform per limb.

The float still cannot be dropped away: the game positions mesh relative to the **bind-pose**
joints, so a foot 42.5 cm above its own ankle joint stays there forever.

**Alpha-cut parts need the shader clip.** SchizoAxe's eye and mouth planes are 90.7% / 91.7%
transparent pixels; without `clip(texel.a - 0.5)` in `kShaderSource` they render as opaque slabs
over the face. The clip is in as of 2026-08-26.

**`HAND_ROLL` is the taste knob** on top of that fit — degrees of pronation about the forearm
axis, per hand. Left is **−25°** by user pick, right is 0. Its sign matches the panel labels from
`python hand_view.py out.png --side back --axis forearm --angles -40,-20,0,20,40`, so choose a
value by looking at the sheet rather than reasoning about rotation direction.

---

## Proven (do not reopen)

User-confirmed 2026-08-22:

| what | status |
|---|---|
| Geometry, skinning, tangent frame (`_22`) | in. Do not `--undo`. v23 retarget (spine 8/11, unwelded) **user-confirmed**. |
| Hands-on-gauntlets (bones 40–71 on `981E`/`09`, chest bind frame) | in. No needles. Sword grip works. Do not `--fingers`. |
| Five-part split on the Scatterhorn chest | in (`037c_28` / `037d_26`) |
| 0–1 UVs | in (`0698_24`). Tiled `0698_25` is attic’d. |
| Unique GLB albedos on **character select / inspect / menus** | in (v12, still true) |
| Sprint / jump dye-quilt flicker | **gone** (v15). Do not undo the G-buffer gate or PS restore. |
| Whole-world neon green | **gone** (v15). That was a leak, not “Io is broken.” |
| Off-lamp body readable (not a silhouette) | **in** (v17 fill, kept in v18) |
| Foreign hex/scale designs on the body | those were Destiny `t2` (v17). v18 does not sample it. |

v18 is the live floor (user: looks good while world-hopping). SkinTats graffiti is ours;
hex/scale marble was Destiny `t2` and is gone.

---

## How the look ships

Destiny will not carry five unique atlases through the chest dye material. The bound slot is one
512 BC7 (`0x80B3611D`) and pixel shader `0x81531EE6` luma-gates whatever you put there. Painting
that tile, swapping to `0x81531CBE`, and stealing off-chest materials are **closed**.

The working path is a **draw-time pixel-shader replace** on five exact index ranges:

`Sunrise/src/client/hooks/custom_albedo/`

| | |
|---|---|
| Identify | `DrawIndexed` / `DrawIndexedInstanced` vtable 12 / 20; **exact** `(StartIndex, IndexCount)` only |
| Gate | only replace when RTs are `nrt>=3 f0=29 f1=24 f2=28` (character G-buffer). Skip `nrt=1 f0=26` (R11G11B10 lighting) — that skip killed the sprint quilt and the green world |
| Restore | PS + class instances + SRV 0–15 + samplers + blend. `PSSetShader(prev, nullptr, 0)` leaked v14 |
| `o0` | GLB albedo on `TEXCOORD3.xy`, `o0.w = 0.2` |
| `o1` | `saturate(normalize(TEXCOORD0.xyz) * 0.375 + 0.5)`, `o1.w = 0`. **Not** `TEXCOORD2` |
| `o2` | `x` = GLB metalRough.g, `y` = 0.5 (open AO), `z` = 0, `w` = `TEXCOORD0.w` |

### Exact draw match

| part | start | count | albedo | roughness |
|---|---|---|---|---|
| tank | 0 | 74358 | `custom_tank.png` (2048) | `custom_tank_mr.png` |
| mask | 74358 | 11580 | `custom_mask.png` (2048) | `custom_mask_mr.png` |
| necklace | 85938 | 3570 | `custom_necklace.png` (2048) | `custom_necklace_mr.png` |
| skin | 89508 | 26760 | `custom_skin.png` (2048) | `custom_skin_mr.png` |
| twirl | 116268 | 6036 | `custom_twirl.png` (512) | none — 1×1 G=0.55 |
| hands | 0 | 16326 | `custom_skin.png` (2048) | `custom_skin_mr.png` |

Chest mesh indices: **122304**. Hands are a separate gauntlet draw, exact `(0, 16326)` —
upper arm + forearm + palm so first-person is a complete arm, not a floating hand.
That draw stays **visible** in first-person (the skip was reverted — empty sleeves are not
acceptable). Bones 40–71 are still unposed on the FP/inspect path, so the mesh can spaghetti
until a real viewmodel is dumped. Do not skip it again. Do not `--fingers` on the chest.

The hook also reads `C:\Sunrise\bin\x64\Sunrise\custom_parts.txt` so a new character's index
ranges do not need a rebuild. Missing file = the six shipped defaults above.

### GLB material map (do not re-guess)

Source: `void_4003GasMask.glb`. Extract: `python tools/pkg/glb_textures.py --extract`.

| GLB material | part | base colour | metalRough | metallicFactor |
|---|---|---|---|---|
| GLSLShader85 | tank | `01_BlackTankTopshader_BaseColor.png` | `02_…_Roughness.png` | 0 |
| GLSLShader66 | mask | `04_GasMaskshader_BaseColor.png` | `05_…_Roughness.png` | 0 |
| GLSLShader60 | necklace | `07_Plancha_BaseColor.png` | `08_Plancha_Metallic-Plancha_Roughness.png` | default (B is metal) |
| GLSLShader13 | skin | `10_SkinTats_BaseColor.png` | `11_SkinTats__Roughness.png` | 0 |
| GLSLShader22 | twirl | `13_Twirlshader_BaseColor.png` | none | 0, roughness 0.55 |

glTF metalRough: **G = roughness**, R unused (255), B = metallic. v18 reads **G only** and writes
`o2.z = 0` because four parts have `metallicFactor = 0`. The GLB has **no occlusion map** — open
AO (`o2.y = 0.5`) is correct, not a missing texture.

---

## Packages (game reads these)

`C:\Sunrise\packages\`

| layer | role |
|---|---|
| `w64_sandbox_037c_32.pkg` | nohands chest + gauntlet arms (upper + forearm + palm) |
| `w64_sandbox_037d_30.pkg` | chest five-part + gauntlet one-part |
| `w64_sandbox_0698_28.pkg` | 0–1 UVs. Tiled attic `0698_25` is a *different* file |
| `w64_globals_06dc_7.pkg` | blank `0x815B9521` (race upper body, not confirmed) |
| `w64_globals_03ed_6.pkg` | blank `0x80FDBE41` (same mesh, second copy) |
| `w64_sandbox_019b_9.pkg` | packed 512 still on the dye tile (unused while the hook fires) |

`0698_25` (tiles) lives in `C:\Sunrise\packages\_reverted_uv_tiles\`.
Do **not** `--restore` `20260822-150046` — that puts tiles back.

The `.glb` never goes in the game folder. Inject scripts write the `.pkg` files.

---

## Files you must not lose

Game (live):

| path | what |
|---|---|
| `C:\Sunrise\bin\x64\steam_api64.dll` | hook v18 |
| `C:\Sunrise\bin\x64\steam_api64.dll.original` | hang revert |
| `C:\Sunrise\bin\x64\Sunrise\custom_{tank,mask,necklace,skin,twirl}.png` | albedos |
| `C:\Sunrise\bin\x64\Sunrise\custom_{tank,mask,necklace,skin}_mr.png` | GLB roughness |
| `C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log` | attach `mode=ours`, skip `f0=26` |
| `C:\Sunrise\destiny2.exe` | launch |

Repo (durable):

| path | what |
|---|---|
| `tools/pkg/known_good/20260822-172401.json` | package snapshot |
| `tools/pkg/known_good/live_chest_ps.bin` + `.asm` | dumped dye PS `0x81531EE6` (5772 B) |
| `tools/pkg/objs/textures/*_BaseColor.png` + `*_Roughness.png` | GLB extracts |
| `tools/pkg/glb_textures.py` | re-extract from the GLB |
| `tools/pkg/bring_guardian.py` | next-character intake engine |
| `bring_guardian.ps1` | intake desk / CLI launcher |
| `Sunrise/src/client/hooks/custom_albedo/` | hook source |

---

## Live dye PS (`0x81531EE6`) — do not re-derive

`ps_5_0`. Mesh UV is **`TEXCOORD3`**. TBN is `TEXCOORD0..2`.

```
worldN = v0*nz + v1*nx + v2*ny     // flat n_map → TEXCOORD0.xyz
o1.xyz = saturate(worldN * ~0.375 + 0.5)
o1.w   = ~0.33 if o0.w > 0.5 else 0
o2.x   = roughness  (must stay > 0.05)
o2.y   = 0.5 * sat(t2.r)           // AO. We write 0.5 (open)
o2.z   = metallic * dye mask
o2.w   = TEXCOORD0.w
o0.w   = packed id; fallback ~0.2
```

Select G-buffer: `nrt=3 f0=29 f1=24 f2=28` (R8G8B8A8_UNORM_SRGB / R10G10B10A2 / R8G8B8A8_UNORM).
World lighting pass we must **not** write: `nrt=1 f0=26`.

---

## Hook versions (do not repeat a closed one)

| ver | what | result |
|---|---|---|
| v1–v2 | sample / magenta-if-dark | Black |
| **v3** | Bright constants on every SV_Target | **Magenta.** Only early write that showed. |
| v4–v7 | colour on RT0, zeros/wrong extras | Black |
| v8 | 512 B snprintf HLSL | compile fail; cowprint = hook off |
| v9 | game PS then RT0-only | Black (other RTs zeroed) |
| v10 | guessed `(0.5,0.5,1)` G-buffer | Black. Dump 5772 B ok. |
| **v11** | dumped encode + 512 quilt on `TEXCOORD3` | **Right colours, smeared.** |
| **v12** | five GLB albedos + `0698_25` attic’d | **Select = Blender.** |
| **v13** | two-pass + **subset** match | **Smashed select.** Closed. |
| **v13b** | two-pass, exact match | **Green quilt on select.** Closed. |
| **v14** | v12 single-pass, no restore | **World radioactive green.** Closed. |
| **v15** | G-buffer gate + fortress restore | **Quilt gone.** Hard to see off-lamp. |
| **v16** | `o1` = TEXCOORD2 | **Closed.** Select dark, world a hole. |
| **v17** | sample Destiny t2 for o2 | **Visible, Scatterhorn designs.** Closed. |
| **v18** | GLB roughness + open AO | installed |

Do not add emission. Cloudstrike already proves real emissives work; the coat is charcoal.

---

## Closed package paths (do not retry)

- `bind_material_textures.py` — hang at character select
- `assign_split_materials.py` / `assign_armor_vs.py` — vanish four parts
- Steal any material not already on the chest
- Dye tiles t3–t8, saturation-boost, another 512 pack
- PS `0x81531CBE`
- `--undo` of the mesh
- Colour-probe hooks that guess RT1/RT2
- Subset-match index ranges (`start=0` UI = tank)
- Two-pass (game PS then our RT0)
- `TEXCOORD2` as the world normal
- Sampling Destiny `t2` for AO/roughness
- `--fingers` / bone indices 34–71 on the chest draw (needled hands to the feet)

---

## Bring a new custom character

You bring the `.glb`. The repo does not ship one. Destiny closed to inject. Dry-run first.

Full walkthrough: [`HOST_INTAKE.md`](HOST_INTAKE.md).

```powershell
cd Sunrise
.\bring_guardian.ps1
# or
python tools/pkg/bring_guardian.py --inspect D:\models\host.glb
python tools/pkg/bring_guardian.py --glb D:\models\host.glb --dry-run
python tools/pkg/bring_guardian.py --glb D:\models\host.glb --inject
```

The desk assigns each GLB material to tank / mask / necklace / skin / twirl (five Destiny
carriers on the chest — do not steal a sixth). `--fingers` is only used at **retarget** time;
inject still refuses it on the chest. Hands go on the gauntlet draw with the chest bind frame.

---

## The character switcher (Character page)

A custom character is three separable things, and only one of them needs a relaunch:

| layer | where it lives | swaps live? |
|---|---|---|
| paint | PNGs named by the part table | **yes** |
| part table | `parts.txt` - exact `(StartIndex, IndexCount)` to PNG, or `hide` | **yes** |
| geometry | injected Tiger package layers | **no** - packages are read at load and cannot be written while the game runs |

So the Character page swaps **paint and visibility** instantly, and a different *silhouette* still
needs `tools/pkg` with Destiny closed. The page says so rather than implying a restart is not
coming.

### Layout

```
bin\x64\Sunrise\characters\
  active.txt          the profile drawn, remembered across launches
  <name>\
    parts.txt         name start count albedo material
    *.png             the textures parts.txt names, resolved beside it
```

A directory counts as a profile only when it holds a `parts.txt`, so a folder of loose textures is
never offered as a character that cannot be drawn. **The shipped defaults are always listed too**,
so a profile that draws badly is one click from a known-good character rather than a file edit and
a relaunch. If a profile's textures fail to load, the defaults are put back automatically and the
log carries `ev=custom_albedo stage=profile result=fail`.

Nothing about the pre-profile layout was removed: with no `characters\` directory the hook reads
`custom_parts.txt` and the flat `custom_*.png` exactly as before.

### `hide` — the draw-time blank

Where a part table names a texture, it may instead name `hide`:

```
race_head 12345 6789 hide
```

That range is then **skipped at draw**: no colour, no depth, no G-buffer write. It is the same
blank a package patch would make, at the one place nothing caches it — which matters, because
`w64_globals_06dc` hangs a world load whenever it carries a layer of ours
(`docs/PACKAGES.md`), and the `SEntityModel` constructor detour never sees the tag.

Two rules it inherits from the paint path:

- **Exact `(start, count)` pairs only.** v13 subset-matched and treated start=0 UI quads as the
  tank, which smashed character select.
- **Gated on the character G-buffer**, like painting. An exact collision with a UI quad would
  otherwise blank the interface. The cost is that shadows may still show hidden geometry; revisit
  when a second character's ranges actually need hiding.

This is the mechanism for the two long-open items — stock gloves over custom hands, and the bald
race head under a custom one — and for drawing one character out of a mesh that holds several.

### Bringing a second character

`bring_guardian.py` already does the intake (retarget, part assignment, inject). What the switcher
adds is that its output can live in its own profile directory instead of overwriting the first
character's files. Geometry is still one injected mesh at a time; drawing two from one buffer means
injecting both at disjoint index ranges and `hide`-ing the inactive one, which the vertex count
does not forbid — `GEOMETRY.md` is explicit that the count is not a hard limit, only the safe first
step, since a new buffer means updating the vertex header, index buffer and part ranges together.

---

## Still open

1. **First-person / inspect arms:** upper arm, forearm and palm now ride the gauntlet
   draw (`(0, 16326)`). Skip-draw is closed. Finger bones 40–71 can still spaghetti.
   Shoulder bones 27/28 stay on the chest (tank straps). If FP still looks armless at
   the camera edge, that is the stock viewmodel, not a missing triangle. Do not
   `--fingers`. Do not AABB-fit. Do not hide the draw.
2. **Next custom character:** `.\bring_guardian.ps1` (or `python tools/pkg/bring_guardian.py --ui`).
   Dry-run first. Destiny closed to inject. See the script docstring.
3. **Leftover stock gloves — probe live:** blanked `0x815B9521` (`globals_06dc_7`) and
   `0x80FDBE41` (`globals_03ed_6`), the two globals copies of the 7685/7754 default upper
   body. UI copy `0x80EFC649` was not touched. Not yet user-confirmed. Leather still there
   = restore `20260822-235602` and read the new unique miss log.
4. **Stiff ring (maybe pinky):** GLB has no `RingFinger*` groups, so bones 44/55 have zero
   verts. Index/middle/thumb/pinky are weighted. Paint ring verts onto 44/55, recut, reinject
   hands only. Do not `--fingers` on the chest.
5. **Leftover cosmetics:** bald race head over the gas mask.
   Necklace UVs on the mesh are collapsed (`u` 0.817–0.859, `v` ~0.410). Unweld does not fix that.
6. Necklace metallic channel exists in the GLB (`08_…` B) but v18 writes `o2.z = 0` on purpose.
7. Lighting can still be refined without reopening closed levers. Direct lamps already work (v15).

---

## Build / restore

Destiny closed.

```powershell
.\build.ps1
.\install.ps1
python tools/pkg/known_good.py --check
python tools/pkg/known_good.py --restore    # newest = 20260822-235602 (hands-on-gauntlets)
```

Hang: copy `C:\Sunrise\bin\x64\steam_api64.dll.original` over `steam_api64.dll`.
