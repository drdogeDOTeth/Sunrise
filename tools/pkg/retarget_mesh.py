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

**The hand is retargeted too, since 2026-08-23.** The arm chain alone left each wrist 4.9 cm
from the joint that drives it, because the GLB's upper arm hangs from its own shoulder rather
than the rig's, and the game rotates the hand about *its* joint - so that gap acted as a lever
and the hand read as screwed on at the wrong angle. `fit_hands()` re-aims and scales the forearm
onto the real wrist joint and turns the hand so its knuckles fan like the donor's. Pass
`--no-fit-hands` to get the old behaviour. Targets come from `hand_targets.py`.

Usage (from tools/pkg):
    blender --background --python retarget_mesh.py -- 65535 out.obj --keep-seams --fingers --glb path.glb
    blender --background --python retarget_mesh.py -- ... --no-fit-hands
"""
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

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
# Which skeleton to pose onto. The default is armour space and estimated; an NPC body needs
# `objs/skeleton/rig_body_space.json`, which is exact and indexed differently - armour puts knees at
# 6/7 and ankles at 9/10 where a body puts them at 5/6 and 8/9, and armour's 18 is the head where a
# body's 18 is an elbow. Pass the matching `--bone-map bone_map_body_space.json` with it; using one
# without the other weights the character to the wrong limbs. See `make_body_rig.py`.
RIG = _take_opt(_ARGS, "--rig", RIG)
MATERIAL_MAP_FILE = _take_opt(_ARGS, "--material-map")
RETARGET = "--no-retarget" not in _ARGS
KEEP_SEAMS = "--keep-seams" in _ARGS
FINGERS = "--fingers" in _ARGS
FIT_HANDS = "--no-fit-hands" not in _ARGS
# Scale the source onto the rig before posing it. Off by default: a GLB already authored against
# `rig.json` (the v25 body) measures ~1.0 on every segment and must not be disturbed. A model
# authored at its own scale needs it, and without it the mesh is weighted to a skeleton it does
# not overlap - SchizoAxe's hips sat 0.57 m below rig joint 1.
FIT_PROPORTIONS = "--fit-proportions" in _ARGS
_BLEND_OPT = _take_opt(_ARGS, "--proportion-blend")
PROPORTION_BLEND = float(_BLEND_OPT) if _BLEND_OPT else 1.0
# Per-chain overrides, e.g. `--chain-blend legs=0,arms=1`. Chains named in FIT_CHAINS.
CHAIN_BLEND = {}
_CHAIN_OPT = _take_opt(_ARGS, "--chain-blend")
if _CHAIN_OPT:
    for _item in _CHAIN_OPT.split(","):
        _name, _, _value = _item.partition("=")
        CHAIN_BLEND[_name.strip()] = float(_value)
# How much of each segment's *lengthwise* stretch to repeat around the limb. The proportion fit
# moves joints apart and the armature stretches the mesh between them, which lengthens a limb
# without widening it - that is exactly what "stilt legs" are. 1.0 scales girth by the same factor
# as length (a uniform limb, which is what the rig's armour is built for), 0.0 is the old
# behaviour. Only meaningful with --fit-proportions; there is no stretch to answer otherwise.
# Scale the head about its own joint, blended by each vertex's head weight so the neck does not
# pop. 1.0 is off. This is the knob a chibi actually needs: measured on SchizoAxe, **64.7% of the
# model is head-dominant** and it reaches 0.784 m above the head joint, so fitting the skeleton to
# human height is what produced a 2.39 m character - the head, not the legs, is the whole of the
# proportion mismatch. Independent of --fit-proportions; useful in both modes.
_HEAD_OPT = _take_opt(_ARGS, "--head-scale")
HEAD_SCALE = float(_HEAD_OPT) if _HEAD_OPT else 1.0
_GIRTH_OPT = _take_opt(_ARGS, "--limb-girth")
LIMB_GIRTH = float(_GIRTH_OPT) if _GIRTH_OPT else 1.0
# Filled by fit_segments: deforming bone name -> how much its segment was stretched lengthwise.
SEGMENT_STRETCH = {}
# The only segments a radial scale is meaningful on: the four limb tubes, named by the joint at
# their near end. A limb really is roughly a cylinder about its bone, so widening it with its
# length is right. Everything else the fit touches is not, and measuring proved it: the foot
# scored x2.251 (ankle->toe is not the foot's shape - the toes overhang it, so this is clown
# feet), and hips/neck/spine are body, not tubes, where a radial scale pinches or barrels the
# torso. Those segments still stretch lengthwise; they simply get no girth answer.
GIRTH_SEGMENTS = {
    "Left arm", "Right arm",       # upper arm: shoulder ball -> elbow
    "Left elbow", "Right elbow",   # forearm:   elbow -> wrist
    "Left leg", "Right leg",       # thigh:     hip -> knee
    "Left knee", "Right knee",     # shin:      knee -> ankle
}
HAND_TARGETS = os.path.join(HERE, "objs", "skeleton", "hand_targets.json")

# Artistic roll, applied after the fit: degrees about the forearm axis (pronation - the palm-up to
# palm-down motion). The fit itself puts the hand on Destiny's own frame to 0.1 deg, so anything
# here is taste, not correctness. **The sign matches the preview panel labels**, so pick a value by
# looking rather than by reasoning about the right-hand rule - no launch needed:
#     python hand_view.py out.png --side back --axis forearm --angles -40,-20,0,20,40
# User picked -25 on the left hand, 2026-08-23, off exactly that sheet. Right hand left alone.
HAND_ROLL = {"L": -25.0, "R": 0.0}
_ROLL_OPT = _take_opt(_ARGS, "--hand-roll")
if _ROLL_OPT:
    for _item in _ROLL_OPT.split(","):
        _side, _, _value = _item.partition("=")
        HAND_ROLL[_side.strip().upper()] = float(_value)
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
# Chest is not a single joint. Split between spine_upper and chest by Destiny Z. These default to
# the armour space's 8 and 11; a bone map may override them with "Chest lower" / "Chest upper",
# which body space must, since its 8 is the left ANKLE and its 11 a collar. Weighting the chest to
# an ankle produces a model that looks correct until it animates.
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
WRIST_FOLD = {name: BONE_MAP["Left wrist" if name.endswith("_L") else "Right wrist"]
              for name in FINGER_MAP}
# Canonical pose-bone name -> the name this armature actually uses, supplied by the bone map.
# `BONE_MAP` above is keyed by *vertex group*; the pose step below indexes the **armature**, which
# is a different namespace, and a VRM out of Blender satisfies neither by accident - its bones are
# `upper_arm.L`, not "Left arm". Empty for a rig that already uses the canonical names.
BONE_RENAME = {}



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
        bones = data.get("bones")
        if bones:
            global BONE_RENAME
            BONE_RENAME = dict(bones)
        return groups, fingers
    return dict(data), {}


if BONE_MAP_FILE:
    extra_groups, extra_fingers = _load_json_map(BONE_MAP_FILE)
    BONE_MAP.update(extra_groups)
    FINGER_MAP.update(extra_fingers)
    # Everything below is *derived* from BONE_MAP rather than restated, because a second
    # hand-written table is exactly how the upper arm once ended up spanning 27->19 instead of
    # 15->19 (noted below), and how a body-space run weighted the chest to bone 8 - its ankle.
    WRIST_FOLD = {name: BONE_MAP["Left wrist" if name.endswith("_L") else "Right wrist"]
                  for name in FINGER_MAP}
    SPINE_UPPER = extra_groups.get("Chest lower", SPINE_UPPER)
    CHEST_BONE = extra_groups.get("Chest upper", CHEST_BONE)

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
    ("Left arm", BONE_MAP["Left shoulder"], BONE_MAP["Left elbow"]),
    ("Left elbow", BONE_MAP["Left elbow"], BONE_MAP["Left wrist"]),
    ("Right arm", BONE_MAP["Right shoulder"], BONE_MAP["Right elbow"]),
    ("Right elbow", BONE_MAP["Right elbow"], BONE_MAP["Right wrist"]),
]
# Consecutive bone pairs down each arm. A *length* is measured across a pair, which ARM_CHAINS
# cannot express: it names the bone and the joints it points between, not the bone below it that
# terminates the segment. The rig joints come from BONE_MAP rather than a second hand-written
# table - measured 2026-08-25, a copied table had the upper arm spanning 27->19 (0.228 m) when
# BONE_MAP puts that bone on joint 15, making the real segment 15->19 (0.081 m).
# Every limb top-down, walked parent before child so a bone is seated before its own child is.
# The rig joint per bone comes from BONE_MAP rather than a second hand-written table - measured
# 2026-08-25, a copied table had the upper arm spanning 27->19 (0.228 m) when BONE_MAP puts that
# bone on joint 15, making the real segment 15->19 (0.081 m).
#
# The legs and spine are here for the same reason the arms are: fitting arms alone left the feet
# floating 42.5 cm over the rig's toe joint, because `fit_scale` seats the hips and a chibi's
# legs are far shorter than the frame below those hips.
# "Chest" is deliberately absent - it is the one group split across two joints by height, so it
# has no single joint to seat.
# Named so one chain can be fitted harder than another: a source whose arms nearly match the rig
# but whose legs are half its length has no single blend that suits both.
# `--chain-blend legs=0,arms=1` overrides per chain.
FIT_CHAINS = [
    ("spine", ["Hips", "Spine", "Neck", "Head"]),
    ("arms", ["Left shoulder", "Left arm", "Left elbow", "Left wrist"]),
    ("arms", ["Right shoulder", "Right arm", "Right elbow", "Right wrist"]),
    ("legs", ["Left leg", "Left knee", "Left ankle", "Left toe"]),
    ("legs", ["Right leg", "Right knee", "Right ankle", "Right toe"]),
]
MAX_INFLUENCES = 4


# The hand fit. `ARM_CHAINS` aims each bone from the *rig's* joint, but the GLB's upper arm hangs
# from its own shoulder — ~4.8 cm inboard of rig joint 27 — so the error is inherited all the way
# down and the wrist lands 4.9 cm from the joint that drives it. The game rotates our hand about
# **its** joint, so that gap is a lever arm: every wrist bend swings the hand ~5 cm wide of the
# forearm and reads as a hand screwed on at the wrong angle (user, 2026-08-23, both first person
# and the character screen).
#
# Two corrections per side, and deliberately no more:
#   1. Re-aim the forearm at the real wrist joint from wherever our elbow actually is, then scale
#      it along its own length so the tail lands *on* that joint. The arm above the elbow is
#      untouched, so the confirmed silhouette survives.
#   2. Rotate the hand so its knuckle fan matches the donor's. Rotation only - our knuckles sit
#      31% closer to the wrist and our fingers run 8 cm longer than Destiny's, and that is the
#      character's hand, not a defect. Scaling to match would trade one wrong hand for another.
HAND_FIT = [
    ("L", "Left elbow", "Left wrist",
     {"index": "IndexFinger1_L", "middle": "MiddleFinger1_L",
      "little": "LittleFinger1_L", "thumb": "Thumb0_L"}),
    ("R", "Right elbow", "Right wrist",
     {"index": "IndexFinger1_R", "middle": "MiddleFinger1_R",
      "little": "LittleFinger1_R", "thumb": "Thumb0_R"}),
]

if BONE_RENAME:
    # Applied to the tables once, so every lookup below already holds a real armature bone name.
    ARM_CHAINS = [(BONE_RENAME.get(name, name), head, tail) for name, head, tail in ARM_CHAINS]
    HAND_FIT = [(side,
                 BONE_RENAME.get(forearm, forearm),
                 BONE_RENAME.get(wrist, wrist),
                 {key: BONE_RENAME.get(bone, bone) for key, bone in knuckles.items()})
                for side, forearm, wrist, knuckles in HAND_FIT]
    print(f"bone rename: {len(BONE_RENAME)} canonical names mapped onto this armature")



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


def armature_bone(canonical):
    """@return The name this armature uses for a canonical bone."""
    return BONE_RENAME.get(canonical, canonical)


def orient_source(armature, meshes):
    """Turn the source to face Destiny's forward if it is authored facing the other way.

    **Measured, not assumed from the spec.** SchizoAxe is a VRM 0.x that faces -Z where the format
    says +Z, and the retarget's axis convention (`to_destiny`) takes glTF +Z to Destiny +x. The
    result was a character standing correctly with its face on the back of its head.

    The test uses the toes: they sit ahead of the ankle on any humanoid, and both joints come from
    the file's own humanoid table, so nothing has to guess which mesh is a face. Destiny forward is
    Blender -y, so toes at a *greater* y than the ankle means the model is backwards.
    @return True when a half turn was applied.
    """
    bones = armature.data.bones
    offsets = []
    for ankle, toe in (("Left ankle", "Left toe"), ("Right ankle", "Right toe")):
        a, t = armature_bone(ankle), armature_bone(toe)
        if a in bones and t in bones:
            offsets.append(bones[t].head_local.y - bones[a].head_local.y)
    if not offsets:
        print("  no ankle/toe pair; facing left alone")
        return False
    forward = sum(offsets) / len(offsets)
    if forward <= 0.0:
        print(f"  facing ok (toes {-forward * 100:+.1f} cm ahead of the ankle)")
        return False
    print(f"  source faces backwards (toes {forward * 100:+.1f} cm behind the ankle); turning 180 deg")
    if meshes:
        bpy.ops.object.select_all(action='DESELECT')
        for mesh in meshes:
            mesh.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        bpy.ops.object.parent_clear(type='CLEAR_KEEP_TRANSFORM')
    turn = Matrix.Rotation(math.pi, 4, 'Z')
    for obj in [armature, *meshes]:
        obj.matrix_world = turn @ obj.matrix_world
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='DESELECT')
    for obj in [armature, *meshes]:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.object.select_all(action='DESELECT')
    for mesh in meshes:
        mesh.select_set(True)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.parent_set(type='ARMATURE_NAME')
    return True


def fit_scale(armature, meshes, rig):
    """Uniformly scale the source onto the rig, then seat its hips on rig joint 1.

    Ported from the RDR2 pipeline's `align_scale_and_origin`. **Hips->head rise is the yardstick**
    rather than overall height, because it is the measurement least affected by the difference
    between the source's T-pose and Destiny's A-pose - both are vertical either way - and because
    total height is dominated by whatever the character has on its head.

    Without this the mesh keeps its authored scale while the weights address a rig it does not
    overlap. Nothing needed it until now: the v25 body was modelled against `rig.json`.
    @return The scale applied.
    """
    hips, head = armature_bone("Hips"), armature_bone("Head")
    bones = armature.data.bones
    if hips not in bones or head not in bones:
        print(f"  no '{hips}'/'{head}' bone; scale left alone")
        return 1.0
    source_hips = bones[hips].head_local.copy()
    source_rise = bones[head].head_local.z - source_hips.z
    rig_hips = to_blender(rig[1])
    rig_rise = to_blender(rig[18]).z - rig_hips.z
    if abs(source_rise) < 1e-6:
        return 1.0
    scale = rig_rise / source_rise
    offset = rig_hips - (source_hips * scale)
    print(f"  hips->head rise: source {source_rise:.3f} rig {rig_rise:.3f} -> scale x{scale:.4f}")
    print(f"  hips seated at rig joint 1: offset ({offset.x:+.3f}, {offset.y:+.3f}, {offset.z:+.3f})")

    # glTF parents the skinned meshes to the armature, so a transform set on both would apply
    # twice. Detach first, keeping the world transform.
    if meshes:
        bpy.ops.object.select_all(action='DESELECT')
        for mesh in meshes:
            mesh.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        bpy.ops.object.parent_clear(type='CLEAR_KEEP_TRANSFORM')

    transform = Matrix.Translation(offset) @ Matrix.Scale(scale, 4)
    for obj in [armature, *meshes]:
        obj.matrix_world = transform @ obj.matrix_world
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='DESELECT')
    for obj in [armature, *meshes]:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    # Re-parent so the pose still drives the meshes when it is baked further down.
    bpy.ops.object.select_all(action='DESELECT')
    for mesh in meshes:
        mesh.select_set(True)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.parent_set(type='ARMATURE_NAME')
    return scale


def fit_segments(armature, rig, blend=1.0):
    """Scale each arm segment to the rig's own length for it, parent before child.

    Ported from the RDR2 pipeline's per-segment fit. The factor is geometric
    (`(target / current) ** blend`) so the blend is symmetric between a segment that must grow and
    one that must shrink, and 0 is exactly 1.0 everywhere.

    This is what `fit_hands` was doing single-handed: with no upper-arm fit and no uniform scale,
    the whole error landed on the forearm as one x4.9 stretch that still left the wrist 55 cm out.
    @return Segments fitted.
    """
    fitted = 0
    world = armature.matrix_world

    def snapshot():
        """World head of every bone this fit touches, so the stretch can be measured, not assumed."""
        out = {}
        for _, chain in FIT_CHAINS:
            for canonical in chain:
                bone = armature.pose.bones.get(armature_bone(canonical))
                if bone is not None:
                    out[canonical] = (world @ bone.matrix).translation.copy()
        return out

    before_fit = snapshot()

    def place(canonical, joint, amount):
        """Move one bone's head toward its rig joint. Rigid: its children come with it."""
        bone = armature.pose.bones.get(armature_bone(canonical))
        if bone is None or joint not in rig:
            return None
        matrix = world @ bone.matrix
        before = matrix.translation.copy()
        wanted = to_blender(rig[joint])
        if amount < 1.0:
            wanted = before.lerp(wanted, amount)
        matrix.translation = wanted
        bone.matrix = world.inverted() @ matrix
        bpy.context.view_layer.update()
        return (wanted - before).length

    # Every joint is placed on the rig's own joint, root first, so each is exact rather than
    # accumulated from a direction and a length. Destiny's rig carries all four arm joints
    # (27 shoulder, 15 upper arm, 19 elbow, 21 wrist), so there is nothing left to infer.
    #
    # Aiming and scaling cannot get here on this source. ARM_CHAINS aims the upper arm along
    # 27->19 while BONE_MAP binds that bone to joint 15, so the aim and the segment disagree; and
    # glTF stores no bone tails, so `parent.scale.y` stretches along a synthesised +Z axis sitting
    # 89 deg off the arm (measured 2026-08-25: the child moved 0.1946 -> 0.1946, not at all).
    # Placement is indifferent to both. The existing aim still sets how the limb is oriented about
    # its own axis; only position is taken over here.
    for group, chain in FIT_CHAINS:
        for canonical in chain:
            joint = BONE_MAP.get(canonical)
            moved = place(canonical, joint, CHAIN_BLEND.get(group, blend))
            if moved is None:
                print(f"  {canonical:>15}  no bone or no rig joint {joint}; skipped")
                continue
            fitted += 1
            print(f"  {canonical:>15} -> rig joint {joint:<3} moved {moved * 100:6.2f} cm")

    # Record how far each segment was stretched lengthwise, so thicken_limbs can repeat it around
    # the limb. Measured from the bones themselves before and after, not inferred from the rig:
    # a blend below 1.0 means a segment lands short of its target and must not claim the full
    # factor. Keyed by the bone that deforms the segment, which is the one at its near end.
    after_fit = snapshot()
    for _, chain in FIT_CHAINS:
        # One factor for the whole limb, not one per segment. Per-segment girth amplifies the rig's
        # least-trustworthy joint: the upper-arm estimate (37 and 47 samples, and a humerus reading
        # 12.20 cm against a 26.44 cm forearm, which is backwards for a human) drove the upper arm
        # to x0.627 while the forearm went to x1.748 - Popeye arms out of one bad joint. Summing the
        # limb cancels an error in where its internal joints sit, and "a limb is a uniform tube" is
        # a claim about the limb anyway, not about each bone in it.
        limb = [(near, far) for near, far in zip(chain, chain[1:])
                if near in GIRTH_SEGMENTS and near in before_fit and far in before_fit]
        if not limb:
            continue
        source = sum((before_fit[far] - before_fit[near]).length for near, far in limb)
        fitted = sum((after_fit[far] - after_fit[near]).length for near, far in limb)
        if source <= 1e-6 or fitted <= 1e-6:
            continue
        for near, _ in limb:
            SEGMENT_STRETCH[armature_bone(near)] = fitted / source

    # Report the thing that actually matters, measured rather than inferred from the factors.
    for canonical in ("Left wrist", "Right wrist", "Left ankle", "Right ankle"):
        bone = armature.pose.bones.get(armature_bone(canonical))
        joint = BONE_MAP.get(canonical)
        if bone is None or joint not in rig:
            continue
        error = ((world @ bone.matrix).translation - to_blender(rig[joint])).length
        print(f"  {canonical} landed {error * 100:.2f} cm from rig joint {joint}")
    return fitted


