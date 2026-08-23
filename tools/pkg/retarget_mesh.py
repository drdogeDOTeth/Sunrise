"""Poses the custom character onto the recovered Guardian rig, and exports its real weights.

Replaces `prepare_mesh.py`, which joined every mesh in the GLB, threw the armature away, and
left `inject_scatterhorn.py` to guess skinning by nearest donor vertex. Three things were wrong
with that, and all three are fixed here.

**The GLB's armature is a clean humanoid already in Destiny's axes and roughly its scale.** Its
legs and torso land within a few centimetres of the rig recovered by `skeleton.py`: custom knee
0.529 vs rig 0.548, custom hips 1.031 vs rig pelvis 1.060, custom head 1.624 vs rig 1.606. Only
the arms disagree, because the GLB is a T-pose and Destiny's bind pose is an A-pose with the
arms angled **forward** — shoulder `(-0.05, 0.19, 1.42)` to wrist `(0.30, 0.39, 1.15)`.

So this script poses **the arms only**. Retargeting the torso would be wrong: rig bone 5 sits at
y -0.120, so pelvis->spine points sideways, and aligning to it would tilt the whole body.

**Unrigged objects are dropped.** The GLB carries an `Icosphere` - 42 vertices, no vertex groups,
a 2 m sphere centred on the origin - which every inject so far has welded into the character.
Anything with no vertex groups is not part of the body.

**Weights come out with the mesh.** Every GLB vertex group maps to a rig bone index, so each
vertex keeps the weights its artist gave it instead of copying them off whichever Scatterhorn
vertex happened to be nearest. Fingers collapse onto the wrist because the rig's finger joints
are above the index ceiling of 28.

Usage (from tools/pkg):
    blender --background --python retarget_mesh.py -- 65535 out.obj --keep-seams --fingers --glb path.glb
"""
import json
import os
import sys

import bpy
from mathutils import Vector

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
HERE = os.path.dirname(os.path.abspath(__file__))
RIG = os.path.join(HERE, "objs", "skeleton", "rig.json")
DEFAULT_GLB = os.environ.get("SUNRISE_GLB", "")


def _take_opt(args, name, default=None):
    if name not in args:
        return default
    at = args.index(name)
    if at + 1 >= len(args):
        raise SystemExit(f"{name} needs a value")
    value = args[at + 1]
    del args[at:at + 2]
    return value


_ARGS = list(ARGV)
GLB = _take_opt(_ARGS, "--glb", DEFAULT_GLB)
BONE_MAP_FILE = _take_opt(_ARGS, "--bone-map")
MATERIAL_MAP_FILE = _take_opt(_ARGS, "--material-map")
RETARGET = "--no-retarget" not in _ARGS
KEEP_SEAMS = "--keep-seams" in _ARGS
FINGERS = "--fingers" in _ARGS
_POSITIONAL = [item for item in _ARGS if not item.startswith("--")]
if len(_POSITIONAL) < 2:
    raise SystemExit("usage: blender --background --python retarget_mesh.py -- "
                     "<target_verts> <out.obj> --glb path.glb [--keep-seams] [--fingers] "
                     "[--bone-map path] [--material-map path]")
TARGET = int(_POSITIONAL[0])
OUT = _POSITIONAL[1]
if not GLB or not os.path.isfile(GLB):
    raise SystemExit(
        "pass --glb path.glb (or set SUNRISE_GLB). "
        "retarget_mesh does not ship a character."
    )

# GLB vertex group -> rig bone index.
#
# The live map skipped bone 8 (spine_upper) and parked Neck on the chest (11). Mid-back verts
# then blended 5↔11 across the missing joint, which is the slide/stretch when the pawn bends.
# The GLB has no UpperChest, so Chest is split 8/11 by vertex height after the pose bake.
# Neck is 13. Do not pose the torso to line up with bone 5 — that bone sits at y −0.120.
BONE_MAP = {
    "Hips": 1, "Spine": 5, "Neck": 13, "Head": 18,
    "Jaw": 18, "LeftEye": 18, "RightEye": 18,
    "Left shoulder": 27, "Left arm": 15, "Left elbow": 19, "Left wrist": 21,
    "Right shoulder": 28, "Right arm": 17, "Right elbow": 20, "Right wrist": 22,
    "Left leg": 3, "Left knee": 6, "Left ankle": 9, "Left toe": 25,
    "Right leg": 4, "Right knee": 7, "Right ankle": 10, "Right toe": 26,
}
# Chest is not a single joint. Split between spine_upper (8) and chest (11) by Destiny Z.
CHEST_GROUP = "Chest"
SPINE_UPPER = 8
CHEST_BONE = 11

