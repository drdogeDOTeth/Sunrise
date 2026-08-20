"""
Shrink-wraps the confirmed in-world Guardian body onto the custom character.

## Why this exists next to wrap_body.py

`wrap_body.py` was written against a guessed set of investment bodies and only ever wraps
`meshes[0]`. Neither assumption survives the real target:

- The Guardian body is **4 meshes**, and the one that matters is mesh 2 (15,740 of the 17,955
  vertices). Wrapping only mesh 0 would move 1,698 vertices and leave the body behind.
- It writes each buffer into `package_of(model.tag)`. That is wrong whenever a model references
  buffers in another package, which `0x80FA2308` does - its entry lives in `globals_03d1` but its
  vertex data lives in `globals_0238`. Writing to the model's package would corrupt an unrelated
  entry. This tool resolves the package from the **buffer** tag.

## The fit

Every mesh is wrapped against **one AABB computed over the whole model**, never per-mesh. A per-mesh
fit would stretch the custom character independently into each piece - mesh 3 is 13 vertices, so it
would receive the entire body crushed into a sliver.

Only bytes 0-5 of each stride-16 vertex are rewritten. Bytes 6-15 carry bone indices and weights, so
leaving them untouched is what keeps the result skinned to Destiny's skeleton and animating.
Vertex count is unchanged, so the shipped index buffer, part table and UVs all stay valid.

Usage:
    python wrap_player_body.py --dry-run
    python wrap_player_body.py --obj wrapped.obj     # verify offline before installing
    python wrap_player_body.py
    python inject_mesh.py --undo
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

from extract_mesh import dumped, read_positions, read_triangles, vertex_stride
from glb import load_mesh, mesh_names, read_glb
from inject_mesh import entry_index_of, package_of, to_destiny, write_all
from parse_models import Model, TAG_MAX, TAG_MIN

DUMPS = [Path(r"C:\Sunrise\bin\x64\Sunrise\dump"),
         Path(r"C:\Sunrise\bin\x64\Sunrise\dump_models")]
GLB = Path(r"C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb")
PACKED_MAX = 32767.0
SURFACE_SAMPLES = 120_000

# 0x80C23B5D is deliberately absent: it is a separate entry over 0x80B9F855's buffers, so wrapping
# the buffers once covers both. 0x80FA2308 is a genuinely separate geometry set.
TARGETS = [0x80B9F855, 0x80FA2308]


# Five iterations already reaches 0% degenerate triangles; ten leaves margin without giving up
# meaningful shape (mean movement 0.0803 -> 0.0795 m).
DEFAULT_SMOOTHING = 10
# Buffers copied aside before any patch was installed. A dump taken while a patch is live reads back
# through it, so `dump/` can silently hold *wrapped* bytes; wrapping those again compounds the
# deformation. Prefer the known-pristine copy whenever it exists.
PRISTINE = Path(r"C:\Users\Round\OneDrive\Desktop\Destiny2ProjectSunrise"
                r"\reference\body_buffers_pristine")


def option(name: str, fallback):
    return type(fallback)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


def original_buffer(tag: int) -> bytes | None:
    """@return The shipped bytes for one buffer, preferring the pristine backup over `dump/`."""
    backup = PRISTINE / f"tag_{tag:08X}.bin"
    if backup.is_file():
        return backup.read_bytes()
    return dumped(tag)


def mesh_triangles(mesh, vertex_count: int):
    """@return Every triangle of one mesh across all LODs, for adjacency, or None.

    All LODs are used deliberately: adjacency only needs connectivity, and the cheap LODs share
    vertices with LOD 0, so including them leaves fewer vertices isolated and the smoothing better
    conditioned.
    """
    indices = dumped(mesh.index_buffer)
    if indices is None:
        return None
    collected: list[tuple[int, int, int]] = []
    for _material, primitive, offset, count, _lod in mesh.parts:
        collected += read_triangles(indices, offset, count, primitive)
    if not collected:
        return None
    triangles = np.asarray(collected)
    return triangles[(triangles < vertex_count).all(1)]


def load_model(tag: int) -> Model:
    for folder in DUMPS:
        path = folder / f"tag_{tag:08X}.bin"
        if path.is_file():
            return Model(tag, path.read_bytes())
    raise SystemExit(f"0x{tag:08X} header not dumped")


def load_character() -> tuple[np.ndarray, np.ndarray]:
    """@return All GLB meshes concatenated into one point cloud and triangle list."""
    document, _ = read_glb(GLB)
    chunks, faces, base = [], [], 0
    for name in mesh_names(document):
        points, tris = load_mesh(GLB, name)
        chunks.append(np.asarray(points, dtype=np.float64))
        faces.append(np.asarray(tris) + base)
        base += len(points)
    return np.concatenate(chunks), np.concatenate(faces)


SHOULDER_Y = 0.141
SHOULDER_Z = 1.430
# The swing ramps in across the shoulder rather than switching on, so the deltoid stays smooth
# instead of creasing where the rotation starts.
SWING_RAMP = (0.10, 0.22)
DEFAULT_SWING = 55.0


def swing_arms(points: np.ndarray, degrees: float) -> np.ndarray:
    """Rotates the character's arms down about each shoulder, in Destiny space.

    The GLB is T-posed; the Guardian body is not. Wrapping across that mismatch is not a cosmetic
    problem - it snaps Destiny's arm vertices onto whatever T-posed surface happens to be nearest,
    usually the torso, and the resulting bind pose then shears when the skeleton animates, because
    each vertex still follows the bone it was weighted to.

    Done as geometry rather than through the armature on purpose: the wrap consumes a point cloud,
    and skinning is inherited from the target's untouched bytes 6-15, so no rig needs to survive.
    Measured against the real body's width-versus-height profile, 55 deg roughly halves the
    mismatch (RMS 0.2554 -> 0.1329).
    """
    if degrees == 0.0:
        return points
    out = points.copy()
    side = np.sign(out[:, 1])
    reach = np.abs(out[:, 1])
    t = np.clip((reach - SWING_RAMP[0]) / (SWING_RAMP[1] - SWING_RAMP[0]), 0.0, 1.0)
    t = t * t * (3.0 - 2.0 * t)
    angle = np.radians(degrees) * t
    dy = reach - SHOULDER_Y
    dz = out[:, 2] - SHOULDER_Z
    cos, sin = np.cos(angle), np.sin(angle)
    moved = t > 0
    out[moved, 1] = side[moved] * (SHOULDER_Y + (dy * cos + dz * sin)[moved])
    out[moved, 2] = SHOULDER_Z + (-dy * sin + dz * cos)[moved]
    return out


def sample_surface(points: np.ndarray, triangles: np.ndarray, count: int) -> np.ndarray:
    """Area-weighted point cloud over the character's surface.

    Wrapping against vertices alone biases towards dense regions (the tank top has 49,584 of the
    61,908 vertices), so a Guardian vertex over a sparse area would snap to a distant fold.
    """
    a, b, c = points[triangles[:, 0]], points[triangles[:, 1]], points[triangles[:, 2]]
    areas = np.linalg.norm(np.cross(b - a, c - a), axis=1) * 0.5
    total = areas.sum()
    if total <= 0:
        raise SystemExit("character mesh has zero surface area")
    rng = np.random.default_rng(0xD2)
    picked = rng.choice(len(triangles), size=count, p=areas / total)
    u = rng.random((count, 1))
    v = rng.random((count, 1))
    over = (u + v) > 1.0
    u[over] = 1.0 - u[over]
    v[over] = 1.0 - v[over]
    a, b, c = a[picked], b[picked], c[picked]
    return (a + u * (b - a) + v * (c - a)).astype(np.float64)


def nearest(queries: np.ndarray, cloud: np.ndarray, batch: int = 512) -> np.ndarray:
    out = np.empty_like(queries)
    squared = (cloud * cloud).sum(1)
    for at in range(0, len(queries), batch):
        chunk = queries[at:at + batch]
        distance = squared[None, :] - 2.0 * (chunk @ cloud.T)
        out[at:at + batch] = cloud[np.argmin(distance, axis=1)]
    return out


def adjacency(triangles: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray]:
    """@return `(directed edge list sorted by source, per-vertex degree)`."""
    edges = np.vstack([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    edges = np.vstack([edges, edges[:, ::-1]])
    edges = edges[np.argsort(edges[:, 0], kind="stable")]
    return edges, np.bincount(edges[:, 0], minlength=count)


def smooth_displacement(displacement: np.ndarray, edges: np.ndarray, degree: np.ndarray,
                        iterations: int) -> np.ndarray:
    """Laplacian-smooths a displacement field over the mesh.

    Snapping each vertex to the nearest point of a *sampled* cloud is what the first wrap did, and
    it destroyed the mesh: many distinct vertices land on the same sample, and a triangle whose
    corners collapse to one point has zero area and never rasterises. Measured on the real body,
    **51.7% of mesh 0 and 43.9% of mesh 2 became degenerate** - which in game looked like sparse
    floating fragments rather than a character.

    Smoothing the displacement instead of the positions fixes it at the root. Distinct vertices
    stay distinct because they start distinct and move by nearly the same vector, so the target's
    own topology is preserved while the silhouette still follows the custom character. Five
    iterations already takes both meshes to **0.0% degenerate** while mean movement only falls from
    0.086 m to 0.080 m, so almost none of the shape change is given up.
    """
    out = displacement.copy()
    safe = np.maximum(degree, 1)[:, None]
    for _ in range(iterations):
        accumulated = np.zeros_like(out)
        np.add.at(accumulated, edges[:, 0], out[edges[:, 1]])
        average = np.where(degree[:, None] > 0, accumulated / safe, out)
        out = 0.5 * out + 0.5 * average
    return out


def wrap_buffer(original: bytes, points: np.ndarray, stride: int,
                scale: float, translation) -> bytes:
    """@return `original` with only the packed xyz of each vertex replaced."""
    packed = np.clip(
        np.round((points - np.asarray(translation)) / scale * PACKED_MAX), -32768, 32767
    ).astype(np.int64)
    out = bytearray(original)
    for index, (x, y, z) in enumerate(packed):
        struct.pack_into("<3h", out, index * stride, int(x), int(y), int(z))
    return bytes(out)


def main() -> None:
    source, faces = load_character()
    source = to_destiny(source)
    degrees = option("--arm-swing", DEFAULT_SWING)
    source = swing_arms(source, degrees)
    cloud_source = sample_surface(source, faces, SURFACE_SAMPLES)
    print(f"character: {len(source):,} verts, {len(faces):,} tris, "
          f"{len(cloud_source):,} surface samples, arms swung {degrees:g} deg")

    by_package: dict[Path, dict[int, bytes]] = {}
    obj_path = option("--obj", "")
    obj_points: list[np.ndarray] = []

    for tag in TARGETS:
        model = load_model(tag)
        scale = model.scale[0]
        translation = np.asarray(model.translation[:3], dtype=np.float64)

        # Pass 1: read every mesh so the fit is computed over the whole body at once.
        loaded = []
        for index, mesh in enumerate(model.meshes):
            if not (TAG_MIN <= mesh.position_buffer <= TAG_MAX):
                continue
            stride = vertex_stride(mesh.positions)
            raw = original_buffer(mesh.position_buffer)
            if not stride or raw is None:
                print(f"  0x{tag:08X} mesh {index}: buffers not dumped, skipping")
                continue
            points = np.asarray(read_positions(raw, stride, scale, tuple(translation)),
                                dtype=np.float64)
            loaded.append((index, mesh, stride, raw, points))
        if not loaded:
            print(f"  0x{tag:08X}: nothing loadable")
            continue

        every = np.concatenate([item[4] for item in loaded])
        dst_min, dst_max = every.min(0), every.max(0)
        src_min, src_max = source.min(0), source.max(0)
        src_size = np.maximum(src_max - src_min, 1e-9)
        dst_size = dst_max - dst_min
        placed_cloud = (cloud_source - src_min) / src_size * dst_size + dst_min
        print(f"\n0x{tag:08X}: {len(loaded)} meshes, {len(every):,} verts, "
              f"box {dst_min[2]:.3f}..{dst_max[2]:.3f} m")

        # Pass 2: wrap each mesh against that single shared fit.
        smoothing = option("--smooth", DEFAULT_SMOOTHING)
        for index, mesh, stride, raw, points in loaded:
            displacement = nearest(points, placed_cloud) - points
            triangles = mesh_triangles(mesh, len(points))
            if triangles is None:
                print(f"  mesh {index}: no index buffer dumped, displacement left unsmoothed - "
                      f"expect collapsed triangles")
            elif smoothing > 0:
                edges, degree = adjacency(triangles, len(points))
                displacement = smooth_displacement(displacement, edges, degree, smoothing)
            wrapped = points + displacement
            moved = float(np.linalg.norm(wrapped - points, axis=1).mean())
            new_bytes = wrap_buffer(raw, wrapped, stride, scale, translation)
            if len(new_bytes) != len(raw):
                raise SystemExit(f"0x{tag:08X} mesh {index}: wrap changed buffer size")
            package = package_of(mesh.position_buffer)
            by_package.setdefault(package, {})[entry_index_of(mesh.position_buffer)] = new_bytes
            obj_points.append(wrapped)
            print(f"  mesh {index}: {len(points):>6,} verts  stride {stride}  "
                  f"moved {moved:.3f} m avg  -> {package.name}")

    if obj_path and obj_points:
        combined = np.concatenate(obj_points)
        with open(obj_path, "w", encoding="ascii") as handle:
            handle.write("# wrapped Guardian body, positions only\n")
            for x, y, z in combined:
                handle.write(f"v {x:.5f} {y:.5f} {z:.5f}\n")
        print(f"\nwrote {len(combined):,} wrapped vertices to {obj_path}")

    total = sum(len(v) for v in by_package.values())
    print(f"\n{total} buffers across {len(by_package)} packages")
    for path, replacements in sorted(by_package.items()):
        print(f"  {path.name}: {len(replacements)} entries")
    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return
    write_all(by_package, "")
    print("\nLaunch and look in world. Undo: python inject_mesh.py --undo")


if __name__ == "__main__":
    main()
