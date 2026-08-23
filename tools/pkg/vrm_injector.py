"""
VRM 0.x and 1.0 Avatar Loader, Retargeter, and Injector for Destiny 2 / Sunrise.

Converts VRM models (standardized glTF 2.0 avatars) into Destiny 2 compatible
character models, extracts texture maps, retargets humanoid bones to Destiny's
28-joint skeleton, calculates tangent frames, and packages model profiles for
the Sunrise runtime and package injector.

Features:
- Pure-Python glTF/VRM parser supporting VRM 0.x (extensions.VRM) and VRM 1.0 (extensions.VRMC_vrm)
- Automatic humanoid bone mapping to Destiny 2 skeleton joints (1..28)
- Automatic T-Pose to A-Pose arm retargeting
- Texture extraction (Albedos, Roughness/Metallic, Normals)
- Material grouping and contiguous DrawIndexed part table generation
- Profile deployment to Sunrise runtime (models/<name>/manifest.json + textures)
- Direct full-body package injection into Destiny 2 (Scatterhorn chest slot + blanks)

Usage:
    python vrm_injector.py input.vrm --info
    python vrm_injector.py input.vrm --out-profile "C:\\Sunrise\\bin\\x64\\Sunrise\\models\\my_avatar"
    python vrm_injector.py input.vrm --inject
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# VRM & glTF Specification Constants & Mappings
# ---------------------------------------------------------------------------

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

COMPONENT_TYPES = {
    5120: np.dtype("<i1"),  # BYTE
    5121: np.dtype("<u1"),  # UNSIGNED_BYTE
    5122: np.dtype("<i2"),  # SHORT
    5123: np.dtype("<u2"),  # UNSIGNED_SHORT
    5125: np.dtype("<u4"),  # UNSIGNED_INT
    5126: np.dtype("<f4"),  # FLOAT
}

TYPE_COMPONENTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT4": 16,
}

# Standard VRM Humanoid Bone Name -> Destiny 2 Rig Joint Index (1..28)
VRM_TO_DESTINY_BONES: dict[str, int] = {
    # Core
    "hips": 1,
    "spine": 5,
    "chest": 11,
    "upperChest": 11,
    "neck": 13,
    "head": 18,
    "jaw": 18,
    "leftEye": 18,
    "rightEye": 18,
    # Left Arm
    "leftShoulder": 27,
    "leftUpperArm": 15,
    "leftLowerArm": 19,
    "leftHand": 21,
    # Right Arm
    "rightShoulder": 28,
    "rightUpperArm": 17,
    "rightLowerArm": 20,
    "rightHand": 22,
    # Left Leg
    "leftUpperLeg": 3,
    "leftLowerLeg": 6,
    "leftFoot": 9,
    "leftToes": 25,
    # Right Leg
    "rightUpperLeg": 4,
    "rightLowerLeg": 7,
    "rightFoot": 10,
    "rightToes": 26,
}

# Add fingers folding into hands
for finger in ("Thumb", "Index", "Middle", "Ring", "Little"):
    for part in ("Metacarpal", "Proximal", "Intermediate", "Distal", "1", "2", "3"):
        VRM_TO_DESTINY_BONES[f"left{finger}{part}"] = 21
        VRM_TO_DESTINY_BONES[f"right{finger}{part}"] = 22
        VRM_TO_DESTINY_BONES[f"left_{finger.lower()}_{part.lower()}"] = 21
        VRM_TO_DESTINY_BONES[f"right_{finger.lower()}_{part.lower()}"] = 22

# Destiny chest-native materials used by inject_scatterhorn.py. Unique RGB still
# comes from the draw hook; these tags only buy one part slot each. Off-chest
# steals vanish the body — do not add non-chest materials here.
CARRIER_SLOT_NAMES: list[str] = [
    "GLSLShader85",  # BlackTankTop  0x80EF98DB
    "GLSLShader13",  # SkinTats      0x80EFA1F7
    "GLSLShader66",  # GasMask       0x80EFA1DC
    "GLSLShader22",  # Twirl         0x81532AE0
    "GLSLShader60",  # Silver_Necklace 0x81531EF0
    "chest_extra_0",  # 0x80BFB5E5, chest pass-1
    "chest_extra_1",  # 0x80EFA1DB
    "chest_extra_2",  # 0x81531EEF
    "chest_extra_3",  # 0x81531EEE
]
MAX_CARRIERS = len(CARRIER_SLOT_NAMES)
MAX_INDEXED_VERTS = 65535
BODY_BONE_CEILING = 28
TARGET_HEIGHT_M = 1.70
ARM_SWING_DEG = 70.0
SHOULDER_Y = 0.141
SHOULDER_Z = 1.430
SWING_RAMP = (0.10, 0.22)
WARLOCK_RIG = (
    Path(__file__).resolve().parents[2] / "docs" / "characters" / "warlock" / "rig.json"
)
# Parent-first chains: (head joint, tail joint, descendant vertex bones).
# Spine runs first so a long VRM torso is scaled onto the dumped head, then arms
# and legs. Rotation alone left Me's face 20 cm above the Destiny head joint —
# the bald race head filled the hoodie hole and the beanie sat on the cowl.
UPPER_BODY = frozenset({
    5, 8, 11, 12, 13, 14, 16, 18,
    27, 15, 19, 21,
    28, 17, 20, 22,
})
SPINE_CHAINS = (
    (1, 5, UPPER_BODY),
    (5, 11, UPPER_BODY - {5}),
    (11, 18, frozenset({12, 13, 14, 16, 18})),
)
SHOULDER_CHAINS = (
    (11, 27, frozenset({27, 15, 19, 21})),
    (11, 28, frozenset({28, 17, 20, 22})),
)
ARM_CHAINS = (
    (27, 19, frozenset({27, 15, 19, 21})),
    (19, 21, frozenset({19, 21})),
    (28, 20, frozenset({28, 17, 20, 22})),
    (20, 22, frozenset({20, 22})),
)
LEG_CHAINS = (
    (1, 3, frozenset({3, 6, 9, 25})),
    (3, 6, frozenset({6, 9, 25})),
    (6, 9, frozenset({9, 25})),
    (1, 4, frozenset({4, 7, 10, 26})),
    (4, 7, frozenset({7, 10, 26})),
    (7, 10, frozenset({10, 26})),
)


def game_models_dirs() -> list[Path]:
    """Install locations the draw hook scans. Skip Windows paths on a POSIX host."""
    candidates = [
        Path(r"C:\Sunrise\bin\x64\Sunrise\models"),
        Path("/home/howie/Sunrise/bin/x64/Sunrise/models"),
    ]
    usable: list[Path] = []
    for path in candidates:
        if os.name != "nt" and path.drive:
            continue
        if path.as_posix().startswith("C:") and os.name != "nt":
            continue
        if path.parent.exists():
            usable.append(path)
    return usable

# Fallback string matching for generic GLB / Mixamo rigs
GENERIC_BONE_FALLBACKS: dict[str, int] = {
    "hips": 1, "pelvis": 1, "waist": 1,
    "spine": 5, "spine1": 5, "spine2": 11, "chest": 11, "upperchest": 11,
    "neck": 13, "head": 18, "jaw": 18,
    "leftshoulder": 27, "leftclavicle": 27, "left_shoulder": 27, "left_clavicle": 27, "left shoulder": 27,
    "leftarm": 15, "leftupperarm": 15, "left_arm": 15, "left_upper_arm": 15, "left arm": 15,
    "leftforearm": 19, "leftlowerarm": 19, "leftelbow": 19, "left_elbow": 19, "left elbow": 19, "left_forearm": 19,
    "lefthand": 21, "leftwrist": 21, "left_hand": 21, "left_wrist": 21, "left hand": 21,
    "rightshoulder": 28, "rightclavicle": 28, "right_shoulder": 28, "right_clavicle": 28, "right shoulder": 28,
    "rightarm": 17, "rightupperarm": 17, "right_arm": 17, "right_upper_arm": 17, "right arm": 17,
    "rightforearm": 20, "rightlowerarm": 20, "rightelbow": 20, "right_elbow": 20, "right elbow": 20, "right_forearm": 20,
    "righthand": 22, "rightwrist": 22, "right_hand": 22, "right_wrist": 22, "right hand": 22,
    "leftupleg": 3, "leftupperleg": 3, "leftthigh": 3, "left_leg": 3, "left_thigh": 3, "left leg": 3,
    "leftleg": 6, "leftlowerleg": 6, "leftknee": 6, "leftshin": 6, "left_knee": 6, "left_shin": 6, "left knee": 6,
    "leftfoot": 9, "leftankle": 9, "left_foot": 9, "left_ankle": 9, "left foot": 9,
    "lefttoe": 25, "lefttoes": 25, "left_toe": 25, "left toe": 25,
    "rightupleg": 4, "rightupperleg": 4, "rightthigh": 4, "right_leg": 4, "right_thigh": 4, "right leg": 4,
    "rightleg": 7, "rightlowerleg": 7, "rightknee": 7, "rightshin": 7, "right_knee": 7, "right_shin": 7, "right knee": 7,
    "rightfoot": 10, "rightankle": 10, "right_foot": 10, "right_ankle": 10, "right foot": 10,
    "righttoe": 26, "righttoes": 26, "right_toe": 26, "right toe": 26,
}


# ---------------------------------------------------------------------------
# VRM / glTF Binary Container Parser
# ---------------------------------------------------------------------------

class VrmError(RuntimeError):
    """Raised when a VRM/GLB file cannot be parsed or is invalid."""


class VrmDocument:
    """Represents a parsed VRM/glTF 2.0 document."""

    def __init__(self, json_doc: dict[str, Any], binary: bytes, path: Path):
        self.json = json_doc
        self.binary = binary
        self.path = path
        self.vrm_version: str = "none"
        self.meta: dict[str, Any] = {}
        self.human_bones: dict[str, int] = {}
        self._detect_vrm()

    def _detect_vrm(self) -> None:
        extensions = self.json.get("extensions", {})
        if "VRMC_vrm" in extensions:
            self.vrm_version = "1.0"
            vrmc = extensions["VRMC_vrm"]
            self.meta = vrmc.get("meta", {})
            humanoid = vrmc.get("humanoid", {})
            for role, bone_data in humanoid.get("humanBones", {}).items():
                if isinstance(bone_data, dict) and "node" in bone_data:
                    self.human_bones[role] = bone_data["node"]
        elif "VRM" in extensions:
            self.vrm_version = "0.x"
            vrm0 = extensions["VRM"]
            self.meta = vrm0.get("meta", {})
            humanoid = vrm0.get("humanoid", {})
            for item in humanoid.get("humanBones", []):
                if isinstance(item, dict) and "bone" in item and "node" in item:
                    self.human_bones[item["bone"]] = item["node"]
        else:
            self.vrm_version = "standard_gltf"

    @classmethod
    def load(cls, path: str | Path) -> VrmDocument:
        file_path = Path(path)
        if not file_path.is_file():
            raise VrmError(f"File not found: {file_path}")
        raw = file_path.read_bytes()
        if len(raw) < 12 or raw[:4] != b"glTF":
            raise VrmError(f"{file_path.name} is not a valid glTF/VRM binary container")

        version, length = struct.unpack_from("<II", raw, 4)
        if version != 2:
            raise VrmError(f"Unsupported glTF version: {version} (must be 2)")

        doc: dict[str, Any] | None = None
        bin_chunk = b""
        at = 12
        while at + 8 <= len(raw):
            chunk_length, chunk_type = struct.unpack_from("<I4s", raw, at)
            at += 8
            if chunk_type == b"JSON":
                doc = json.loads(raw[at:at + chunk_length].decode("utf-8", errors="replace"))
            elif chunk_type.startswith(b"BIN"):
                bin_chunk = raw[at:at + chunk_length]
            at += chunk_length + (-chunk_length % 4)

        if doc is None:
            raise VrmError("No JSON chunk found in VRM/GLB file")
        return cls(doc, bin_chunk, file_path)

    def read_accessor(self, accessor_index: int) -> np.ndarray:
        accessor = self.json["accessors"][accessor_index]
        if "bufferView" not in accessor:
            count = accessor.get("count", 0)
            width = TYPE_COMPONENTS[accessor.get("type", "SCALAR")]
            dtype = COMPONENT_TYPES[accessor.get("componentType", 5126)]
            return np.zeros((count, width), dtype=dtype)

        view = self.json["bufferViews"][accessor["bufferView"]]
        dtype = COMPONENT_TYPES[accessor["componentType"]]
        width = TYPE_COMPONENTS[accessor["type"]]
        count = accessor["count"]

        start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        stride = view.get("byteStride")

        if stride and stride != dtype.itemsize * width:
            raw_bytes = np.frombuffer(self.binary, np.uint8, count=stride * count, offset=start)
            rows = raw_bytes.reshape(count, stride)[:, : dtype.itemsize * width]
            values = np.ascontiguousarray(rows).view(dtype).reshape(count, width)
        else:
            values = np.frombuffer(
                self.binary, dtype, count=count * width, offset=start
            ).reshape(count, width)

        return values[:, 0] if width == 1 else values


# ---------------------------------------------------------------------------
# Texture Extraction & Management
# ---------------------------------------------------------------------------

def extract_vrm_textures(doc: VrmDocument, output_dir: Path) -> dict[int, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    views = doc.json.get("bufferViews", [])
    images = doc.json.get("images", [])
    extracted: dict[int, Path] = {}

    for idx, img in enumerate(images):
        if "bufferView" not in img:
            continue
        view = views[img["bufferView"]]
        start = view.get("byteOffset", 0)
        data = doc.binary[start: start + view["byteLength"]]

        mime = img.get("mimeType", "")
        suffix = ".png" if "png" in mime or data.startswith(b"\x89PNG") else ".jpg"
        name = img.get("name") or f"texture_{idx:02d}"
        clean_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        out_path = output_dir / f"{idx:02d}_{clean_name}{suffix}"
        out_path.write_bytes(data)
        extracted[idx] = _as_png(out_path)

    return extracted


def _as_png(path: Path) -> Path:
    """WIC loads JPEG, but Proton's decoder has been flaky; the hook already expects PNG."""
    if path.suffix.lower() == ".png":
        return path
    png = path.with_suffix(".png")
    magick = shutil.which("magick") or shutil.which("convert")
    if magick is None:
        print(f"[VRM] No ImageMagick; leaving {path.name} as {path.suffix}")
        return path
    subprocess.run([magick, str(path), str(png)], check=True)
    path.unlink(missing_ok=True)
    print(f"[VRM] Converted {path.name} -> {png.name}")
    return png


