# Custom appearance in Sunrise

Everything about wearing ornaments, shaders and skins you did not earn — what works, what it costs,
and what is out of reach.

## The three tiers

Not all of "custom cosmetics" is equally reachable.

| tier | what | status |
|---|---|---|
| Existing ornaments and shaders, applied normally | any plug the game already ships | works upstream, no changes needed |
| Any plug on any item, regardless of pool or ownership | cross-class ornaments, unowned shaders | **this fork**, see below |
| Genuinely new art — custom meshes and textures | a model that is not in the game | not reachable, see [Custom art](#custom-art) |

## How the game decides what you look like

An ornament is not a special kind of item. It is a *plug* sitting in a *socket lane* on an equipped
item, exactly like a mod or a perk. Your appearance is a pure function of which plugs sit in which
lanes.

`character_appearance_render.cpp` folds them in a fixed order. The base item contributes its gear
art and art arrangement. Then each plug is layered over: an ornament declares its own
`gearArtIndex` and overlay arrangement and replaces only the fields it declares; a shader declares
material pairs instead, folded across three stages (base, plug-only, late) where only the first six
distinct material keys survive.

The consequence: **making an ornament show up is entirely a question of getting its hash into a
plug lane.** The rendering side was never the obstacle.

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
| Texture replacement | no geometry, no skeleton — proves the repack pipeline end to end |
| Rigid prop (sword, ship) | one mesh, one bone attachment, no deformation |
| Skinned armor (helmet, boots) | must bind correctly to the existing skeleton |
| Full player model | everything above, plus animation compatibility |

Start with a texture. If a repacked package loads and a recoloured texture shows up in game, every
hard question after that is about mesh authoring rather than about whether the approach works.

### How a custom model would be delivered

You would not add a new item. You would **replace the art an existing ornament points at**, then
equip that ornament — which is exactly what the settings above make easy. The plug plumbing is the
delivery mechanism; the repacked package supplies the geometry.

### The alternative path

Intercepting draws at the D3D11 level and substituting geometry avoids packages entirely. Sunrise
hooks only `IDXGISwapChain::Present`, `ResizeBuffers` and `SetFullscreenState` — swap-chain level,
for the ImGui overlay. Nothing hooks the device context or any geometry submission, so this would
be built from scratch, and matching D2's skinning and material state at draw time is likely harder
than repacking. Worth knowing it exists; not worth starting there.
