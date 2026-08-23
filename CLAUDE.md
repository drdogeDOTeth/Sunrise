# Sunrise — fork notes

Fork of [stanuwu/Sunrise](https://github.com/stanuwu/Sunrise), a Destiny 2 offline exploration mod.
This fork adds custom-appearance work: any ornament or shader on any item, and a custom GLB
character on the Warlock.

**Live status:** [`HANDOFF.md`](HANDOFF.md) and [`docs/CUSTOM_CHARACTER.md`](docs/CUSTOM_CHARACTER.md).
Character select is the Blender look. Hook **v18** (GLB roughness + open AO). Snapshot
`20260822-172401`. Do not `--undo` the mesh. Do not restore snapshot `150046`.

Upstream is on the `upstream` remote. Our work lives on the `cosmetics` branch.

## Building

```powershell
.\build.ps1              # Release
.\build.ps1 -Configuration Debug
```

Output is `build\x64\<Config>\steam_api64.dll`. The mod ships as a proxy `steam_api64.dll` that
replaces the game's own.

To install it:

```powershell
.\install.ps1                    # defaults to C:\Sunrise
.\install.ps1 -GamePath D:\...
.\install.ps1 -Restore           # put the game's original DLL back
```

The first run saves the game's own DLL as `steam_api64.dll.original` and never overwrites that
backup afterwards, so repeated installs cannot lose it.

Two gotchas the script or this file exist to absorb:

- **`Sunrise.vcxproj` pins `PlatformToolset v145`**, which ships with Visual Studio 2026. A VS 2022
  install tops out at v143 (MSVC 14.4x). `build.ps1` detects this and overrides to v143 only when
  v145 is absent. Both build clean; the Windows SDK the project wants (10.0.26100.0) is on both.
- **New source files must be added to `Sunrise.vcxproj` by hand.** `CMakeLists.txt` globs sources,
  the VS project enumerates them. Adding a `.cpp` without touching the vcxproj gives an
  `LNK2001 unresolved external` at link time and nothing earlier. Add `.h` files to `ClInclude`
  and `.cpp` files to `ClCompile`.

Warnings are errors (`TreatWarningAsError`), so anything sloppy fails the build outright.

## Install layout

The game is installed separately, via Steam depots — see the upstream wiki. On this machine it
lives at `C:\Sunrise`.

| path | what |
|---|---|
| `C:\Sunrise\destiny2.exe` | game entry point |
| `C:\Sunrise\bin\x64\steam_api64.dll` | **our build goes here** |
| `C:\Sunrise\bin\x64\Sunrise\settings.json` | live config, seeded from the bundled default on first run |
| `C:\Sunrise\bin\x64\Sunrise\custom_*.png` | GLB albedos + `custom_*_mr.png` roughness |
| `C:\Sunrise\packages\*.pkg` | Tiger content packages (read-only to Sunrise) |

`settings.json` is read at startup and needs no rebuild to change. It is seeded from
`Sunrise/resources/default_settings.json`, which is compiled in as a resource — editing that file
only affects a *fresh* install, not an existing `settings.json`.

## Architecture, briefly

Sunrise runs a local server in-process and speaks the game's own protocol to it. Nothing touches
Bungie's servers.

- `src/client/` — hooks into the running game (`hooks/`, `patterns/`, `targets/`)
- `src/server/` — the in-process server (`bap/` is the main protocol surface)
- `src/state/` — authored account state: characters, inventory, sockets, unlocks
- `src/state/build_data/` — tables extracted from the installed packages at runtime
- `src/middleware/content/packages/` — Tiger `.pkg` **readers**. There is no writer in the mod; the
  one this fork added lives offline in `tools/pkg/` — see `docs/PACKAGES.md`.
- `src/client/hooks/package_trace/`, `src/client/hooks/model_trace/` — dormant F8-gated capture of
  package reads and live model instances. `docs/TOOLS.md` covers the whole toolchain and the
  capture procedure.
- `src/middleware/datagen/` — encodes state into the records the game reads
- `src/core/` — settings, logging, filesystem, the ImGui overlay

## Where appearance is decided

`src/middleware/datagen/character_record/appearance/character_appearance_render.cpp` is the whole
story. For each equipped item it takes the base art, then folds every socket plug over it:
ornaments replace `gearArtIndex` and append an overlay arrangement, shaders contribute material
pairs across three ordered stages. This already worked upstream — nothing needed adding to make
ornaments render.

What gates appearance is not rendering but *permission*. See `docs/COSMETICS.md`.

## Where this fork is going

Custom GLB on the playable Warlock. Geometry, skinning, unique albedos, and in-world lighting
that is no longer a silhouette are **in**. See `docs/CUSTOM_CHARACTER.md` (the durable inventory).

Established:

- The fork builds and loads (0.3.2.0 + upstream/master, Shadowkeep `86657.20.08.23`).
- Ornament model replacement works.
- Tiger `.pkg` writer exists (`tools/pkg/`) and written layers load in game.
- Custom mesh is injected on the Scatterhorn chest draw (`037c_28` / `037d_26` / `0698_24`).
- Unique RGB cannot ride the dye tile. Draw hook `custom_albedo` (**v18**) binds five GLB albedos
  plus GLB roughness. G-buffer gate + full PS restore. Do not sample Destiny `t2`. Do not encode
  `TEXCOORD2`.

Remaining:

1. Stock gloves and the bald race head still overlay the custom mesh.
2. Necklace UVs are collapsed on the mesh.

## Contributing back

Upstream's rules, if we ever open a PR there: no game data committed (everything extracted at
runtime), one feature per PR, follow clang-format/clang-tidy, document what changed and why.
Prefer routing through proper requests over client patches.
