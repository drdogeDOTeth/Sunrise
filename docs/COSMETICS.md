# Custom appearance in Sunrise

Everything about wearing ornaments, shaders and skins you did not earn — what works, what it costs,
and what is out of reach.

## The three tiers

Not all of "custom cosmetics" is equally reachable.

| tier | what | status |
|---|---|---|
| Existing ornaments and shaders, applied normally | any plug the game already ships | works upstream, no changes needed |
| Any plug on any item, regardless of pool or ownership | cross-class ornaments, unowned shaders | **this fork**, see below |
| Genuinely new art — custom meshes and textures | a model that is not in the game | **in:** Warlock GLB on character select (hook v12, 2026-08-22). In-world lighting still dark. See `CUSTOM_CHARACTER.md` and `HANDOFF.md`. |

## How the game decides what you look like

An ornament is not a special kind of item. It is a *plug* sitting in a *socket lane* on an equipped
item, exactly like a mod or a perk. Your appearance is a pure function of which plugs sit in which
lanes.

`character_appearance_render.cpp` folds them in a fixed order. The base item contributes its gear
art and art arrangement. Then each plug is layered over: an ornament declares its own
`gearArtIndex` and overlay arrangement and replaces only the fields it declares; a shader declares
material pairs instead, folded across three stages (base, plug-only, late) where only the first six
distinct material keys survive.

The consequence: **making an ornament show up is a question of getting its hash into a plug lane.**

### Ornament model replacement works — verified

Confirmed in game on 2026-08-16 against build 0.3.0.0: equipping the Omega Mechanos Crown universal
ornament on a Scatterhorn Hood changed the rendered model, in world, not just in menus. All
`state.cosmetics` switches were off, so this is stock upstream behaviour.

This matters because Sundial's FAQ says otherwise — that Sunrise "does not apply every ornament's
model replacement." That was true of **0.2.1**, which is what Sundial targets. Two commits landed
afterwards:

```
39215d4 fix(appearance): select class-specific item art
df8dd6a feat(appearance): refresh equipped gear across live families
```

`39215d4` is the fix. Before it, a plug's arrangement *replaced* `art[kArtArrangementSlot]`; after
it, the arrangement is appended to the bounded overlay list while the base arrangement stays put,
which is what "preserves armor and ornament composition" in its message means. Anyone reading
`apply_plug_art` and seeing the asymmetry between gear art (replaces) and arrangement (appends)
should know that asymmetry **is the fix, not a bug** — restoring the symmetry reintroduces 0.2.1's
behaviour.

The practical upshot for custom art: the ornament delivery path works, so replacing the art an
ornament points at is viable and does not require sacrificing a base item.

## What actually blocks it

Three independent checks in the socket-plug staging path, all in
`src/state/runtime/state_account_socket_runtime.cpp`:

1. **Pool compatibility** — `is_socket_plug_allowed(item, lane, plug)`. Every item/lane pair has an
   authored pool of plugs the game will accept. A Titan ornament is not in a Warlock helmet's pool.
2. **Ownership** — shaders are a finite stack the account draws down, so applying one you do not
   hold is refused. Ornaments were already exempt upstream: they are permanent unlocks, not stacks.
3. **Insertion cost** — some plugs carry an authored material requirement set.

## Turning them off

Add to `settings.json` under `state`. Every field defaults to `false`, so an unconfigured build
judges a plug exactly as the game does.

```json
"state": {
  "cosmetics": {
    "unrestricted_plugs": true,
    "ignore_plug_ownership": true,
    "ignore_insertion_cost": true
  }
}
```

| key | drops |
|---|---|
| `unrestricted_plugs` | the pool check — any installed plug in any real socket lane |
| `ignore_plug_ownership` | the stack check — unowned shaders apply |
| `ignore_insertion_cost` | the material charge |