# Gauntlet finger indices, recovered from Scatterhorn co-weights (see recover_finger_joints.py).
# 34/39 are the glove-back pad, not a digit — leave them unused. Each non-thumb finger is a
# two-joint chain; GLB tips fold onto the distal joint. Off unless --fingers: those indices
# sit above BODY_BONE_CEILING and the chest draw has never posed them without needles.
FINGER_MAP = {
    "Thumb0_L": 45, "Thumb1_L": 56, "Thumb2_L": 66,
    "IndexFinger1_L": 40, "IndexFinger2_L": 52, "IndexFinger3_L": 52,
    "MiddleFinger1_L": 42, "MiddleFinger2_L": 53, "MiddleFinger3_L": 53,
    "LittleFinger1_L": 43, "LittleFinger2_L": 54, "LittleFinger3_L": 54,
    "Thumb0_R": 51, "Thumb1_R": 61, "Thumb2_R": 71,
    "IndexFinger1_R": 46, "IndexFinger2_R": 57, "IndexFinger3_R": 57,
    "MiddleFinger1_R": 48, "MiddleFinger2_R": 58, "MiddleFinger3_R": 58,
    "LittleFinger1_R": 49, "LittleFinger2_R": 59, "LittleFinger3_R": 59,
}
WRIST_FOLD = {name: (21 if name.endswith("_L") else 22) for name in FINGER_MAP}


def _load_json_map(path):
    if not path:
        return {}, {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict) and "groups" in data:
        groups = dict(data["groups"])
        fingers = dict(data.get("fingers") or {})
        chest = data.get("chest_group")
        if chest:
            global CHEST_GROUP
            CHEST_GROUP = chest
        return groups, fingers
    return dict(data), {}


if BONE_MAP_FILE:
    extra_groups, extra_fingers = _load_json_map(BONE_MAP_FILE)
    BONE_MAP.update(extra_groups)
    FINGER_MAP.update(extra_fingers)
    WRIST_FOLD = {name: (21 if name.endswith("_L") else 22) for name in FINGER_MAP}

if FINGERS:
    BONE_MAP.update(FINGER_MAP)
else:
    BONE_MAP.update(WRIST_FOLD)

MATERIAL_RENAME = {}
if MATERIAL_MAP_FILE:
    with open(MATERIAL_MAP_FILE, encoding="utf-8") as handle:
        MATERIAL_RENAME = json.load(handle)

# (pose bone, rig joint at its head, rig joint at its tail). Parent first - posing the upper arm
# moves the elbow, so the elbow has to be aligned against where it ends up.
ARM_CHAINS = [
    ("Left arm", 27, 19), ("Left elbow", 19, 21),
    ("Right arm", 28, 20), ("Right elbow", 20, 22),
]
MAX_INFLUENCES = 4


def to_destiny(p):
    """Blender world -> Destiny (x forward, y left, z up). A +90 deg turn about Z."""
    return Vector((-p.y, p.x, p.z))


def to_blender(d):
    """Destiny -> Blender world. The inverse turn."""
    return Vector((d[1], -d[0], d[2]))


def load_rig():
    with open(RIG) as fh:
        data = json.load(fh)
    return {joint["bone"]: Vector(joint["position"]) for joint in data["joints"]}


