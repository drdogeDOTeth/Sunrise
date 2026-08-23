"""Dump and extract the three class base bodies (Hunter, Titan, Warlock).

Not helmets, not Tower frames, not cosmetics. The playable body is equipped armour
(chest + legs + gauntlets) on one global skeleton. Character-select uses a separate
globals / ui preview mesh.

    python dump_class_bodies.py --request     # fill dump/request.txt (then launch once)
    python dump_class_bodies.py --extract     # write docs/characters/ from dumps
    python dump_class_bodies.py --resolve     # arrangement hop -> Hunter/Titan models
"""
from __future__ import annotations

import json
import struct
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from extract_mesh import dumped, read_positions, read_triangles, vertex_stride
from game_paths import artifact_dir, dump_dir, packages_dir
from parse_models import (
    INDEX,
    MODEL_SCALE,
    MODEL_TRANSLATION,
    TAG_MAX,
    TAG_MIN,
    Model,
    lookup,
)
from tigerpkg import TAG_BASE, TAG_ENTRY_BITS

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs" / "characters"
PACKED_MAX = 32767.0
MAIN_LOD = 0
TRIANGLES = 3
FILLER = {254, 255}
BODY_BONE_CEILING = 28

# Wire class: 0 Titan, 1 Hunter, 2 Warlock. Helmet hashes listed only to skip them.
CHARACTERS = {
    "hunter": {
        "class": 1,
        "soid": "0x9EAA300100100101",
        "equipment": {
            "gauntlets": 0x61868551,
            "chest": 0x6F0DBB07,
            "legs": 0x0B8E36D0,
            "class_item": 0xB578E716,
        },
        "skip": {"helmet": 0xF2994B80},
        "models": {
            "chest": [0x80BF9969, 0x80BF9936],
            "gauntlets": [0x80BF92FD, 0x80BF92E2],
            "legs": [0x80EF8D4C, 0x80EF8D4D],
            "class_item": [0x80BFA060, 0x80BFA03D],
        },
    },
    "titan": {
        "class": 0,
        "soid": "0x9EAA300100100102",
        "equipment": {
            "gauntlets": 0xE98EBABD,
            "chest": 0x542FC543,
            "legs": 0xD2F64E86,
            "class_item": 0x295F70BA,
        },
        "skip": {"helmet": 0xECB479E4},
        "models": {
            "chest": [0x80BDF1E4, 0x80BDF1C7],
            "gauntlets": [0x80BDECAF, 0x80BDEC91],
            "legs": [0x80EF71EF, 0x80EF71E6],
            "class_item": [0x80BDF5A5, 0x80BDF584],
        },
    },
    "warlock": {
        "class": 2,
        "soid": "0x9EAA300100100103",
        "equipment": {
            "gauntlets": 0x188C5834,
            "chest": 0xF8689C4C,
            "legs": 0x083E04B6,
            "class_item": 0x99446581,
        },
        "skip": {"helmet": 0xEA042965},
        # Proven Scatterhorn body (HANDOFF / GEOMETRY.md). Gender A/B both written.
        "models": {
            "chest": [0x80EFA1CA, 0x80EFA1A9],
            "gauntlets": [0x80EF981E, 0x80EF9809],
            "legs": [0x80EFA93B, 0x80EFA92E],
            "class_item": [0x80EFA528, 0x80EFA51F],
        },
        "entities": [0x80EFA1D8, 0x80EFA1B7],
    },
}

# Character-select / preview bodies. 0x80B9F962 is the 72k card mesh (GEOMETRY.md).
# ui_037e holds the large person-sized UI models (two 46,448 B headers).
SELECT_BODIES = [0x80B9F962, 0x80C717CA, 0x80B9E810, 0x80F56B13, 0x80FA2DF2]
HOP_TABLES = [
    0x81319329,  # arrangement index -> hash
    0x81613D23,  # hash -> assignment hashes
    0x80EC3F61,  # assignment -> entity-parent
    0x81327D22,  # investment root
]
SKELETON_EXTRAS = [0x81531EE8, 0x81531EE9]
TOWER_FRAMES = {0x80B9F855, 0x80C23B5D, 0x80FA2308}  # do not treat as player