def scale_head(armature, meshes, factor):
    """Scale head-weighted vertices about the head joint. Call after the bake.

    A chibi is a chibi because of its head, and on this source that is not a figure of speech:
    64.7% of the vertices are head-dominant and the mass reaches 0.784 m above the head joint.
    Seating the skeleton on a human rig therefore produces a 2.39 m character, and no amount of
    limb fitting touches it.

    Each vertex moves by its own head weight, so the scale fades out through the neck instead of
    tearing there. The pivot is the head joint itself, which is where the game will rotate the
    head anyway - so shrinking about it leaves the animation pivot exactly where it was.

    @return Vertices moved.
    """
    if abs(factor - 1.0) < 1e-6:
        return 0
    bone_name = armature_bone("Head")
    bone = armature.pose.bones.get(bone_name)
    if bone is None:
        print(f"  no '{bone_name}' bone; head left alone")
        return 0
    pivot = (armature.matrix_world @ bone.matrix).translation.copy()
    moved = 0
    for o in meshes:
        group = o.vertex_groups.get(bone_name)
        if group is None:
            continue
        matrix = o.matrix_world
        inverse = matrix.inverted()
        for vertex in o.data.vertices:
            weight = 0.0
            for entry in vertex.groups:
                if entry.group == group.index:
                    weight = entry.weight
                    break
            if weight <= 0.0:
                continue
            scale = 1.0 + (factor - 1.0) * weight
            point = matrix @ vertex.co
            vertex.co = inverse @ (pivot + (point - pivot) * scale)
            moved += 1
        o.data.update()
    print(f"  head x{factor:.3f} about joint z {pivot.z:.3f}, {moved:,} vertices")
    return moved


