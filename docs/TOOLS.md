# Toolchain

Everything used to get custom geometry into the game, and what each piece is for.

Two halves that never mix. **In-game hooks** (C++, in the mod) can decrypt and can see live objects,
because the block keys only exist inside the running process. **Offline tools** (`tools/pkg/`,
Python) can read entry tables, write packages, and do geometry, because none of that needs keys.
Anything that needs both is a two-step: the game writes files, the tools read them.

Format reference lives in [PACKAGES.md](PACKAGES.md) (the Tiger container) and
[GEOMETRY.md](GEOMETRY.md) (classes, vertex layouts, and the running targeting log).

---

> **Status, 2026-08-19:** write pipeline proven. Assignment hop **closed** (`0x81319329` →
> `0x81613D23` → `0x80EC3F61` → sandbox Scatterhorn). Wrap of the equipped Scatterhorn set is
> **installed** — see `../../HANDOFF.md`. Tower frames `0x80B9F855` / `0x80C23B5D` / `0x80FA2308`
> are **not** the player. Do not run `wrap_player_body.py` for the playable Guardian.

## Rules that have each cost a session

1. **Archive a capture the moment you analyse it.** `sunrise.log` rotates to `sunrise.log.old` on
   every launch, one deep. Two launches destroy a capture. The 2026-08-19 Tower capture was lost
   exactly this way, after the hazard had already been noticed. Copy to `reference/captures/`
   immediately.
2. **Never dump with a patch installed.** `dump_if_requested` reads through the live package stack,
   so a dump taken over your own patch returns your own bytes. Blank `request.txt` whenever a patch
   is in `packages\`. `check_dumps.py` refuses when `inject_receipt.json` names a live file.
3. **Tags are additive, never OR.** `tag = 0x80800000 + (package_id << 13) + entry`. OR aliases every
   package at id `0x400` and above — that bug hid **96 packages / 4,126 entity models, 19.4% of the
   install**. `globals_06dc` is `0x815Bxxxx`, not `0x80DBxxxx`.
4. **Judge the playable Guardian on inspect / APPEARANCE only.** The in-world Guardian is a
   permanent dissolve shell. The inspect paperdoll draws armour solidly — that is the live mesh
   (Scatterhorn, not a globals body).
4b. **Never confirm anything by eyeballing the in-world Guardian.** It is a dissolve shell in every
   screenshot, before and after every patch. A `--match` negative is also not evidence (see 4c).
4c. **A `--match` negative is not evidence.** It only sees models already dumped. Check the package
   histogram (`live_models.py` with no `--match`) before concluding anything is absent.
5. **Never write packages while `destiny2.exe` is running** — `WinError 32`.
6. **No BOM in files the game reads.** Use Python `write_text(..., encoding="utf-8", newline="")`;
   PowerShell `Set-Content -Encoding UTF8` writes a BOM and breaks `settings.json` with a
   misleading error.
7. **One launch is the scarce resource.** Requests are nearly free and capped at 1024 — fill the
   budget rather than making a second trip.

---

## The pipeline

```
   census            request           LAUNCH          parse            identify
entity_models.py -> entity_models.py -> (game dumps) -> parse_models.py -> live_models.py
                    --request                                              --match
                                                                              |
                        install <- write <- verify <- wrap                    v
                       LAUNCH   inject_mesh  compare_    wrap_player_body.py <-+
                                 write_all   silhouettes
