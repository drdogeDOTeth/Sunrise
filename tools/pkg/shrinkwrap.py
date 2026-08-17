"""
Reshapes Destiny's own terrain meshes into the silhouette of an imported model.

## Why this works without decoding the index buffer

Replacing a vertex buffer's positions with an imported mesh's vertices, in the imported mesh's own
order, produces nonsense: the game still draws triangles using *its* index buffer, so the points get
wired together in the wrong topology. Decoding index buffers would fix that, and they have not been
identified — the two candidate classes in a destination package carry odd byte counts, which a
`uint16` index array cannot.

A continuous deformation sidesteps the problem entirely. Move every terrain vertex to the nearest
point on the imported surface and neighbouring vertices stay neighbours, so every triangle stays
small and correctly wound. The game's index buffer remains exactly as valid as it was. What renders
is Destiny's mesh shrink-wrapped onto the imported shape.

The cost is that silhouette fidelity is bounded by how finely the terrain chunk was tessellated, and
that concavities fill in — a wrap cannot reach inside a shape its topology does not enclose.

## What is preserved

Only bytes 0-5 of each 16-byte vertex, the packed `int16` position, are rewritten. Bytes 6-15 carry
normals, tangents and an index whose layout is not decoded, and are copied verbatim. See
`reshape.py` for why that rule exists.

Undo with `python gametest.py --undo`.

Usage:
    python shrinkwrap.py --list
    python shrinkwrap.py [--mesh GasMask] [--axes xzy] [--flip z] [--min-verts 400]
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np

from glb import load_mesh
from patch import write_patch_package_multi
from tigerpkg import Package

PACKAGES = Path(r"C:\Sunrise\packages")
DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
RECEIPT = Path(__file__).with_name("gametest_receipt.json")
MODEL = Path(r"C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb")
VERTEX_BUFFER_CLASS = 0x80807173
HEADER_SIZE = 48
STRIDE = 16
# Enough surface samples that the nearest-point search resolves features finer than the terrain
# tessellation, so the wrap is limited by the target mesh rather than by this number.
SURFACE_SAMPLES = 120_000
CACHE_GLOBS = (
    (Path(r"C:\Sunrise"), "cache_phr_*.dat"),
    (Path(r"C:\Sunrise\bin\x64\Sunrise\cache"), "content_manifest.bin"),
)


def option(name: str, fallback):
    return type(fallback)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


def sample_surface(points: np.ndarray, triangles: np.ndarray, count: int) -> np.ndarray:
    """
    Scatters `count` points over a triangle mesh, in proportion to triangle area.

    Area weighting matters: sampling uniformly per triangle would crowd points onto the many tiny
    triangles around detailed features and leave large flat regions almost unrepresented, which
    shows up as the wrap tearing away from broad surfaces.
    """
    a, b, c = points[triangles[:, 0]], points[triangles[:, 1]], points[triangles[:, 2]]
    areas = np.linalg.norm(np.cross(b - a, c - a), axis=1) * 0.5
    total = areas.sum()
    if total <= 0:
        raise SystemExit("model has zero surface area")

    rng = np.random.default_rng(0xD2)  # fixed, so a rerun reproduces the same mesh exactly
    picked = rng.choice(len(triangles), size=count, p=areas / total)
    u = rng.random((count, 1))
    v = rng.random((count, 1))
    over = (u + v) > 1.0
    u[over] = 1.0 - u[over]
    v[over] = 1.0 - v[over]
    a, b, c = a[picked], b[picked], c[picked]
    return (a + u * (b - a) + v * (c - a)).astype(np.float32)


def nearest(queries: np.ndarray, cloud: np.ndarray, batch: int = 256) -> np.ndarray:
    """@return For each query point, the closest point in `cloud`. Brute force, batched."""
    out = np.empty_like(queries)
    squared = (cloud * cloud).sum(1)
    for at in range(0, len(queries), batch):
        chunk = queries[at:at + batch]
        # |q-c|^2 expanded, dropping the constant |q|^2 term since only the argmin matters.
        distance = squared[None, :] - 2.0 * (chunk @ cloud.T)
        out[at:at + batch] = cloud[np.argmin(distance, axis=1)]
    return out


mesh_name = option("--mesh", "GasMask")
axes = option("--axes", "xyz")
flip = option("--flip", "")
min_verts = option("--min-verts", 400)
wanted = option("--package", "mercury_destination_03a7")

if sorted(axes) != ["x", "y", "z"]:
    raise SystemExit("--axes must be a permutation of xyz, e.g. xzy")


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


matches = [p for p in PACKAGES.glob("*.pkg") if wanted.lower() in p.name.lower()]
if not matches:
    raise SystemExit(f"no package matching {wanted!r}")
source = max(matches, key=patch_index)
pkg = Package(source)

points, triangles = load_mesh(MODEL, mesh_name)
order = ["xyz".index(axis) for axis in axes]
points = points[:, order]
for axis in flip:
    points[:, "xyz".index(axis)] *= -1.0
cloud = sample_surface(points, triangles, SURFACE_SAMPLES)
# Normalise into a unit cube once, so each terrain chunk only has to scale it to its own extent.
low, high = cloud.min(0), cloud.max(0)
cloud = (cloud - low) / max((high - low).max(), 1e-9)
print(f"{MODEL.name}:{mesh_name} - {len(points):,} verts, {len(triangles):,} triangles, "
      f"{len(cloud):,} surface samples")

targets = []
for entry in pkg.entries:
    if entry.reference != VERTEX_BUFFER_CLASS or entry.size <= HEADER_SIZE:
        continue
    if entry.start_block >= pkg.header.block_count:
        continue
    if not pkg.blocks[entry.start_block].encrypted:
        raise SystemExit(
            f"entry {entry.index} resolves to a plain block, so {source.name} is one of ours.\n"
            "Run 'python gametest.py --undo' first: a patch has to be based on a shipped file, and\n"
            "a dump taken while ours was installed would already be modified. Re-dump only if the\n"
            "files under the dump directory are newer than the patch you are removing."
        )
    dumped = DUMP / f"tag_{pkg.tag_for(entry.index):08X}.bin"
    if dumped.is_file() and (entry.size - HEADER_SIZE) // STRIDE >= min_verts:
        targets.append((entry, dumped))

if not targets:
    raise SystemExit(f"no dumped buffers with >= {min_verts} vertices; run request_buffers.py first")

if "--list" in sys.argv:
    print(f"\n{len(targets)} buffers would be wrapped:")
    for entry, _ in sorted(targets, key=lambda t: -t[0].size)[:15]:
        print(f"  entry {entry.index:5d}  {(entry.size - HEADER_SIZE) // STRIDE:7,} vertices")
    raise SystemExit(0)

if RECEIPT.is_file():
    raise SystemExit(f"{RECEIPT.name} exists; run 'python gametest.py --undo' first")

replacements: dict[int, bytes] = {}
moved = 0
for entry, dumped in targets:
    data = bytearray(dumped.read_bytes())
    if len(data) != entry.size:
        raise SystemExit(f"entry {entry.index}: dumped {len(data):,}, declared {entry.size:,}")
    size, count, stride = struct.unpack_from("<QQQ", data, 0)
    if size != len(data) or stride != STRIDE or HEADER_SIZE + count * stride != len(data):
        raise SystemExit(f"entry {entry.index}: header does not describe a {STRIDE}-byte buffer")

    raw = np.frombuffer(bytes(data), np.int16, count=count * 8, offset=HEADER_SIZE).reshape(count, 8)
    original = raw[:, :3].astype(np.float32)

    # Each chunk is wrapped into its own extent, so the shape lands where that terrain sat and at
    # its scale. Fitting every chunk to one global box would pile them on top of each other.
    low, high = original.min(0), original.max(0)
    span = max((high - low).max(), 1.0)
    placed = cloud * span + low + (high - low - span * (cloud.max(0) - cloud.min(0))) * 0.5
    wrapped = nearest(original, placed.astype(np.float32))

    clipped = np.clip(np.rint(wrapped), -32768, 32767).astype(np.int16)
    for index in range(count):
        struct.pack_into("<3h", data, HEADER_SIZE + index * STRIDE, *clipped[index])
    replacements[entry.index] = bytes(data)
    moved += count

print(f"{source.name}: patch {pkg.header.patch_id}")
print(f"  {len(replacements)} buffers wrapped, {moved:,} vertices moved onto the model")

plans = write_patch_package_multi(source, replacements)
written = source.with_name(f"{pkg.stem}_{pkg.header.patch_id + 1}.pkg")
blocks = sum(len(p.spans) for p in plans.values())
print(f"  {written.name}: {written.stat().st_size:,} bytes, {blocks} block records")

check = Package(written)
introduced = set(check.check()) - set(pkg.check())
if introduced:
    written.unlink()
    for complaint in sorted(introduced)[:3]:
        print(f"  {complaint}")
    raise SystemExit("structural problems; removed, nothing installed")
for index, expected in replacements.items():
    if check.read_entry(index) != expected:
        written.unlink()
        raise SystemExit(f"entry {index} did not read back; removed")
print("  verified byte-exact")

spare = Path(__file__).parent / "cache_backup"
spare.mkdir(exist_ok=True)
cleared = 0
for directory, pattern in CACHE_GLOBS:
    for cache in directory.glob(pattern):
        cache.replace(spare / cache.name)
        cleared += 1
print(f"  cleared {cleared} validation cache file(s)")

RECEIPT.write_text(json.dumps({"written": [written.name], "mode": "shrinkwrap"}, indent=2))
print(f"\nInstalled {written.name}. Travel to Mercury.")
print("Undo with: python gametest.py --undo")
