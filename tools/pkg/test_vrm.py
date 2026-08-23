"""
Unit test for VRM Document parser, bone mapping, and converter.
"""
import json
import struct
import tempfile
import unittest
from pathlib import Path
import numpy as np

from vrm_injector import (
    GLB_MAGIC,
    CHUNK_JSON,
    CHUNK_BIN,
    CARRIER_SLOT_NAMES,
    VrmDocument,
    VrmConverter,
    VRM_TO_DESTINY_BONES,
    build_node_to_bone_map,
    fit_groups_to_carriers,
    game_models_dirs,
    meta_author,
    profile_id_from_name,
    to_destiny_space,
    weld_mesh,
)


def create_synthetic_vrm(vrm_type="1.0"):
    # Create simple binary buffer (1 vertex at origin, 1 normal, 1 uv, 1 triangle)
    positions = np.array([[0.0, 1.0, 0.0], [0.5, 0.0, 0.0], [-0.5, 0.0, 0.0]], dtype=np.float32)
    normals = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 1.0], [0.0, 0.0, 1.0]], dtype=np.float32)
    uvs = np.array([[0.5, 1.0], [1.0, 0.0], [0.0, 0.0]], dtype=np.float32)
    indices = np.array([0, 1, 2], dtype=np.uint16)
    joints = np.array([[0, 0, 0, 0], [1, 0, 0, 0], [2, 0, 0, 0]], dtype=np.uint16)
    weights = np.array([[1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]], dtype=np.float32)

    # 1x1 white PNG image
    png_1x1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00"
        b"\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    bin_data = bytearray()

    def add_buf(data_bytes):
        offset = len(bin_data)
        bin_data.extend(data_bytes)
        while len(bin_data) % 4 != 0:
            bin_data.append(0)
        return offset, len(data_bytes)

    pos_off, pos_len = add_buf(positions.tobytes())
    norm_off, norm_len = add_buf(normals.tobytes())
    uv_off, uv_len = add_buf(uvs.tobytes())
    idx_off, idx_len = add_buf(indices.tobytes())
    joint_off, joint_len = add_buf(joints.tobytes())
    weight_off, weight_len = add_buf(weights.tobytes())
    img_off, img_len = add_buf(png_1x1)

    json_doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(bin_data)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": pos_off, "byteLength": pos_len},
            {"buffer": 0, "byteOffset": norm_off, "byteLength": norm_len},
            {"buffer": 0, "byteOffset": uv_off, "byteLength": uv_len},
            {"buffer": 0, "byteOffset": idx_off, "byteLength": idx_len},
            {"buffer": 0, "byteOffset": joint_off, "byteLength": joint_len},
            {"buffer": 0, "byteOffset": weight_off, "byteLength": weight_len},
            {"buffer": 0, "byteOffset": img_off, "byteLength": img_len},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC2"},
            {"bufferView": 3, "componentType": 5123, "count": 3, "type": "SCALAR"},
            {"bufferView": 4, "componentType": 5123, "count": 3, "type": "VEC4"},
            {"bufferView": 5, "componentType": 5126, "count": 3, "type": "VEC4"},
        ],
        "images": [{"bufferView": 6, "mimeType": "image/png", "name": "AvatarTexture"}],
        "textures": [{"source": 0}],
        "materials": [
            {
                "name": "AvatarMaterial",
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0},
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.5,
                },
            }
        ],
        "meshes": [
            {
                "name": "AvatarBody",
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": 0,
                            "NORMAL": 1,
                            "TEXCOORD_0": 2,
                            "JOINTS_0": 4,
                            "WEIGHTS_0": 5,
                        },
                        "indices": 3,
                        "material": 0,
                    }
                ],
            }
        ],
        "nodes": [
            {"name": "HipsNode", "mesh": 0, "skin": 0},
            {"name": "SpineNode"},
            {"name": "HeadNode"},
        ],
        "skins": [{"joints": [0, 1, 2]}],
        "scene": 0,
        "scenes": [{"nodes": [0, 1, 2]}],
        "extensions": {},
    }

    if vrm_type == "1.0":
        json_doc["extensions"]["VRMC_vrm"] = {
            "specVersion": "1.0",
            "meta": {"title": "Test Avatar 1.0", "authors": ["DeepMind"]},
            "humanoid": {
                "humanBones": {
                    "hips": {"node": 0},
                    "spine": {"node": 1},
                    "head": {"node": 2},
                }
            },
        }
    elif vrm_type == "0.x":
        json_doc["extensions"]["VRM"] = {
            "exporterVersion": "UniVRM-0.58.0",
            "meta": {"title": "Test Avatar 0.x", "author": "DeepMind"},
            "humanoid": {
                "humanBones": [
                    {"bone": "hips", "node": 0},
                    {"bone": "spine", "node": 1},
                    {"bone": "head", "node": 2},
                ]
            },
        }

    json_bytes = json.dumps(json_doc).encode("utf-8")
    while len(json_bytes) % 4 != 0:
        json_bytes += b" "

    total_len = 12 + 8 + len(json_bytes) + 8 + len(bin_data)

    header = struct.pack("<III", GLB_MAGIC, 2, total_len)
    json_chunk_hdr = struct.pack("<II", len(json_bytes), CHUNK_JSON)
    bin_chunk_hdr = struct.pack("<II", len(bin_data), CHUNK_BIN)

    return header + json_chunk_hdr + json_bytes + bin_chunk_hdr + bytes(bin_data)