```

---

## Reading the container

| tool | what it does |
|---|---|
| `tigerpkg.py` | Tiger `.pkg` reader — both header layouts, entry/block tables, patch siblings. Everything else imports this. |
| `oodle.py` | Binds the game's own `oo2core_3_win64.dll` so blocks decompress exactly as the game does. |
| `verify_all.py` | Parses every installed package and reports disagreements. The regression test for the reader. |
| `resolve.py` | Resolves a tag to class, size and package without decrypting. |
| `inspect_entry.py` | Prints one dumped entry with the understood fields annotated. |
| `classes.py` | Histograms entry classes across the install. Entry tables are plain, so this needs no keys. |
| `lookup_arrangement.py` | Arrangement index → hash → assignment → entity-parent → model. Hop is **closed**. `--census` / `--request` / `--inspect` / no-args loadout walk. |

## Writing the container

| tool | what it does |
|---|---|
| `patch.py` | Replaces the bytes behind an entry, writing the next patch file the way Bungie's updates do. Writes the per-block SHA-1 — a null hash is accepted by the registrar and then **hangs the geometry streamer**. |
| `inject_mesh.py` | The write front end: `blank_model`, `write_all`, `--undo`, plus topology injection. `--undo` also clears the validation caches. |
| `roundtrip_test.py`, `multiblock_test.py`, `gametest.py`, `bisect_one.py` | Writer proofs: offline round trip, multi-block bodies, one real patch into the install, and minimal-change bisection when the loader rejects something. |

## Getting bytes out of the game

Encrypted entries need the running process. `src/client/content/items/packages/package_dump.cpp`
reads `dump\request.txt` **once per process** and writes `tag_XXXXXXXX.bin` beside it.

| tool | what it does |
|---|---|
| `entity_models.py` | Censuses entity models per package; `--request a,b` writes a dump request for every `0x808073A5` in the named packages. Cap 1024. |
| `make_request.py`, `request_buffers.py`, `request_gear.py` | Narrower request builders. |
| `check_dumps.py` | Compares dump sizes against the shipped entry tables — catches dumps poisoned by an installed patch. |

Request format: `tag 0x815B8295` or `class 0x808073A5`, one per line, `#` comments. The parser breaks
on `\r` **or** `\n`, so either line ending works.

## Geometry

| tool | what it does |
|---|---|
| `parse_models.py` | Parses dumped `SEntityModel` headers into meshes and parts. `--tag` for detail, `--dump` to point at another folder. |
| `extract_mesh.py` | Dumped model + buffers → viewable OBJ. LOD 0 only unless `--all-lods`, because the cheap LODs sit *inside* the main body. |
| `glb.py` | Reads meshes from a `.glb` with no third-party dependency. |
| `compare_silhouettes.py` | **New.** Renders OBJs as front/side silhouettes so a wrap can be judged before installing. Filters to face-referenced vertices, and uses one shared scale so a squash cannot hide. |
| `preview_obj.py` | Contact sheet for identifying an unknown mesh. |
| `wrap_player_body.py` | Tower-frame wrap. **Not** the playable Guardian. Kept for the old false-positive target. |
| `wrap_body.py` | Earlier injection: Scatterhorn chest/hood/gauntlets → custom GLB by smoothed displacement. Superseded by topology injection. |
| `inject_scatterhorn.py` | **Current injection.** Whole custom body at its own topology on the chest draw, one bind frame, weighted across joints 1–28. `--dry-run` prints a per-joint drift audit. |
| `fit_mask.py`, `shrinkwrap.py`, `reshape.py`, `decimate_mask.py`, `distort.py` | Earlier reshaping experiments, kept for reference. |

## Skinning

Bone indices are **one global skeleton index space** shared by every armour piece — see
[GEOMETRY.md](GEOMETRY.md). These three tools establish and use that, all offline.

| tool | what it does |
|---|---|
| `bone_frames.py` | Compares the centroid of each bone across the pieces that share it. Eight shared indices, eight agreements: the index space is global. Run this before ever believing a palette theory again. |
| `bone_probe.py` | Dumps the raw 0x20-byte part records with the twenty bytes `parse_models` leaves undecoded, plus the bone set each part's own vertices use. Standalone — importing `parse_models` costs ~90s of package scanning. |
| `skeleton.py` | Recovers the 25-joint bind skeleton from armour weights, estimating each joint at the parent blend rather than the bone centroid. Writes `objs/skeleton/rig.json`, `rig.obj` and `rig.svg`. |
| `retarget_mesh.py` | Blender. Drops unrigged objects, poses the custom mesh's arms onto `rig.json`, then exports the OBJ **and its real per-vertex weights** already mapped to rig bone indices. Replaces `prepare_mesh.py`, which joined the GLB's unrigged icosphere into the body and discarded the armature. |
| `census_bones.py` | Per-piece bone sets → `objs/skeleton/palette.json`. Read as *where to take a weight from*, never as what a draw may pose. |

## Live tracing — the part that ended the guessing

