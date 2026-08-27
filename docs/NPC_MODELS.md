# NPC models

Swapping a named Tower character for a custom mesh. Zavala is the worked example; the method is
general, and `tools/pkg/trace_entity_chain.py` is it, automated.

**Nothing is injected yet.** What follows is measured. Where a claim came from a launch, the verdict
and its duration are given, because a frozen window is what a *healthy* load looks like too.

---

## Why an NPC is easier than an enemy

[`ENEMY_MODELS.md`](ENEMY_MODELS.md) put a humanoid enemy at "materially less than weeks" because the
skeleton is readable rather than recoverable. A Tower NPC is cheaper still, for one reason:

**Five humanoid NPCs share FK skeleton `0x80C2321D`** — Zavala, Tess, Ikora, Amanda, the Gunsmith —
and `parse_bind_pose.py` decodes it to 64 bones. Our character's `rig.json` is an *estimate* that
sits **4.53 cm** from that skeleton, against 14.95 cm from a thrall's and 47.69 from a shank's. So
the rig our character is already skinned to **is Zavala's rig**. The retarget, which was the
expensive part of the Guardian, is largely already paid for.

Confirmed directly on the entity: `80808545 -> 80C2321D` appears in Zavala's own class/tag table.

---

## The chain, and the two places it misleads you

    SEntity  ->  entity resource  ->  SEntityModel  ->  buffer header  ->  raw buffer

Every hop lands on an encrypted body, so each one that has to be *read* costs a launch. Two hops
look like they can be skipped and cannot.

### The model is not in the entity's package

| tag | what | class | package |
|---|---|---|---|
| `80B9EDBA` | `zavala` | `80809C0F` SEntity | `globals_01cf` |
| `80BC8F1E` | `ai_zavala` | `80809C0F` SEntity | `sandbox_01e4` |
| `80B9F651` / `80B9F653` | resources | `80809C36` | `globals_01cf` |
| **`80C714B2`** | **body model, 3 meshes** | `808073A5` SEntityModel | **`globals_0238`** |
| `80C714B3` | body model B, 1 mesh | `808073A5` | `globals_0238` |
| `80EFB97A` | head model | `808073A5` | `sandbox_037d` |

A probe was written against `globals_01cf` on the strength of the entity living there. It passed, but
it was **the wrong package** — the model is one hop further out. Trace the chain to its end *before*
choosing what to probe.

### The headers are not in the model's package either

Zavala's model is in `globals_0238`. Its buffer headers are in `globals_01fe`, `globals_01cf` and
`globals_03ab`. A pass that bulk-requested all 746 headers of `globals_0238` dumped none of the
twelve that were wanted.

**So four packages must each accept a layer for this one swap**, not one. `trace_entity_chain.py`
reports the full set; probe every one.

### What is free, and what costs a launch

A buffer header's `reference` field in the entry table is the raw buffer's tag, and entry tables are
plain. So **buffer sizes resolve offline**. Only the **stride** lives in the header body — and stride
is what turns a byte count into a vertex count.

`ai_zavala` and `zavala` are the same body: 47 vs 46 classes, identical tag sets but for one entry.
Whichever is patched, the actor that spawns and vendors gets it.

---

## Zavala's body, measured

Model scale 0.8955 on both models.

| model | mesh | parts | positions | stride | **verts** | fits our 4,227? |
|---|---|---|---|---|---|---|
| `80C714B2` | 0 | 39 | 98,352 B | 16 | 6,147 | yes |
| `80C714B2` | 1 | 15 | 3,996 B | 12 | 333 | **no** |
| `80C714B2` | 2 | 132 | 98,936 B | 8 | **12,367** | yes |
| `80C714B3` | 0 | 8 | 10,560 B | 48 | 220 | **no** |

**The parse self-checks.** Within every mesh the position and texcoord buffers divide to the *same*
integer by different strides — mesh 0 gives 6,147 from stride 16 and from stride 20, mesh 2 gives
12,367 from stride 8 and stride 20. Two independent divisions agreeing on a whole number is not
something a misread layout produces.

Mesh 2's **stride 8 is packed int16**, which is exactly the format `inject_mesh.py` already writes.

`80C714B3` is the only model carrying a **weights** buffer (`80B9F66A`, 1,760 B, stride 8 → 220);
all three meshes of `80C714B2` hold `0xFFFFFFFF` in that slot.

**No decimate is needed** for meshes 0 or 2 — the injector may never *exceed* the target's count and
ours is 4,227. Meshes 1 and `80C714B3`'s single mesh are both smaller than our character, so they
are not swap targets.

**Which mesh is the visible body is not yet known.** Guessing costs a launch per guess; extracting
positions and indices and rendering them costs none, which is the pass that answers it.

---

## Package verdicts

A package that rejects our layers hangs the world load whatever the bytes say
(see the no-op result in [`PACKAGES.md`](PACKAGES.md)). Each verdict below is a Tower load with a
no-op layer live — a layer that stores no bodies of its own and changes nothing.

| package | holds | verdict |
|---|---|---|
| `globals_01cf` | entity, resources, some headers | **PASS** — Tower 6.1s |
| `globals_0238` | the body model | **PASS** — Tower 6.1s |
| `globals_01fe` | most headers + buffers | **PASS** — Tower 6.2s |
| `globals_03ab` | mesh 0 positions buffer | **PASS** — Tower 6.2s |
| `sandbox_037d` | the head model | already carries our layers every build |

Every verdict is `activity:in_world` reached with zero stall or host-lost lines. **All four packages
this swap needs are cleared**, and the probes have been atticked so nothing of ours sits on them.

`globals_01cf` and `globals_0238` are the **first evidence a `globals` package can carry our layers
at all**. Before them the only globals datapoint was `globals_06dc`, which rejects even a no-op — so
"globals is the problem" was a live theory. It is not; rejection stays a per-package property.

---

## What is still open

1. **Which mesh is the visible body.** Positions and indices are requested; render them and look.
2. **The paint path.** `custom_albedo` is keyed to the Guardian draw — its `(start, count)` ranges
   and G-buffer gate are written for that mesh and need redoing for Zavala's.
4. **Row order of the true rig** is not the global bone index, so `rig_true_human.json` still cannot
   replace `rig.json` (see [`the-rig-is-an-estimate`](CUSTOM_CHARACTER.md)).
5. **The head.** `80EFB97A` is in `sandbox_037d`, which we already write to, so it is the cheapest
   part of this to attempt first.
