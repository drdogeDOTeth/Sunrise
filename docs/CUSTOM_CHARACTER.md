# Custom character — what is in, and what is next

Live memory and launch discipline live in [`HANDOFF.md`](../HANDOFF.md). This file is the durable
status so a new session does not have to reconstruct the last two weeks.

**Target:** `void_4003GasMask.glb` on the playable Warlock, looking like
`tools/pkg/objs/textures/_glb_blender_preview.png` — green SkinTats graffiti, charcoal tank, black
gas mask, twirl, teal necklace. Not a dye tint.

**Proven (user-confirmed 2026-08-22):** geometry, skinning, tangent frame, five-part split, and
**unique GLB albedos on character select** (hook v12). Snapshot `20260822-172401.json`.

---

## How the look actually ships

Destiny will not carry five unique atlases through the chest dye material. The bound slot is one
512 BC7 (`0x80B3611D`) and pixel shader `0x81531EE6` luma-gates whatever you put there. Painting
that tile, swapping to `0x81531CBE`, and stealing off-chest materials are all **closed**.

The working path is a **draw-time pixel-shader replace** on the five custom index ranges only:

`Sunrise/src/client/hooks/custom_albedo/`

| | |
|---|---|
| Identify | `DrawIndexed` / `DrawIndexedInstanced` vtable 12 / 20, exact `(StartIndex, IndexCount)` |
| Shader | `ps_5_0`, full ISGN of the live dye PS |
| Albedo | per-part PNG on `TEXCOORD3.xy` (tank/mask/necklace/skin 2048², twirl 512²) |
| Normal | `o1.xyz = saturate(normalize(TEXCOORD0.xyz) * 0.375 + 0.5)`, `o1.w = 0` |
| Material | `o2 = (0.5, 0.25, 0.0, TEXCOORD0.w)`, `o0.w = 0.2` |
| Files | `C:\Sunrise\bin\x64\Sunrise\custom_{tank,mask,necklace,skin,twirl}.png` |

The live dye PS dump and disassembly are `tools/pkg/known_good/live_chest_ps.bin` / `.asm`
(copies also under `tools/pkg/objs/`, which is gitignored).
`TEXCOORD3` is the mesh UV. `TEXCOORD0..2` are TBN. Flat normal reconstruct is **`TEXCOORD0.xyz`**
(`worldN = v0*nz + v1*nx + v2*ny`).

v3 magenta (bright constants on every target) was the only write that showed until that encode
was matched. Do not guess RT1/RT2 again.

---

## Packages

| layer | role |
|---|---|
| `037c_28` | five-part split geometry (verts byte-identical to `_22`) |
| `037d_26` | all five parts name chest-native `0x80EFA1DC` |
| `019b_9` | packed 512 still sitting on the dye tile (unused while the hook fires) |
| `0698_24` | 0–1 UVs. **`0698_25` (tiles) is attic’d** in `packages\_reverted_uv_tiles\` |

Restore: `python tools/pkg/known_good.py --restore` (newest = `20260822-172401`).
Do **not** restore `20260822-150046` — that puts `0698_25` back.

---

## Closed paths (do not retry)

- `bind_material_textures.py` — hang at character select
- `assign_split_materials.py` / `assign_armor_vs.py` — vanish four parts
- Steal any material not already on the chest
- Dye tiles t3–t8, saturation-boost, another 512 pack
- PS `0x81531CBE`
- `--undo` of the mesh
- Colour-probe hooks that guess the G-buffer

---

## Still open

**In-world lighting (user 2026-08-22, video `DestinyTexturesTest.mp4`).** Character select is the
Blender look. In destinations the body is a near-black silhouette. Sprint/jump pops green (often
the old quilt smear) then back to black; clothes appear to glitch in and out.

Most likely:

1. World deferred lighting is stricter about `o1`/`o2` than the select studio. Our material
   constants are a flat-normal approximation, not a port of `live_chest_ps.asm`.
2. The hook matches **LOD-0 counts only**. Other LODs / motion passes miss and fall through to the
   dye PS + live `019b_9` quilt — that is the smeared-green flash.
3. Black clothes (tank, pants) * a rejected lighting term = a silhouette, so parts look like they
   vanish.

Next: port more of the dumped dye PS for `o1`/`o2` (keep our albedo on `o0.xyz`), and match every
LOD range that draws these parts. Do not pack another 512. Do not guess another G-buffer.

**Leftover cosmetics:** stock gloves over custom hands; bald race head over the gas mask.
Necklace UVs on the mesh are collapsed (`u` 0.817–0.859, `v` ~0.410).

---

## Build / install

Destiny closed.

```powershell
.\build.ps1
.\install.ps1
```

Hang: copy `C:\Sunrise\bin\x64\steam_api64.dll.original` back over `steam_api64.dll`.