Two F8-gated hooks in the mod, both dormant until you press F8:

- `src/client/hooks/package_trace/package_read_trace.{h,cpp}` — records `.pkg` `ReadFile`s (file,
  offset, size, caller RVA, stack). Capped at 8,192 reads; **hitting the cap only stops read
  logging**, `g_capturing` stays set so the model trace survives a full world load.
- `src/client/hooks/model_trace/model_class_trace.{h,cpp}` — detours the reflected class lookup for
  `SEntityModel` and its constructor. Reads a bounded `0x800` window of each live object and logs
  every dword in Tiger handle range as `r9tags=` / `paytags=` / `dsttags=`. Reads are guarded by
  `VirtualQuery` (committed, readable, clamped to the region). Event cap 16,384 — a Tower load
  produced 8,014, so the old 4,096 would have truncated it.

| tool | what it does |
|---|---|
| `analyze_package_trace.py` | Maps physical reads back to Tiger blocks and entries; ranks `SEntityModel` candidates. |
| `live_models.py` | Resolves captured handles to entries. **Run with no `--match` first** — the package histogram needs no dumps. `--match` names models, but only ones already dumped. |
| `probe_scale_body.py` | Scales a model's vertices in place. The decisive "do buffer writes render?" test. |
| `inject_player_body.py` | Full topology swap: custom vertices, triangles and part table. |
| `prepare_mesh.py` | Blender: join, weld and triangulate a GLB to fit a vertex budget. |

### Why `live_models.py --match` is an identity test, not a ranking

`r9` is **not** the `SEntityModel` blob — across 878 instance windows, zero named a model handle, but
248 carried **vertex buffer headers**. A buffer header belongs to exactly one mesh of exactly one
model, so intersecting the live header set against every dumped model's mesh table names the resident
models exactly. A miss means "not yet dumped", never "not the target".

**Trust `r9tags`; distrust `paytags`.** One resource-event `paytags` window held **1,522 handles** —
a serialized reference table including orbit gizmos and NPCs. Presence there proves reference, not
identity.

### Capture procedure

Capture a **world transition**, not a static screen. The inspect capture found nothing because the
body is resident from character select; a Tower load *builds* it inside the window.

1. Launch, reach orbit. 2. **F8 on.** 3. Travel to the Tower, let it load, move around.
4. **F8 off.** 5. Close the game. 6. **Copy `sunrise.log` to `reference/captures/` now.**
7. `python live_models.py --log <archived> --match`

## Probes

Blanking every part's index count makes a model invisible, and needs only the dumped header — no
buffers. Cheap, and now used to *confirm* an identification rather than search for one.

| tool | target |
|---|---|
| `probe_player_body.py` | **The confirmed in-world Guardian body.** Blanks only the globals triple, so player and NPC crowd separate in one launch. |
| `probe_heads.py`, `probe_undersuit.py`, `probe_investment.py`, `probe_globals_bodies.py`, `probe_ui.py` | Earlier searches. All negative — and all judged against the inspect screen, so they never tested the in-world body. |

---

## Worked example: finding the in-world Guardian body

**Retracted.** The triple `0x80B9F855` + `0x80C23B5D` + `0x80FA2308` is **Tower frames**, not the
playable Guardian. The playable character is its equipped armour (Scatterhorn in `sandbox_037c` /
`037d` / `0698`). Keep the write-up below as a record of how the live-trace loop works, not as an
identification.

The problem: 21,240 entity models, one bit of feedback per launch. Two sessions of blank probes had
ruled out investment (343), the globals person-sized set (44), and `ui_037e` (21) — all against the
wrong screen.

1. **Fixed the tag encoder** (additive, 12-bit ids) — reopened 4,126 previously unaddressable models.
2. **Dumped 438 model headers** in one launch, filling the request budget rather than the 16 the
   prior lead suggested.
3. **Screened by bounding box** — `floor ≈ 0` **and** `top ∈ [1.5, 2.3]`, far sharper than span plus
   centre height. 25 person-shaped models. The prior lead's two candidates turned out to be 5.04 m
   world objects.