def align(armature, name, direction_world):
    """Rotate one pose bone about its head so it points along `direction_world`."""
    pose_bone = armature.pose.bones[name]
    world = armature.matrix_world @ pose_bone.matrix
    current = (world.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
    turn = current.rotation_difference(direction_world.normalized())
    head = world.translation.copy()
    posed = (turn.to_matrix() @ world.to_3x3()).to_4x4()
    posed.translation = head
    pose_bone.matrix = armature.matrix_world.inverted() @ posed
    bpy.context.view_layer.update()
    return current, direction_world.normalized()


def corner_normal(mesh, loop, index):
    """Blender moved split normals off MeshLoop; take whichever this build offers."""
    corners = getattr(mesh, "corner_normals", None)
    if corners is not None and len(corners):
        return Vector(corners[index].vector)
    return Vector(loop.normal)


def write_frame(obj, mesh, matrix, path):
    """Write the tangent frame Destiny's second vertex buffer wants, one row per vertex.

    Nine float32 per vertex: u, v, normal xyz, tangent xyz, handedness. In Destiny axes, and
    packed into the stride-24 layout by `inject_scatterhorn.py`, which owns the quantisation
    because the scale and translation live in the model header it rewrites.

    UV and tangent frame are **per face corner** in Blender and **per vertex** in Destiny, so a
    vertex sitting on a UV seam has to pick one of its corners. That is a real limit of welding
    the mesh down to 23,512: the GLB's 61,908 unwelded vertices would fit under the 65,535 index
    ceiling and give exact seams, which is the upgrade if seam smear ever shows.
    """
    import array

    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        print("WARNING no UV layer on the joined mesh; skipping the tangent frame")
        return
    try:
        mesh.calc_tangents(uvmap=uv_layer.name)
    except Exception as error:  # noqa: BLE001 - Blender raises bare RuntimeError here
        print(f"WARNING calc_tangents failed ({error}); skipping the tangent frame")
        return

    rotation = matrix.to_3x3()
    rows = [None] * len(mesh.vertices)
    seams = 0
    for loop in mesh.loops:
        vertex = loop.vertex_index
        uv = uv_layer.data[loop.index].uv
        if rows[vertex] is not None:
            if (abs(rows[vertex][0] - uv[0]) > 1e-4
                    or abs(rows[vertex][1] - (1.0 - uv[1])) > 1e-4):
                seams += 1
            continue
        normal = to_destiny((rotation @ corner_normal(mesh, loop, loop.index)).normalized())
        tangent = to_destiny((rotation @ Vector(loop.tangent)).normalized())
        # Blender's UV origin is bottom left; Destiny's texcoords run top down like every other
        # DirectX-era engine, so v is flipped. This is the one part of the layout that geometry
        # cannot confirm - if the texture lands upside down, this line is why.
        rows[vertex] = (uv[0], 1.0 - uv[1],
                        normal.x, normal.y, normal.z,
                        tangent.x, tangent.y, tangent.z,
                        float(loop.bitangent_sign))

    missing = sum(1 for row in rows if row is None)
    for index, row in enumerate(rows):
        if row is None:
            rows[index] = (0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0)

    flat = array.array("f", [value for row in rows for value in row])
    with open(path, "wb") as fh:
        flat.tofile(fh)
    print(f"wrote {path}: {len(rows):,} vertices x 9 float32 "
          f"(u v nx ny nz tx ty tz sign)")
    if seams:
        print(f"  {seams:,} face corners disagreed with their vertex's UV (seams welded shut)")
    if missing:
        print(f"  WARNING {missing:,} vertices had no face corner; parked at uv 0,0")


def write_groups(obj, mesh, path):
    """Record which source material each triangle belongs to.

    The custom model has **five materials, each with its own UV atlas** - tank top, gas mask,
    necklace, skin, and the twirl - and a Destiny material samples one texture set. So the body
    cannot wear its own textures through the two carrier parts the injector currently writes: it
    needs one part per source material, each pointing at a material we control.

    Triangle order here is `mesh.polygons` order, which is exactly the OBJ's face order, so the
    injector can sort faces into contiguous per-material index ranges without re-deriving
    anything. Written even when the texture work is not done yet, because it costs nothing and
    the alternative is rebuilding the mesh later just to learn this.
    """
    slots = [material.name if material else "<none>" for material in obj.data.materials]
    if MATERIAL_RENAME:
        slots = [MATERIAL_RENAME.get(name, name) for name in slots]
    faces = [poly.material_index for poly in mesh.polygons if len(poly.vertices) == 3]
    counts = {}
    for index in faces:
        counts[index] = counts.get(index, 0) + 1
    with open(path, "w") as fh:
        json.dump({
            "note": ("Per-triangle source material, in the OBJ's face order. One Destiny part "
                     "per group is what lets each group keep its own texture set."),
            "slots": slots,
            "triangles": len(faces),
            "face_material": faces,
        }, fh)
    print(f"wrote {path}: {len(slots)} material groups over {len(faces):,} triangles")
    for index, name in enumerate(slots):
        print(f"  [{index}] {name:<20} {counts.get(index, 0):>7,} tris")


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=GLB)

    loose = [o for o in bpy.data.objects if o.type == 'MESH' and not o.vertex_groups]
    for o in loose:
        lo = min((o.matrix_world @ v.co).z for v in o.data.vertices)
        hi = max((o.matrix_world @ v.co).z for v in o.data.vertices)
        print(f"dropping unrigged '{o.name}': {len(o.data.vertices)} verts, z {lo:.2f}..{hi:.2f}")
        bpy.data.objects.remove(o, do_unlink=True)

    armature = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    print(f"kept {len(meshes)} rigged meshes: "
          + ", ".join(f"{o.name}({len(o.data.vertices):,})" for o in meshes))

    if RETARGET:
        rig = load_rig()
        bpy.context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode='POSE')
        for name, head_bone, tail_bone in ARM_CHAINS:
            if head_bone not in rig or tail_bone not in rig:
                print(f"  no rig joints for {name}; left in T-pose")
                continue
            target = to_blender(rig[tail_bone] - rig[head_bone])
            was, now = align(armature, name, target)
            angle = was.angle(now)
            print(f"  {name:>14}: rig {head_bone}->{tail_bone}, turned {angle * 57.2958:5.1f} deg")
        bpy.ops.object.mode_set(mode='OBJECT')
    else:
        print("--no-retarget: mesh stays in its T-pose")

    # Applying the armature modifier bakes the pose into the mesh. Vertex groups survive it,
    # and survive the decimate below, which is what lets the weights come out at the end.
    for o in meshes:
        bpy.context.view_layer.objects.active = o
        # Shape keys have to go first - Blender refuses to apply any modifier while they exist.
        if o.data.shape_keys:
            o.shape_key_clear()
        for modifier in list(o.modifiers):
            if modifier.type == 'ARMATURE':
                bpy.ops.object.modifier_apply(modifier=modifier.name)

    for o in bpy.data.objects:
        o.select_set(False)
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.join()
    joined = bpy.context.view_layer.objects.active

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    if KEEP_SEAMS:
        # Destiny stores one UV/normal per vertex. Welding 61k GLB verts down to 23,512 forced
        # 7,785 face corners to share a neighbour's UV — those are the visible weld seams.
        # The unwelded mesh still fits under the 65,535 R16 index ceiling.
        print("keep-seams: skipping remove_doubles")
    else:
        bpy.ops.mesh.remove_doubles(threshold=0.0001)
    bpy.ops.mesh.quads_convert_to_tris(quad_method='BEAUTY', ngon_method='BEAUTY')
    bpy.ops.object.mode_set(mode='OBJECT')
    print(f"joined{'+seams-kept' if KEEP_SEAMS else '+welded'}: "
          f"{len(joined.data.vertices):,} verts, {len(joined.data.polygons):,} tris")

    while joined.modifiers:
        bpy.ops.object.modifier_apply(modifier=joined.modifiers[0].name)

    if KEEP_SEAMS:
        if len(joined.data.vertices) > 0xFFFF:
            raise RuntimeError(f"{len(joined.data.vertices):,} verts need 32-bit indices")
        print(f"keep-seams: not decimating ({len(joined.data.vertices):,} <= 65535)")
    else:
        passes = 0
        while len(joined.data.vertices) > TARGET:
            passes += 1
            if passes > 8:
                raise RuntimeError(f"still {len(joined.data.vertices):,} verts; target {TARGET:,}")
            modifier = joined.modifiers.new(name="dec", type='DECIMATE')
            modifier.decimate_type = 'COLLAPSE'
            modifier.ratio = max(0.05, TARGET / max(len(joined.data.vertices), 1) * 0.92)
            bpy.ops.object.modifier_apply(modifier="dec")
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.quads_convert_to_tris(quad_method='BEAUTY', ngon_method='BEAUTY')
            bpy.ops.object.mode_set(mode='OBJECT')
            print(f"decimated: {len(joined.data.vertices):,} verts, {len(joined.data.polygons):,} tris")

    mesh = joined.data
    matrix = joined.matrix_world
    group_bone = {}
    chest_groups = set()
    unmapped = set()
    for group in joined.vertex_groups:
        if group.name == CHEST_GROUP:
            chest_groups.add(group.index)
        elif group.name in BONE_MAP:
            group_bone[group.index] = BONE_MAP[group.name]
        else:
            unmapped.add(group.name)
    if unmapped:
        print(f"unmapped vertex groups (ignored): {sorted(unmapped)}")
    with open(RIG, encoding="utf-8") as fh:
        rig_z = {joint["bone"]: joint["position"][2] for joint in json.load(fh)["joints"]}
    z_upper = float(rig_z[SPINE_UPPER])
    z_chest = float(rig_z[CHEST_BONE])
    z_span = max(z_chest - z_upper, 1e-4)
    print(f"chest split: bone {SPINE_UPPER} at z {z_upper:.3f} -> "
          f"bone {CHEST_BONE} at z {z_chest:.3f}")
    print(f"fingers: {'gauntlet joints 34-71' if FINGERS else 'folded onto wrists 21/22'}")

    lo = [1e9] * 3
    hi = [-1e9] * 3
    skins = []
    unweighted = 0
    for vertex in mesh.vertices:
        point = to_destiny(matrix @ vertex.co)
        for axis in range(3):
            lo[axis] = min(lo[axis], point[axis])
            hi[axis] = max(hi[axis], point[axis])
        totals = {}
        for element in vertex.groups:
            if element.weight <= 0.0:
                continue
            if element.group in chest_groups:
                t = min(1.0, max(0.0, (point.z - z_upper) / z_span))
                totals[SPINE_UPPER] = totals.get(SPINE_UPPER, 0.0) + element.weight * (1.0 - t)
                totals[CHEST_BONE] = totals.get(CHEST_BONE, 0.0) + element.weight * t
                continue
            bone = group_bone.get(element.group)
            if bone is not None:
                totals[bone] = totals.get(bone, 0.0) + element.weight
        best = sorted(totals.items(), key=lambda item: -item[1])[:MAX_INFLUENCES]
        total = sum(weight for _bone, weight in best)
        if total <= 0.0:
            unweighted += 1
            skins.append([[1, 255]])
            continue
        quantized = [[bone, int(round(weight / total * 255.0))] for bone, weight in best]
        quantized[0][1] += 255 - sum(w for _b, w in quantized)
        skins.append([[b, w] for b, w in quantized if w > 0])

    with open(OUT, "w") as fh:
        for vertex in mesh.vertices:
            point = to_destiny(matrix @ vertex.co)
            fh.write(f"v {point.x:.6f} {point.y:.6f} {point.z:.6f}\n")
        for poly in mesh.polygons:
            if len(poly.vertices) == 3:
                a, b, c = poly.vertices
                fh.write(f"f {a + 1} {b + 1} {c + 1}\n")

    write_frame(joined, mesh, matrix, os.path.splitext(OUT)[0] + "_frame.bin")
    write_groups(joined, mesh, os.path.splitext(OUT)[0] + "_groups.json")

    weights_path = os.path.splitext(OUT)[0] + "_weights.json"
    with open(weights_path, "w") as fh:
        json.dump({
            "note": ("Per-vertex skinning taken from the GLB armature and mapped onto the rig's "
                     "global bone indices. Weights are 0-255 and sum to 255. Vertex order "
                     "matches the OBJ beside this file exactly."),
            "retargeted": RETARGET,
            "keep_seams": KEEP_SEAMS,
            "fingers": FINGERS,
            "vertices": len(skins),
            "skins": skins,
        }, fh)

    used = sorted({bone for skin in skins for bone, _weight in skin})
    print(f"\nwrote {OUT}: {len(mesh.vertices):,} verts, {len(mesh.polygons):,} tris")
    print(f"wrote {weights_path}: joints used {used}")
    if unweighted:
        print(f"WARNING {unweighted:,} vertices had no mapped weight; parked on bone 1")
    print(f"destiny aabb  x {lo[0]:.3f}..{hi[0]:.3f}  y {lo[1]:.3f}..{hi[1]:.3f}  "
          f"z {lo[2]:.3f}..{hi[2]:.3f}")


main()