They are separate switches because they fail for different reasons and you usually want one:
a pool refusal is about *where* a plug may go, an ownership refusal about *whether you hold it*,
a cost refusal about *what it charges*.

Two implementation notes worth knowing if you touch this code:

- `ignore_plug_ownership` drops the entire stack transition, not just its refusal. A plug the
  account never held has no row to check, no unit to spend and nothing to release — leaving the
  spend in place would fail one step later with a confusing `plug_stack` error.
- Relaxed pools break lane disambiguation. `state_account_item_action_runtime.cpp` figures out
  which socket you meant by asking which lanes accept the plug; once every lane accepts it, any
  multi-socket item goes ambiguous and the request is refused. It now takes the lane the request
  already names whenever that lane exists.

## Finding hashes

**Sunrise's definition hashes are the same hashes Bungie publishes.** Verified: `0xE516CF40` is
3843477312 decimal, which is Blast Furnace — and that hash sits in the `kinetic` slot of the
shipped default loadout. Right hash, right slot, right weapon type.

This means the entire public Destiny 2 database ecosystem works as a lookup table. Convert the hex
hash to decimal and search it, or go straight to a database URL:

- `https://www.light.gg/db/items/<decimal-hash>/`
- Bungie's own manifest, DIM, and d2foundry all key on the same number

```powershell
# hex -> decimal
[Convert]::ToUInt32("E516CF40", 16)
```

The one caveat: Sunrise runs an **old build**. Any item added to Destiny 2 after that build does
not exist in the installed packages, so its hash resolves to nothing no matter what light.gg says.
Anything from the shipped era works.

## Tooling: use Sundial

