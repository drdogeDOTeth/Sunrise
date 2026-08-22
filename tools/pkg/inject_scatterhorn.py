"""
Puts the whole custom character on the equipped Scatterhorn chest draw, on one skeleton.

**The three-slot split this file used to do was built on a false premise.** `bone_frames.py`
compares the eight bone indices that appear on more than one armour piece and every one of
them lands on the same joint, same side, in every piece that uses it — bone 3 is the left
thigh whether the chest or the legs names it, bone 19 the left forearm whether the chest or
the gauntlets names it. Bone indices are **one global skeleton index space**, not a per-mesh
palette, and the character-select body poses 51 joints spanning 1..63 from a single mesh.

So a draw is not restricted to the joints its original vertices happened to use, and splitting
a welded body across three slots with three different model frames — which is what `_18` did —
buys nothing and costs the bind frame that holds the body together.

What actually bounds this is the index *range*, and the shipped chest already writes up to 28.
Joints 1..28 are a complete humanoid:

  1 waist  3/4 thighs  5/8 torso  6/7 shins  9/10 feet  11-14 chest/collar
  15/17 sleeves  18 head  19/20 forearms  21/22 hands  25/26 toes  27/28 shoulders

Fingers (34-71) are the only thing above 28, and a chrome body does not need them. So the
whole mesh goes on the chest at its own topology, weighted from **every** Scatterhorn piece —
the legs donate shin and foot weights, the gauntlets donate hand weights — and legs, gauntlets,
hood, second robe and class item are all blanked because the one mesh already covers them.

UVs stay tiled Scatterhorn (stride 24).

Usage:
    blender --background --python prepare_mesh.py -- 23512 character_chest.obj
    python inject_scatterhorn.py --dry-run
    python inject_scatterhorn.py
    python inject_scatterhorn.py --rigid    # bone 1 / weight 255, chest only
    python inject_mesh.py --undo
"""
from __future__ import annotations

import collections
import json
import struct
import sys
from pathlib import Path

import numpy as np

from extract_mesh import dumped, read_positions, vertex_stride
from inject_mesh import (
    PART_INDEX_COUNT,
    PART_INDEX_OFFSET,
    PART_LOD,
    PART_MATERIAL,
    PART_PRIMITIVE,
    PART_STRIDE,
    TRIANGLES,
    blank_model,
    entry_index_of,
    package_of,
    resize_per_vertex,
    rewrite_header,
    write_all,
)
from parse_models import MODEL_SCALE, MODEL_TRANSLATION, Model
from wrap_player_body import (
    adjacency,
    option,
    swing_arms,
)

DUMP = Path(r"C:\Sunrise\bin\x64\Sunrise\dump")
PACKED_MAX = 32767.0
PACKED_W = 32767
MESH = Path(__file__).with_name("character_body.obj")
CHESTS = [0x80EFA1CA, 0x80EFA1A9]
# Matching gender/race side. Hands go on the gauntlet mesh so they can use bones 20–71.
GAUNTLETS = {
    0x80EFA1CA: 0x80EF981E,
    0x80EFA1A9: 0x80EF9809,
}
LEGS = {
    0x80EFA1CA: 0x80EFA93B,
    0x80EFA1A9: 0x80EFA92E,
}
DONORS = {
    0x80EFA1CA: {"chest": 0x80EFA1CA, "legs": 0x80EFA93B, "gaunt": 0x80EF981E},
    0x80EFA1A9: {"chest": 0x80EFA1A9, "legs": 0x80EFA92E, "gaunt": 0x80EF9809},
}
BLANK = [
    0x80EFA859, 0x80EFA850,  # hood (stride 8, no inline skin)
    0x80EFA1D4, 0x80EFA1B3,  # second robe (stride 48)
    0x80EFA528, 0x80EFA51F,  # class item
    0x80EFA843,              # neighbouring 928 B cluster, not the bond
    0x80EF981E, 0x80EF9809,  # gauntlets — the one body mesh already has hands
    0x80EFA93B, 0x80EFA92E,  # legs — and legs
]
RIGID_BONE = 1
# Model header fields the texcoord buffer is dequantised through, beside scale/translation.
MODEL_TEXCOORD_SCALE = 0x70
MODEL_TEXCOORD_TRANSLATION = 0x78
# We rewrite the header, so we pick the texcoord frame too. 0.5/0.5 maps the full int16 range
# onto exactly 0..1, which is where a UV lives, and costs nothing to decode.
UV_SCALE = 0.5
UV_TRANSLATION = 0.5
TEXCOORD_STRIDE = 24
# Destiny Z-up, metres, after the custom mesh is centred on the robe translation.
LEG_Z = 0.98
SMOOTH_ITERS = 4
# Gauntlets use 66 and 71. 64 dropped those onto bone 1, which the glove does not pose.
MAX_BONE = 80
# Per-piece bone sets, from census_bones.py — what each piece's own vertices use, not what
# its draw is allowed to pose. Kept because they say which piece to take a weight *from*.
CHEST_BONES = frozenset({1, 3, 4, 5, 8, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 27, 28})
LEG_BONES = frozenset({1, 3, 4, 5, 6, 7, 9, 10, 25, 26})
GAUNT_BONES = frozenset({
    15, 17, 19, 20, 21, 22, 34, 39, 40,
    42, 43, 44, 45, 46, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61,
    66, 71,
})
# The whole-body rig: every joint the three pieces name that is inside the index range the
# shipped chest mesh already writes (max 28). Staying under that ceiling keeps this safe even
# if the runtime sizes a draw's matrix array from the mesh's own highest index — the pessimistic
# reading of the evidence — while `bone_frames.py` says the array is the pawn skeleton outright.
# Everything above 28 is fingers. See BODY_BONE_CEILING.
BODY_BONE_CEILING = 28
BODY_BONES = frozenset(
    bone for bone in (CHEST_BONES | LEG_BONES | GAUNT_BONES) if bone <= BODY_BONE_CEILING
)
LEFT_SLEEVE = frozenset({15, 19, 21, 27})
RIGHT_SLEEVE = frozenset({17, 20, 22, 28})
LEFT_HAND = frozenset({15, 19, 21, 34, 40, 42, 43, 44, 45, 52, 53, 54, 55, 56, 66})
RIGHT_HAND = frozenset({17, 20, 22, 39, 46, 48, 49, 50, 51, 57, 58, 59, 60, 61, 71})
ARM_Y = 0.16
ARM_Z = (0.55, 1.58)
# The GLB is T-pose; robe sleeves rest at |y| ≈ 0.40. 70° matches the robe width.
ARM_SWING = 70.0
# This character is skinny (median |y| ≈ 0.07). Smooth across the midline mixed
# left shin into right shin — the sheet between the legs.
SIDE_EPS = 0.04
LEG_PAIRS = ((3, 4), (6, 7), (9, 10), (25, 26))
ARM_PAIRS = ((15, 17), (15, 28), (19, 17), (19, 28), (27, 28), (27, 17),
             (19, 20), (21, 22), (21, 20), (19, 22))
