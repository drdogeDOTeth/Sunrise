"""
Tests dumped entries for the vertex-buffer container, to identify the skinned mesh class.

`0x80807173` stores a 48-byte `{size, count, stride}` header followed by `count` packed vertices.
Static geometry uses a 16-byte stride, which cannot carry bone indices and weights — and that class
appears in 79 world families and zero gear packages. A skinned mesh must therefore be a different
class, but very likely the same container with a wider stride, because the header describes the
stride rather than assuming it.

So every dumped entry is checked for the triple, with no assumption about what the stride should be:

    size == len(data) and 48 + count * stride == len(data)

A class whose members all satisfy that is a buffer class. The stride then says what it holds — 16 is
static geometry; 20, 24, 28 or 32 would fit positions plus bone weights and indices.

Entries are also scored for a float-heavy or index-heavy layout so that non-buffer classes can be
told apart at a glance rather than by guesswork.

Usage:
    python find_mesh_class.py
"""
from __future__ import annotations

import struct
from collections import defaultdict
from pathlib import Path

DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
HEADER_SIZE = 48
# A stride wider than this is not a vertex; it is a record of something else.
MAX_STRIDE = 256


def describes_buffer(data: bytes) -> tuple[bool, int, int]:
    """@return `(is_buffer, count, stride)` for the `{size, count, stride}` header at offset 0."""
    if len(data) < HEADER_SIZE:
        return False, 0, 0
    size, count, stride = struct.unpack_from("<QQQ", data, 0)
    if size != len(data) or not 0 < stride <= MAX_STRIDE or count == 0:
        return False, 0, 0
    return HEADER_SIZE + count * stride == len(data), count, stride


def class_of(path: Path, requests: dict[int, int]) -> int:
    return requests.get(int(path.stem.split("_")[1], 16), 0)


request = DUMP / "request.txt"
requests: dict[int, int] = {}
if request.is_file():
    for line in request.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("tag ") and "class 0x" in line:
            tag = int(line.split()[1], 16)
            requests[tag] = int(line.split("class 0x")[1].split(",")[0], 16)

buffers: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
others: dict[int, list[int]] = defaultdict(list)
for path in sorted(DUMP.glob("tag_*.bin")):
    class_id = class_of(path, requests)
    if not class_id:
        continue
    data = path.read_bytes()
    ok, count, stride = describes_buffer(data)
    if ok:
        buffers[class_id].append((len(data), count, stride))
    else:
        others[class_id].append(len(data))

if not buffers and not others:
    raise SystemExit(f"no dumped entries matching request.txt under {DUMP}; run request_gear.py first")

print(f"{'class':>12} {'samples':>8} {'strides':>18} {'vertices':>12}  verdict")
for class_id in sorted(buffers, key=lambda c: -sum(v[1] for v in buffers[c])):
    rows = buffers[class_id]
    strides = sorted({stride for _, _, stride in rows})
    total = sum(count for _, count, _ in rows)
    note = "STATIC geometry" if strides == [16] else "BUFFER - candidate for skinned geometry"
    print(f"  0x{class_id:08X} {len(rows):8,} {str(strides):>18} {total:12,}  {note}")

print()
print(f"{len(others)} classes did not match the container:")
for class_id in sorted(others, key=lambda c: -sum(others[c]))[:10]:
    sizes = others[class_id]
    print(f"  0x{class_id:08X} {len(sizes):3,} samples, {sum(sizes):,} bytes total")