def thicken_limbs(armature, meshes, blend):
    """Widen each limb by the same factor its segment was lengthened. Call after the bake.

    The proportion fit moves joints apart and the armature stretches the mesh between them. That
    is a purely lengthwise scale: a leg fitted x1.70 gets 70% longer and not one millimetre wider,
    which is the whole of what "stilt legs" are. Real armour on this rig is built for a uniform
    limb, so repeating the factor radially is the correction, not a stylistic choice.

    Each vertex is scaled about the axis of its dominant bone, by the weighted mean of the factors
    of every bone it is weighted to. The mean is what keeps a seam from opening where two segments
    with different factors meet; the single axis is what keeps the shape from shearing.

    @return Vertices moved.
    """
    if blend <= 0.0 or not SEGMENT_STRETCH:
        return 0
    world = armature.matrix_world
    axes = {}
    for name, stretch in SEGMENT_STRETCH.items():
        bone = armature.pose.bones.get(name)
        if bone is None:
            continue
        head = world @ bone.head
        direction = (world @ bone.tail) - head
        if direction.length < 1e-6:
            continue
        axes[name] = (head, direction.normalized(), 1.0 + (stretch - 1.0) * blend)

    for name, (_, _, factor) in sorted(axes.items()):
        print(f"  {name:>18} girth x{factor:.3f}")

    moved = 0
    for o in meshes:
        names = {group.index: group.name for group in o.vertex_groups}
        matrix = o.matrix_world
        inverse = matrix.inverted()
        for vertex in o.data.vertices:
            total = 0.0
            blended = 0.0
            dominant, best = None, 0.0
            for group in vertex.groups:
                axis = axes.get(names.get(group.group))
                if axis is None or group.weight <= 0.0:
                    continue
                total += group.weight
                blended += group.weight * axis[2]
                if group.weight > best:
                    dominant, best = names[group.group], group.weight
            if dominant is None or total <= 0.0:
                continue
            factor = blended / total
            if abs(factor - 1.0) < 1e-4:
                continue
            head, direction, _ = axes[dominant]
            point = matrix @ vertex.co
            offset = point - head
            along = offset.dot(direction)
            radial = offset - direction * along
            vertex.co = inverse @ (head + direction * along + radial * factor)
            moved += 1
        o.data.update()
    return moved


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