def meta_author(meta: dict[str, Any]) -> str:
    value = meta.get("author") or meta.get("authors") or "Unknown"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "Unknown"
    return str(value)


def profile_id_from_name(name: str) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "avatar"


# ---------------------------------------------------------------------------
# Humanoid Bone Mapping & Retargeting
# ---------------------------------------------------------------------------

def build_node_to_bone_map(doc: VrmDocument) -> dict[int, int]:
    node_to_bone: dict[int, int] = {}

    if doc.human_bones:
        for role, node_idx in doc.human_bones.items():
            if role in VRM_TO_DESTINY_BONES:
                destiny_joint = VRM_TO_DESTINY_BONES[role]
                node_to_bone[node_idx] = destiny_joint

    nodes = doc.json.get("nodes", [])
    for node_idx, node in enumerate(nodes):
        if node_idx in node_to_bone:
            continue
        name = node.get("name", "").lower()
        for key, joint in GENERIC_BONE_FALLBACKS.items():
            if key in name:
                node_to_bone[node_idx] = joint
                break

    return node_to_bone


def to_destiny_space(point_xyz: np.ndarray) -> np.ndarray:
    """
    glTF/VRM (right-handed, Y-up) -> Destiny character space
    (+X forward, +Y left, +Z up).
    """
    if point_xyz.ndim == 1:
        x, y, z = point_xyz
        return np.array([-z, -x, y], dtype=np.float32)
    return np.stack([-point_xyz[:, 2], -point_xyz[:, 0], point_xyz[:, 1]], axis=-1)