class TestVrmSuite(unittest.TestCase):

    def test_vrm_1_0_parsing(self):
        raw = create_synthetic_vrm("1.0")
        with tempfile.NamedTemporaryFile(suffix=".vrm", delete=False) as f:
            f.write(raw)
            temp_path = Path(f.name)

        try:
            doc = VrmDocument.load(temp_path)
            self.assertEqual(doc.vrm_version, "1.0")
            self.assertEqual(doc.meta.get("title"), "Test Avatar 1.0")
            self.assertIn("hips", doc.human_bones)
            self.assertEqual(doc.human_bones["hips"], 0)

            node_map = build_node_to_bone_map(doc)
            self.assertEqual(node_map[0], 1)   # Hips -> Waist (1)
            self.assertEqual(node_map[1], 5)   # Spine -> Torso (5)
            self.assertEqual(node_map[2], 18)  # Head -> Head (18)

            with tempfile.TemporaryDirectory() as out_dir:
                converter = VrmConverter(doc)
                manifest = converter.build_profile("TestAvatar", Path(out_dir))
                self.assertEqual(manifest["vertex_count"], 3)
                self.assertEqual(len(manifest["parts"]), 1)
                self.assertEqual(manifest["parts"][0]["count"], 3)
                self.assertTrue((Path(out_dir) / "manifest.json").is_file())
        finally:
            temp_path.unlink()

    def test_vrm_0_x_parsing(self):
        raw = create_synthetic_vrm("0.x")
        with tempfile.NamedTemporaryFile(suffix=".vrm", delete=False) as f:
            f.write(raw)
            temp_path = Path(f.name)

        try:
            doc = VrmDocument.load(temp_path)
            self.assertEqual(doc.vrm_version, "0.x")
            self.assertEqual(doc.meta.get("title"), "Test Avatar 0.x")
            self.assertIn("hips", doc.human_bones)
            self.assertEqual(doc.human_bones["head"], 2)

            node_map = build_node_to_bone_map(doc)
            self.assertEqual(node_map[0], 1)
            self.assertEqual(node_map[2], 18)
        finally:
            temp_path.unlink()

    def test_destiny_coordinates(self):
        p = np.array([1.0, 2.0, 3.0])
        # glTF: +X right, +Y up, -Z forward
        # Destiny: -Z -> +X forward, -X -> +Y left, +Y -> +Z up
        destiny_p = to_destiny_space(p)
        self.assertAlmostEqual(destiny_p[0], -3.0)  # X forward = -Z
        self.assertAlmostEqual(destiny_p[1], -1.0)  # Y left = -X
        self.assertAlmostEqual(destiny_p[2], 2.0)   # Z up = +Y

    def test_profile_helpers(self):
        self.assertEqual(profile_id_from_name("Me Avatar"), "me_avatar")
        self.assertEqual(meta_author({"author": "Ada"}), "Ada")
        self.assertEqual(meta_author({"authors": ["Ada", "Grace"]}), "Ada, Grace")
        for path in game_models_dirs():
            self.assertFalse(path.as_posix().startswith("C:"), msg=str(path))

    def test_weld_collapses_duplicates(self):
        positions = np.array([[0, 0, 0], [0, 0, 0], [1, 0, 0]], dtype=np.float32)
        normals = np.array([[0, 0, 1], [0, 0, 1], [0, 0, 1]], dtype=np.float32)
        uvs = np.array([[0, 0], [0, 0], [1, 0]], dtype=np.float32)
        weights = np.ones((3, 4), dtype=np.float32)
        bones = np.ones((3, 4), dtype=np.int32)
        parts = [(0, np.array([[0, 1, 2]], dtype=np.int64))]
        welded_pos, _n, _uv, _w, _b, welded_parts = weld_mesh(
            positions, normals, uvs, weights, bones, parts
        )
        self.assertEqual(len(welded_pos), 2)
        self.assertEqual(welded_parts[0][1].shape, (1, 3))
        self.assertEqual(int(welded_parts[0][1].max()), 1)

    def test_fit_groups_to_carriers_merges_overflow(self):
        names = [f"mat_{i}" for i in range(11)]
        # 11 groups, decreasing triangle counts
        faces = []
        for i in range(11):
            faces.extend([i] * (11 - i))
        carriers, remapped, display = fit_groups_to_carriers(names, faces)
        self.assertLessEqual(len(carriers), len(CARRIER_SLOT_NAMES))
        self.assertEqual(len(carriers), 9)
        self.assertTrue(all(name in CARRIER_SLOT_NAMES for name in carriers))
        self.assertEqual(len(remapped), len(faces))
        self.assertLessEqual(max(remapped), 8)
        self.assertIn("+", display[0])

    def test_export_geometry_sidecars(self):
        raw = create_synthetic_vrm("1.0")
        with tempfile.NamedTemporaryFile(suffix=".vrm", delete=False) as handle:
            handle.write(raw)
            temp_path = Path(handle.name)
        try:
            doc = VrmDocument.load(temp_path)
            converter = VrmConverter(doc)
            with tempfile.TemporaryDirectory() as out_dir:
                out = Path(out_dir)
                manifest = converter.build_profile("TestAvatar", out)
                self.assertEqual(manifest["kind"], "full_body")
                self.assertEqual(manifest["slot"], "scatterhorn_chest")
                self.assertTrue((out / "character_body.obj").is_file())
                self.assertTrue((out / "character_body_weights.json").is_file())
                self.assertTrue((out / "character_body_frame.bin").is_file())
                self.assertTrue((out / "character_body_groups.json").is_file())
                groups = json.loads((out / "character_body_groups.json").read_text(encoding="utf-8"))
                self.assertEqual(groups["slots"][0], "GLSLShader85")
                self.assertEqual(len(groups["face_material"]), groups["triangles"])
                weights = json.loads((out / "character_body_weights.json").read_text(encoding="utf-8"))
                self.assertTrue(weights["retargeted"])
                self.assertEqual(len(weights["skins"]), manifest["vertex_count"])
                self.assertEqual(manifest["parts"][0]["carrier"], "GLSLShader85")
        finally:
            temp_path.unlink()


if __name__ == "__main__":
    unittest.main()