HIERARCHY = {
    1: (None, "pelvis"),
    5: (1, "spine_lower"),
    8: (5, "spine_upper"),
    11: (8, "chest"),
    12: (11, "collar_L"),
    14: (11, "collar_R"),
    13: (11, "neck"),
    16: (11, "neck_02"),
    18: (11, "head"),
    27: (11, "shoulder_L"),
    15: (27, "upperarm_L"),
    19: (15, "forearm_L"),
    21: (19, "hand_L"),
    28: (11, "shoulder_R"),
    17: (28, "upperarm_R"),
    20: (17, "forearm_R"),
    22: (20, "hand_R"),
    3: (1, "thigh_L"),
    6: (3, "shin_L"),
    9: (6, "foot_L"),
    25: (9, "toe_L"),
    4: (1, "thigh_R"),
    7: (4, "shin_R"),
    10: (7, "foot_R"),
    26: (10, "toe_R"),
}


def tag_of(package_id: int, entry: int) -> int:
    return TAG_BASE + (package_id << TAG_ENTRY_BITS) + entry


def split_tag(tag: int) -> tuple[int, int]:
    handle = tag - TAG_BASE
    return handle >> TAG_ENTRY_BITS, handle & 0x1FFF


def window_tags(tag: int, before: int = 4, after: int = 24) -> list[int]:
    pkg, entry = split_tag(tag)
    return [tag_of(pkg, i) for i in range(max(0, entry - before), min(0x1FFF, entry + after) + 1)]


def ui_037e_models() -> list[int]:
    out = []
    for pid, path in INDEX.items():
        if "ui_037e" not in path.name.lower():
            continue
        from tigerpkg import Package
        pkg = Package(path)
        for entry in pkg.entries:
            if entry.reference == 0x808073A5:
                out.append(tag_of(pid, entry.index))
    return out


def load_model(tag: int) -> Model | None:
    path = dump_dir() / f"tag_{tag:08X}.bin"
    if not path.is_file():
        return None
    data = path.read_bytes()
    if len(data) < 0xA0:
        return None
    return Model(tag, data)


def mesh_tags(model: Model) -> list[int]:
    tags: list[int] = []
    for mesh in model.meshes:
        for tag in (
            mesh.positions,
            mesh.position_buffer,
            mesh.texcoords,
            mesh.texcoord_buffer,
            mesh.indices,
            mesh.index_buffer,
        ):
            if TAG_MIN <= tag <= TAG_MAX and tag not in tags:
                tags.append(tag)
    return tags


def write_request() -> Path:
    wanted: list[int] = []
    notes: list[str] = [
        "# Base class bodies: Hunter / Titan / Warlock. No helmets, no Tower frames.",
        "# One launch. Fill the 1024 budget. See docs/characters/README.md.",
        "",
        "class 0x808073A5",
        "",
    ]
    for tag in HOP_TABLES + SKELETON_EXTRAS + SELECT_BODIES:
        wanted.append(tag)
    wanted.extend(ui_037e_models())
    warlock = CHARACTERS["warlock"]
    for group in warlock["models"].values():
        wanted.extend(group)
    wanted.extend(warlock["entities"])
    # Already-dumped models name their buffers — request those too.
    extra: list[int] = []
    for tag in list(wanted):
        model = load_model(tag)
        if model is not None:
            extra.extend(mesh_tags(model))
        else:
            extra.extend(window_tags(tag))
    wanted.extend(extra)
    seen: set[int] = set()
    ordered: list[int] = []
    for tag in wanted:
        if tag in seen or tag in TOWER_FRAMES:
            continue
        seen.add(tag)
        ordered.append(tag)
    ordered = ordered[:1000]
    for tag in ordered:
        notes.append(f"tag 0x{tag:08X}")
    path = dump_dir() / "request.txt"
    path.write_text("\r\n".join(notes) + "\r\n", encoding="utf-8")
    print(f"wrote {len(ordered)} tag requests + class listing -> {path}")
    return path