def _quat_to_matrix(quat: np.ndarray) -> np.ndarray:
    x, y, z, w = quat
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=np.float64)


def node_local_matrix(node: dict[str, Any]) -> np.ndarray:
    matrix = np.eye(4, dtype=np.float64)
    if "matrix" in node:
        raw = np.asarray(node["matrix"], dtype=np.float64).reshape(4, 4).T
        return raw
    translation = np.asarray(node.get("translation", [0.0, 0.0, 0.0]), dtype=np.float64)
    scale = np.asarray(node.get("scale", [1.0, 1.0, 1.0]), dtype=np.float64)
    rotation = _quat_to_matrix(np.asarray(node.get("rotation", [0.0, 0.0, 0.0, 1.0]), dtype=np.float64))
    matrix[:3, :3] = rotation * scale
    matrix[:3, 3] = translation
    return matrix


def node_world_matrices(doc: VrmDocument) -> list[np.ndarray]:
    nodes = doc.json.get("nodes", [])
    locals_ = [node_local_matrix(node) for node in nodes]
    children: dict[int, list[int]] = {index: [] for index in range(len(nodes))}
    has_parent = [False] * len(nodes)
    for index, node in enumerate(nodes):
        for child in node.get("children", []):
            children[index].append(int(child))
            has_parent[int(child)] = True
    worlds = [np.eye(4, dtype=np.float64) for _ in nodes]

    def walk(index: int, parent: np.ndarray) -> None:
        worlds[index] = parent @ locals_[index]
        for child in children[index]:
            walk(child, worlds[index])

    for index, parented in enumerate(has_parent):
        if not parented:
            walk(index, np.eye(4, dtype=np.float64))
    return worlds


def transform_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    ones = np.ones((len(points), 1), dtype=np.float64)
    homogen = np.concatenate([points.astype(np.float64), ones], axis=1)
    return (homogen @ matrix.T)[:, :3].astype(np.float32)