def hand_frame(palm, index, middle, little):
    """@return Orthonormal frame of a hand: down the hand, across the knuckles, palm normal.

    **Not** a Procrustes fit over knuckle positions. Our knuckles sit 31% closer to the wrist
    than Destiny's, and with radii that different, minimising landmark *position* trades
    orientation away to buy radial error it can never win - measured: fitting positions left the
    knuckle line 13.6 deg out and a second round made it 19.2 deg, while aligning these two
    directions leaves 4.4 deg and a *lower* landmark error. Orientation is what we can fix
    without rescaling the character's hand, so orientation is what this fits.
    """
    axis = (middle - palm).normalized()
    across = index - little
    across -= axis * across.dot(axis)
    across.normalize()
    return Matrix((axis, across, axis.cross(across))).transposed()


def frame_rotation(ours, theirs):
    """@return Rotation taking our hand frame onto the donor's, and its angle in degrees."""
    rotation = theirs @ ours.transposed()
    trace = rotation[0][0] + rotation[1][1] + rotation[2][2]
    return rotation, float(math.degrees(math.acos(min(1.0, max(-1.0, (trace - 1) / 2)))))


def rotate_pose_bone(armature, name, rotation):
    """Turn one pose bone about its own head, keeping its children attached."""
    pose_bone = armature.pose.bones[name]
    world = armature.matrix_world @ pose_bone.matrix
    head = world.translation.copy()
    posed = (rotation @ world.to_3x3()).to_4x4()
    posed.translation = head
    pose_bone.matrix = armature.matrix_world.inverted() @ posed
    bpy.context.view_layer.update()