HAND_PAIRS = (
    (15, 17), (19, 20), (21, 22), (34, 39), (40, 46), (42, 48), (43, 49),
    (44, 50), (45, 51), (52, 57), (53, 58), (54, 59), (55, 60), (56, 61), (66, 71),
)


def load_model(tag: int) -> Model:
    path = DUMP / f"tag_{tag:08X}.bin"
    if not path.is_file():
        raise SystemExit(f"0x{tag:08X} header not dumped")
    return Model(tag, path.read_bytes())


def read_obj(path: Path) -> tuple[np.ndarray, np.ndarray]:
    points: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if line.startswith("v "):
            _, x, y, z = line.split()[:4]
            points.append((float(x), float(y), float(z)))
        elif line.startswith("f "):
            tokens = line.split()[1:]
            if len(tokens) >= 3:
                faces.append(tuple(int(t.split("/")[0]) - 1 for t in tokens[:3]))
    return np.asarray(points, dtype=np.float64), np.asarray(faces, dtype=np.int64)


def nearest_index(queries: np.ndarray, cloud: np.ndarray, batch: int = 256) -> np.ndarray:
    """@return Index of the nearest cloud point for each query, batched like wrap_player_body."""
    out = np.empty(len(queries), dtype=np.int64)
    squared = (cloud * cloud).sum(1)
    for at in range(0, len(queries), batch):
        chunk = queries[at : at + batch]
        distance = squared[None, :] - 2.0 * (chunk @ cloud.T)
        out[at : at + batch] = np.argmin(distance, axis=1)
    return out


def fit_queries(queries: np.ndarray, cloud: np.ndarray) -> np.ndarray:
    """AABB-fit queries onto the donor cloud. Matching only — packed xyz stays in world space."""
    qmin, qmax = queries.min(0), queries.max(0)
    cmin, cmax = cloud.min(0), cloud.max(0)
    qspan = np.maximum(qmax - qmin, 1e-6)
    return (queries - qmin) * ((cmax - cmin) / qspan) + cmin


def nearest_fitted(queries: np.ndarray, cloud: np.ndarray) -> np.ndarray:
    return nearest_index(fit_queries(queries, cloud), cloud)


def load_donors(tags: tuple[int, ...], meshes: tuple[int, ...] = (0,),
                allowed: frozenset[int] | None = None,
                remap: dict[int, int] | None = None) -> tuple[np.ndarray, list[bytes]]:
    """Dequantised vertices plus an 8-byte weight/bone tail of each.

    Legs mesh 1 (stride 12) is the real foot cloud — mesh 0 only has a dozen verts on
    bones 9/10. Toe extras 25/26 are remapped to 9/10 so they can live on mesh 0.
    """
    points: list[tuple[float, float, float]] = []
    skins: list[bytes] = []
    remap = remap or {}
    for tag in tags:
        model = load_model(tag)
        for mesh_index in meshes:
            if mesh_index >= len(model.meshes):
                continue
            mesh = model.meshes[mesh_index]
            stride = vertex_stride(mesh.positions)
            body = dumped(mesh.position_buffer)
            if not stride or not body:
                print(f"  skip donor 0x{tag:08X} mesh {mesh_index}: "
                      f"stride {stride}, dumped={body is not None}")
                continue
            xyz = read_positions(body, stride, model.scale[0], model.translation[:3])
            kept = 0
            for index, point in enumerate(xyz):
                at = index * stride
                if stride >= 16:
                    weights = bytes(body[at + 8 : at + 12])
                    bones = [body[at + 12 + slot] for slot in range(4)]
                elif stride == 12:
                    bones = [body[at + 8], body[at + 9], 0, 0]
                    weights = bytes((body[at + 10], body[at + 11], 0, 0))
                else:
                    continue
                bones = [remap.get(b, b) for b in bones]
                if bones[0] == 254 and weights[0] == 255:
                    continue
                if allowed is not None and bones[0] not in allowed:
                    continue
                points.append(point)
                skins.append(bytes(weights) + bytes(bones))
                kept += 1
            print(f"  donor 0x{tag:08X} mesh {mesh_index}: "
                  f"{kept:,}/{len(xyz):,} verts, stride {stride}")
    if not points:
        raise SystemExit(f"no skinned donors in {['0x%08X' % tag for tag in tags]}")
    return np.asarray(points, dtype=np.float64), skins


def load_authored(mesh_path: Path) -> list[bytes] | None:
    """@return One 8-byte weight/bone tail per vertex from `retarget_mesh.py`, or None.

    These are the artist's own weights, carried through the GLB armature and mapped onto the
    rig's global bone indices. They beat any donor transfer outright, because a transfer can
    only ever ask "which Scatterhorn vertex is nearest", and the answer is wrong wherever the
    two bodies are not the same shape - which is how the hands ended up on the forearm bones.
    """
    path = mesh_path.with_name(mesh_path.stem + "_weights.json")
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[bytes] = []
    dropped = 0
    for skin in data["skins"]:
        pairs = [(int(bone), int(weight)) for bone, weight in skin
                 if weight > 0 and bone <= BODY_BONE_CEILING]
        dropped += len(skin) - len(pairs)
        pairs.sort(key=lambda pair: -pair[1])
        pairs = pairs[:4]
        total = sum(weight for _bone, weight in pairs)
        if not pairs or total <= 0:
            pairs = [(RIGID_BONE, 255)]
            total = 255
        # Requantise so the four bytes still sum to 255 after any clamp above.
        weights = [int(round(weight / total * 255.0)) for _bone, weight in pairs]
        weights[0] += 255 - sum(weights)
        bones = [bone for bone, _weight in pairs]
        while len(weights) < 4:
            weights.append(0)
            bones.append(0)
        out.append(bytes(weights + bones))
    print(f"  authored weights: {len(out):,} vertices, retargeted={data.get('retargeted')}"
          + (f", dropped {dropped} influences above bone {BODY_BONE_CEILING}" if dropped else ""))
    return out


