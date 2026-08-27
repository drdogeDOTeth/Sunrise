# Enemy models

Can a custom character stand in for a combatant? This is the scoping file. **Nothing is injected
yet** — what follows is measured, and the measurements moved the answer a long way.

---

## What is already free

**Finding them.** 206 enemy entity tags are named in `EntityNames.json`, with clean species names:

| species | entity | species | entity |
|---|---|---|---|
| `thrall` | `80BF8A1D` | `Legionary` | `80C1A52D` |
| `Thrall` (v2) | `80F7A35A` | `dreg` | `80C1AF8A` |
| `Acolyte` | `80F7A499` | `shank` | `80C1B45C` |
| `Acolyte` (v2) | `80FD7696` | `knight` | `80BF9223` |

**Spawning them.** The client carries the whole combat loop — a spawned enemy tracks the player,
shoots back and takes damage. See [`WORLD_POPULATION.md`](WORLD_POPULATION.md).

**Writing geometry.** Same injector as the chest. Nothing new needed.

---

## The measurement that mattered: enemies carry their own skeletons, and they are readable

Dumped 2026-08-26 at character select (the pass runs at investment refresh, so **no world load and
no enemy spawned** — it never touches the unsolved package-rejection hang).

Every enemy entity contains Charm's FK skeleton class `0x80808545`, **four times**, each occurrence
immediately followed by the same tag:

```
80808545 80BF8816 80809C50 00000598
^class   ^tag
```

| species | FK skeleton tag |
|---|---|
| thrall, Thrall v2 | `80BF8816` |
| Acolyte, Acolyte v2 | `80BF88B8` |
| knight | `80BF89BF` |
| Legionary | `80C1A1F3` |
| dreg | `80BFDBD9` |
| shank | `80C18723` |

**Six distinct skeletons across eight species.** So each species has its own — confirmed, not
inferred. But the important half is that the tag is *right there*: an enemy's bind pose can be
**read**, where the Guardian's had to be estimated from armour skin weights
(`skeleton.py`, and see the caveats in `symmetrize_rig.py`).

The blob layout is byte-identical across all eight, **shank included** — a limbless drone parses
the same as a biped. The container is uniform; only the skeleton differs. That was exactly what the
dreg (four arms) and shank (no limbs) were included to test.

`+0x00` is the record's byte length, exact on all four checked (`0xADC4` = 44,484).

### The resource table

Every class id pairs with the tag it introduces, one each. For the thrall:

| class | tag | |
|---|---|---|
| `80808545` | `80BF8816` | FK skeleton |
| `80808F96` | `80BF881B` | adjacent to it |
| `80808506` / `80808569` | `80BF96F1` | adjacent to it |
| `80807314`, `808072B8`, `808072C0` | `80FC7312` | **model candidate** — three classes agree |
| `808072CB` | 7 tags | material/texture array |

Note `0x808073A5` (`SEntityModel`, per `parse_models.py`) does **not** appear. The model is reached
by another class; `80FC7312` is the candidate on the strength of three separate classes naming it.

**Resolved 2026-08-27.** `tools/pkg/trace_entity_chain.py` walks the remaining hop offline and ranks
`80FC7312` first with **four** classes agreeing, then follows it:

    80BF8A1D thrall -> resource 80FC7312 -> MODEL 0x80FC7300, 4,592 B, w64_sandbox_03e3_5

So the thrall's model is `0x80FC7300` and the package a thrall swap must patch is `sandbox_03e3`.
Neither the model nor the resource is in the entity's own package (`sandbox_01fc`) — the same trap
documented for Zavala in [`NPC_MODELS.md`](NPC_MODELS.md). `80FC7300` is not dumped, so its mesh
table and vertex counts are still unread.

---

## What this costs

| | what you get | effort |
|---|---|---|
| **Rigid swap** | model glides where the enemy was, no limb animation | **days** — weight everything to the root bone, skeleton compatibility sidestepped |
| **One humanoid species** | properly animated Thrall / Acolyte / Legionary | **materially less than the "weeks" first estimated**, because the skeleton is readable rather than recoverable |
| **All enemies** | — | not realistic. Six skeletons for eight species, and Shanks/Servitors/Harpies have no humanoid form to retarget onto |

Fallen — `dreg`, `vandal`, `captain` — have **four arms**. They are not retarget targets.

Two constraints bite before animation does: our character is **4,227 vertices** and the injector may
never exceed the target's count; and the paint path is keyed to the Guardian draw, so
`custom_albedo`'s exact `(start, count)` ranges and its G-buffer gate need redoing for an enemy.

And the honest risk: **some packages reject our layers** — still unsolved, and a rejected layer
hangs the world load rather than failing gracefully. Enemy models live in packages we have never
written to.

---

## The Guardian-skeleton lead

`find_guardian_skeleton.py` dead-ended on 2026-08-20: the two encrypted resources on the equipped
chest dumped as `0x80803EB6/B7`, not FK/bind, and the conclusion was that armour entities do not
carry the player bind-pose table. **That conclusion still stands — but it only ever looked at
armour.**