[Sundial](https://github.com/KyleThmpsn/sundial) (Rust, GPL-3.0) is a mature GUI for editing
Sunrise's `settings.json` and is the right tool for loadout work. It scans the installed packages
to build a searchable catalog of items, plugs and abilities, cached at
`%LOCALAPPDATA%\Sundial\catalog\d2sk-86657.json`, and offers browse/search per equipment slot and
socket. It also has an "unsafe" mode showing every plug matching a socket type, and a
"really unsafe" mode allowing any discovered plug in any socket.

It targets Sunrise 0.1 / 0.2 / 0.2.1 against Destiny 2 Shadowkeep build `86657.20.08.23` — which is
also the build this fork runs on.

Sundial and the settings above are complementary, not redundant. Sundial edits the file *before*
launch and so never touches the runtime staging path; the `state.cosmetics` switches govern plugs
applied *in game* through the character screen. Sundial cannot help with the latter, and the
switches cannot help with the former.

Changes made in Sundial need a full exit to desktop and relaunch to take effect. It backs up every
save to `%LOCALAPPDATA%\Sundial\backups`.

## Editing a loadout by hand

In `settings.json`, each character's `equipment` names its slots, and each slot carries a `plugs`
array of definition hashes — one entry per socket lane, `null` for an empty lane.

```json
"helmet": {
  "instance_soid": "0x4000000000000004",
  "definition_hash": "0xF2994B80",
  "level": 106,
  "quantity": 1,
  "plugs": ["0xEC2137DA", "0x301CB225", null, "0x54CCDFF2"]
}
```

Lane order matches the item's own socket order. Restart the game to apply; no rebuild needed.

## Custom art

Importing your own mesh — a sword model, a helmet, a whole avatar — is a **large project, but not
a blocked one**. The obstacles are not where they first appear.

### What is already solved

**Integrity checking is defeated, in this codebase, today.**
`src/client/hooks/package_trust/package_trust_bypass.cpp` does three things to the running client:

1. Detours the package-header validator and forces its `rsaTrusted` argument to 1, so RSA
   signature verification cannot refuse a header.
2. Patches the extended-header authentication failure (`mov eax, -89`) to return success.
3. NOPs the cached-data hash gate's conditional jump into an unconditional one, so a failed
   content hash takes the success path.

A tampered package therefore loads. This is the check that would normally end the conversation,
and upstream already removed it — not for modding, but because the offline client needs it.

**Compression is optional, so no Oodle encoder is needed.** `reader/layout.h` defines
`BlockFlags::kCompressed = 0x1` as a *per-block* flag. A repacked package can clear it and store
block bodies raw. `kEncrypted = 0x2` is likewise per-block. Sunrise only ever calls
`OodleLZ_Decompress`, and the game's shipped `oo2core_3_win64.dll` is very likely decode-only —
which turns out not to matter.

**The container format is fully documented in this repo.** `reader/layout.h` gives the header size
(`0x180`), the entry table offset (`0x110`), the block table layout, the fixed decompressed block
size (`0x40000`), and exact record strides with static asserts. A writer is the inverse of code
that already exists here.

**Mesh reading is solved upstream.**
[MontevenDynamicExtractor](https://github.com/MontagueM/MontevenDynamicExtractor) converts Destiny 2
dynamic models to FBX with textures, skeletons and full vertex data. The formats are understood.

### What is actually hard

- **No `.pkg` writer exists publicly.** tiger-pkg, DestinyUnpacker, alkahest and Charm all read
  only. This has to be written. It is bounded work against a documented format, not research into
  an unknown one.
- **The header records its own file size**, and the validator still compares it against the real
  one — see the mismatch logging in `validate_header`. A repacked package has to stay internally
  consistent.
- **Authoring a mesh the renderer accepts** is the real cost: vertex layouts, index buffers,
  skeleton binding and material/shader assignment all have to match what the shaders expect.
  Reading a format tells you its shape but not which of its invariants the consumer relies on.

### Realistic ordering

Roughly increasing difficulty, and worth attacking in this order:

| target | why this order |
|---|---|
| Texture on a base item | no geometry, no skeleton — proves the repack pipeline end to end |
| Rigid prop (sword, ship) | one mesh, one bone attachment, no deformation |
| Helmet or mask | close to rigid: rides the head with little deformation |
| Chest, arms, legs | real skinning against the existing skeleton |
| Full player model | everything above, plus animation compatibility |

Start with a texture. If a repacked package loads and a recoloured texture shows up in game, every
hard question after that is about mesh authoring rather than about whether the approach works.

A mask or helmet is the right first *geometry* target for the same reason a sword is: it barely
deforms. Chest and leg armour is where skinning starts to bite.

### How a custom model would be delivered

You would not add a new item. You would replace the art an existing item points at. There are two
candidate items to point at, and the choice matters more than it looks.

**Replace the base item's art.** The render path sets `art[kGearArtSlot]` and
`art[kArtArrangementSlot]` straight from the base item's detail, before any plug is folded in. That
path demonstrably works — it is what draws your gear today. Repacking the asset a given helmet
references therefore depends on nothing unresolved.

**Replace an ornament's art.** More flexible, since equipping toggles it and the original item is
left intact. It rides `apply_plug_art`, which is **verified working** on 0.3.0.0 — see above.

**Prefer the ornament.** An earlier version of this document recommended the base item, on the
grounds that the ornament path was unproven. It is now proven, and the ornament is strictly better:
base replacement is global, so every instance of that item becomes the new model, including on NPCs
if the asset is shared. An ornament is opt-in and reversible by unequipping.

If you do go the base-item route, prefer **exotic armour with a unique model** — distinctive,
unlikely to be shared with generic gear, and obvious in game, so success or failure is unambiguous.

### The alternative path

Intercepting draws at the D3D11 level is **how unique albedo ships**. Geometry still comes from
the package inject (skinning and the tangent frame have to match the armor VS). The dye pixel
shader luma-gates any unique RGB in the bound 512, so `custom_albedo` replaces that PS at
`DrawIndexed` for the five custom index ranges only. Character select is confirmed. In-world
lighting is the leftover — see `CUSTOM_CHARACTER.md`. Do not start from swap-chain `Present`.
