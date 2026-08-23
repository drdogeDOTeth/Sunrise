# Base class bodies — Hunter, Titan, Warlock

Working dump of the **playable base bodies**, not helmets, not Tower frames, not ornaments.
Use these files for retarget / inject instead of guessing a VRM onto Scatterhorn.

The VRM inject on 2026-08-23 loaded, then stretched and UV-smeared because it did not
match this data: Destiny bind pose is an **A-pose with arms forward**, mesh UVs live in
the stride-24 second buffer on `TEXCOORD3`, and a dump taken through a live mesh patch
returns the patch, not stock geometry.

## What “base body” means here

In-game inspect / APPEARANCE draws **equipped armour**, not a nude mesh and not the
72k character-select card.

| class | wire | soid | body slots dumped |
|---|---:|---|---|
| Titan | 0 | `0x9EAA300100100102` | chest, gauntlets, legs (+ class item) |
| Hunter | 1 | `0x9EAA300100100101` | chest, gauntlets, legs (+ class item) |
| Warlock | 2 | `0x9EAA300100100103` | Scatterhorn chest, gauntlets, legs (+ class item) |

Helmet hashes are recorded only so they stay **skipped**.

Gender A/B both written. Which mapping is male is still unproven; keep both.

Character-select 3D cards are a **different** mesh family (`select/`). Replacing
`0x80B9F962` previously spun the select screen. Do not inject a playable body there.

## Layout

```
docs/characters/
  README.md                 this file
  catalog.json              machine-readable index
  hop_resolved.json         arrangement hop for Hunter/Titan
  hunter/ titan/ warlock/
    chest_*.obj             LOD0 triangles, Destiny metres (X forward, Y left, Z up)
    chest_*_weights.json    per-vertex [bone, weight] (global skeleton 1..28)
    chest_*.json            tags, stride, parts, AABB
    gauntlets_*.obj
    legs_*.obj
    class_item_*.obj
    rig.json / rig.obj      joints estimated at blend regions (skeleton.py method)
  select/                   character-select / UI person meshes
  captures/                 archived sunrise.log from the deformed-VRM launch
  _attic_injected_me/       the 2026-08-23 Me.vrm package layers (not live)
```

Raw decrypted dumps stay in `C:\Sunrise\bin\x64\Sunrise\dump` (this box:
`/home/howie/Sunrise/bin/x64/Sunrise/dump`). Re-extract with:

```
python tools/pkg/dump_class_bodies.py --extract
```

## Tags

### Warlock (Scatterhorn, HANDOFF)

| slot | models A / B |
|---|---|
| chest | `0x80EFA1CA` / `0x80EFA1A9` |
| gauntlets | `0x80EF981E` / `0x80EF9809` |
| legs | `0x80EFA93B` / `0x80EFA92E` |
| class item | `0x80EFA528` / `0x80EFA51F` |

### Hunter (equipped hashes `0x6F0DBB07` chest, `0x61868551` gauntlets, `0x0B8E36D0` legs)

| slot | models |
|---|---|
| chest | `0x80BF9969` / `0x80BF9936` |
| gauntlets | `0x80BF92FD` / `0x80BF92E2` |
| legs | `0x80EF8D4C` / `0x80EF8D4D` |
| class item | `0x80BFA060` / `0x80BFA03D` |

### Titan (equipped hashes `0x542FC543` chest, `0xE98EBABD` gauntlets, `0xD2F64E86` legs)

| slot | models |
|---|---|
| chest | `0x80BDF1E4` / `0x80BDF1C7` |
| gauntlets | `0x80BDECAF` / `0x80BDEC91` |
| legs | `0x80EF71EF` / `0x80EF71E6` |
| class item | `0x80BDF5A5` / `0x80BDF584` |

Hop tables (dumped): arrangement `0x81319329` → assignment `0x81613D23` → parent `0x80EC3F61`.
SEntity is at parent `+0x10`. Model tag is inside the entity **resource** (`0x80809C36`),
often the first resource at SEntity `+0xC0`.

## Rig (do not re-guess)

Bone indices are **one global skeleton**, not a per-mesh palette. Joints 1–28 are a complete
humanoid. Everything above 28 is fingers.

Bind pose is an **A-pose with the arms forward**: shoulder `(−0.05, 0.19, 1.42)` to wrist
`(0.30, 0.39, 1.15)` in Destiny metres. A T-posed VRM swung about Z only will miss the hands
(that is why the Me inject flagged bones 21/22 ~34 cm off the donors).

Use `warlock/rig.json` (23 joints from chest+legs+gauntlets) as the retarget target.
Hunter/Titan rigs are the same index space recovered from their own armour.

Vertex layout (GEOMETRY.md): stride 16 positions (`int16 xyz` + 4 weights + 4 bones).
Second buffer stride 24: `u v`, normal, tangent, handedness, UV2. Mesh UV for the dye PS
is **`TEXCOORD3`**.

## Textures

These armour shaders luma-gate unique RGB. Stock albedos are **not** the look of a VRM.
Unique colour still goes through `custom_albedo` (v18): exact `(StartIndex, IndexCount)`
on the character G-buffer, GLB albedo on `TEXCOORD3`, no Destiny `t2`.

Do not paint dye tiles. Do not steal off-chest materials.

## How to convert a VRM against this

1. Pose the VRM onto `docs/characters/warlock/rig.json` (A-pose, arms forward). Blender
   path: `retarget_mesh.py`. Do not use a T-pose nearest-donor transfer.
2. Emit `character_body.obj` + `_weights.json` + `_frame.bin` + `_groups.json`.
3. Inject with `inject_scatterhorn.py` onto the **Warlock chest** only if that is the
   live carrier. Destiny closed. Do not dump with that patch installed.
4. Point the draw hook at the injected index ranges, not the old gas-mask table.

## Rules that burned launches

- Never dump through a live mesh patch (`TOOLS.md`). `request.txt` is blanked.
- Never write packages while Destiny is running.
- Archive `sunrise.log` before the next launch (`captures/`).
- Tower frames `0x80B9F855` / `0x80C23B5D` / `0x80FA2308` are not the player.
- `0x80B9F962` is the select preview, not the in-game body.