def transform_vectors(vectors: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    rotated = vectors.astype(np.float64) @ matrix[:3, :3].T
    norms = np.linalg.norm(rotated, axis=1, keepdims=True)
    return np.where(norms > 1e-8, rotated / np.maximum(norms, 1e-8), rotated).astype(np.float32)


def swing_arms_a_pose(points: np.ndarray, degrees: float = ARM_SWING_DEG) -> np.ndarray:
    """Rotate T-pose arms down about the shoulders in Destiny space. Same ramp as wrap_player_body."""
    if degrees == 0.0 or len(points) == 0:
        return points
    out = points.astype(np.float64, copy=True)
    side = np.sign(out[:, 1])
    side = np.where(side == 0.0, 1.0, side)
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
    return out.astype(np.float32)


def load_warlock_rig(path: Path | None = None) -> dict[int, np.ndarray]:
    rig_path = path or WARLOCK_RIG
    if not rig_path.is_file():
        return {}
    data = json.loads(rig_path.read_text(encoding="utf-8"))
    return {
        int(joint["bone"]): np.asarray(joint["position"], dtype=np.float64)
        for joint in data.get("joints", [])
    }


def rotation_align(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """3x3 rotation taking direction `src` onto `dst`."""
    a = np.asarray(src, dtype=np.float64)
    b = np.asarray(dst, dtype=np.float64)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na < 1e-8 or nb < 1e-8:
        return np.eye(3)
    a = a / na
    b = b / nb
    cos = float(np.dot(a, b))
    if cos > 0.999999:
        return np.eye(3)
    if cos < -0.999999:
        axis = np.cross(a, np.array([1.0, 0.0, 0.0]))
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, np.array([0.0, 1.0, 0.0]))
        axis = axis / np.linalg.norm(axis)
        return 2.0 * np.outer(axis, axis) - np.eye(3)
    v = np.cross(a, b)
    s2 = float(np.dot(v, v))
    skew = np.array(
        [[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]],
        dtype=np.float64,
    )
    return np.eye(3) + skew + skew @ skew * ((1.0 - cos) / s2)


def apply_bone_chain(
    pos: np.ndarray,
    nrm: np.ndarray,
    live: dict[int, np.ndarray],
    bones: np.ndarray,
    weights: np.ndarray,
    rig: dict[int, np.ndarray],
    head: int,
    tail: int,
    group: frozenset[int],
    label: str,
) -> None:
    """Rotate and length-scale `group` so live[head]->live[tail] matches the dumped bone."""
    if head not in live or tail not in live or head not in rig or tail not in rig:
        return
    src = live[tail] - live[head]
    dst = rig[tail] - rig[head]
    src_len = float(np.linalg.norm(src))
    dst_len = float(np.linalg.norm(dst))
    if src_len < 1e-8 or dst_len < 1e-8:
        return
    rot = rotation_align(src, dst)
    scale = min(2.0, max(0.5, dst_len / src_len))
    origin = live[head]
    mask = np.zeros(len(pos), dtype=bool)
    for slot in range(bones.shape[1]):
        mask |= np.isin(bones[:, slot], list(group)) & (weights[:, slot] > 1e-4)
    offset = pos[mask] - origin
    pos[mask] = origin + (offset @ rot.T) * scale
    nrm[mask] = nrm[mask] @ rot.T
    for bone, point in list(live.items()):
        if bone in group and bone != head:
            live[bone] = origin + rot @ (point - origin) * scale
    err = float(np.linalg.norm(live[tail] - rig[tail]))
    print(
        f"[VRM] {label} {head}->{tail}: {int(mask.sum()):,} verts, "
        f"scale {scale:.3f}, tail err {err:.3f} m"
    )


def retarget_to_warlock_rig(
    positions: np.ndarray,
    normals: np.ndarray,
    weights: np.ndarray,
    bones: np.ndarray,
    joints: dict[int, np.ndarray],
    rig: dict[int, np.ndarray],
) -> tuple[np.ndarray, np.ndarray, dict[int, np.ndarray]]:
    """
    Place the VRM on the dumped Warlock bind pose.

    Hip-align, then parent-first spine / arm / leg chains. Each chain rotates and
    length-scales onto the dumped bone so a long VRM torso cannot leave the face
    floating above the Destiny head joint.
    """
    pos = positions.astype(np.float64, copy=True)
    nrm = normals.astype(np.float64, copy=True)
    live = {bone: np.asarray(point, dtype=np.float64) for bone, point in joints.items()}
    if 1 in live and 1 in rig:
        delta = rig[1] - live[1]
        pos += delta
        live = {bone: point + delta for bone, point in live.items()}
        print(f"[VRM] Hip-aligned to dumped pelvis by ({delta[0]:+.3f}, {delta[1]:+.3f}, {delta[2]:+.3f})")

    for head, tail, group in (*SPINE_CHAINS, *SHOULDER_CHAINS, *ARM_CHAINS, *LEG_CHAINS):
        if (head, tail, group) in SPINE_CHAINS:
            kind = "Spine"
        elif (head, tail, group) in SHOULDER_CHAINS:
            kind = "Shoulder"
        elif (head, tail, group) in ARM_CHAINS:
            kind = "Arm"
        else:
            kind = "Leg"
        apply_bone_chain(pos, nrm, live, bones, weights, rig, head, tail, group, kind)

    for bone, name in ((18, "Head"), (21, "Hand L"), (22, "Hand R"), (9, "Foot L"), (10, "Foot R")):
        if bone in live and bone in rig:
            dist = float(np.linalg.norm(live[bone] - rig[bone]))
            print(f"[VRM] {name} bone {bone} now {dist:.3f} m from dumped joint")
    norms = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = np.where(norms > 1e-8, nrm / np.maximum(norms, 1e-8), nrm)
    return pos.astype(np.float32), nrm.astype(np.float32), live


def looks_like_t_pose(positions: np.ndarray, bones: np.ndarray) -> bool:
    """True when hand-weighted vertices stick out sideways, i.e. a T-pose."""
    if len(positions) == 0:
        return False
    hand = (bones == 21) | (bones == 22)
    mask = hand.any(axis=1) if bones.ndim == 2 else np.zeros(len(positions), dtype=bool)
    if not np.any(mask):
        mask = np.abs(positions[:, 1]) > 0.35
    if not np.any(mask):
        return False
    return float(np.percentile(np.abs(positions[mask, 1]), 75)) > 0.40


def scale_to_human_height(positions: np.ndarray) -> tuple[np.ndarray, float]:
    height = float(positions[:, 2].max() - positions[:, 2].min()) if len(positions) else 0.0
    if height <= 1e-4:
        return positions, 1.0
    if 0.8 <= height <= 2.4:
        return positions, 1.0
    scale = TARGET_HEIGHT_M / height
    print(f"[VRM] Height {height:.3f} m is outside a humanoid range; scaling by {scale:.3f}")
    return (positions * scale).astype(np.float32), scale


def weld_mesh(
    positions: np.ndarray,
    normals: np.ndarray,
    uvs: np.ndarray,
    weights: np.ndarray,
    bones: np.ndarray,
    parts: list[tuple[int, np.ndarray]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[tuple[int, np.ndarray]]]:
    """Collapse identical position+UV vertices so the body fits in 16-bit indices."""
    keys = np.empty((len(positions), 5), dtype=np.int64)
    keys[:, 0] = np.rint(positions[:, 0] * 1e5)
    keys[:, 1] = np.rint(positions[:, 1] * 1e5)
    keys[:, 2] = np.rint(positions[:, 2] * 1e5)
    keys[:, 3] = np.rint(uvs[:, 0] * 1e4)
    keys[:, 4] = np.rint(uvs[:, 1] * 1e4)

    remap: dict[tuple[int, int, int, int, int], int] = {}
    old_to_new = np.empty(len(positions), dtype=np.int64)
    keep: list[int] = []
    for index, key in enumerate(map(tuple, keys)):
        existing = remap.get(key)
        if existing is None:
            existing = len(keep)
            remap[key] = existing
            keep.append(index)
        old_to_new[index] = existing

    keep_idx = np.asarray(keep, dtype=np.int64)
    welded_parts = [(mat, old_to_new[faces]) for mat, faces in parts]
    print(f"[VRM] Welded {len(positions):,} -> {len(keep_idx):,} vertices")
    return (
        positions[keep_idx],
        normals[keep_idx],
        uvs[keep_idx],
        weights[keep_idx],
        bones[keep_idx],
        welded_parts,
    )


def recompute_normals(positions: np.ndarray, faces: np.ndarray) -> np.ndarray:
    normals = np.zeros_like(positions, dtype=np.float64)
    if len(faces) == 0:
        return normals.astype(np.float32)
    v1 = positions[faces[:, 1]] - positions[faces[:, 0]]
    v2 = positions[faces[:, 2]] - positions[faces[:, 0]]
    face_n = np.cross(v1, v2)
    for corner in range(3):
        np.add.at(normals, faces[:, corner], face_n)
    norms = np.linalg.norm(normals, axis=1, keepdims=True)
    return np.where(norms > 1e-8, normals / np.maximum(norms, 1e-8), np.array([0.0, 0.0, 1.0])).astype(np.float32)


def fit_groups_to_carriers(
    slot_names: list[str],
    face_materials: list[int],
) -> tuple[list[str], list[int], list[str]]:
    """
    Map an arbitrary VRM material list onto chest-native carriers.

    Largest groups keep their own part. Overflow (more than 9) merges into the
    largest group. Returns (carrier names, remapped face group ids, display names).
    """
    if not slot_names:
        return [], [], []
    counts = [0] * len(slot_names)
    for group in face_materials:
        counts[group] += 1
    ranked = sorted(range(len(slot_names)), key=lambda index: -counts[index])
    keep = [index for index in ranked if counts[index] > 0][:MAX_CARRIERS]
    remap = {old: new for new, old in enumerate(keep)}
    for old in ranked[len(keep):]:
        remap[old] = 0
    if len(ranked) > len(keep):
        merged = [slot_names[index] for index in ranked[len(keep):] if counts[index]]
        print(f"[VRM] Merged {len(merged)} extra materials into '{slot_names[keep[0]]}': {merged}")
    carrier_names = [CARRIER_SLOT_NAMES[index] for index in range(len(keep))]
    display = []
    for new, old in enumerate(keep):
        extras = [slot_names[index] for index, target in remap.items()
                  if target == new and index not in keep]
        label = slot_names[old]
        if extras:
            label = f"{label} +{len(extras)}"
        display.append(label)
    remapped = [remap[group] for group in face_materials]
    return carrier_names, remapped, display


# ---------------------------------------------------------------------------
# Mesh Processing & Tangents Calculation
# ---------------------------------------------------------------------------

class ProcessedPart:
    def __init__(self, name: str, material_idx: int, start_idx: int, count: int,
                 albedo_file: str, material_file: str = "",
                 display_name: str = "", carrier: str = ""):
        self.name = name
        self.material_idx = material_idx
        self.start_idx = start_idx
        self.count = count
        self.albedo_file = albedo_file
        self.material_file = material_file
        self.display_name = display_name or name
        self.carrier = carrier

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.display_name,
            "start": self.start_idx,
            "count": self.count,
            "albedo": self.albedo_file,
            "material": self.material_file,
            "carrier": self.carrier,
        }


def compute_tangents(positions: np.ndarray, faces: np.ndarray,
                     uvs: np.ndarray, normals: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n_verts = len(positions)
    tan1 = np.zeros((n_verts, 3), dtype=np.float64)
    tan2 = np.zeros((n_verts, 3), dtype=np.float64)

    p0 = positions[faces[:, 0]]
    p1 = positions[faces[:, 1]]
    p2 = positions[faces[:, 2]]

    uv0 = uvs[faces[:, 0]]
    uv1 = uvs[faces[:, 1]]
    uv2 = uvs[faces[:, 2]]

    v1 = p1 - p0
    v2 = p2 - p0

    duv1 = uv1 - uv0
    duv2 = uv2 - uv0

    r = 1.0 / np.maximum(duv1[:, 0] * duv2[:, 1] - duv1[:, 1] * duv2[:, 0], 1e-8)

    sdir = np.stack([
        (duv2[:, 1] * v1[:, 0] - duv1[:, 1] * v2[:, 0]) * r,
        (duv2[:, 1] * v1[:, 1] - duv1[:, 1] * v2[:, 1]) * r,
        (duv2[:, 1] * v1[:, 2] - duv1[:, 1] * v2[:, 2]) * r,
    ], axis=1)

    tdir = np.stack([
        (duv1[:, 0] * v2[:, 0] - duv2[:, 0] * v1[:, 0]) * r,
        (duv1[:, 0] * v2[:, 1] - duv2[:, 0] * v1[:, 1]) * r,
        (duv1[:, 0] * v2[:, 2] - duv2[:, 0] * v1[:, 2]) * r,
    ], axis=1)

    for i in range(3):
        idx = faces[:, i]
        np.add.at(tan1, idx, sdir)
        np.add.at(tan2, idx, tdir)

    dot_nt = np.sum(normals * tan1, axis=1, keepdims=True)
    tangents = tan1 - normals * dot_nt
    norms = np.linalg.norm(tangents, axis=1, keepdims=True)
    tangents = np.where(norms > 1e-8, tangents / np.maximum(norms, 1e-8), np.array([1.0, 0.0, 0.0]))

    cross_nt = np.cross(normals, tan1)
    dot_cross = np.sum(cross_nt * tan2, axis=1)
    handedness = np.where(dot_cross < 0.0, -1.0, 1.0)

    return tangents.astype(np.float32), handedness.astype(np.float32)


# ---------------------------------------------------------------------------
# Complete Model Extraction & Conversion Pipeline
# ---------------------------------------------------------------------------

class ExtractedMesh:
    def __init__(self) -> None:
        self.positions = np.zeros((0, 3), dtype=np.float32)
        self.normals = np.zeros((0, 3), dtype=np.float32)
        self.uvs = np.zeros((0, 2), dtype=np.float32)
        self.weights = np.zeros((0, 4), dtype=np.float32)
        self.bones = np.zeros((0, 4), dtype=np.int32)
        self.parts: list[tuple[int, np.ndarray]] = []
        self.swung = False
        self.scale = 1.0


class VrmConverter:
    def __init__(self, doc: VrmDocument):
        self.doc = doc
        self.node_to_bone = build_node_to_bone_map(doc)
        self._cached: ExtractedMesh | None = None

    def extract_full_model(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[tuple[int, np.ndarray]]]:
        mesh = self.extract_body()
        return mesh.positions, mesh.normals, mesh.uvs, np.stack(
            [mesh.weights, mesh.bones.astype(np.float32)], axis=1
        ), mesh.parts

    def extract_body(self, swing: bool = True) -> ExtractedMesh:
        if self._cached is not None:
            return self._cached

        doc = self.doc
        skins = doc.json.get("skins", [])
        meshes = doc.json.get("meshes", [])
        worlds = node_world_matrices(doc)
        nodes = doc.json.get("nodes", [])

        mesh_nodes: dict[int, int] = {}
        for node_idx, node in enumerate(nodes):
            if "mesh" in node and node["mesh"] not in mesh_nodes:
                mesh_nodes[int(node["mesh"])] = node_idx

        all_positions: list[np.ndarray] = []
        all_normals: list[np.ndarray] = []
        all_uvs: list[np.ndarray] = []
        all_weights: list[np.ndarray] = []
        all_bones: list[np.ndarray] = []
        material_faces: dict[int, list[np.ndarray]] = {}
        unskinned = 0
        base_vertex = 0

        for mesh_idx, mesh in enumerate(meshes):
            node_idx = mesh_nodes.get(mesh_idx)
            world = worlds[node_idx] if node_idx is not None else np.eye(4)
            skin_idx = None
            if node_idx is not None and "skin" in nodes[node_idx]:
                skin_idx = int(nodes[node_idx]["skin"])
            else:
                for node in nodes:
                    if node.get("mesh") == mesh_idx and "skin" in node:
                        skin_idx = int(node["skin"])
                        break
            joint_nodes = skins[skin_idx]["joints"] if skin_idx is not None and skin_idx < len(skins) else []

            for primitive in mesh.get("primitives", []):
                mode = primitive.get("mode", 4)
                if mode != 4:
                    print(f"[VRM] Skipping non-triangle primitive (mode={mode}) on mesh {mesh_idx}")
                    continue
                attrs = primitive.get("attributes", {})
                if "POSITION" not in attrs or "indices" not in primitive:
                    continue

                pos = transform_points(doc.read_accessor(attrs["POSITION"]).astype(np.float32), world)
                if "NORMAL" in attrs:
                    norm = transform_vectors(doc.read_accessor(attrs["NORMAL"]).astype(np.float32), world)
                else:
                    norm = np.zeros_like(pos)
                if "TEXCOORD_0" in attrs:
                    uv = doc.read_accessor(attrs["TEXCOORD_0"]).astype(np.float32)
                    if uv.ndim == 1:
                        uv = np.stack([uv, np.zeros_like(uv)], axis=1)
                else:
                    uv = np.zeros((len(pos), 2), dtype=np.float32)

                skinned = "JOINTS_0" in attrs and "WEIGHTS_0" in attrs
                if skinned:
                    raw_joints = np.atleast_2d(doc.read_accessor(attrs["JOINTS_0"]).astype(np.int32))
                    raw_weights = np.atleast_2d(doc.read_accessor(attrs["WEIGHTS_0"]).astype(np.float32))
                    if raw_joints.shape[1] < 4:
                        pad = np.zeros((len(raw_joints), 4 - raw_joints.shape[1]), dtype=np.int32)
                        raw_joints = np.concatenate([raw_joints, pad], axis=1)
                    if raw_weights.shape[1] < 4:
                        pad = np.zeros((len(raw_weights), 4 - raw_weights.shape[1]), dtype=np.float32)
                        raw_weights = np.concatenate([raw_weights, pad], axis=1)
                else:
                    unskinned += 1
                    raw_joints = np.zeros((len(pos), 4), dtype=np.int32)
                    raw_weights = np.zeros((len(pos), 4), dtype=np.float32)
                    raw_weights[:, 0] = 1.0

                mapped_bones = np.ones((len(pos), 4), dtype=np.int32)
                for i in range(len(pos)):
                    for j in range(4):
                        j_idx = int(raw_joints[i, j])
                        if 0 <= j_idx < len(joint_nodes):
                            mapped_bones[i, j] = self.node_to_bone.get(int(joint_nodes[j_idx]), 1)

                indices = doc.read_accessor(primitive["indices"]).astype(np.int64)
                if indices.size % 3:
                    indices = indices[: indices.size - (indices.size % 3)]
                if indices.size == 0:
                    continue
                faces = indices.reshape(-1, 3)
                mat_idx = int(primitive.get("material", 0))

                all_positions.append(pos)
                all_normals.append(norm)
                all_uvs.append(uv)
                all_weights.append(raw_weights[:, :4])
                all_bones.append(mapped_bones)
                material_faces.setdefault(mat_idx, []).append(faces + base_vertex)
                base_vertex += len(pos)

        if not all_positions:
            raise VrmError("No geometric primitives found in VRM/GLB file")
        if unskinned:
            print(f"[VRM] {unskinned} unskinned primitive(s) parked on waist bone 1")

        positions = np.concatenate(all_positions, axis=0)
        normals = np.concatenate(all_normals, axis=0)
        uvs = np.concatenate(all_uvs, axis=0)
        weights = np.concatenate(all_weights, axis=0)
        bones = np.concatenate(all_bones, axis=0)
        merged_parts = [
            (mat_idx, np.concatenate(faces_list, axis=0))
            for mat_idx, faces_list in sorted(material_faces.items())
        ]

        positions = to_destiny_space(positions)
        normals = to_destiny_space(normals)
        uvs = np.asarray(uvs, dtype=np.float32)
        uvs[:, 1] = 1.0 - uvs[:, 1]

        positions, scale = scale_to_human_height(positions)
        if scale != 1.0:
            # Uniform scale around origin; normals are directions and stay put.
            pass

        positions, normals, uvs, weights, bones, merged_parts = weld_mesh(
            positions, normals, uvs, weights, bones, merged_parts
        )
        if len(positions) > MAX_INDEXED_VERTS:
            raise VrmError(
                f"{len(positions):,} vertices after weld; Destiny's chest index buffer is 16-bit "
                f"(max {MAX_INDEXED_VERTS:,}). Decimate the VRM before converting."
            )

        joints: dict[int, np.ndarray] = {}
        acc: dict[int, list[np.ndarray]] = {}
        for node_idx, bone in self.node_to_bone.items():
            point = to_destiny_space(np.asarray(worlds[node_idx][:3, 3], dtype=np.float32))
            if scale != 1.0:
                point = point * scale
            acc.setdefault(int(bone), []).append(np.asarray(point, dtype=np.float64))
        for bone, points in acc.items():
            joints[bone] = np.mean(np.stack(points, axis=0), axis=0)

        swung = False
        rig = load_warlock_rig()
        if swing and rig:
            print(f"[VRM] Retargeting onto dumped Warlock rig ({len(rig)} joints)")
            positions, normals, joints = retarget_to_warlock_rig(
                positions, normals, weights, bones, joints, rig
            )
            swung = True
        elif swing and looks_like_t_pose(positions, bones):
            print(f"[VRM] No dumped rig; falling back to Z-swing {ARM_SWING_DEG:g} deg")
            positions = swing_arms_a_pose(positions, ARM_SWING_DEG)
            swung = True
            all_faces = np.concatenate([faces for _mat, faces in merged_parts], axis=0)
            normals = recompute_normals(positions, all_faces)

        body = ExtractedMesh()
        body.positions = positions
        body.normals = normals
        body.uvs = uvs
        body.weights = weights
        body.bones = bones
        body.parts = merged_parts
        body.swung = swung
        body.scale = scale
        self._cached = body
        return body

    def _part_textures(
        self, mat_idx: int, extracted_textures: dict[int, Path]
    ) -> tuple[str, str]:
        materials = self.doc.json.get("materials", [])
        textures = self.doc.json.get("textures", [])
        albedo_name = ""
        mr_name = ""
        if mat_idx < len(materials):
            pbr = materials[mat_idx].get("pbrMetallicRoughness", {})
            base_col_tex = pbr.get("baseColorTexture")
            if base_col_tex is not None:
                tex_idx = int(base_col_tex.get("index", 0))
                if tex_idx < len(textures):
                    src_img = textures[tex_idx].get("source")
                    if src_img in extracted_textures:
                        albedo_name = extracted_textures[src_img].name
            mr_tex = pbr.get("metallicRoughnessTexture")
            if mr_tex is not None:
                tex_idx = int(mr_tex.get("index", 0))
                if tex_idx < len(textures):
                    src_img = textures[tex_idx].get("source")
                    if src_img in extracted_textures:
                        mr_name = extracted_textures[src_img].name
        if not albedo_name and extracted_textures:
            albedo_name = list(extracted_textures.values())[0].name
        return albedo_name, mr_name

    def export_geometry(self, obj_path: Path, extracted_textures: dict[int, Path] | None = None) -> dict[str, Any]:
        """
        Write the four inject_scatterhorn.py inputs (OBJ, weights, tangent frame, groups)
        plus the draw-hook part table. Returns that part table so the manifest matches
        the ranges the chest will actually draw.
        """
        body = self.extract_body()
        materials = self.doc.json.get("materials", [])
        extracted_textures = extracted_textures or {}

        original_names: list[str] = []
        original_albedos: list[str] = []
        original_mrs: list[str] = []
        ordered_faces: list[np.ndarray] = []
        face_materials: list[int] = []
        for mat_idx, faces in body.parts:
            name = f"part_{mat_idx:02d}"
            if mat_idx < len(materials):
                name = materials[mat_idx].get("name") or name
            albedo, mr = self._part_textures(mat_idx, extracted_textures)
            original_names.append(name)
            original_albedos.append(albedo)
            original_mrs.append(mr)
            group = len(original_names) - 1
            for _ in range(len(faces)):
                face_materials.append(group)
            ordered_faces.append(faces)

        all_faces = np.concatenate(ordered_faces, axis=0) if ordered_faces else np.zeros((0, 3), dtype=np.int64)
        carrier_names, remapped, display_names = fit_groups_to_carriers(original_names, face_materials)

        remap_order = np.argsort(np.asarray(remapped, dtype=np.int64), kind="stable")
        sorted_faces = all_faces[remap_order]
        sorted_groups = np.asarray(remapped, dtype=np.int64)[remap_order]

        parts: list[ProcessedPart] = []
        at = 0
        for group, carrier in enumerate(carrier_names):
            count = int((sorted_groups == group).sum())
            if count == 0:
                continue
            source_idx = next(
                (i for i, name in enumerate(original_names) if display_names[group].startswith(name)),
                0,
            )
            parts.append(ProcessedPart(
                name=carrier,
                material_idx=group,
                start_idx=at * 3,
                count=count * 3,
                albedo_file=original_albedos[source_idx] if source_idx < len(original_albedos) else "",
                material_file=original_mrs[source_idx] if source_idx < len(original_mrs) else "",
                display_name=display_names[group],
                carrier=carrier,
            ))
            at += count

        tangents, handedness = compute_tangents(body.positions, sorted_faces, body.uvs, body.normals)

        obj_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[VRM] Writing full-body OBJ: {obj_path} "
              f"({len(body.positions):,} verts, {len(sorted_faces):,} tris, {len(parts)} parts)...")
        with open(obj_path, "w", encoding="ascii") as handle:
            handle.write("# Exported by Sunrise VRM Injector - full Warlock body for inject_scatterhorn.py\n")
            for point in body.positions:
                handle.write(f"v {point[0]:.6f} {point[1]:.6f} {point[2]:.6f}\n")
            for uv in body.uvs:
                handle.write(f"vt {uv[0]:.6f} {uv[1]:.6f}\n")
            for normal in body.normals:
                handle.write(f"vn {normal[0]:.6f} {normal[1]:.6f} {normal[2]:.6f}\n")
            for face in sorted_faces:
                a, b, c = int(face[0]) + 1, int(face[1]) + 1, int(face[2]) + 1
                handle.write(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}\n")

        skins_list: list[list[list[int]]] = []
        for index in range(len(body.positions)):
            vert_skin = []
            for influence in range(4):
                weight = int(round(float(body.weights[index, influence]) * 255.0))
                bone = int(body.bones[index, influence])
                if weight > 0 and bone <= BODY_BONE_CEILING:
                    vert_skin.append([bone, weight])
            if not vert_skin:
                vert_skin = [[1, 255]]
            skins_list.append(vert_skin)
        weights_path = obj_path.with_name(obj_path.stem + "_weights.json")
        weights_path.write_text(json.dumps({"retargeted": True, "skins": skins_list}), encoding="utf-8")
        print(f"[VRM] Wrote weights JSON: {weights_path}")

        frame_data = np.zeros((len(body.positions), 9), dtype=np.float32)
        frame_data[:, 0] = body.uvs[:, 0]
        frame_data[:, 1] = body.uvs[:, 1]
        frame_data[:, 2:5] = body.normals
        frame_data[:, 5:8] = tangents
        frame_data[:, 8] = handedness
        frame_path = obj_path.with_name(obj_path.stem + "_frame.bin")
        frame_data.tofile(str(frame_path))
        print(f"[VRM] Wrote tangent frame: {frame_path}")

        groups_path = obj_path.with_name(obj_path.stem + "_groups.json")
        groups_path.write_text(json.dumps({
            "note": ("Per-triangle chest-native carrier, in the OBJ's face order. "
                     "Names are inject_scatterhorn.py GROUP_MATERIALS keys."),
            "slots": carrier_names,
            "display": display_names,
            "triangles": len(sorted_faces),
            "face_material": sorted_groups.tolist(),
        }), encoding="utf-8")
        print(f"[VRM] Wrote groups JSON: {groups_path}")

        low = body.positions.min(0)
        high = body.positions.max(0)
        print(f"[VRM] AABB x {low[0]:.3f}..{high[0]:.3f}  y {low[1]:.3f}..{high[1]:.3f}  "
              f"z {low[2]:.3f}..{high[2]:.3f}  swung={body.swung}")
        return {
            "vertex_count": int(len(body.positions)),
            "total_indices": int(len(sorted_faces) * 3),
            "parts": [part.to_dict() for part in parts],
            "swung": body.swung,
        }

    def build_profile(self, profile_name: str, output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"[VRM] Extracting textures from {self.doc.path.name}...")
        extracted = extract_vrm_textures(self.doc, output_dir)
        print("[VRM] Extracting and retargeting full-body geometry...")
        geometry = self.export_geometry(output_dir / "character_body.obj", extracted)

        title = self.doc.meta.get("title") or profile_name
        manifest = {
            "id": profile_id_from_name(profile_name),
            "name": title,
            "author": meta_author(self.doc.meta),
            "version": self.doc.vrm_version,
            "kind": "full_body",
            "slot": "scatterhorn_chest",
            "vertex_count": geometry["vertex_count"],
            "total_indices": geometry["total_indices"],
            "swung": geometry["swung"],
            "parts": geometry["parts"],
        }
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"[VRM] Wrote profile manifest: {manifest_path}")
        return manifest