def posed_centroids(armature, wanted):
    """@return name -> weight-averaged **posed** world position of the vertices in that group.

    Positions come from the evaluated mesh (so the pose is applied) and weights from the original
    (the armature modifier changes neither vertex count nor order). Same quantity the donor side
    is measured with in `hand_targets.py`, which is the point — fitting one measure and checking
    another is how the first version of this rolled the hand the wrong way.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    totals = {name: [Vector((0.0, 0.0, 0.0)), 0.0] for name in wanted}
    for obj in [o for o in bpy.data.objects if o.type == 'MESH']:
        index_of = {group.name: group.index for group in obj.vertex_groups}
        present = {name: index_of[bone] for name, bone in wanted.items() if bone in index_of}
        if not present:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        matrix = evaluated.matrix_world
        try:
            for vertex, source in zip(mesh.vertices, obj.data.vertices):
                weights = {element.group: element.weight for element in source.groups}
                for name, group in present.items():
                    weight = weights.get(group, 0.0)
                    if weight > 0.0:
                        totals[name][0] += (matrix @ vertex.co) * weight
                        totals[name][1] += weight
        finally:
            evaluated.to_mesh_clear()
    return {name: total / mass for name, (total, mass) in totals.items() if mass > 0.0}


def fit_hands(armature):
    """Put each wrist on the joint that drives it, then aim the knuckles like the donor's.

    Runs after ARM_CHAINS, because it re-aims the forearm using where the elbow actually ended
    up. Everything is done in Blender world space - converting a rotation between Blender and
    Destiny axes is an easy sign error, and the targets convert cleanly the other way.
    """
    if not os.path.isfile(HAND_TARGETS):
        print(f"  no {HAND_TARGETS}; run `python hand_targets.py`. Hands left unfitted.")
        return
    with open(HAND_TARGETS, encoding="utf-8") as handle:
        targets = json.load(handle)["sides"]

    for side, forearm_name, wrist_name, knuckle_names in HAND_FIT:
        spec = targets.get(side)
        if spec is None or forearm_name not in armature.pose.bones:
            print(f"  {side}: no targets or no '{forearm_name}' bone; skipped")
            continue
        wrist_target = to_blender(spec["wrist"])
        elbow = (armature.matrix_world @ armature.pose.bones[forearm_name].matrix).translation
        before = ((armature.matrix_world @ armature.pose.bones[wrist_name].matrix).translation
                  - wrist_target).length

        # 1. aim the forearm at the real joint, then set its length so the tail lands on it
        align(armature, forearm_name, wrist_target - elbow)
        wrist_head = (armature.matrix_world @ armature.pose.bones[wrist_name].matrix).translation
        reach = (wrist_head - elbow).length
        want = (wrist_target - elbow).length
        factor = 1.0
        if reach > 1e-6:
            factor = want / reach
            # The hand must not inherit that squash: it would shorten the fingers along one axis
            # and shear the palm. It still inherits rotation and translation, so it stays attached.
            armature.data.bones[wrist_name].inherit_scale = 'NONE'
            pose_bone = armature.pose.bones[forearm_name]
            pose_bone.scale = (1.0, factor * pose_bone.scale.y, 1.0)
            bpy.context.view_layer.update()
        wrist_head = (armature.matrix_world @ armature.pose.bones[wrist_name].matrix).translation
        print(f"  {side} wrist: {before * 100:5.2f} cm off the joint -> "
              f"{(wrist_head - wrist_target).length * 100:4.2f} cm, forearm x{factor:.3f}")

        # 2. turn the hand so it faces the way the donor's does
        wanted = dict(knuckle_names)
        wanted["palm"] = wrist_name
        ours = posed_centroids(armature, wanted)
        theirs = spec.get("centroids", {})
        needed = ("palm", "index", "middle", "little")
        missing = [key for key in needed if key not in ours or key not in theirs]
        if missing:
            print(f"  {side}: no centroid for {missing}; hand left unturned")
            continue
        rotation, angle = frame_rotation(
            hand_frame(*(ours[key] for key in needed)),
            hand_frame(*(to_blender(theirs[key]["position"]) for key in needed)))
        rotate_pose_bone(armature, wrist_name, rotation)
        after = posed_centroids(armature, wanted)
        drift = [(after[key] - to_blender(theirs[key]["position"])).length
                 for key in needed if key in after]
        print(f"  {side} hand : turned {angle:5.1f} deg (frame fit), "
              f"landmark drift {sum(drift) / len(drift) * 100:5.2f} cm")

        # 3. artistic roll on top of the fit. Negated so the value matches the sign printed on the
        # `hand_view.py --axis forearm` preview panels, which is how it gets chosen.
        roll = HAND_ROLL.get(side, 0.0)
        if roll:
            axis = wrist_target - elbow
            rotate_pose_bone(armature, wrist_name,
                             Matrix.Rotation(math.radians(-roll), 3, axis.normalized()))
            print(f"  {side} roll : {roll:+.1f} deg about the forearm (taste, not fit; "
                  f"same sign as the preview sheet)")


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
        # Always checked, never flagged: it fires only on a source measured to be backwards, so a
        # correctly authored one is untouched.
        print("facing:")
        if orient_source(armature, meshes):
            meshes = [o for o in bpy.data.objects if o.type == 'MESH']
        if FIT_PROPORTIONS:
            print("proportion fit:")
            fit_scale(armature, meshes, rig)
            meshes = [o for o in bpy.data.objects if o.type == 'MESH']
        bpy.ops.object.mode_set(mode='POSE')
        for name, head_bone, tail_bone in ARM_CHAINS:
            if head_bone not in rig or tail_bone not in rig:
                print(f"  no rig joints for {name}; left in T-pose")
                continue
            target = to_blender(rig[tail_bone] - rig[head_bone])
            was, now = align(armature, name, target)
            angle = was.angle(now)
            print(f"  {name:>14}: rig {head_bone}->{tail_bone}, turned {angle * 57.2958:5.1f} deg")
        if FIT_PROPORTIONS:
            # After the aim, so a segment is scaled along the direction it will actually point.
            print("segment fit:")
            fit_segments(armature, rig, PROPORTION_BLEND)
        if FIT_HANDS:
            print("hand fit:")
            fit_hands(armature)
        else:
            print("--no-fit-hands: wrists stay wherever the arm chain leaves them")
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

    # After the bake, so the lengthwise stretch is already in the vertices and the girth pass is
    # answering what actually happened rather than what was asked for.
    if RETARGET and FIT_PROPORTIONS and LIMB_GIRTH > 0.0:
        print("limb girth:")
        moved = thicken_limbs(armature, meshes, LIMB_GIRTH)
        print(f"  widened {moved:,} vertices (--limb-girth {LIMB_GIRTH})")
    if RETARGET and abs(HEAD_SCALE - 1.0) > 1e-6:
        print("head scale:")
        scale_head(armature, meshes, HEAD_SCALE)

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
    # Report the wrists actually used, not a hardcoded pair: they are 21/22 in armour space and
    # 20/21 in body space, and a message that always says 21/22 hides a wrong fold.
    folded = sorted(set(WRIST_FOLD.values()))
    print(f"fingers: {'gauntlet joints 34-71' if FINGERS else f'folded onto wrists {folded}'}")

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
