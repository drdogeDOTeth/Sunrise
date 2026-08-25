"""Write a patch layer that changes nothing, to separate a bad edit from a bad package.

**The question this answers.** Four layers of ours have each broken a world load while their
contents checked out clean: `0699_7` (Tower), `0699_6` (Moon), `020c_8` and `globals_06dc_7`
(Titan). Every one was well-formed, and `blank_model` is size-preserving, so "the record was
resized" does not explain any of them. That leaves two possibilities which look identical from the
outside:

  * the *edit* is wrong - the game cannot use what we put in the entry, or
  * the *package* cannot carry a layer of ours at all, whatever the bytes say.

They call for opposite fixes. If the edit is at fault a narrower one may work - blanking only the
hand and head parts of the shared body mesh instead of every part of it. If the package is at
fault no package-side edit will ever work and the change has to move to a runtime hook.

So this writes the entry back **exactly as it was dumped**. The layer is real - same package, same
entry, same write path, a genuine patch file the registrar must resolve - but the bytes the game
reads through it are identical to stock. A world that still fails is failing on the existence of
the layer. A world that now loads is telling us the content was the problem.

Usage:
    python noop_patch_probe.py --dry-run
    python noop_patch_probe.py 0x815B9521
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from inject_mesh import entry_index_of, package_of, write_all
from parse_models import Model

DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
# The replicated player-body mesh, head through hands, in globals_06dc. Blanking every part of it
# is what breaks Titan; this writes it back untouched.
DEFAULT_TAG = 0x815B9521


def load_model(tag: int) -> Model:
    path = DUMP / f"tag_{tag:08X}.bin"
    if not path.is_file():
        raise SystemExit(f"0x{tag:08X} is not dumped")
    data = path.read_bytes()
    (declared,) = struct.unpack_from("<q", data, 0)
    if declared != len(data):
        raise SystemExit(f"0x{tag:08X} dump is not an entity model")
    model = Model(tag, data)
    if not model.meshes:
        raise SystemExit(f"0x{tag:08X} has no meshes")
    return model


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    tag = int(args[0], 16) if args else DEFAULT_TAG
    model = load_model(tag)
    drawn = sum(1 for mesh in model.meshes for part in mesh.parts if part[3])
    package = package_of(tag)
    print(f"no-op 0x{tag:08X}  {len(model.meshes)} meshes  {drawn} drawn parts  "
          f"{len(model.data):,} B  -> {package.name}")
    # The whole point: the payload is the dump, unmodified. Nothing is blanked, nothing resized.
    payload = bytes(model.data)
    assert payload == model.data, "payload must be byte-identical to the dump"
    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return
    write_all({package: {entry_index_of(tag): payload}}, "")


if __name__ == "__main__":
    main()