Enemies carry theirs inline. No player entity is named in `EntityNames.json`, but Bungie reuses the
human rig, so a humanoid NPC's FK skeleton is very likely the one our character is skinned to. Pass
2 requests seven of them (`ai_zavala` ×2, `ai_tess_everis`, `ai_ikora_rey`, `ai_amanda_holliday`,
`ai_gunsmith`, `ai_soldier_assault_rifle`).

### Pass 2 result, 2026-08-26: they do, and five of them share one rig

| NPC | FK skeleton |
|---|---|
| `ai_zavala`, `ai_tess_everis`, `ai_ikora_rey`, `ai_amanda_holliday`, `ai_gunsmith` | **`80C2321D`** |
| `ai_zavala` (v2) | `80F2F5C5` |
| `ai_soldier_assault_rifle` | `80C224D1` |

**Five different humanoid NPCs name the same skeleton.** That is the shared human rig, and almost
certainly the one our character is skinned to. `find_guardian_skeleton.py` dead-ended in August
because it only ever looked at *armour* entities — NPCs carry theirs inline exactly as enemies do.

### The bind-pose format decodes

Rows of **unit quaternion + position**. Verified on the thrall skeleton: the middle four floats of
a row sum of squares to **1.0001**, and rows pair up as exact **mirrors** — left and right limbs:

```
 2.0231  1.0000 | -0.1431 -0.8501 -0.4092  0.2991 |  0.4804  0.6041
 1.1564  1.0000 | -0.4092  0.2991  0.1431  0.8501 |  0.4804 -0.6041   <- mirror of the above
```

Skeleton size corroborates the shape: **shank 2,576 B** (limbless drone), thrall 5,888, Acolyte and
knight 6,048, Legionary 8,016, **dreg 8,656** (four arms). Header is uniform — `+0x00` byte length,
`+0x38` pointing 88 bytes before EOF.

Pass 3 requests `80C2321D`, `80F2F5C5`, `80C224D1`. If it parses, `rig.json` stops being an
estimate recovered from skin weights — and
[`symmetrize_rig.py`](../tools/pkg/symmetrize_rig.py) and its remaining known error (a 12.20 cm
humerus against a 26.44 cm forearm) become obsolete rather than a correction. That humerus is the
sharpest test available: ground truth either confirms it or convicts the estimator.

### Pass 3 result: the bind pose is decoded, and the estimate is convicted

`parse_bind_pose.py` reads it. **32-byte rows: `[scale] [quat x y z w] [pos x y z]`.** The unit
quaternion is the row test and is what *locates* the table — scan every 4-byte alignment and keep
the longest passing run. The header does not point at it: `+0x08`/`+0x10` are constant 96/112 on
every skeleton and `+0x18` points elsewhere. (Dividing the region by 48 looked clean and produced
NaNs. Alignment scanning is what works; the human rig starts at `0xA5C`.)

| rig | bones | extent |
|---|---|---|
| human `80C2321D` | **64** | z 0.000 → 1.677 m, y ±0.427 |
| thrall | 48 | — |
| shank | **15** | z 0.000 → 0.939, y ±0.614 |

**It is the right rig, and the control proves it.** Mean nearest-neighbour from our estimated
`rig.json`:

| vs | mean error |
|---|---|
| **human `80C2321D`** | **4.53 cm** |
| thrall | 14.95 cm |
| shank | 47.69 cm |

3.3× better against the human rig than a thrall's, 10× than a drone's. (An earlier attempt to
confirm this by searching the blob for our joint *coordinates* was discarded: random numbers scored
80.8% against real coordinates' 84.6%. With 1,664 sane floats the value range is saturated and that
test is worthless. The species control is the one that discriminates.)

**The humerus called it.** `rig.json` read a 12.20 cm humerus against a 26.44 cm forearm, flagged
here as backwards for a human but unprovable by symmetry. Ground truth:

| | estimate | **truth** |
|---|---|---|
| humerus (R) | 15.47 cm | **27.17 cm** |
| forearm (R) | 26.55 cm | **25.44 cm** |

Humerus slightly longer than forearm — textbook. The estimator had it at half. Most joints are good
(hands and toes **0.55 cm**, pelvis 3.03, feet 2.44, head 2.26); the bad ones are `spine_lower`
13.14 cm and `upperarm` 8.73–11.63 cm — interior joints, where a dominant-weight centroid lands
mid-shaft instead of on the pivot.

**Still open: the index mapping.** Row order is **not** the global bone index that armour weights
use — testing `row[i]` against `rig.json` bone `i` matches only the pelvis, and that is chance.
Using the true rig needs the NodeHierarchy (class `0x80808A08`) or bone-name hashes. Until then
`rig.json` stays as it is; `objs/skeleton/rig_true_human.json` holds the truth alongside it.

---

## Procedure

```bash
python scan_entity_tags.py --request     # read dumped blobs, emit the next request block
```

`dump/request.txt` takes `tag 0x…` lines; write it **without a BOM**. The pass runs once per
process, so a repeat needs a fresh launch. Archive `logs/sunrise.log` immediately after — it
rotates one deep, and two launches destroy a capture.