4. **Captured a Tower transition** with the window-scanning hook.
5. **`live_models.py --match`** returned two families with 100% of their headers live:
   `sandbox_06d9` (six humanoids, all *different* sizes → an NPC crowd) and
   `0x80B9F855` + `0x80C23B5D` (**byte-identical** geometry, 4 meshes, 287,288 B, plus a third copy
   `0x80FA2308`). Replication across several `globals` packages is the player-body signature.
6. **`probe_player_body.py`** blanked only the triple. In-world the Guardian lost its geometry; a
   Frame standing beside it rendered normally. Confirmed.

**The body:** 4 meshes, 33,877 verts, box −0.009..1.842 m, scale 0.925731, translation
(0.1841, 0, 0.9165). Strides are **mixed** — mesh 0 is 16, mesh 1 is 12, meshes 2–3 are 8. Mesh 2 is
the body proper: 31,480 verts, 256 parts. `0x80C23B5D` points at `0x80B9F855`'s buffers, so wrapping
once covers both; `0x80FA2308`'s buffers live in `globals_0238`.

---

## Injection: `wrap_body.py` (inspect armour)

Current path for the playable Guardian. Rewrites **bytes 0–5 of each vertex and nothing else**.
Bytes 6+ carry `w`, bone indices and weights. Vertex count is unchanged.

It now does the three things that used to live only in `wrap_player_body.py`:

- **Wraps every mesh**, not just `meshes[0]` — Scatterhorn chest mesh 2 is the hanging panels.
- **Resolves the destination package from the buffer tag**, not the model tag. Chest mesh 0 is
  `sandbox_037d`; panels are `sandbox_0698`; gauntlets mesh 0 is `sandbox_037c`.
- **Fits one AABB over the whole equipped set**, never per-mesh or per-slot. A per-hood fit would
  crush the custom character into the helmet box.

Laplacian-smooths the displacement (a raw nearest-point snap collapsed 40–50% of triangles on the
full-body wrap). `--arm-swing` 55° matches the T-posed GLB to Destiny's bind pose.

`wrap_player_body.py` still exists and still targets Tower frames. Do not run it for this.

```powershell
python wrap_body.py --dry-run
python wrap_body.py
# launch, judge inspect / APPEARANCE
python inject_mesh.py --undo
```

### The arm swing

The GLB is **T-posed**; the Guardian body is not. Wrapping across that snaps Destiny's arm vertices
onto whatever T-posed surface is nearest — usually the torso — and the resulting bind pose then
shears when the skeleton animates, because each vertex still follows the bone it was weighted to.

`--arm-swing` (default 55°) rotates the arms down about each shoulder with a smoothstep ramp across
the deltoid. Done as **geometry, not through the armature**: the wrap consumes a point cloud, skinning
is inherited from the target, so no rig needs to survive — and it iterates in numpy in seconds rather
than through a Blender round trip. Measured against the real body's width-versus-height profile, 55°
roughly halves the mismatch (RMS 0.2554 → 0.1329).

Match the *pose*, not the bulk. The Guardian has armored pauldrons and a helmet; the custom character
has slim shoulders and a gas mask. Making the widths agree would defeat the point — what matters is
that arm vertices find arm surface.

```powershell
python wrap_player_body.py --dry-run --obj wrapped.obj
python compare_silhouettes.py body_lod0.obj wrapped.obj check.png   # look before installing
python wrap_player_body.py
# launch, look in world
python inject_mesh.py --undo
```

**Known follow-up:** an injected mesh inherits part 0's material, so texturing is wrong until the
material is fixed. Expected, not a failure.

---

## Environment

| what | where |
|---|---|
| Game | `C:\Sunrise`, Shadowkeep `86657.20.08.23` |
| Our DLL | `C:\Sunrise\bin\x64\steam_api64.dll` |
| Packages | `C:\Sunrise\packages\` |
| Dumps / archived headers | `C:\Sunrise\bin\x64\Sunrise\dump\`, `dump_models\` |
| Logs | `C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log` |
| Archived captures | `reference/captures/` |
| Custom character | `C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb` |
| Blender | `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe` |

Schema is Charm's `DESTINY2_SHADOWKEEP_2601`. Monteven's class ids are Beyond Light and appear zero
times here. Restore armor with `settings.json.bak_armor`; restore the stock DLL with
`.\install.ps1 -Restore`.