def deploy_profile(source_dir: Path, profile_id: str) -> list[Path]:
    deployed: list[Path] = []
    for game_models_dir in game_models_dirs():
        target = game_models_dir / profile_id
        target.mkdir(parents=True, exist_ok=True)
        for item in source_dir.iterdir():
            if item.is_file():
                shutil.copy2(item, target / item.name)
        print(f"[VRM] Deployed profile to game models folder: {target}")
        print("[VRM] The Player menu Refresh scans this folder. Destiny must be closed to inject geometry.")
        deployed.append(target)
    return deployed


def inject_full_body(obj_path: Path, dry_run: bool = False) -> None:
    """Install the converted mesh as the playable Warlock, not as a helmet/ornament."""
    pkg_dir = Path(__file__).resolve().parent
    inject_script = pkg_dir / "inject_scatterhorn.py"
    if not inject_script.is_file():
        raise VrmError(f"inject_scatterhorn.py not found next to {pkg_dir}")
    if not obj_path.is_file():
        raise VrmError(f"converted body OBJ not found: {obj_path}")
    for sidecar in ("_weights.json", "_frame.bin", "_groups.json"):
        path = obj_path.with_name(obj_path.stem + sidecar)
        if not path.is_file():
            raise VrmError(f"missing {path.name}; conversion did not finish")

    # inject_scatterhorn.py reads tools/pkg/character_body.obj by default. Copy the
    # converted sidecars there so the proven chest+blank path runs unchanged.
    dest = pkg_dir / "character_body.obj"
    if obj_path.resolve() != dest.resolve():
        shutil.copy2(obj_path, dest)
        for sidecar in ("_weights.json", "_frame.bin", "_groups.json"):
            src = obj_path.with_name(obj_path.stem + sidecar)
            shutil.copy2(src, dest.with_name(dest.stem + sidecar))
        print(f"[VRM] Copied full-body mesh to {dest} for package injection")

    cmd = [sys.executable, str(inject_script), "--mesh", str(dest)]
    if dry_run:
        cmd.append("--dry-run")
    print("[VRM] Running inject_scatterhorn.py — whole custom body on the Scatterhorn chest,")
    print("[VRM] legs/gauntlets/hood/class item blanked. Destiny must be closed.")
    subprocess.run(cmd, check=True, cwd=str(pkg_dir))


