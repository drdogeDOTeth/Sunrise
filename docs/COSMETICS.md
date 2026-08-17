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

Genuinely new geometry or hand-authored textures are **not reachable in this codebase**, and this
is not a matter of effort.

Sunrise reads Tiger packages and never writes them. `src/middleware/content/packages/reader/`
contains readers only; there is no writer anywhere in the tree. Every tool in the wider Destiny 2
ecosystem that upstream credits — alkahest, tiger-pkg, tiger-parse, Charm, D2TextureRipper — also
only reads. Nobody has published working `.pkg` authoring.

So "custom player model" in the Skyrim sense would mean first solving Tiger archive repacking,
which is a research project in its own right and independent of Sunrise. What *is* reachable is
recombining the art the game already ships, which is what everything above does.
