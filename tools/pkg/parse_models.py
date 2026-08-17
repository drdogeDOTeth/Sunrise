"""
Parses dumped `SEntityModel` entries into meshes, and ranks them by how helmet-shaped they are.

## Layout

Shadowkeep `SEntityModel` (class `0x808073A5`, 0xA0):

    0x00  int64   file size
    0x10  array   meshes            count at 0x10, self-relative offset at 0x18
    0x50  float4  model scale
    0x60  float4  model translation
    0x70  float2  texcoord scale
    0x78  float2  texcoord translation

A **DynamicArray** is two int64s: the count, then a self-relative offset. The data sits at
`offset_field_position + value + 0x10`. That +0x10 is the array's own header, and getting it wrong
lands you in the middle of the previous record rather than failing loudly - so every offset here is
bounds-checked before use.

One mesh record is 0x88 bytes:

    0x00  tag     positions vertex buffer header
    0x04  tag     texcoord/normal vertex buffer header
    0x08  tag     old-weights vertex buffer header
    0x0C  ----    always 0xFFFFFFFF
    0x10  tag     index buffer header
    0x18  array   parts             count at 0x18, self-relative offset at 0x20
    0x28  short   x48 stage part offsets

`0x28 + 48*2 == 0x88`, which is exactly the size Charm declares - the arithmetic closing is what
confirms the record stride rather than a guess at it.

One part is 0x20 bytes: material tag at 0x00, primitive type at 0x06, index offset at 0x08, index
count at 0x0C, and the LOD level at 0x1B.

## Finding a helmet without names

Item names live behind investment tables Charm does not decode below Witch Queen, so the item ->
model link is not available. Geometry answers it instead. `ModelScale` and `ModelTranslation`
dequantise the packed positions, so together they *are* the model's bounding box: the box spans
`translation` to `translation + scale`. A helmet is a small box sitting at head height, which is a
much sharper filter than vertex count alone.

Buffer sizes come from the entry tables offline, so vertex counts need no second dump.

Usage:
    python parse_models.py                    # every dumped model
    python parse_models.py --helmets          # only helmet-shaped ones
    python parse_models.py --tag 0x80B363C2   # one model in detail
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from tigerpkg import HEADER_SIZE, TAG_BASE, TAG_ENTRY_BITS, TAG_ENTRY_MASK, Package, PackageError

PACKAGES = Path(r"C:\Sunrise\packages")
DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")

MESH_ARRAY = 0x10
MESH_STRIDE = 0x88
MODEL_SCALE = 0x50
MODEL_TRANSLATION = 0x60
PART_ARRAY = 0x18
PART_STRIDE = 0x20
ARRAY_HEADER = 0x10
EMPTY_TAG = 0xFFFFFFFF

TAG_MIN, TAG_MAX = 0x80800000, 0x80FFFFFF
# The one stride Shadowkeep uses for a skinned position buffer; weights are 8.
POSITION_STRIDE = 16
# A mesh table longer than this is a misparse, not a model.
MAX_MESHES = 64
MAX_PARTS = 256

# A helmet is small and sits at head height. Bounds are deliberately loose - this ranks candidates
# for a human to look at, it does not decide anything on its own.
HELMET_MAX_SPAN = 1.2
HELMET_MIN_HEIGHT = 0.8


def option(name: str, fallback):
    return type(fallback)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


def build_index() -> dict[int, Path]:
    newest: dict[str, Path] = {}
    for path in PACKAGES.glob("*.pkg"):
        stem = path.stem.rsplit("_", 1)[0]
        if stem not in newest or patch_index(path) > patch_index(newest[stem]):
            newest[stem] = path
    index: dict[int, Path] = {}
    for path in newest.values():
        try:
            with open(path, "rb") as fh:
                head = fh.read(HEADER_SIZE)
        except OSError:
            continue
        if len(head) < HEADER_SIZE or struct.unpack_from("<H", head, 0)[0] != 38:
            continue
        index[struct.unpack_from("<H", head, 0x04)[0]] = path
    return index


INDEX = build_index()
_opened: dict[int, Package | None] = {}


def entry_size(tag: int) -> int:
    """@return Size of the entry a tag names, read from the plain entry table, or 0."""
    if not TAG_MIN <= tag <= TAG_MAX:
        return 0
    handle = tag - TAG_BASE
    package_id, entry_index = handle >> TAG_ENTRY_BITS, handle & TAG_ENTRY_MASK
    if package_id not in _opened:
        path = INDEX.get(package_id)
        try:
            _opened[package_id] = Package(path) if path else None
        except PackageError:
            _opened[package_id] = None
    pkg = _opened[package_id]
    if pkg is None or entry_index >= len(pkg.entries):
        return 0
    return pkg.entries[entry_index].size


def array_at(data: bytes, at: int) -> tuple[int, int]:
    """@param at Offset of the count field. @return `(count, data offset)`, or `(0, 0)`."""
    if at + 16 > len(data):
        return 0, 0
    count, relative = struct.unpack_from("<qq", data, at)
    start = at + 8 + relative + ARRAY_HEADER
    if count <= 0 or start <= 0 or start > len(data):
        return 0, 0
    return count, start


def read_tag(data: bytes, at: int) -> int:
    if at + 4 > len(data):
        return EMPTY_TAG
    (value,) = struct.unpack_from("<I", data, at)
    return value


class Mesh:
    def __init__(self, data: bytes, at: int) -> None:
        self.positions = read_tag(data, at + 0x00)
        self.texcoords = read_tag(data, at + 0x04)
        self.weights = read_tag(data, at + 0x08)
        self.indices = read_tag(data, at + 0x10)
        self.position_bytes = entry_size(self.positions)
        self.weight_bytes = entry_size(self.weights)
        self.index_bytes = entry_size(self.indices)
        self.lods: set[int] = set()
        count, start = array_at(data, at + PART_ARRAY)
        self.parts = []
        for index in range(min(count, MAX_PARTS)):
            part = start + index * PART_STRIDE
            if part + PART_STRIDE > len(data):
                break
            material = read_tag(data, part)
            primitive, = struct.unpack_from("<h", data, part + 0x06)
            offset, indices = struct.unpack_from("<II", data, part + 0x08)
            lod = data[part + 0x1B]
            self.lods.add(lod)
            self.parts.append((material, primitive, offset, indices, lod))

    @property
    def vertices(self) -> int:
        """@return Vertex count implied by the position buffer at the one skinned stride."""
        return self.position_bytes // POSITION_STRIDE

    @property
    def skinned(self) -> bool:
        """@return True when a weight buffer pairs with the positions at half the stride."""
        return self.weight_bytes > 0 and self.weight_bytes * 2 == self.position_bytes


class Model:
    def __init__(self, tag: int, data: bytes) -> None:
        self.tag = tag
        self.data = data
        self.scale = struct.unpack_from("<4f", data, MODEL_SCALE) if len(data) >= 0x70 else None
        self.translation = (struct.unpack_from("<4f", data, MODEL_TRANSLATION)
                            if len(data) >= 0x70 else None)
        self.meshes: list[Mesh] = []
        count, start = array_at(data, MESH_ARRAY)
        for index in range(min(count, MAX_MESHES)):
            at = start + index * MESH_STRIDE
            if at + MESH_STRIDE > len(data):
                break
            self.meshes.append(Mesh(data, at))

    @property
    def span(self) -> float:
        """@return Longest side of the model-space bounding box."""
        return max(abs(value) for value in self.scale[:3]) if self.scale else 0.0

    @property
    def height(self) -> float:
        """@return Height of the box centre, which for a bind-pose mesh is where it sits on a body."""
        if not (self.scale and self.translation):
            return 0.0
        return self.translation[2] + self.scale[2] / 2.0

    @property
    def vertices(self) -> int:
        return sum(mesh.vertices for mesh in self.meshes)

    @property
    def helmet_like(self) -> bool:
        return (0 < self.span <= HELMET_MAX_SPAN
                and self.height >= HELMET_MIN_HEIGHT
                and any(mesh.skinned for mesh in self.meshes))


models: list[Model] = []
for path in sorted(DUMP.glob("tag_*.bin")):
    data = path.read_bytes()
    if len(data) < 0xA0:
        continue
    (declared,) = struct.unpack_from("<q", data, 0)
    if declared != len(data):
        continue
    model = Model(int(path.stem[4:], 16), data)
    if model.meshes:
        models.append(model)

if not models:
    raise SystemExit(
        f"no dumped entity models under {DUMP}\n"
        "Build a request first:  python entity_models.py --request investment_0361,investment_01d3")

single = option("--tag", "")
if single:
    wanted = int(single, 0)
    for model in models:
        if model.tag != wanted:
            continue
        print(f"0x{model.tag:08X}  {len(model.data):,} bytes, {len(model.meshes)} meshes")
        print(f"  scale       {model.scale}")
        print(f"  translation {model.translation}")
        for index, mesh in enumerate(model.meshes):
            print(f"  mesh {index}: positions 0x{mesh.positions:08X} {mesh.position_bytes:>9,}B "
                  f"-> {mesh.vertices:,} verts, weights {mesh.weight_bytes:>9,}B, "
                  f"indices {mesh.index_bytes:>9,}B, "
                  f"lods {sorted(mesh.lods)}, {len(mesh.parts)} parts"
                  f"{'  SKINNED' if mesh.skinned else ''}")
        raise SystemExit(0)
    raise SystemExit(f"0x{wanted:08X} is not among the {len(models)} dumped models")

shown = [model for model in models if model.helmet_like] if "--helmets" in sys.argv else models
shown.sort(key=lambda model: -model.vertices)

print(f"{len(models)} models parsed, {sum(len(m.meshes) for m in models)} meshes, "
      f"{sum(1 for m in models if m.helmet_like)} helmet-shaped\n")
print(f"{'tag':>10} {'meshes':>6} {'vertices':>9} {'span':>7} {'height':>7} {'skinned':>7}  shape")
for model in shown[:40]:
    skinned = sum(1 for mesh in model.meshes if mesh.skinned)
    note = "HELMET-SHAPED" if model.helmet_like else ""
    print(f"0x{model.tag:08X} {len(model.meshes):6} {model.vertices:9,} "
          f"{model.span:7.3f} {model.height:7.3f} {skinned:7}  {note}")
