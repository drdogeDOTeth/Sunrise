# Host Intake — bring a custom character onto the playable Warlock

This is the front desk. **You bring the `.glb`.** The repo does not ship a character.
Whoever forked this runs their own model through the same confirmed path: retarget,
cut, textures, hook part table, inject.

Live memory: [`HANDOFF.md`](../HANDOFF.md). Durable status: [`CUSTOM_CHARACTER.md`](CUSTOM_CHARACTER.md).

---

## What the incoming body needs

The tool does not fix a bad export. Check this before you hit inject.

| need | why |
|---|---|
| `.glb` (glTF 2), not FBX sitting loose | intake reads the binary glTF |
| One humanoid **armature** with **vertex groups** | Destiny plays its own 72 bones; we copy your weights onto that rig |
| Mixamo-style joint names, or a `--bone-map` JSON | Unmapped groups are ignored and those verts park on the waist |
| Your own UVs, 0–1 per atlas | The hook samples `TEXCOORD3` |
| At most **five** unique atlases | The chest only has five carrier materials (tank / mask / necklace / skin / twirl) |
| Roughly metre-scale, Destiny axes | Retarget poses the **arms only** onto the A-pose. A 10× sculpt will not shrink itself |
| ≤ 65,535 unwelded verts | 16-bit indices. `--keep-seams` will not decimate |
| PBR base color (+ metalRough G if you have it) | v18 reads roughness G, writes metallic 0 |

Unrigged props (ground planes, leftover sculpt cages) are dropped. Extra materials beyond
five must be parked on one of the five slots or they share an atlas.

Warlock only. The mesh lands on the **Scatterhorn chest + those gauntlets**. Wear that chest
in-game or you are looking at some other draw.

---

## Floor this machine already has

Intake does **not** rebuild the first-time cosmetics floor. That work is done and closed.

Already live, do not reopen:

- Geometry / skinning / tangent frame
- Five-part Scatterhorn chest (`037c` / `037d`)
- 0–1 UV layer (`0698_26`). Do not `--restore` `20260822-150046`
- Hook v18 (G-buffer gate, no Destiny `t2`, no `TEXCOORD2`)
- Hands on the gauntlet draw with the **chest bind frame** (not the glove 0.45 AABB)

Once per install, not per character:

```powershell
.\build.ps1
.\install.ps1
# launch Destiny once so C:\Sunrise\bin\x64\Sunrise\dump\ fills
# then close it
```

If the five carrier materials are missing from the chest dump, restore first:

```powershell
python tools/pkg/known_good.py --restore --snapshot=20260822-235602.json
```

---

## How to run it

Destiny **closed** for a real inject. Point the desk at **your** `.glb`. There is no
default host in the tree. Optional env: `SUNRISE_GLB` (your model), `SUNRISE_GAME`
(install root, default `C:\Sunrise`). The last path you ingested is remembered only
on this machine (`tools/pkg/intake/last_glb.txt`, not committed).

### Desk

```powershell
cd Sunrise
.\bring_guardian.ps1
```

Pick the GLB. Scan. Assign each material to tank / mask / necklace / skin / twirl.
**Dry run** first. Then **Inject**.

### Command line

```powershell
cd Sunrise\tools\pkg

python bring_guardian.py --inspect D:\models\host.glb
python bring_guardian.py --preflight --glb D:\models\host.glb
python bring_guardian.py --glb D:\models\host.glb --dry-run
python bring_guardian.py --glb D:\models\host.glb --inject
python bring_guardian.py --glb D:\models\host.glb --inject --snapshot
```

`--bone-map path.json` and `--material-map path.json` override guesses.

Work files land in `tools/pkg/intake/<glb-stem>/`. Atlases and `custom_parts.txt`
land in `C:\Sunrise\bin\x64\Sunrise\`. The hook reads that part table at attach —
a new mesh’s index counts do not need a DLL rebuild.

---

## What the line actually does

1. **Preflight** — game folder, hook DLL, Blender, chest/gauntlet dumps, five carriers, Destiny closed
2. **Inspect** — joints, materials, guessed slots
3. **Extract** the GLB’s own images
4. **Copy atlases** to `custom_{tank,mask,necklace,skin,twirl}.png` (+ `_mr` when present)
5. **Blender retarget** — pose arms to the Guardian A-pose, keep seams, keep finger weights, write OBJ + frame + groups + weights
6. **Cut hands** — complete arms (upper + forearm + palm + fingers + shoulder cuff)
   onto the gauntlet draw so first-person is not a floating hand. Chest keeps the
   shoulder/torso stump (ceiling 28). Bones 27/28 stay on the chest (tank straps).
7. **Write `custom_parts.txt`** — exact `(start, count)` the hook matches
8. **Inject** `--hands-on-gauntlets` — one body on the chest, hands on `981E` / `9809`, same bind frame; blank legs / hood / class item
9. Optional **snapshot** of the live package set

`--fingers` is used at **retarget** only. Inject still refuses it on the chest
(that needled hands to the feet). Do not AABB-fit. Do not run
`bind_material_textures.py`, `assign_split_materials.py`, or `assign_armor_vs.py`.

---

## After inject — look at it

Launch `C:\Sunrise\destiny2.exe`. Warlock. Scatterhorn chest.

| view | what you want |
|---|---|
| Character select / inspect | your atlases, not the dye quilt |
| Destination, third-person | same body, sword grip works |
| First-person | hands **visible**. They can still spaghetti — FP does not pose bones 40–71. Do not hide that draw |

If it looks right:

```powershell
python tools/pkg/known_good.py --save "intake <name>: hands-on-gauntlets"
```

Or pass `--snapshot` on the inject you just ran.

Hang / needles / tiled UVs: `--restore` `20260822-235602` (hands-on-gauntlets confirmed).
DLL hang: copy `steam_api64.dll.original` over `steam_api64.dll`.

---

## What this desk does not do

These are leftover jobs, not missing intake steps:

- First-person **posed** hands (needs a dumped viewmodel, not `--fingers` on the chest)
- Stiff ring finger when the GLB has no `RingFinger*` groups (bones 44/55 stay empty)
- Race leftover leather / bald head (`blank_race_gloves.py` is a probe, not this line)
- Necklace UVs that were authored collapsed
- A sixth unique atlas — Destiny will not carry it on this chest
- Hunter / Titan / some other armor slot

---

## Closed paths (do not walk them)

- `--fingers` on the chest inject
- AABB-fit hands / glove 0.45 bind
- `bind_material_textures.py` (hangs select)
- `assign_split_materials.py` / `assign_armor_vs.py` (vanished four parts)
- Dye tiles, PS `0x81531CBE`, another 512 pack
- Sampling Destiny `t2`, encoding `TEXCOORD2`
- `--restore` `20260822-150046` (puts tiled UVs back)
