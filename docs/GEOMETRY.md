# Geometry: how a mesh is reached, and where armour lives

The container is finished — see [PACKAGES.md](PACKAGES.md). What was left was the shape of the data
inside an entry, and specifically how to reach a **skinned** mesh, since armour is what custom
cosmetics have to replace.

## Class ids move between Destiny versions

This is the single fact that cost the most time, so it goes first.

[MontevenDynamicExtractor](https://github.com/MontevenDynamicExtractor) targets Beyond Light, where
the entity class is `0x80809AD8`. That id appears **zero** times in this install. The install is
Shadowkeep `86657.20.08.23`, and [Charm](https://github.com/DeltaDesigns/Charm) carries a per-version
schema whose `DESTINY2_SHADOWKEEP_2601` strategy matches it exactly.

So a class id read from any extractor is only meaningful together with the version it came from.
Before trusting one, count it:

```powershell
cd tools\pkg
python entity_models.py --packages investment,globals
```

## The Shadowkeep classes

| class | struct | size | what |
|---|---|---|---|
| `0x80809C0F` | `SEntity` | 0xA0 | entity root; entity-resource array at 0x10 |
| `0x80809C36` | entity resource | 0xA0 | one resource; a model resource names the model |
| `0x808073A5` | `SEntityModel` | 0xA0 | mesh array at 0x10, scale/translation at 0x50/0x60 |
| `0x80807378` | `SEntityModelMesh` | 0x88 | buffer tags, then a part table |
| `0x8080737E` | part | 0x20 | material, primitive type, index range, LOD |

Counted in this install: 46,235 entities, 241,586 entity resources, 21,240 entity models.

`SEntityModel`:

```
0x00  int64   file size
0x10  array   meshes                  count at 0x10, self-relative offset at 0x18
0x50  float4  model scale
0x60  float4  model translation
0x70  float2  texcoord scale
0x78  float2  texcoord translation
```

One mesh record, 0x88 bytes:

```
0x00  tag     positions vertex buffer header
0x04  tag     texcoord/normal vertex buffer header
0x08  tag     old-weights vertex buffer header
0x0C  ----    always 0xFFFFFFFF
0x10  tag     index buffer header
0x18  array   parts                   count at 0x18, self-relative offset at 0x20
0x28  short   x48 stage part offsets
```

`0x28 + 48*2 == 0x88`, which is exactly the size Charm declares. That the arithmetic closes is what
confirms the record stride, rather than a guess at it. Beyond Light uses 0x80 with 37 stage offsets
and two extra buffer tags, so this record is version-specific too.

A **DynamicArray** is two int64s — count, then a self-relative offset — and its data sits at
`offset_field_position + value + 0x10`. Getting that `+ 0x10` wrong lands you inside the previous
record instead of failing loudly, so bounds-check before every use.

## Buffers are raw arrays behind a 12-byte header

A **vertex buffer header** is a 12-byte entry:

```
0x00  uint32  data size
0x04  int16   stride
0x06  int16   type
0x08  uint32  deadbeef
```

Its `reference` field *in the entry table* is the tag of the raw buffer. An **index buffer header**
is 24 bytes with the same arrangement. The buffers themselves carry no header at all — unlike static
geometry class `0x80807173`, which has a 48-byte `{size, count, stride}` prefix.

That is why our earlier note — "`reference` is a class id for structured entries, a pointer for
12-24 byte stubs" — was half the story. The pointer *is* the link to the geometry.

Because the stride lives in the header's encrypted body while the buffer's size is in the plain
entry table, a **vertex count is computable offline** once the stride is known: skinned positions in
Shadowkeep are stride 16 with a paired stride-8 weight buffer, so `weights * 2 == positions` is a
usable skinned-mesh test with no keys and no launch.

## What does not work: pairing by size

An earlier scan looked for entries sized `16n` and `8n` and called them a mesh. Across the install
that matches **41,488** times, almost all noise — a texture is divisible by 16 too. Ranking classes
by total bytes fails for the same reason, and so does testing gear classes for the static
`{size, count, stride}` container: 76 entries across the 30 largest gear classes, zero matches,
correctly, because these buffers do not use it.

Shape alone cannot settle this. The mesh table has to be read, which means one dump.

## Armour is not in `gear`

`w64_gear_01aa` is 149 entries and holds **no** entity classes at all. Player gear models live in
the investment packages:

| package | models | vtx headers | buffer MB |
|---|---|---|---|
| `w64_investment_0361` | 186 | 774 | 27.3 |
| `w64_investment_01d3` | 157 | 694 | 26.4 |
| `w64_globals_03ab` | 81 | 558 | 12.0 |
| `w64_npcs_03e6` | 83 | 525 | 66.8 |

`sandbox` carries 11,867 models — weapons and world objects — and `environments` 3,612.

## Finding a helmet without names

The item → model link runs `item → artArrangementIndex → hash table → assignment table →
entity-parent → SEntity → resource → SEntityModel`. Charm decodes the assignment hop **only for
Witch Queen and later**: it logs "API is not supported for versions below DESTINY2_WITCHQUEEN_6307",
and its assignment-table class `0x808055CE` has **zero** entries in this install.

Shadowkeep tables (dumped, hop **closed** 2026-08-19):

| tag | class | what |
|---|---|---|
| `0x81319329` | `0x80807546` | arrangement index → hash (`index * 4 + 48`) |
| `0x81613D23` | `0x80805DF5` | hash → two assignment hashes (32-byte record; search by hash) |
| `0x80EC3F61` | `0x808056EA` | assignment hash → entity-parent (stride 8, 11,519 rows) |
| `0x8080744A` | | 24-byte parent; SEntity tag at **`+0x10`** (D1 layout) |

Equipped Scatterhorn Robe is arrangement 2293, hash `0x4A8A34A0`, models `0x80EFA1CA` /
`0x80EFA1A9` in **`sandbox_037d`**, not `investment_0361`. Scan the entity resource for class
`0x808073A5` — the model tag is often at `+0x64C` / `+0x65C`. Full chain: `HANDOFF.md`.

The first hop is already extracted. Sunrise's `build_data.bin` carries every item's arrangement
index (`tools/pkg/lookup_item.py`). The investment root is `0x81327D22` (class `0x80807D84`).
Tiger tags use an additive bias: `0x80800000 + (package_id << 13) + entry`. Thus package id
`0x0593` (also stored at header offset `0x04`) correctly produces `0x813xxxxx`. Using bitwise OR
instead aliases it to a wrong `0x80Bxxxxx` handle that the in-game dumper cannot find.

Observed on the live Warlock loadout:

| item | hash | arrangement |
|---|---|---|
| equipped helmet (Scatterhorn) | `0xEA042965` | 2295 |
| swapped helmet | `0x5CA160F5` | 479 |
| swapped helmet | `0x0B117DAB` | 2290 |
| ornament plug applied in-game | `0x231DBD19` | 1314 |

The Witch Queen assignment table is no longer the missing hop — see the table above. `gearArtIndex`
is unset (`0xFFFF`) on armour; appearance is the arrangement index.

The 52 "helmet-shaped" models are hard hats: span 0.15–0.55 m at ~1.72 m. Scatterhorn / Winterhart
are hoods. Six dumped models sit at head height with a larger cowl box (span 0.59–0.74 m) and were
never injected.

Geometry answers it instead. `ModelScale` and `ModelTranslation` dequantise the packed positions, so
together they *are* the model-space bounding box. A helmet is a small box sitting at head height —
a far sharper filter than vertex count, and it needs nothing but the model header.

```powershell
python entity_models.py --request investment_0361,investment_01d3   # then launch and close
python parse_models.py --helmets
python parse_models.py --tag 0x80EC2012
```

## Vertex layouts

Positions are packed **signed 16-bit**, dequantised as `value / 32767 * scale + translation`. The
stride says what else shares the vertex:

| stride | 0x00 | 0x08 |
|---|---|---|
| 8 | position x,y,z,w as int16 | — |
| 12 | position x,y,z,w as int16 | 2 bone indices + 2 weights, or a texcoord |
| 16 | position x,y,z,w as int16 | 4 weight values, then 4 bone indices |

So **skinning is inline in the position stride**, not in a paired buffer. The legacy "old weights"
tag is absent on 686 of 724 meshes here, which is why testing for a stride-8 weight buffer beside a
stride-16 position buffer finds almost nothing — that pairing is the exception, not armour's shape.

Indices are 16-bit on every helmet candidate. A part declares its own index range and primitive
type: 3 for triangles, 5 for strips. `ELodCategory` 0 is the main geometry; 4, 7 and 9 are cheaper
copies and 1, 2, 3 and 8 are attachments, so exporting every LOD at once buries a low-poly duplicate
inside the model.

## Bone indices are one global skeleton, not a per-mesh palette

Stride 16 packs four weights then four bone indices, so every armour vertex names its joints.
The obvious reading — that a draw can only pose the indices its own vertices already use, so
each armour slot has a private palette — is **wrong**, and believing it cost a whole day of
splitting a custom body across three slots.

Three offline checks settle it, all in seconds and none needing a game launch:

1. **`bone_frames.py`** — eight indices appear on more than one piece (1, 3, 4, 5 on chest and
   legs; 15, 17, 19, 20 on chest and gauntlets). Dequantised model space is character space, so
   their centroids are comparable, and **all eight agree on joint and side**. Bone 3 is the left
   thigh whether the chest robe or the leg armour names it. A per-piece remap would scramble
   sides; none does.
2. **`bone_probe.py`** — parts inside one mesh share indices heavily instead of partitioning
   them (bone 1 appears in nine separate chest parts), so there is no per-part palette either.
   The twenty undecoded bytes of the 0x20-byte part record hold no palette selector.
3. The character-select body `0x80B9F962` poses **51 joints spanning 1..63 from a single mesh**.

What does bound a write is the index *range*. The shipped chest mesh already writes up to 28,
and joints 1–28 are a complete humanoid — waist, thighs, shins, feet, toes, torso, chest,
collars, head, shoulders, upper arms, forearms, hands. Everything above 28 is fingers, and 34–71
are the only indices ever observed to misbehave when written somewhere new.

## The rig, recovered from the armour bound to it

Charm's FK/bind classes are nested inside an entity resource rather than being package entries,
and Shadowkeep has no hardcoded player base hash, so there is no bind-pose table to read: the
runtime pawn owns the rig. It does not matter. Every armour vertex carries its joints and its
bind-pose position, so the skeleton is recoverable from the geometry hanging off it.

`skeleton.py` estimates joints **at the blend, not the centroid**. Vertices dominated by one bone
sit along the middle of the limb it drives, so their centroid is a mid-shaft point; the vertices
carrying real weight on both a bone *and its parent* straddle the pivot, and their centroid lands
on the joint. The difference is about 0.2 m on a limb — the difference between a knee and a shin.

25 joints come out, and the proportions check: thigh 0.43 m, shin 0.40 m, standing height 1.87 m.
Written to `tools/pkg/objs/skeleton/rig.json` and `rig.obj`, in the same character-space metres
the injector places a custom mesh in. Bone 5 sits at y −0.120, so it is a right-of-centre torso
bone rather than a true spine — the one name in the table that is a guess.

The bind pose is an A-pose with the arms **forward**: shoulder `(−0.05, 0.19, 1.42)` to wrist
`(0.30, 0.39, 1.15)`. A T-posed custom mesh swung about Z alone cannot match it, which is why
hand joints 21/22 pick up no geometry from a nearest-donor transfer.

## Confirmed by looking at it

```powershell
python extract_mesh.py --helmets --limit 6 --out-dir out
python preview_obj.py out sheet.png
```

52 models with a ~0.30 m box centred at ~1.72 m render as unmistakable helmets — visor slits, ear
housings, crests, antennae. The ~0.58 m box at ~1.39 m is the torso cluster. Since a model carries
no name, shape is the identification.

## The plan for the mask

Unchanged from what already worked on terrain, which is the point of having proven the container
first: **rewrite positions only** — bytes 0-5 of each vertex — inherit every other byte, and keep
the vertex count identical. The `w` component, the bone indices and the weights all survive, so the
mesh stays rigged exactly as it was and the game's index buffer stays valid.

That vertex count is **not a hard limit**, only the safe first step. We write the package, so a new
buffer at a different count is writable too — it just also means updating the 12-byte vertex header,
the index buffer and the part index ranges together. Keeping the count fixed isolates one variable
for the first attempt.

The same reasoning bounds what a *whole custom character* can be. Because positions are rewritten in
place, each vertex keeps the bone weights it already had: a vertex weighted to the left forearm
stays weighted to the left forearm whatever geometry is written there. A helmet is forgiving —
near-rigid, essentially one bone. A body is not, and needs a region-aware resample rather than a
nearest-point fit. Replacing an armour *set* piece by piece is the tractable route to a full custom
character, and it sidesteps armour occlusion entirely. **2026-08-19:** that wrap is installed on
equipped Scatterhorn (chest, hood, gauntlets) via `wrap_body.py`. Judge on inspect. Legs models
`0x80EFA93B` / `0x80EFA92E` are named but not dumped. See `HANDOFF.md`.

## What the inspect screen actually draws

`helmet_mode=1` hides helmet items, so the character screen shows the **race/gender head** plus
the equipped armour set. Wrapping eight person-sized investment bodies (`0x80BA75A7` and
neighbours) did not change that view — those meshes sit under the robes.

The only globals heads in this install (body-space, ~0.31 m span at ~1.72 m):

| tag | package | meshes | parts |
|---|---|---|---|
| `0x80C71828` | `w64_globals_0238` | 4 | 26 |
| `0x80FA3678` | `w64_globals_03d1` | 3 | 12 |
| `0x80B9F9B7` | `w64_globals_01cf` | 3 | 30 |

`probe_heads.py` blanked those three; the inspect screen still showed the bald Awoken, so the
player head is not a separate globals helmet-sized mesh.

The playable-character path is `playable_character.py`. The custom GLB is already skinned
(Mixamo-style: Hips / Spine / Head / limbs, 48 joints). Destiny will not play those bones, so
each custom vertex copies bone indices from the nearest Destiny vertex.

Observed 2026-08-19: nulling Warlock armour slots shows the **default undersuit** (sleeveless
tunic, pants, boots, bald head) — not a nude mesh and not the custom character. The 72k globals
body `0x80B9F962` is the **character-select preview**; replacing it left the select screen
loading with no 3D cards while in-game inspect still drew the undersuit. That patch was undone.

In-game inspect with armour off still drew the default undersuit after wrapping those eight
bodies — they are not the live mesh. The beige tunic is a **chest** in the torso cluster
(span ~0.6–0.9 m at ~1.35 m). Those model headers were dumped; their vertex buffers were not.
`probe_undersuit.py` blanked all 59 dumped torso models; the beige tunic still drew, so the
undersuit is not in that bbox cluster. Empty equipment slots make the client fall back on
race/gender/class defaults rather than item art. `probe_investment.py` blanks **every**
entity model in `investment_01d3` and `0361` (343 models). The tunic survived, so the
undersuit is **not** in those gear packages; empty slots make the client draw a
race/gender/class default from elsewhere. Character-select spinners on that launch were
Hunter/Titan armour in the same packages — undone.

`probe_globals_bodies.py` blanked the remaining 41 person-sized globals bodies (leaving the
72k select preview). The beige undersuit still drew.

Ruled out as the in-game default mesh: investment (all 343), globals person-bodies and the
three globals heads. The inspect screen is orbit/UI, not gear. `w64_ui_037e` carries 21
entity models including two **46,448-byte** headers (far larger than a typical 1,120-byte
armour model). `w64_orbit_d2_03b1` has 152 models but they are tiny (416–1,120 B), more
likely gizmos than a skinned guardian. Dumped 238 of those 254 tags. The remaining globals_06dc
failure was traced to offline handle encoding: Tiger adds `0x80800000` to the shifted package id;
bitwise OR produced wrong `0x80DB...` aliases for the real `0x815B...` handles. `Package.tag_for()`
and the high-tag bounds are fixed; globals_06dc is now the primary focused Guardian dump.
`w64_ui_037e` is full of person-sized models (span ~1.85 m, height ~0.93 m), including
two 6-mesh 46,448-byte headers. Orbit models are kilometre-scale gizmos. `probe_ui.py`
blanks all 21 `ui_037e` entity models. Restore `settings.json.bak_armor` to put the
robes back.

## Live package-read trace (preferred targeting path)

Broad blank probes established where the default undersuit is not, but they do not reveal what the
inspect renderer actually requests. The client now attaches dormant `ReadFile` / `ReadFileEx`
observers to the retail process. They forward reads unchanged and only record calls originating in
`destiny2.exe` whose final path is below `packages` and ends in `.pkg`.

1. Launch clean, reach orbit, and press **F8** to start capture.
2. Open the character inspect view (or trigger the model transition being investigated).
3. Press **F8** again to stop. A capture is capped at 8,192 package reads if the second press is
   missed.
4. Close the game and run `python tools/pkg/analyze_package_trace.py`.

Each event records the patch filename, physical read offset, byte count, immediate caller RVA and
up to four `destiny2.exe` stack RVAs. `analyze_package_trace.py` resolves physical reads through the
newest Tiger block table and ranks every logical entry crossing a touched block, with
`SEntityModel` (`0x808073A5`) candidates first. The caller/stack histogram is also the evidence for
a later direct internal loader hook, without guessing a function signature from packed disk code.

The 2026-08-19 focused Guardian-inspect capture recorded 1,036 reads. After correcting Tiger's
additive high-package tag encoding, its model-bearing timeline contained exactly two entries from
the runtime-selected globals_06dc family: `0x815B868B` and `0x815B8697`, both 544-byte
`SEntityModel` headers. These are the primary default-Guardian candidates for the next dump/blank
test; the other 14 globals_06dc models remain a small fallback set.

### 2026-08-19 later — that lead is dead, and why

A 438-tag dump (`keys=ok`) resolved all 16 globals_06dc models plus the whole newly reachable
`0x06D0`–`0x06DE` cluster, both cinematics packages, `npcs_0938` and `sandbox_0691`. Parsing kills
the primary lead outright:

- **`0x815B868B` and `0x815B8697` are not bodies.** Both carry model scale 2.519 and translation
  z 0.625, i.e. a 5.04 m box spanning −1.89 m to +3.14 m, one mesh, 6 parts, LODs [1, 7]. A standing
  Guardian is a ~1.86 m box with its floor at 0. Their identical size and shape make them a matched
  pair of world objects, not sex/race variants.
- **The three captured "owner" tags are not owners.** All three decode identically: class
  `0x80807140`, 208 bytes, holding an array of `0x808071E8` records at `+0x090` and two `0x808071DC`
  references at `+0x0A0`/`+0x0A4`. None references an `SEntityModel`. This is a material/technique
  resource that the loose 100 ms association window caught, exactly the mis-pairing the hook's own
  notes warned about. Package adjacency is the only signal they carry.

Bounding-box screening is sharper than span+height. A body has `box_floor ≈ 0` **and**
`box_top ∈ [1.5, 2.3]`; that rejects both kilometre gizmos and torso-only pieces. Across all 438
dumped models this yields **25 person-shaped models**, the largest being `0x81A700AC`
(`npcs_0938`, 3 meshes, 1.33 MB of positions) and the only globals_06dc body being `0x815B85A1`
(6 meshes, 0.018–1.858 m, 272,720 B; `0x815B85A3` shares its exact bounding box and adds an
old-weights buffer).

**None of those 25 appear anywhere in the inspect capture.** globals_06dc was read 10 times and
touched only the two 5 m objects. Absence of a read is not proof of absence of use — the Guardian
body is resident from character select, well before the F8 window — but it does mean the capture
provides no positive evidence for any of them, and a blank probe over the 25 is a coin flip rather
than a targeted test.

### The actual blocker, and the instrument that closes it

The capture's 127 `stage=lookup` events carry only **five distinct `r9` values**
(`0x25BB312EA10`, `0x25BB3130408`, `0x25BB3131A10`, `0x25BB3133AE0`, `0x25BB3138490`), repeated
across the whole window: five live `SEntityModel` instances drawn during inspect. `r9` is a heap
pointer with no offline meaning, which is precisely why every targeting attempt so far has had to
guess.

The fix is to make the hook self-resolving rather than to guess better. At `stage=lookup`, read a
bounded window of the object at `r9` and log every dword in tag range. A live instance necessarily
carries its own mesh and buffer handles, and buffer tags resolve offline through the plain entry
tables to exactly one package and entry — turning "which of 21,240 models" into a table lookup.
This needs no correlation window and no assumption about which materializer path ran.