# ---------------------------------------------------------------------------
# CLI & Command Interface
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a VRM/GLB into a full Sunrise Warlock body (Scatterhorn chest inject)."
    )
    parser.add_argument("vrm_file", type=Path, help="Path to input .vrm or .glb file")
    parser.add_argument("--info", action="store_true", help="Print model and VRM metadata")
    parser.add_argument("--out-profile", type=Path, help="Output directory for model profile")
    parser.add_argument("--name", type=str, default="", help="Profile name")
    parser.add_argument(
        "--inject",
        action="store_true",
        help="Write the mesh into Destiny packages via inject_scatterhorn.py (Destiny must be closed)",
    )
    parser.add_argument("--dry-run", action="store_true", help="With --inject, do not write packages")
    parser.add_argument("--no-swing", action="store_true", help="Skip T-pose to A-pose arm swing")
    args = parser.parse_args()

    if not args.vrm_file.is_file():
        print(f"Error: File does not exist: {args.vrm_file}")
        sys.exit(1)

    print(f"Loading {args.vrm_file.name}...")
    doc = VrmDocument.load(args.vrm_file)
    author = meta_author(doc.meta)

    print("\n--- VRM Model Summary ---")
    print(f"File: {args.vrm_file.name}")
    print(f"Format: {doc.vrm_version.upper()}")
    print(f"Title: {doc.meta.get('title', 'N/A')}")
    print(f"Author: {author}")
    print(f"Meshes: {len(doc.json.get('meshes', []))}")
    print(f"Materials: {len(doc.json.get('materials', []))}")
    print(f"Images: {len(doc.json.get('images', []))}")
    print(f"Humanoid Bones Mapped: {len(doc.human_bones)}")

    if args.info:
        return

    profile_name = args.name or args.vrm_file.stem
    pkg_dir = Path(__file__).resolve().parent
    out_dir = args.out_profile or pkg_dir / "models" / profile_id_from_name(profile_name)

    converter = VrmConverter(doc)
    if args.no_swing:
        converter.extract_body(swing=False)
    manifest = converter.build_profile(profile_name, out_dir)
    deploy_profile(out_dir, manifest["id"])

    if args.inject:
        inject_full_body(out_dir / "character_body.obj", dry_run=args.dry_run)

    print(f"\n[Success] Full-body profile '{manifest['name']}' "
          f"({manifest['vertex_count']:,} verts, {len(manifest['parts'])} parts).")
    if not args.inject:
        print("Geometry is converted but NOT in the game yet. Close Destiny and rerun with --inject")
        print("to install it as the playable Warlock (Scatterhorn chest + blanked other armour).")
        print("The in-game Player menu only switches draw-hook textures/part ranges after inject.")


if __name__ == "__main__":
    main()