def decode_uvs(model: Model, mesh, vert_count: int) -> np.ndarray:
    body = dumped(mesh.texcoord_buffer)
    header = dumped(mesh.texcoords)
    uvs = np.zeros((vert_count, 2), dtype=np.float32)
    if body is None or header is None or len(header) < 6:
        return uvs
    stride = struct.unpack_from("<h", header, 4)[0]
    if stride < 4:
        return uvs
    scale = struct.unpack_from("<2f", model.data, 0x70)
    translation = struct.unpack_from("<2f", model.data, 0x78)
    n = min(vert_count, len(body) // stride)
    words = np.frombuffer(body[: n * stride], dtype=np.int16).reshape(n, stride // 2)
    uvs[:n, 0] = words[:, 0] / PACKED_MAX * scale[0] + translation[0]
    uvs[:n, 1] = words[:, 1] / PACKED_MAX * scale[1] + translation[1]
    return uvs


def decode_skins(mesh, vert_count: int) -> list[list[list[int]]]:
    body = dumped(mesh.position_buffer)
    stride = vertex_stride(mesh.positions)
    skins: list[list[list[int]]] = []
    if body is None or stride < 16:
        return [[[1, 255]] for _ in range(vert_count)]
    n = min(vert_count, len(body) // stride)
    for index in range(n):
        at = index * stride
        weights = list(body[at + 8 : at + 12])
        bones = list(body[at + 12 : at + 16])
        pairs = [
            [int(bones[i]), int(weights[i])]
            for i in range(4)
            if weights[i] > 0 and bones[i] not in FILLER
        ]
        skins.append(pairs or [[1, 255]])
    while len(skins) < vert_count:
        skins.append([[1, 255]])
    return skins


def extract_model(tag: int, dest: Path, label: str) -> dict | None:
    model = load_model(tag)
    if model is None:
        print(f"  skip 0x{tag:08X} ({label}): not dumped")
        return None
    dest.mkdir(parents=True, exist_ok=True)
    scale = model.scale[0]
    translation = model.translation[:3]
    summary: dict = {
        "tag": f"0x{tag:08X}",
        "label": label,
        "span": model.span,
        "height": model.height,
        "scale": list(model.scale[:3]),
        "translation": list(translation),
        "texcoord_scale": list(struct.unpack_from("<2f", model.data, 0x70)),
        "texcoord_translation": list(struct.unpack_from("<2f", model.data, 0x78)),
        "meshes": [],
        "files": {},
    }
    all_points: list[np.ndarray] = []
    all_faces: list[np.ndarray] = []
    all_uvs: list[np.ndarray] = []
    all_skins: list[list[list[int]]] = []
    base = 0
    for mesh_index, mesh in enumerate(model.meshes):
        stride = vertex_stride(mesh.positions)
        pos = dumped(mesh.position_buffer)
        idx = dumped(mesh.index_buffer)
        note = {
            "index": mesh_index,
            "stride": stride,
            "positions": f"0x{mesh.positions:08X}",
            "position_buffer": f"0x{mesh.position_buffer:08X}",
            "texcoords": f"0x{mesh.texcoords:08X}",
            "texcoord_buffer": f"0x{mesh.texcoord_buffer:08X}",
            "indices": f"0x{mesh.indices:08X}",
            "index_buffer": f"0x{mesh.index_buffer:08X}",
            "dumped": bool(stride and pos is not None and idx is not None),
            "parts": [],
        }
        if not note["dumped"]:
            summary["meshes"].append(note)
            continue
        points = np.asarray(read_positions(pos, stride, scale, tuple(translation)), dtype=np.float64)
        uvs = decode_uvs(model, mesh, len(points))
        skins = decode_skins(mesh, len(points))
        faces: list[tuple[int, int, int]] = []
        for material, primitive, offset, count, lod in mesh.parts:
            if lod != MAIN_LOD:
                continue
            tris = read_triangles(idx, offset, count, primitive)
            note["parts"].append({
                "material": f"0x{material:08X}",
                "primitive": primitive,
                "offset": offset,
                "count": count,
                "triangles": len(tris),
            })
            for a, b, c in tris:
                if max(a, b, c) < len(points):
                    faces.append((base + a, base + b, base + c))
        note["vertices"] = len(points)
        note["triangles"] = len(faces)
        summary["meshes"].append(note)
        all_points.append(points)
        all_faces.append(np.asarray(faces, dtype=np.int64) if faces else np.zeros((0, 3), dtype=np.int64))
        all_uvs.append(uvs)
        all_skins.extend(skins)
        base += len(points)

    if not all_points:
        print(f"  0x{tag:08X} {label}: header only, no geometry buffers")
        (dest / f"{label}_{tag:08X}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary

    points = np.concatenate(all_points, axis=0)
    faces = np.concatenate(all_faces, axis=0) if any(len(f) for f in all_faces) else np.zeros((0, 3), dtype=np.int64)
    uvs = np.concatenate(all_uvs, axis=0)
    obj_path = dest / f"{label}_{tag:08X}.obj"
    lines = [f"# Destiny base body 0x{tag:08X} {label}", f"o {label}_{tag:08X}"]
    for x, y, z in points:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for u, v in uvs:
        lines.append(f"vt {u:.6f} {v:.6f}")
    for a, b, c in faces:
        if len(uvs):
            lines.append(f"f {a+1}/{a+1} {b+1}/{b+1} {c+1}/{c+1}")
        else:
            lines.append(f"f {a+1} {b+1} {c+1}")
    obj_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    weights_path = dest / f"{label}_{tag:08X}_weights.json"
    weights_path.write_text(json.dumps({"retargeted": False, "skins": all_skins}), encoding="utf-8")
    summary["vertex_count"] = int(len(points))
    summary["triangle_count"] = int(len(faces))
    summary["aabb"] = {
        "min": points.min(0).tolist(),
        "max": points.max(0).tolist(),
    }
    summary["files"] = {"obj": obj_path.name, "weights": weights_path.name}
    (dest / f"{label}_{tag:08X}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"  0x{tag:08X} {label}: {len(points):,} verts, {len(faces):,} tris -> {obj_path.name}")
    return summary


def recover_rig(models: list[int], dest: Path) -> dict:
    """Joint estimates at blend regions, same method as skeleton.py."""
    dest.mkdir(parents=True, exist_ok=True)
    points: list[np.ndarray] = []
    skins: list[list[tuple[int, int]]] = []
    for tag in models:
        model = load_model(tag)
        if model is None or not model.meshes:
            continue
        mesh = model.meshes[0]
        pos = dumped(mesh.position_buffer)
        stride = vertex_stride(mesh.positions)
        if pos is None or stride < 16:
            continue
        xyz = np.asarray(
            read_positions(pos, stride, model.scale[0], tuple(model.translation[:3])),
            dtype=np.float64,
        )
        n = len(xyz)
        for index in range(n):
            at = index * stride
            weights = list(pos[at + 8 : at + 12])
            bones = list(pos[at + 12 : at + 16])
            pairs = [
                (int(bones[i]), int(weights[i]))
                for i in range(4)
                if weights[i] > 0 and bones[i] not in FILLER and bones[i] <= BODY_BONE_CEILING
            ]
            if pairs:
                points.append(xyz[index])
                skins.append(pairs)
    if not points:
        return {}
    cloud = np.vstack(points)
    joints = {}
    for bone, (parent, name) in HIERARCHY.items():
        blend = []
        dominant = []
        for point, pairs in zip(cloud, skins):
            weights = {b: w for b, w in pairs}
            if bone not in weights:
                continue
            if parent is not None and parent in weights and weights[bone] >= 40 and weights[parent] >= 40:
                blend.append(point)
            elif max(pairs, key=lambda p: p[1])[0] == bone:
                dominant.append(point)
        source = blend if blend else dominant
        if not source:
            continue
        pos = np.mean(source, axis=0)
        joints[str(bone)] = {
            "bone": bone,
            "name": name,
            "parent": parent,
            "position": pos.tolist(),
            "from": "blend" if blend else "centroid",
            "samples": len(source),
        }
    rig = {"space": "destiny_character", "units": "metres", "joints": list(joints.values())}
    (dest / "rig.json").write_text(json.dumps(rig, indent=2), encoding="utf-8")
    lines = ["# recovered Guardian bind rig", "o rig"]
    index_of = {}
    for i, joint in enumerate(rig["joints"], start=1):
        x, y, z = joint["position"]
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
        index_of[joint["bone"]] = i
    for joint in rig["joints"]:
        parent = joint["parent"]
        if parent in index_of:
            lines.append(f"l {index_of[parent]} {index_of[joint['bone']]}")
    (dest / "rig.obj").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  rig {len(rig['joints'])} joints -> {dest / 'rig.json'}")
    return rig


def extract_all() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    catalog: dict = {"characters": {}, "select_bodies": [], "notes": []}
    for cls, spec in CHARACTERS.items():
        folder = DOCS / cls
        folder.mkdir(exist_ok=True)
        pieces = {}
        body_tags: list[int] = []
        models = spec.get("models", {})
        for slot, tags in models.items():
            if slot == "helmet":
                continue
            slot_files = []
            for tag in tags:
                info = extract_model(tag, folder, slot)
                if info:
                    slot_files.append(info)
                    if slot in ("chest", "legs", "gauntlets"):
                        body_tags.append(tag)
            pieces[slot] = slot_files
        catalog["characters"][cls] = {
            "class": spec["class"],
            "soid": spec["soid"],
            "equipment_hashes": {k: f"0x{v:08X}" for k, v in spec["equipment"].items()},
            "pieces": pieces,
        }
        if body_tags:
            catalog["characters"][cls]["rig"] = recover_rig(body_tags, folder)

    select_dir = DOCS / "select"
    select_dir.mkdir(exist_ok=True)
    for tag in SELECT_BODIES + ui_037e_models():
        info = extract_model(tag, select_dir, "select")
        if info:
            catalog["select_bodies"].append(info)

    (DOCS / "catalog.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    print(f"catalog -> {DOCS / 'catalog.json'}")


def main() -> None:
    if "--request" in sys.argv or not sys.argv[1:]:
        write_request()
    if "--extract" in sys.argv:
        extract_all()


if __name__ == "__main__":
    main()
