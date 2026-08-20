"""
Scales the Guardian body's vertices in place, as a decisive test of whether buffer writes render.

## What this isolates, and why it is needed

Blanking the three model entries made the in-world body vanish, so **model-entry writes are proven**
to reach the renderer. Every injection since has *also* rewritten vertex and index buffers, and
produced no visible change - but "no visible change" has been impossible to interpret, because the
body renders as a near-invisible dissolve shell whose silhouette is hard to judge by eye.

Two very different explanations fit the evidence equally well:

1. The buffer writes never reach the renderer, and the body being drawn comes from bytes we are not
   touching.
2. The buffer writes land fine, and something about the topology swap - part table, index layout,
   material - makes the result invisible.

Scaling separates them with no ambiguity. It writes **only** the position buffers: same vertex
count, same byte length, same index buffer, same part table, only bytes 0-5 of each vertex changed.
Nothing about topology, materials or LODs is involved.

- **Body visibly shrinks** -> buffer writes render. The bug is in the topology swap.
- **Nothing changes** -> buffer writes do not render, and the visible body is not this geometry.
  Every mesh edit so far has been aimed at the wrong bytes, and targeting has to be revisited.

Shrinking rather than enlarging is deliberate: the body already fills its quantisation box
(positions pack as `(p - translation) / scale * 32767`), so scaling up would clip against the
model's own `scale` field and change the model header too - which would stop this being a pure
buffer test.

Usage:
    python probe_scale_body.py --dry-run
    python probe_scale_body.py --factor 0.5
    python inject_mesh.py --undo
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

from extract_mesh import vertex_stride
from inject_mesh import entry_index_of, package_of, write_all
from parse_models import Model, TAG_MAX, TAG_MIN
from wrap_player_body import original_buffer

DUMPS = [Path(r"C:\Sunrise\bin\x64\Sunrise\dump"),
         Path(r"C:\Sunrise\bin\x64\Sunrise\dump_models")]
PACKED_MAX = 32767.0
# All three entries reference two geometry sets; scaling every mesh of each makes the whole body
# change size rather than one limb.
TARGETS = [0x80B9F855, 0x80C23B5D, 0x80FA2308]
DEFAULT_FACTOR = 0.5


def option(name: str, fallback):
    return type(fallback)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


def load_model(tag: int) -> Model:
    for folder in DUMPS:
        path = folder / f"tag_{tag:08X}.bin"
        if path.is_file():
            return Model(tag, path.read_bytes())
    raise SystemExit(f"0x{tag:08X} header not dumped")


def main() -> None:
    factor = option("--factor", DEFAULT_FACTOR)
    if not 0.05 <= factor <= 1.0:
        raise SystemExit("--factor must be between 0.05 and 1.0; scaling up would clip")

    by_package: dict[Path, dict[int, bytes]] = {}
    written: set[int] = set()
    for tag in TARGETS:
        model = load_model(tag)
        scale = model.scale[0]
        print(f"\n0x{tag:08X}: scale {scale:.6f}")
        for index, mesh in enumerate(model.meshes):
            if not (TAG_MIN <= mesh.position_buffer <= TAG_MAX):
                continue
            if mesh.position_buffer in written:
                print(f"  mesh {index}: buffer 0x{mesh.position_buffer:08X} already scaled, shared")
                continue
            stride = vertex_stride(mesh.positions)
            raw = original_buffer(mesh.position_buffer)
            if not stride or raw is None:
                print(f"  mesh {index}: not dumped, skipping")
                continue
            count = len(raw) // stride
            out = bytearray(raw)
            for vertex in range(count):
                at = vertex * stride
                x, y, z = struct.unpack_from("<3h", out, at)
                # Packed values are already relative to the model's translation, so scaling them
                # directly scales about the model's own origin - no dequantise round trip needed.
                struct.pack_into("<3h", out, at,
                                 int(np.clip(round(x * factor), -32768, 32767)),
                                 int(np.clip(round(y * factor), -32768, 32767)),
                                 int(np.clip(round(z * factor), -32768, 32767)))
            if len(out) != len(raw):
                raise SystemExit("scaling changed buffer length")
            package = package_of(mesh.position_buffer)
            by_package.setdefault(package, {})[entry_index_of(mesh.position_buffer)] = bytes(out)
            written.add(mesh.position_buffer)
            print(f"  mesh {index}: {count:,} verts stride {stride} scaled x{factor:g} "
                  f"-> {package.name}")

    total = sum(len(v) for v in by_package.values())
    print(f"\n{total} position buffers across {len(by_package)} packages (no other bytes touched)")
    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return
    write_all(by_package, "")
    print(f"\nLaunch and look in world.")
    print(f"  body visibly smaller -> buffer writes DO render; the topology swap is the bug")
    print(f"  no change            -> buffer writes do NOT render; the visible body is other bytes")
    print("Undo: python inject_mesh.py --undo")


if __name__ == "__main__":
    main()
