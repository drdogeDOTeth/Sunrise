"""
Reads meshes out of a `.glb`, with no third-party dependency.

The install has numpy but not trimesh or pygltflib, and glTF's binary container is small enough that
a loader is cheaper than asking the user to install anything. Only what a mesh edit needs is
implemented: positions and triangle indices for one named mesh.

Accessor decoding covers the ordinary path — a `bufferView` into the single embedded `BIN` chunk,
optionally interleaved via `byteStride`. Sparse accessors and external buffers are rejected loudly
rather than silently mis-read, because a wrong mesh here would look exactly like a wrong shrink-wrap
several steps later.

Usage:
    python glb.py <file.glb>              # list meshes
    from glb import load_mesh
    positions, triangles = load_mesh(path, "GasMask")
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np

# glTF component type -> numpy dtype. The spec fixes these; there is no endianness choice, glTF is
# always little-endian.
COMPONENT = {
    5120: np.dtype("<i1"),
    5121: np.dtype("<u1"),
    5122: np.dtype("<i2"),
    5123: np.dtype("<u2"),
    5125: np.dtype("<u4"),
    5126: np.dtype("<f4"),
}
COMPONENTS_PER = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class GlbError(RuntimeError):
    """Raised when a file is not a glb this loader can honestly read."""


def read_glb(path: str | Path) -> tuple[dict, bytes]:
    """@return The parsed JSON chunk and the binary chunk of one `.glb`."""
    raw = Path(path).read_bytes()
    if len(raw) < 12 or raw[:4] != b"glTF":
        raise GlbError(f"{path} is not a glb")
    version, length = struct.unpack_from("<II", raw, 4)
    if version != 2:
        raise GlbError(f"glb version {version} is not supported")
    if length != len(raw):
        raise GlbError(f"header declares {length:,} bytes, file holds {len(raw):,}")

    document: dict | None = None
    binary = b""
    at = 12
    while at + 8 <= len(raw):
        chunk_length, chunk_type = struct.unpack_from("<I4s", raw, at)
        at += 8
        if chunk_type == b"JSON":
            document = json.loads(raw[at:at + chunk_length])
        elif chunk_type.startswith(b"BIN"):
            binary = raw[at:at + chunk_length]
        at += chunk_length
    if document is None:
        raise GlbError("no JSON chunk")
    return document, binary


def read_accessor(document: dict, binary: bytes, index: int) -> np.ndarray:
    """@return One accessor as an `(count, components)` array, squeezed for SCALAR."""
    accessor = document["accessors"][index]
    if "sparse" in accessor:
        raise GlbError(f"accessor {index} is sparse, which this loader does not decode")
    if "bufferView" not in accessor:
        raise GlbError(f"accessor {index} has no bufferView")

    dtype = COMPONENT[accessor["componentType"]]
    width = COMPONENTS_PER[accessor["type"]]
    count = accessor["count"]

    view = document["bufferViews"][accessor["bufferView"]]
    if document["buffers"][view.get("buffer", 0)].get("uri") is not None:
        raise GlbError("external buffers are not supported; export a self-contained glb")
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)

    stride = view.get("byteStride")
    if stride and stride != dtype.itemsize * width:
        # Interleaved attributes: take one element's worth out of each stride step rather than
        # reading a contiguous run, which would silently mix in neighbouring attributes.
        rows = np.frombuffer(binary, np.uint8, count=stride * count, offset=start)
        rows = rows.reshape(count, stride)[:, : dtype.itemsize * width]
        values = np.ascontiguousarray(rows).view(dtype).reshape(count, width)
    else:
        values = np.frombuffer(binary, dtype, count=count * width, offset=start).reshape(count, width)
    return values[:, 0] if width == 1 else values


def mesh_names(document: dict) -> list[str]:
    return [mesh.get("name", f"mesh{i}") for i, mesh in enumerate(document["meshes"])]


def load_mesh(path: str | Path, name: str) -> tuple[np.ndarray, np.ndarray]:
    """
    Loads one named mesh, merging its primitives into a single vertex and triangle array.

    Primitives split a mesh by material, not by shape, so merging them is what reconstitutes the
    object the modeller actually authored.

    @param path The `.glb` to read.
    @param name Mesh name, matched case-insensitively.
    @return `(positions (n,3) float32, triangles (m,3) int32)`.
    """
    document, binary = read_glb(path)
    wanted = [
        mesh for mesh in document["meshes"]
        if mesh.get("name", "").lower() == name.lower()
    ]
    if not wanted:
        raise GlbError(f"no mesh named {name!r}; file has {', '.join(mesh_names(document))}")

    chunks: list[np.ndarray] = []
    faces: list[np.ndarray] = []
    base = 0
    for mesh in wanted:
        for primitive in mesh["primitives"]:
            if primitive.get("mode", 4) != 4:
                continue  # not triangles
            positions = read_accessor(document, binary, primitive["attributes"]["POSITION"])
            if "indices" not in primitive:
                raise GlbError("non-indexed primitives are not supported")
            indices = read_accessor(document, binary, primitive["indices"]).astype(np.int64)
            chunks.append(positions.astype(np.float32))
            faces.append(indices.reshape(-1, 3) + base)
            base += len(positions)
    if not chunks:
        raise GlbError(f"mesh {name!r} carries no triangle primitives")
    return np.concatenate(chunks), np.concatenate(faces).astype(np.int32)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb")
    doc, _ = read_glb(target)
    print(f"{target.name}: {len(doc['meshes'])} meshes")
    for index, mesh in enumerate(doc["meshes"]):
        total = sum(
            doc["accessors"][p["attributes"]["POSITION"]]["count"] for p in mesh["primitives"])
        print(f"  {index:3d}  {mesh.get('name','?'):32s} {total:7,} verts")
