"""
Blanks the replicated globals body identified by live buffer-header capture.

## Why these three and nothing else

Every earlier probe guessed a model and judged the result by whether the *inspect* tunic changed.
Two things were wrong with that. Inspect and in-world are different render paths - the in-world
Guardian can change while the character screen does not - and a guess over 21,240 models cannot be
narrowed by a binary visible/invisible answer.

The 2026-08-19 Tower capture replaced the guess with an identity test. `model_class_trace` now logs
every Tiger handle found in a bounded window of each live render structure. Those windows never name
an `SEntityModel`, but 248 of them carry **vertex buffer headers**, and a buffer header belongs to
exactly one mesh of exactly one model. Intersecting the live header set against the mesh table of
every dumped model therefore names the resident models exactly, with no heuristic in the loop.

Two families came back with 100% of their headers live:

| family | shape | reading |
|---|---|---|
| `sandbox_06d9`, 6 models, 9/9 headers | eleven humanoids, all *different* sizes (107k-192k B) | a crowd of distinct NPCs |
| `0x80B9F855` + `0x80C23B5D` (+ `0x80FA2308`) | **byte-identical** 4 meshes, 287,288 B, -0.009..1.842 m | one asset replicated |

An asset replicated byte-for-byte across three separate `globals` packages is how content that must
be resident in every destination ships. A crowd of distinct humanoids is not. So the triple is the
player body and the sandbox set is the Tower population.

Blanking *only* the triple is what makes this one launch decisive: if the Guardian disappears while
the Tower vendors stay, player and crowd are separated at the same time. Blanking both families
would prove neither.

Usage:
    python probe_player_body.py --dry-run
    python probe_player_body.py
    python inject_mesh.py --undo
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from inject_mesh import blank_model, entry_index_of, package_of, write_all
from parse_models import Model

# Model headers land in `dump` and are archived into `dump_models`; either may hold a given tag.
DUMPS = [Path(r"C:\Sunrise\bin\x64\Sunrise\dump"),
         Path(r"C:\Sunrise\bin\x64\Sunrise\dump_models")]

# The replicated player-body candidate. All three carry the same 4-mesh, 287,288-byte geometry at
# the same bounding box; only the 03d1 copy has a smaller header (11,712 B against 15,616 B).
BODY = [0x80B9F855, 0x80C23B5D, 0x80FA2308]


def load(tag: int) -> Model:
    """@return The parsed dumped model for one tag. @raise SystemExit when it was never dumped."""
    for folder in DUMPS:
        path = folder / f"tag_{tag:08X}.bin"
        if not path.is_file():
            continue
        data = path.read_bytes()
        if len(data) < 0xA0:
            continue
        (declared,) = struct.unpack_from("<q", data, 0)
        if declared != len(data):
            raise SystemExit(f"0x{tag:08X} in {folder.name} is truncated or not a model")
        model = Model(tag, data)
        if not model.meshes:
            raise SystemExit(f"0x{tag:08X} parsed with no meshes")
        return model
    raise SystemExit(f"0x{tag:08X} has not been dumped; request it before probing")


def main() -> None:
    by_package: dict[Path, dict[int, bytes]] = {}
    for tag in BODY:
        model = load(tag)
        half = max(abs(value) for value in model.scale[:3])
        floor = model.translation[2] - half
        top = model.translation[2] + half
        positions = sum(mesh.position_bytes for mesh in model.meshes)
        print(f"0x{tag:08X}  {len(model.meshes)} meshes  {floor:6.3f}..{top:6.3f} m  "
              f"{positions:,} B positions  -> blank")
        by_package.setdefault(package_of(tag), {})[entry_index_of(tag)] = blank_model(model)

    for path, replacements in sorted(by_package.items()):
        print(f"  {path.name}: {len(replacements)} entries")
    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return
    write_all(by_package, "")
    print("\nLaunch and look at your Guardian IN WORLD, not the character screen.")
    print("  body gone, Tower vendors still there -> this is the player body")
    print("  body gone, vendors also gone         -> shared asset, narrow further")
    print("  nothing changes                      -> in-world body is elsewhere")
    print("Undo with: python inject_mesh.py --undo")


if __name__ == "__main__":
    main()
