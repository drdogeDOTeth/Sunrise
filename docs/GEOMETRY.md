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

The item → model link runs `item → artArrangementIndex → entity assignment → SEntity → resource →
SEntityModel`. Charm decodes it, but **only for Witch Queen and later**: it logs "API is not
supported for versions below DESTINY2_WITCHQUEEN_6307". So for this build the investment tables are
undecoded, and item names are not available that way.

Geometry answers it instead. `ModelScale` and `ModelTranslation` dequantise the packed positions, so
together they *are* the model-space bounding box. A helmet is a small box sitting at head height —
a far sharper filter than vertex count, and it needs nothing but the model header.

```powershell
python entity_models.py --request investment_0361,investment_01d3   # then launch and close
python parse_models.py --helmets
python parse_models.py --tag 0x80EC2012
```

## The plan for the mask

Unchanged from what already worked on terrain, which is the point of having proven the container
first: **rewrite positions only**, inherit every other byte, and keep the vertex count identical so
the game's index buffer and bone weights stay valid. The stride-8 weight buffer is never touched, so
the mesh stays rigged to whatever bones it was rigged to.