def load_frame(mesh_path: Path) -> np.ndarray | None:
    """@return `(vertices, 9)` of u, v, normal xyz, tangent xyz, handedness, or None."""
    path = mesh_path.with_name(mesh_path.stem + "_frame.bin")
    if not path.is_file():
        return None
    frame = np.fromfile(path, dtype=np.float32).reshape(-1, 9).astype(np.float64)
    print(f"  tangent frame: {len(frame):,} vertices, u {frame[:, 0].min():.3f}.."
          f"{frame[:, 0].max():.3f}  v {frame[:, 1].min():.3f}..{frame[:, 1].max():.3f}")
    return frame


def original_uv2(uv_body: bytes, model: Model) -> tuple[float, float]:
    """@return The mean secondary texcoord of the shipped buffer, in real UV units.

    UV2 is all but constant across Scatterhorn - 0.773..0.795 - so whatever it selects, it
    selects one thing. Carrying that value across rather than inventing one keeps the second
    channel doing what it already did, under our own texcoord scale.
    """
    words = np.frombuffer(uv_body, dtype=np.int16).reshape(-1, TEXCOORD_STRIDE // 2)
    scale = struct.unpack_from("<2f", model.data, MODEL_TEXCOORD_SCALE)
    translation = struct.unpack_from("<2f", model.data, MODEL_TEXCOORD_TRANSLATION)
    return (float(words[:, 10].mean()) / PACKED_MAX * scale[0] + translation[0],
            float(words[:, 11].mean()) / PACKED_MAX * scale[1] + translation[1])


def pack_texcoords(frame: np.ndarray, uv2: tuple[float, float]) -> bytes:
    """@return A stride-24 second vertex buffer: the layout `decode_texcoords.py` verified.

        0x00 u, 0x02 v          value/32767 * UV_SCALE + UV_TRANSLATION
        0x04 nx ny nz, 0x0A 0   unit normal, /32767, then a zero
        0x0C tx ty tz, 0x12 w   unit tangent, /32767, then +/-32767 handedness
        0x14 u2, 0x16 v2        secondary texcoord
    """
    def quantise_uv(values, translation):
        return np.clip(np.round((values - translation) / UV_SCALE * PACKED_MAX),
                       -32767, 32767).astype(np.int16)

    def quantise_unit(values):
        return np.clip(np.round(values * PACKED_MAX), -32767, 32767).astype(np.int16)

    count = len(frame)
    words = np.zeros((count, TEXCOORD_STRIDE // 2), dtype=np.int16)
    words[:, 0] = quantise_uv(frame[:, 0], UV_TRANSLATION)
    words[:, 1] = quantise_uv(frame[:, 1], UV_TRANSLATION)
    words[:, 2:5] = quantise_unit(frame[:, 2:5])
    words[:, 5] = 0
    words[:, 6:9] = quantise_unit(frame[:, 5:8])
    words[:, 9] = np.where(frame[:, 8] >= 0.0, 32767, -32767).astype(np.int16)
    words[:, 10] = quantise_uv(np.full(count, uv2[0]), UV_TRANSLATION)
    words[:, 11] = quantise_uv(np.full(count, uv2[1]), UV_TRANSLATION)
    return words.tobytes()


def pack_authored(points: np.ndarray, stride: int, scale: float, translation,
                  skins: list[bytes]) -> bytes:
    """@return Custom xyz carrying the mesh's own weights, with no donor lookup at all."""
    if stride < 16:
        raise SystemExit(f"need stride 16 for inline skinning, got {stride}")
    if len(skins) != len(points):
        raise SystemExit(f"{len(skins):,} weights for {len(points):,} vertices - rebuild the mesh")
    packed = np.clip(
        np.round((points - np.asarray(translation)) / scale * PACKED_MAX),
        -32768, 32767,
    ).astype(np.int64)
    out = bytearray(len(points) * stride)
    for index, (x, y, z) in enumerate(packed):
        at = index * stride
        struct.pack_into("<4h", out, at, int(x), int(y), int(z), PACKED_W)
        out[at + 8 : at + 16] = skins[index]
    return bytes(out)


def pack_rigid(points: np.ndarray, stride: int, scale: float, translation) -> bytes:
    """@return Position buffer: custom xyz, 100% weight on RIGID_BONE."""
    if stride < 16:
        raise SystemExit(f"need stride 16 for inline skinning, got {stride}")
    packed = np.clip(
        np.round((points - np.asarray(translation)) / scale * PACKED_MAX),
        -32768, 32767,
    ).astype(np.int64)
    out = bytearray(len(points) * stride)
    for index, (x, y, z) in enumerate(packed):
        at = index * stride
        struct.pack_into("<4h", out, at, int(x), int(y), int(z), PACKED_W)
        out[at + 8] = 255
        out[at + 9] = 0
        out[at + 10] = 0
        out[at + 11] = 0
        out[at + 12] = RIGID_BONE
        out[at + 13] = 0
        out[at + 14] = 0
        out[at + 15] = 0
    return bytes(out)


def pack_skinned(points: np.ndarray, stride: int, scale: float, translation,
                 donor_points: np.ndarray, donor_skins: list[bytes]) -> bytes:
    """@return Custom xyz with skinning copied from the nearest donor vertex."""
    if stride < 16:
        raise SystemExit(f"need stride 16 for inline skinning, got {stride}")
    packed = np.clip(
        np.round((points - np.asarray(translation)) / scale * PACKED_MAX),
        -32768, 32767,
    ).astype(np.int64)
    chosen = nearest_index(points, donor_points)
    out = bytearray(len(points) * stride)
    bones: collections.Counter[int] = collections.Counter()
    for index, (x, y, z) in enumerate(packed):
        at = index * stride
        struct.pack_into("<4h", out, at, int(x), int(y), int(z), PACKED_W)
        skin = donor_skins[int(chosen[index])]
        out[at + 8 : at + 16] = skin
        bones[skin[4]] += 1
    print(f"  nearest-skin primary bones: {bones.most_common(8)}")
    return bytes(out)


def skins_to_dense(skins: list[bytes], allowed: frozenset[int]) -> np.ndarray:
    dense = np.zeros((len(skins), MAX_BONE), dtype=np.float64)
    for index, skin in enumerate(skins):
        for slot in range(4):
            bone = skin[4 + slot]
            if bone in allowed and bone < MAX_BONE:
                dense[index, bone] += skin[slot]
    return dense


def clamp_dense(dense: np.ndarray, allowed: frozenset[int], fallback: int = RIGID_BONE,
                fallback_rows: np.ndarray | None = None) -> np.ndarray:
    """Zero joints this mesh does not pose and restore rows that collapse."""
    mask = np.zeros(MAX_BONE, dtype=np.float64)
    for bone in allowed:
        if bone < MAX_BONE:
            mask[bone] = 1.0
    out = dense * mask
    empty = out.sum(1) <= 1e-6
    if np.any(empty):
        if fallback_rows is not None:
            rows = np.flatnonzero(empty)
            out[rows, fallback_rows[rows]] = 255.0
        else:
            out[empty, fallback] = 255.0
        print(f"  clamped {int(empty.sum()):,} verts onto fallback")
    return out


def dense_to_skin(row: np.ndarray, fallback: int = RIGID_BONE) -> bytes:
    order = np.argpartition(row, -4)[-4:]
    order = order[np.argsort(-row[order])]
    weights = np.maximum(row[order], 0.0)
    total = float(weights.sum())
    if total <= 1e-6:
        return bytes([255, 0, 0, 0, fallback, 0, 0, 0])
    quantized = np.clip(np.rint(weights / total * 255.0).astype(np.int64), 0, 255)
    quantized[0] = int(np.clip(quantized[0] + (255 - int(quantized.sum())), 0, 255))
    bones = [int(order[i]) if quantized[i] else 0 for i in range(4)]
    return bytes((int(quantized[0]), int(quantized[1]), int(quantized[2]), int(quantized[3]),
                  bones[0], bones[1], bones[2], bones[3]))


def smooth_weights(dense: np.ndarray, faces: np.ndarray, iterations: int,
                   labels: np.ndarray | None = None) -> np.ndarray:
    """Laplacian-smooths bone weights. Edges that cross a height band are dropped so
    thigh weights cannot bleed into the waist (and vice versa)."""
    edges, degree = adjacency(faces, len(dense))
    if labels is not None:
        keep = labels[edges[:, 0]] == labels[edges[:, 1]]
        edges = edges[keep]
        degree = np.bincount(edges[:, 0], minlength=len(dense))
    out = dense
    safe = np.maximum(degree, 1)[:, None]
    for _ in range(iterations):
        accumulated = np.zeros_like(out)
        np.add.at(accumulated, edges[:, 0], out[edges[:, 1]])
        average = np.where(degree[:, None] > 0, accumulated / safe, out)
        out = 0.5 * out + 0.5 * average
    return out


def split_side(cloud: np.ndarray, skins: list[bytes]) -> dict[str, tuple[np.ndarray, list[bytes]]]:
    """Left queries may use left+mid donors; right uses right+mid. Mid stays mid."""
    y = cloud[:, 1]
    left = y >= -SIDE_EPS
    right = y <= SIDE_EPS
    mid = np.abs(y) <= SIDE_EPS
    return {
        "L": (cloud[left], [skins[i] for i in np.flatnonzero(left)]),
        "R": (cloud[right], [skins[i] for i in np.flatnonzero(right)]),
        "C": (cloud[mid], [skins[i] for i in np.flatnonzero(mid)]),
    }


def assign_sided(chosen: list[bytes], points: np.ndarray, mask: np.ndarray,
                 cloud: np.ndarray, skins: list[bytes], fitted: bool = False) -> None:
    if not np.any(mask) or not skins:
        return
    parts = split_side(cloud, skins)
    y = points[:, 1]
    pick = nearest_fitted if fitted else nearest_index
    for name, select in (
        ("L", mask & (y > SIDE_EPS)),
        ("R", mask & (y < -SIDE_EPS)),
        ("C", mask & (np.abs(y) <= SIDE_EPS)),
    ):
        side_cloud, side_skins = parts[name]
        if not np.any(select) or not side_skins:
            continue
        nearest = pick(points[select], side_cloud)
        for local, vertex in enumerate(np.flatnonzero(select)):
            chosen[int(vertex)] = side_skins[int(nearest[local])]


def strip_crossed(dense: np.ndarray, pairs: tuple[tuple[int, int], ...]) -> np.ndarray:
    """A vertex must not blend left and right limbs — that draws the inseam sheet."""
    for left, right in pairs:
        if left >= MAX_BONE or right >= MAX_BONE:
            continue
        both = (dense[:, left] > 0) & (dense[:, right] > 0)
        prefer_left = dense[:, left] >= dense[:, right]
        dense[both & prefer_left, right] = 0
        dense[both & ~prefer_left, left] = 0
    return dense


def pack_banded(points: np.ndarray, stride: int, scale: float, translation,
                pools: dict[str, tuple[np.ndarray, list[bytes]]], faces: np.ndarray,
                kind: str) -> bytes:
    """Sided nearest skin, clamped to the palette this mesh actually poses."""
    if stride < 16:
        raise SystemExit(f"need stride 16 for inline skinning, got {stride}")
    if kind == "body":
        allowed, pairs, fallback = BODY_BONES, LEG_PAIRS + ARM_PAIRS, RIGID_BONE
        fallback_rows = None
        left_bones, right_bones = LEFT_SLEEVE, RIGHT_SLEEVE
    elif kind == "chest":
        allowed, pairs, fallback = CHEST_BONES, LEG_PAIRS + ARM_PAIRS, RIGID_BONE
        fallback_rows = None
        left_bones, right_bones = LEFT_SLEEVE, RIGHT_SLEEVE
    elif kind == "legs":
        allowed, pairs, fallback = LEG_BONES, LEG_PAIRS, RIGID_BONE
        fallback_rows = None
        left_bones, right_bones = None, None
    elif kind == "gaunt":
        allowed, pairs, fallback = GAUNT_BONES, HAND_PAIRS, 19
        fallback_rows = np.where(points[:, 1] >= 0, 19, 20).astype(np.int64)
        left_bones, right_bones = LEFT_HAND, RIGHT_HAND
    else:
        raise SystemExit(f"unknown slot {kind}")

    y = points[:, 1]
    z = points[:, 2]
    chosen: list[bytes] = [b"\x00" * 8] * len(points)
    pool_name = {"body": "body", "chest": "chest", "legs": "legs", "gaunt": "gaunt"}[kind]
    if pool_name not in pools:
        raise SystemExit(f"{kind} missing donor pool")
    # AABB-fitting queries onto a donor cloud is what turned thighs into feet in `_18`. The body
    # pool spans the whole character in the same space as the placed mesh, so plain nearest is
    # both correct and the only thing that cannot rescale a limb onto the wrong donor.
    assign_sided(chosen, points, np.ones(len(points), dtype=bool), *pools[pool_name],
                 fitted=kind not in ("chest", "body"))

    arm = (np.abs(y) > ARM_Y) & (z >= ARM_Z[0]) & (z <= ARM_Z[1])
    if kind == "gaunt":
        arm = np.ones(len(points), dtype=bool)
    cloud, skins = pools[pool_name]
    if kind in ("chest", "body") and left_bones and right_bones and skins and np.any(arm):
        for bones, select in (
            (left_bones, arm & (y > 0)),
            (right_bones, arm & (y < 0)),
        ):
            idx = [index for index, skin in enumerate(skins) if skin[4] in bones]
            if not idx or not np.any(select):
                continue
            pick = nearest_index if kind == "body" else nearest_fitted
            nearest = pick(points[select], cloud[np.asarray(idx)])
            sleeve_skins = [skins[index] for index in idx]
            for local, vertex in enumerate(np.flatnonzero(select)):
                chosen[int(vertex)] = sleeve_skins[int(nearest[local])]

    labels = np.full(len(points), 1, dtype=np.int8)
    labels[y < -SIDE_EPS] = 0
    labels[y > SIDE_EPS] = 2
    if kind in ("chest", "body"):
        labels[:] = 3
        labels[(z < LEG_Z) & (y < -SIDE_EPS)] = 0
        labels[(z < LEG_Z) & (np.abs(y) <= SIDE_EPS)] = 1
        labels[(z < LEG_Z) & (y > SIDE_EPS)] = 2
        labels[arm & (y < 0)] = 4
        labels[arm & (y > 0)] = 5

    dense = strip_crossed(clamp_dense(skins_to_dense(chosen, allowed), allowed,
                                      fallback, fallback_rows), pairs)
    dense = strip_crossed(clamp_dense(smooth_weights(dense, faces, SMOOTH_ITERS, labels),
                                      allowed, fallback, fallback_rows), pairs)
    packed = np.clip(
        np.round((points - np.asarray(translation)) / scale * PACKED_MAX),
        -32768, 32767,
    ).astype(np.int64)
    out = bytearray(len(points) * stride)
    bones: collections.Counter[int] = collections.Counter()
    illegal = 0
    for index, (px, py, pz) in enumerate(packed):
        at = index * stride
        struct.pack_into("<4h", out, at, int(px), int(py), int(pz), PACKED_W)
        row_fallback = fallback if fallback_rows is None else int(fallback_rows[index])
        skin = dense_to_skin(dense[index], row_fallback)
        out[at + 8 : at + 16] = skin
        bones[skin[4]] += 1
        if skin[4] not in allowed:
            illegal += 1
    audit(out, points, stride, cloud, skins)
    if illegal:
        raise SystemExit(f"  {illegal} verts still on unposed bones for {kind}")
    return bytes(out)


def audit(out: bytearray, points: np.ndarray, stride: int,
          cloud: np.ndarray, skins: list[bytes]) -> None:
    """Print, per joint, where the custom vertices on it sit against where the donors sit.

    `_18`'s pillar was thigh geometry weighted to foot bones, and the dry run printed only a
    bone histogram, which cannot show that. A joint whose custom vertices sit far from the
    donor vertices on the same joint is the failure, and it is visible here before a launch.
    """
    assigned = np.array([out[at * stride + 12] for at in range(len(points))])
    donor_bone = np.array([skin[4] for skin in skins])
    print(f"{'bone':>5} {'verts':>7} {'custom z':>9} {'donor z':>9} {'drift':>7}")
    for bone in sorted(set(assigned.tolist())):
        mine = points[assigned == bone, 2]
        theirs = cloud[donor_bone == bone, 2]
        if not len(theirs):
            print(f"{bone:>5} {len(mine):>7} {mine.mean():>9.3f} {'-':>9} {'-':>7}")
            continue
        drift = abs(float(mine.mean()) - float(theirs.mean()))
        flag = "  <-- far from its donors" if drift > 0.30 else ""
        print(f"{bone:>5} {len(mine):>7} {mine.mean():>9.3f} {theirs.mean():>9.3f} "
              f"{drift:>7.3f}{flag}")


def pack_indices(faces: np.ndarray) -> bytes:
    return faces.reshape(-1).astype(np.uint16).tobytes()


def carrier_slots(mesh) -> list[int]:
    lod0 = [(slot, part) for slot, part in enumerate(mesh.parts) if part[4] == 0]
    if not lod0:
        return [0]
    biggest = max(lod0, key=lambda item: item[1][3])[1]
    return [slot for slot, part in lod0 if part[2] == biggest[2] and part[3] == biggest[3]]


# One LOD-0 part per source material, each carrying a *different* Destiny material.
#
# The chest model's 29 LOD-0 parts are three passes over the same seven index ranges - Destiny's
# three ordered material stages. Slots 0-20 carry nine distinct materials, slots 22-42 are almost
# all `0x80EF938B`, and slots 44-56 are all `0x80EFAD53`. `carrier_slots()` picks the largest range,
# which is offset 3,465 count 3,087, and that range appears in **both** slot 10 and slot 32 - so the
# "two carrier parts" were never two parts, they were one range drawn twice in two passes. Every
# triangle we injected therefore wore one material, and a single material can only hold one texture
# set, so five atlases had nowhere to go however well they were encoded.
#
# Carriers are named by material tag rather than by slot number, because a slot number means nothing
# outside the one model it was read from, and this mapping is applied to both chest models. Both
# happen to lay their pass-one slots out identically, but `slot_for_material()` checks that rather
# than assuming it. Original index counts do not constrain the choice - offset and count are both
# rewritten - so the five largest pass-one materials are used, largest group onto largest material.
# SLOT_MATERIALS finds the five-part-split slot. GROUP_MATERIALS is what that slot draws with.
# They must match. 2026-08-22 pointed GROUP at sandbox_0691 slot-3 binders (world VS
# 0x80FEBAC3). Character select loaded, mask stayed, tank/skin/twirl/necklace vanished.
# Armor parts only draw with the chest vertex shader (0x81532C62 / 0x81532C5B).
SLOT_MATERIALS = {
    "GLSLShader85": 0x80EF98DB,   # BlackTankTop, 24,786 tris
    "GLSLShader13": 0x80EFA1F7,   # SkinTats: body and arms
    "GLSLShader66": 0x80EFA1DC,   # GasMask
    "GLSLShader22": 0x81532AE0,   # Twirl
    "GLSLShader60": 0x81531EF0,   # Silver_Necklace
}
GROUP_MATERIALS = dict(SLOT_MATERIALS)


def load_groups(mesh_path: Path) -> tuple[list[str], np.ndarray] | None:
    """@return `(material names, per-triangle group index)` from the retargeter's groups file."""
    path = mesh_path.with_name(mesh_path.stem + "_groups.json")
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return list(raw["slots"]), np.asarray(raw["face_material"], dtype=np.int64)


def order_by_group(faces: np.ndarray, names: list[str],
                   face_group: np.ndarray) -> tuple[np.ndarray, list[tuple[str, int, int, int, int]]]:
    """
    Sorts triangles so each source material is one contiguous run of indices.

    @return The reordered faces, and `(name, draw material, slot-finder material, offset, count)`.

    A Destiny part is a range of the index buffer, so a material can only get its own part if its
    triangles are contiguous. Sorting is all that takes, and it permutes triangles only - the vertex
    buffer, and with it the weights and the tangent frame, is untouched.
    """
    if len(face_group) != len(faces):
        raise SystemExit(f"groups file has {len(face_group):,} triangles, mesh has {len(faces):,}; "
                         "rebuild both with retarget_mesh.py")
    order = np.argsort(face_group, kind="stable")
    groups: list[tuple[str, int, int, int, int]] = []
    at = 0
    for group, name in enumerate(names):
        count = int((face_group == group).sum())
        if not count:
            print(f"  {name}: no triangles, no part")
            continue
        material = GROUP_MATERIALS.get(name)
        if material is None:
            raise SystemExit(f"no carrier material mapped for source material {name}; "
                             f"add it to GROUP_MATERIALS")
        finder = SLOT_MATERIALS.get(name, material)
        groups.append((name, material, finder, at * 3, count * 3))
        at += count
    return faces[order], groups


def slot_for_material(mesh, material: int) -> int:
    """@return The first LOD-0 part drawing with `material`, refusing if this model has none."""
    for slot, part in enumerate(mesh.parts):
        if part[0] == material and part[4] == 0:
            return slot
    raise SystemExit(f"no LOD-0 part uses material 0x{material:08X}; this model lays its parts out "
                     f"differently from the one GROUP_MATERIALS was read off")


def rewrite_chest(model: Model, index_count: int, scale: float,
                  translation: np.ndarray, own_texcoords: bool = False,
                  groups: list[tuple[str, int, int, int, int]] | None = None
                  ) -> tuple[bytes, list[tuple[str, int, int, int, int]]]:
    """@return The rewritten model, and `(name, slot, material, offset, count)` per drawn part."""
    out = bytearray(model.data)
    struct.pack_into("<4f", out, MODEL_SCALE, scale, scale, scale, 0.0)
    struct.pack_into("<4f", out, MODEL_TRANSLATION,
                     float(translation[0]), float(translation[1]), float(translation[2]), scale)
    if own_texcoords:
        # The texcoord frame is ours to choose once we write our own second vertex buffer.
        struct.pack_into("<2f", out, MODEL_TEXCOORD_SCALE, UV_SCALE, UV_SCALE)
        struct.pack_into("<2f", out, MODEL_TEXCOORD_TRANSLATION, UV_TRANSLATION, UV_TRANSLATION)
    chosen: list[tuple[str, int, int, int, int]] = []
    for number, mesh in enumerate(model.meshes):
        if number != 0:
            plan: dict[int, tuple[int, int, int]] = {}
        elif groups:
            plan = {}
            for name, material, finder, offset, count in groups:
                slot = slot_for_material(mesh, finder)
                if slot in plan:
                    raise SystemExit(f"{name} resolved to slot {slot}, already taken; two groups "
                                     "cannot share one material")
                plan[slot] = (offset, count, material)
                chosen.append((name, slot, material, offset, count))
        else:
            plan = {slot: (0, index_count, mesh.parts[slot][0]) for slot in carrier_slots(mesh)}
            chosen = [(f"all {index_count // 3:,} tris", slot, mesh.parts[slot][0], 0, index_count)
                      for slot in sorted(plan)]
        for slot in range(mesh.part_count):
            at = mesh.parts_at + slot * PART_STRIDE
            if at + PART_STRIDE > len(out):
                break
            if slot in plan:
                offset, count, material = plan[slot]
                struct.pack_into("<I", out, at + PART_MATERIAL, material)
                struct.pack_into("<h", out, at + PART_PRIMITIVE, TRIANGLES)
                struct.pack_into("<I", out, at + PART_INDEX_OFFSET, offset)
                struct.pack_into("<I", out, at + PART_INDEX_COUNT, count)
                out[at + PART_LOD] = 0
            else:
                # Zeroed, and that includes the second and third passes over the ranges we keep -
                # otherwise the same triangles draw again under a material we did not choose.
                struct.pack_into("<I", out, at + PART_INDEX_COUNT, 0)
    blob = bytes(out)
    check_parts(model, blob, chosen)
    return blob, chosen


def check_parts(model: Model, blob: bytes,
                chosen: list[tuple[str, int, int, int, int]]) -> None:
    """Re-read the part table out of the bytes we are about to ship and confirm what draws.

    Written against the parser rather than against the plan, because the plan is what we meant and
    the bytes are what the game sees. The failure this catches is a slot resolved off one model's
    part list and packed into another's - a mistake that leaves every printed line correct.
    """
    reparsed = Model(model.tag, blob)
    drawn = {(slot, part[0], part[2], part[3])
             for mesh in reparsed.meshes
             for slot, part in enumerate(mesh.parts) if part[3]}
    planned = {(slot, material, offset, count) for _, slot, material, offset, count in chosen}
    if drawn != planned:
        extra = "\n".join(f"    drawn but not planned: slot {s} mat 0x{m:08X} {o:,}+{c:,}"
                          for s, m, o, c in sorted(drawn - planned))
        missing = "\n".join(f"    planned but not drawn: slot {s} mat 0x{m:08X} {o:,}+{c:,}"
                            for s, m, o, c in sorted(planned - drawn))
        raise SystemExit(f"0x{model.tag:08X}: part table does not read back as planned\n"
                         f"{extra}\n{missing}".rstrip())


def put(by_package: dict[Path, dict[int, bytes]], tag: int, data: bytes) -> None:
    by_package.setdefault(package_of(tag), {})[entry_index_of(tag)] = data


def region_mesh(points: np.ndarray, faces: np.ndarray,
                vert_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """Keep any triangle that touches the region so the cut is an overlap, not a hole."""
    face_mask = vert_mask[faces[:, 0]] | vert_mask[faces[:, 1]] | vert_mask[faces[:, 2]]
    if not np.any(face_mask):
        return None
    return extract_faces(points, faces, face_mask)


def fit_frame(model: Model, points: np.ndarray) -> tuple[float, np.ndarray]:
    """Keep this slot's translation. Grow scale only so custom points still pack."""
    translation = np.asarray(model.translation[:3], dtype=np.float64)
    old_scale = model.scale[0]
    needed = float(np.max(np.abs(points - translation)) * 1.02)
    return max(old_scale, needed), translation


def extract_faces(points: np.ndarray, faces: np.ndarray,
                  face_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    chosen = faces[face_mask]
    if len(chosen) == 0:
        raise SystemExit("region produced no triangles")
    used, inverse = np.unique(chosen.ravel(), return_inverse=True)
    return points[used].copy(), inverse.reshape(chosen.shape)


def clone_uvs(mesh, vert_count: int, fallback_header: bytes,
              fallback_body: bytes) -> tuple[bytes, bytes, str]:
    """Resize this mesh's UV buffer, or tile the chest's stride-24 chrome if it was never dumped."""
    header = dumped(mesh.texcoords)
    body = dumped(mesh.texcoord_buffer)
    if header is not None and body is not None:
        stride = max(struct.unpack_from("<h", header, 4)[0], 1)
        old_count = len(body) // stride
        note = f"native stride {stride}"
    else:
        header = fallback_header
        body = fallback_body
        stride = max(struct.unpack_from("<h", header, 4)[0], 1)
        old_count = len(body) // stride
        note = f"cloned chest UVs, stride {stride}"
        if mesh.texcoord_buffer == 0 or mesh.texcoords == 0xFFFFFFFF:
            raise SystemExit(f"0x{mesh.texcoords:08X}: no UV buffer tag to write")
    resized = resize_per_vertex(body, old_count, vert_count)
    return rewrite_header(header, len(resized), 0, "<I"), resized, note


def inject_mesh0(by_package: dict[Path, dict[int, bytes]], tag: int,
                 placed: np.ndarray, faces: np.ndarray, positions: bytes,
                 scale: float, translation: np.ndarray,
                 uv_header_fb: bytes, uv_body_fb: bytes,
                 frame: np.ndarray | None = None,
                 groups: list[tuple[str, int, int, int, int]] | None = None) -> None:
    model = load_model(tag)
    mesh = model.meshes[0]
    stride = vertex_stride(mesh.positions)
    original = dumped(mesh.position_buffer)
    position_header = dumped(mesh.positions)
    index_header = dumped(mesh.indices)
    if index_header is None:
        index_header = dumped(load_model(CHESTS[0]).meshes[0].indices)
        if index_header is None:
            raise SystemExit(f"0x{tag:08X}: index header not dumped and no chest fallback")
        print(f"  cloned chest index header for 0x{mesh.indices:08X}")
    if not stride or original is None or position_header is None:
        raise SystemExit(f"0x{tag:08X}: position/index headers not dumped")
    old_count = len(original) // stride
    indices = pack_indices(faces)
    own_texcoords = frame is not None and len(frame) == len(placed)
    if own_texcoords:
        native = dumped(mesh.texcoord_buffer) or uv_body_fb
        uvs = pack_texcoords(frame, original_uv2(native, model))
        texcoord_header = dumped(mesh.texcoords) or uv_header_fb
        uv_header = rewrite_header(texcoord_header, len(uvs), 0, "<I")
        uv_note = f"authored UVs + tangent frame, stride {TEXCOORD_STRIDE}"
    else:
        if frame is not None:
            raise SystemExit(
                f"tangent frame has {len(frame):,} vertices for {len(placed):,} in the mesh - "
                "rebuild both with retarget_mesh.py")
        uv_header, uvs, uv_note = clone_uvs(mesh, len(placed), uv_header_fb, uv_body_fb)
    model_blob, chosen = rewrite_chest(model, faces.size, scale, translation, own_texcoords,
                                       groups)
    print(f"\n0x{tag:08X}: original {old_count:,} verts, stride {stride}  "
          f"-> {len(placed):,} verts / {len(faces):,} tris, scale {scale:.3f}")
    print(f"  positions {len(original):,} -> {len(positions):,} B")
    print(f"  indices   {len(dumped(mesh.index_buffer) or b''):,} -> {len(indices):,} B")
    print(f"  uvs       {len(uvs):,} B ({uv_note})")
    print("  carrier parts, all others zeroed:")
    for name, slot, material, offset, count in chosen:
        print(f"    slot {slot:>2}  mat 0x{material:08X}  indices {offset:>7,}..{offset + count:>7,}"
              f"  {count // 3:>6,} tris  {name}")
    drawn = sum(count for _, _, _, _, count in chosen)
    if drawn != faces.size:
        raise SystemExit(f"parts draw {drawn:,} indices but the buffer holds {faces.size:,}")
    put(by_package, mesh.position_buffer, positions)
    put(by_package, mesh.positions, rewrite_header(position_header, len(positions), 0, "<I"))
    put(by_package, mesh.index_buffer, indices)
    put(by_package, mesh.indices, rewrite_header(index_header, len(indices), 8, "<q"))
    put(by_package, mesh.texcoord_buffer, uvs)
    put(by_package, mesh.texcoords, uv_header)
    put(by_package, model.tag, model_blob)
    check_buffer_headers(by_package, mesh, stride, len(positions), len(uvs))


def check_buffer_headers(by_package: dict[Path, dict[int, bytes]], mesh,
                         position_stride: int, position_bytes: int, uv_bytes: int) -> None:
    """Refuse to ship a buffer header whose stride does not match the buffer it describes.

    A 12-byte header is `size, stride, type, deadbeef`, and only the size is ever rewritten -
    so the stride can only be wrong by having started from the *other* buffer's header. That
    happened once: the texcoord header was copied over the position header, the game read
    23,512 stride-16 vertices as stride-24, and the body came apart into shards. It cost a
    launch, and nothing in the pipeline objected, because every size was right.
    """
    for tag, expected_stride, expected_size, what in (
        (mesh.positions, position_stride, position_bytes, "position"),
        (mesh.texcoords, TEXCOORD_STRIDE, uv_bytes, "texcoord"),
    ):
        written = by_package[package_of(tag)][entry_index_of(tag)]
        size, stride = struct.unpack_from("<Ih", written, 0)
        if stride != expected_stride or size != expected_size:
            raise SystemExit(
                f"0x{tag:08X}: {what} header says size {size:,} stride {stride}, "
                f"but the buffer is {expected_size:,} bytes at stride {expected_stride}")
        if expected_size % expected_stride:
            raise SystemExit(
                f"0x{tag:08X}: {what} buffer of {expected_size:,} B is not a whole number of "
                f"stride-{expected_stride} vertices")


def inject_slot(by_package: dict[Path, dict[int, bytes]], tag: int, kind: str,
                world_points: np.ndarray, world_faces: np.ndarray,
                pools: dict[str, tuple[np.ndarray, list[bytes]]],
                uv_header_fb: bytes, uv_body_fb: bytes, rigid: bool,
                authored: list[bytes] | None = None,
                frame: np.ndarray | None = None,
                groups: list[tuple[str, int, int, int, int]] | None = None) -> None:
    model = load_model(tag)
    mesh = model.meshes[0]
    stride = vertex_stride(mesh.positions)
    scale, translation = fit_frame(model, world_points)
    print(f"\n=== {kind} 0x{tag:08X} ===")
    print(f"  {len(world_points):,} verts / {len(world_faces):,} tris, "
          f"scale {model.scale[0]:.3f} -> {scale:.3f}")
    if rigid and kind in ("chest", "body"):
        positions = pack_rigid(world_points, stride, scale, translation)
    elif authored is not None:
        positions = pack_authored(world_points, stride, scale, translation, authored)
        # Donors are no longer the source of the weights, but they are still the yardstick:
        # a joint whose custom vertices sit far from the donor vertices on the same joint is
        # the `_18` failure, and the audit is the only thing that shows it before a launch.
        audit(bytearray(positions), world_points, stride, *pools["body"])
    else:
        positions = pack_banded(world_points, stride, scale, translation,
                                pools, world_faces, kind)
    inject_mesh0(by_package, tag, world_points, world_faces, positions,
                 scale, translation, uv_header_fb, uv_body_fb, frame, groups)


def main() -> None:
    rigid = "--rigid" in sys.argv
    source = Path(option("--mesh", str(MESH)))
    if not source.is_file():
        raise SystemExit(
            f"custom mesh not found: {source}\n"
            r'Build it: "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" '
            r"--background --python retarget_mesh.py -- 23512 character_body.obj"
        )
    points, faces = read_obj(source)
    authored = None if "--no-authored" in sys.argv else load_authored(source)
    frame = None if "--no-uvs" in sys.argv else load_frame(source)
    if authored is None:
        # Legacy path: an unposed T-pose mesh with no weights of its own, swung by hand and
        # skinned off the nearest donor. Kept only so an old mesh still builds.
        points = swing_arms(points, option("--arm-swing", ARM_SWING))
    groups = None
    if "--one-part" not in sys.argv:
        loaded = load_groups(source)
        if loaded is None:
            print(f"no {source.stem}_groups.json; the whole body goes on one part and can only "
                  "wear one texture set. Rebuild with retarget_mesh.py to split it.")
        else:
            names, face_group = loaded
            faces, groups = order_by_group(faces, names, face_group)
    print(f"custom mesh: {len(points):,} verts, {len(faces):,} tris from {source.name}")
    if groups:
        print(f"parts: {len(groups)} - one per source material, sorted into contiguous ranges")
    print(f"skin: one welded body on the chest draw, joints {sorted(BODY_BONES)}")
    print("      " + ("authored weights, mesh already posed on the rig"
                      if authored is not None else
                      f"nearest-donor weights, arms swung {option('--arm-swing', ARM_SWING)} deg"))
    if len(points) > 0xFFFF:
        raise SystemExit(f"{len(points):,} vertices needs 32-bit indices")

    chest0 = load_model(CHESTS[0])
    uv_header_fb = dumped(chest0.meshes[0].texcoords)
    uv_body_fb = dumped(chest0.meshes[0].texcoord_buffer)
    if uv_header_fb is None or uv_body_fb is None:
        raise SystemExit("chest UV buffer not dumped")

    # A retargeted mesh is already standing in character space - the same metres the rig and the
    # donor clouds live in - so it is placed where it stands. Re-centring it on the chest model's
    # bounding box, which is what the unposed path has to do, lifted the whole body 10.6 cm and
    # left the feet hovering above the foot joints that drive them.
    if authored is not None and "--recentre" not in sys.argv:
        placed = points
        placement = "absolute (rig space)"
    else:
        old_translation = np.asarray(chest0.translation[:3], dtype=np.float64)
        low, high = points.min(0), points.max(0)
        placed = points - (low + high) / 2 + old_translation
        placement = "re-centred on the chest bounding box"
    print(f"  placement: {placement}")
    print(f"  placed aabb x {placed[:, 0].min():.3f}..{placed[:, 0].max():.3f}  "
          f"y {placed[:, 1].min():.3f}..{placed[:, 1].max():.3f}  "
          f"z {placed[:, 2].min():.3f}..{placed[:, 2].max():.3f}")

    by_package: dict[Path, dict[int, bytes]] = {}
    for tag in CHESTS:
        # One donor cloud for the whole character. The pieces are already in a common space -
        # each model's own scale/translation puts it where it sits on the body - and the bone
        # indices mean the same joints across all three, so the legs can donate shin and foot
        # weights straight into the chest buffer.
        donor_tags = tuple(DONORS[tag][name] for name in ("chest", "legs", "gaunt"))
        print(f"\n  body donors {['0x%08X' % t for t in donor_tags]}:")
        pools = {"body": load_donors(donor_tags, meshes=(0, 1), allowed=BODY_BONES)}
        inject_slot(by_package, tag, "body", placed, faces, pools,
                    uv_header_fb, uv_body_fb, rigid, authored, frame, groups)

    for tag in BLANK:
        model = load_model(tag)
        put(by_package, model.tag, blank_model(model))
        print(f"  blank 0x{tag:08X}")
    class_a = 0x80EFA843
    if class_a in BLANK:
        put(by_package, 0x80EFA83A, by_package[package_of(class_a)][entry_index_of(class_a)])
        print("  blank 0x80EFA83A (copy of A)")

    total = sum(len(v) for v in by_package.values())
    print(f"\n{total} entries across {len(by_package)} packages")
    for path, replacements in sorted(by_package.items()):
        print(f"  {path.name}: {len(replacements)} entries")
    if "--dry-run" in sys.argv:
        print("dry run; nothing written")
        return
    write_all(by_package, "")
    print("\nWhole custom body on the chest draw, one bind frame, joints 1-28.")
    print("Legs, gauntlets, hood, second robe and class item are blanked.")
    print("Undo: python inject_mesh.py --undo")


if __name__ == "__main__":
    main()
