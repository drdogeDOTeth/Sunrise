"""
Turns a dumped entity model plus its buffers into a viewable OBJ.

## Vertex layouts

Positions are **packed signed 16-bit**, dequantised as `value / 32767 * scale + translation`, where
scale and translation come from the model header. The stride says what else shares the vertex:

| stride | 0x00 | 0x08 |
|---|---|---|
| 8 | position x,y,z,w as int16 | — |
| 12 | position x,y,z,w as int16 | 2 bone indices + 2 weights, or a texcoord |
| 16 | position x,y,z,w as int16 | 4 weight values then 4 bone indices |

**Stride 16 is a skinned vertex with its skinning inline**, which is the case that matters for
armour. It also fixes the write plan precisely: replacing a mesh means rewriting **bytes 0-5 of
each vertex** and nothing else, so `w`, the bone indices and the weights all survive untouched and
the mesh stays rigged exactly as it was.

Indices are 16-bit here (`Is32Bit` is 0 on every helmet candidate). A part declares its own index
range and primitive type - 3 for triangles, 5 for strips.

## LODs

`ELodCategory` 0 is the main geometry; 4, 7 and 9 are progressively cheaper copies, and 1, 2, 3 and
8 are attachments like stickers and grips. Exporting everything at once produces a model with its
own low-poly duplicate buried inside it, so only LOD 0 is emitted unless asked otherwise.

Usage:
    python extract_mesh.py 0x80BA7474
    python extract_mesh.py 0x80BA7474 --out helmet.obj --all-lods
    python extract_mesh.py --helmets --out-dir helmets
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import parse_models as models_module
from parse_models import DUMP, Model, models, option

GEOMETRY = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
PACKED_MAX = 32767.0
TRIANGLES = 3
TRIANGLE_STRIP = 5
MAIN_LOD = 0


def dumped(tag: int) -> bytes | None:
    path = GEOMETRY / f"tag_{tag:08X}.bin"
    return path.read_bytes() if path.is_file() else None


def vertex_stride(header_tag: int) -> int:
    """@return Stride from the 12-byte vertex buffer header, or 0 when it was not dumped."""
    data = dumped(header_tag)
    if data is None or len(data) < 12:
        return 0
    return struct.unpack_from("<h", data, 4)[0]


def read_positions(buffer: bytes, stride: int, scale: float,
                   translation: tuple[float, float, float]) -> list[tuple[float, float, float]]:
    """Dequantises packed positions into model space."""
    out = []
    for at in range(0, len(buffer) - stride + 1, stride):
        x, y, z = struct.unpack_from("<3h", buffer, at)
        out.append((x / PACKED_MAX * scale + translation[0],
                    y / PACKED_MAX * scale + translation[1],
                    z / PACKED_MAX * scale + translation[2]))
    return out


def read_triangles(buffer: bytes, offset: int, count: int, primitive: int) -> list[tuple[int, int, int]]:
    """@return Triangles for one part's index range, unrolling strips as it goes."""
    total = len(buffer) // 2
    indices = [struct.unpack_from("<H", buffer, at * 2)[0]
               for at in range(offset, min(offset + count, total))]
    faces = []
    if primitive == TRIANGLES:
        for at in range(0, len(indices) - 2, 3):
            faces.append((indices[at], indices[at + 1], indices[at + 2]))
    elif primitive == TRIANGLE_STRIP:
        for at in range(len(indices) - 2):
            a, b, c = indices[at], indices[at + 1], indices[at + 2]
            # A strip repeats an index to restart; those degenerate triangles are not geometry.
            if a == b or b == c or a == c:
                continue
            faces.append((a, b, c) if at % 2 == 0 else (a, c, b))
    return faces


def extract(model: Model, all_lods: bool) -> tuple[list, list, list[str]]:
    """@return `(vertices, faces, notes)` for one model, in model space."""
    scale = model.scale[0] if model.scale else 1.0
    translation = model.translation[:3] if model.translation else (0.0, 0.0, 0.0)
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    notes: list[str] = []
    for index, mesh in enumerate(model.meshes):
        stride = vertex_stride(mesh.positions)
        position_bytes = dumped(mesh.position_buffer)
        index_bytes = dumped(mesh.index_buffer)
        if not stride or position_bytes is None or index_bytes is None:
            notes.append(f"mesh {index}: not dumped (stride {stride})")
            continue
        base = len(vertices)
        points = read_positions(position_bytes, stride, scale, translation)
        vertices.extend(points)
        kept = 0
        for _material, primitive, offset, count, lod in mesh.parts:
            if not all_lods and lod != MAIN_LOD:
                continue
            for a, b, c in read_triangles(index_bytes, offset, count, primitive):
                if max(a, b, c) < len(points):
                    faces.append((base + a, base + b, base + c))
                    kept += 1
        notes.append(f"mesh {index}: stride {stride}, {len(points):,} verts, {kept:,} tris")
    return vertices, faces, notes


def write_obj(path: Path, vertices, faces, name: str) -> None:
    lines = [f"# {name}", f"o {name}"]
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in vertices]
    lines += [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in faces]
    path.write_text("\n".join(lines), encoding="utf-8")


all_lods = "--all-lods" in sys.argv
wanted = [argument for argument in sys.argv[1:] if argument.startswith("0x")]

if "--helmets" in sys.argv:
    chosen = sorted((model for model in models if model.helmet_like),
                    key=lambda model: -model.vertices)[:option("--limit", 12)]
elif wanted:
    tags = {int(argument, 0) for argument in wanted}
    chosen = [model for model in models if model.tag in tags]
    if not chosen:
        raise SystemExit(f"none of {wanted} are among the {len(models)} dumped models")
else:
    raise SystemExit(__doc__)

out_dir = Path(option("--out-dir", "."))
out_dir.mkdir(parents=True, exist_ok=True)
single_out = option("--out", "")

for model in chosen:
    vertices, faces, notes = extract(model, all_lods)
    if not faces:
        print(f"0x{model.tag:08X}  no geometry: " + "; ".join(notes))
        continue
    path = Path(single_out) if single_out else out_dir / f"model_{model.tag:08X}.obj"
    write_obj(path, vertices, faces, f"model_{model.tag:08X}")
    print(f"0x{model.tag:08X}  {len(vertices):,} verts, {len(faces):,} tris  "
          f"span {model.span:.3f} height {model.height:.3f}  -> {path}")
    for note in notes:
        print(f"    {note}")
    if single_out:
        break
