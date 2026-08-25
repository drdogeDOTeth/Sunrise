"""Bring a custom GLB onto the playable Warlock — the confirmed Sunrise path.

This desk does not ship a character. You bring the .glb. The line extracts
that model's textures, poses the arms onto the recovered Guardian rig in
Blender, keeps seams and finger weights, cuts the arms (upper + forearm + palm)
onto the gauntlet draw, writes the hook's part table, copies atlases next to the DLL, and
injects with --hands-on-gauntlets.

Closed on purpose:
  --fingers on the chest, AABB-fit hands, bind_material_textures,
  assign_split_materials, assign_armor_vs, dye-tile painting.

Destiny must be closed to write packages or overwrite the DLL.

Usage (from tools/pkg):
    python bring_guardian.py --inspect path.glb
    python bring_guardian.py --preflight --glb path.glb
    python bring_guardian.py --glb path.glb --dry-run
    python bring_guardian.py --glb path.glb --inject
    python bring_guardian.py --glb path.glb --inject --snapshot
    python bring_guardian.py --ui

Read docs/HOST_INTAKE.md before the first new host.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from glb import read_glb
from glb_textures import chunks, dimensions

HERE = Path(__file__).resolve().parent
GAME = Path(os.environ.get("SUNRISE_GAME", r"C:\Sunrise"))
ART = GAME / "bin" / "x64" / "Sunrise"
DUMP = ART / "dump"
WORK = HERE / "intake"
LAST_GLB = WORK / "last_glb.txt"


def recalled_glb() -> Path | None:
    """Last model this machine ingested, or SUNRISE_GLB. Never a baked-in character."""
    env = os.environ.get("SUNRISE_GLB", "").strip()
    if env:
        path = Path(env)
        return path if path.is_file() else None
    if LAST_GLB.is_file():
        path = Path(LAST_GLB.read_text(encoding="utf-8").strip())
        if path.is_file():
            return path
    return None


def remember_glb(path: Path) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    LAST_GLB.write_text(str(path.resolve()), encoding="utf-8")


def resolve_glb() -> Path | None:
    explicit = option("--glb")
    if explicit:
        return Path(explicit)
    return recalled_glb()

CANON = {
    "tank": "GLSLShader85",
    "mask": "GLSLShader66",
    "necklace": "GLSLShader60",
    "skin": "GLSLShader13",
    "twirl": "GLSLShader22",
}
GLSL_TO_SLOT = {value: key for key, value in CANON.items()}
SLOT_ALBEDO = {
    "tank": ("custom_tank.png", "custom_tank_mr.png"),
    "mask": ("custom_mask.png", "custom_mask_mr.png"),
    "necklace": ("custom_necklace.png", "custom_necklace_mr.png"),
    "skin": ("custom_skin.png", "custom_skin_mr.png"),
    "twirl": ("custom_twirl.png", "-"),
    "hands": ("custom_skin.png", "custom_skin_mr.png"),
}
SLOT_KEYWORDS = {
    "tank": ("tank", "shirt", "top", "cloth", "vest", "jacket", "coat"),
    "mask": ("mask", "helmet", "gas", "visor", "goggle"),
    "necklace": ("necklace", "plancha", "jewel", "chain", "pendant", "collar"),
    "skin": ("skin", "body", "tats", "flesh", "arm", "hand"),
    "twirl": ("twirl", "hair", "accent", "trim", "extra", "cloth2"),
}
BONE_ALIASES = {
    "hips": "Hips", "pelvis": "Hips", "hip": "Hips",
    "spine": "Spine", "spine1": "Spine", "spine2": "Chest",
    "chest": "Chest", "upperchest": "Chest", "spine3": "Chest",
    "neck": "Neck", "head": "Head", "jaw": "Jaw",
    "lefteye": "LeftEye", "righteye": "RightEye",
    "leftshoulder": "Left shoulder", "rightshoulder": "Right shoulder",
    "leftarm": "Left arm", "leftupperarm": "Left arm",
    "rightarm": "Right arm", "rightupperarm": "Right arm",
    "leftforearm": "Left elbow", "leftelbow": "Left elbow",
    "rightforearm": "Right elbow", "rightelbow": "Right elbow",
    "lefthand": "Left wrist", "leftwrist": "Left wrist",
    "righthand": "Right wrist", "rightwrist": "Right wrist",
    "leftupleg": "Left leg", "leftthigh": "Left leg", "leftleg": "Left knee",
    "rightupleg": "Right leg", "rightthigh": "Right leg", "rightleg": "Right knee",
    "leftfoot": "Left ankle", "leftankle": "Left ankle",
    "rightfoot": "Right ankle", "rightankle": "Right ankle",
    "lefttoebase": "Left toe", "lefttoe": "Left toe",
    "righttoebase": "Right toe", "righttoe": "Right toe",
    # VRM's own humanoid names. Every one of these was missing, so a VRM read through its
    # humanoid table still lost both forearms and the entire leg chain — knees, thighs and toes —
    # while the shoulder, upper arm and foot around them mapped, which is what makes the failure
    # look like bad weights rather than a missing alias.
    "leftlowerarm": "Left elbow", "rightlowerarm": "Right elbow",
    "leftupperleg": "Left leg", "rightupperleg": "Right leg",
    "leftlowerleg": "Left knee", "rightlowerleg": "Right knee",
    "lefttoes": "Left toe", "righttoes": "Right toe",
    "leftthumbproximal": "Thumb0_L", "leftthumbintermediate": "Thumb1_L",
    "leftthumbdistal": "Thumb2_L",
    "rightthumbproximal": "Thumb0_R", "rightthumbintermediate": "Thumb1_R",
    "rightthumbdistal": "Thumb2_R",
    "leftindexproximal": "IndexFinger1_L", "leftindexintermediate": "IndexFinger2_L",
    "leftindexdistal": "IndexFinger3_L",
    "rightindexproximal": "IndexFinger1_R", "rightindexintermediate": "IndexFinger2_R",
    "rightindexdistal": "IndexFinger3_R",
    "leftmiddleproximal": "MiddleFinger1_L", "leftmiddleintermediate": "MiddleFinger2_L",
    "leftmiddledistal": "MiddleFinger3_L",
    "rightmiddleproximal": "MiddleFinger1_R", "rightmiddleintermediate": "MiddleFinger2_R",
    "rightmiddledistal": "MiddleFinger3_R",
    "leftringproximal": "RingFinger1_L", "leftringintermediate": "RingFinger2_L",
    "leftringdistal": "RingFinger3_L",
    "rightringproximal": "RingFinger1_R", "rightringintermediate": "RingFinger2_R",
    "rightringdistal": "RingFinger3_R",
    "leftlittleproximal": "LittleFinger1_L", "leftlittleintermediate": "LittleFinger2_L",
    "leftlittledistal": "LittleFinger3_L",
    "rightlittleproximal": "LittleFinger1_R", "rightlittleintermediate": "LittleFinger2_R",
    "rightlittledistal": "LittleFinger3_R",
}


def log(message: str) -> None:
    print(message, flush=True)


def option(name: str, fallback: str = "") -> str:
    if name not in sys.argv:
        return fallback
    at = sys.argv.index(name)
    if at + 1 >= len(sys.argv):
        raise SystemExit(f"{name} needs a value")
    return sys.argv[at + 1]


def flag(name: str) -> bool:
    return name in sys.argv


def preflight(*, glb: Path | None, inject: bool) -> None:
    """Fail loud on a missing floor. Warnings for model shape, not install shape."""
    from inject_scatterhorn import CHESTS, GAUNTLETS, SLOT_MATERIALS, load_model
    from known_good import game_running

    errors: list[str] = []
    warnings: list[str] = []
    if not GAME.is_dir():
        errors.append(f"game folder missing: {GAME}")
    if not (GAME / "destiny2.exe").is_file():
        errors.append(f"destiny2.exe not under {GAME}")
    if not (GAME / "bin" / "x64" / "steam_api64.dll").is_file():
        errors.append("hook DLL missing — run .\\build.ps1 and .\\install.ps1 once")
    try:
        blender = find_blender()
        log(f"preflight blender: {blender}")
    except SystemExit as error:
        errors.append(str(error))
    if not DUMP.is_dir():
        errors.append(f"no dump folder at {DUMP} — launch once with the hook so models dump")
    else:
        tags = list(CHESTS) + list(GAUNTLETS.values())
        for tag in tags:
            path = DUMP / f"tag_{tag:08X}.bin"
            if not path.is_file():
                errors.append(f"missing dump 0x{tag:08X} ({path.name})")
        chest_dump = DUMP / f"tag_{CHESTS[0]:08X}.bin"
        if chest_dump.is_file():
            try:
                model = load_model(CHESTS[0])
                have = {part[0] for mesh in model.meshes[:1] for part in mesh.parts}
                missing = [f"0x{tag:08X}" for tag in SLOT_MATERIALS.values() if tag not in have]
                if missing:
                    errors.append(
                        "chest dump is missing the five carrier materials "
                        f"({', '.join(missing)}). Restore 20260822-235602 first — "
                        "intake assumes the live five-part Scatterhorn floor."
                    )
            except Exception as error:  # noqa: BLE001 — dump can be truncated
                errors.append(f"could not read chest dump: {error}")
    if inject and game_running():
        errors.append("destiny2.exe is running — close it before inject")
    if glb is not None:
        if not glb.is_file():
            errors.append(f"no GLB at {glb}")
        else:
            info = inspect_glb(glb)
            if not info["joints"]:
                errors.append("GLB has no skin joints — need a humanoid armature with vertex groups")
            if not info["materials"]:
                errors.append("GLB has no materials / atlases")
            if len(info["materials"]) > 5:
                warnings.append(
                    f"{len(info['materials'])} materials — Destiny only has five chest carriers. "
                    "Park extras on tank/mask/necklace/skin/twirl or they share an atlas."
                )
            unmapped = [joint["name"] for joint in info["joints"] if joint_bone(joint) is None]
            if unmapped:
                shown = ", ".join(unmapped[:8])
                extra = "…" if len(unmapped) > 8 else ""
                warnings.append(
                    f"{len(unmapped)} unmapped joints will be ignored: {shown}{extra}. "
                    "Pass --bone-map if those should drive the mesh."
                )
    for warning in warnings:
        log(f"preflight warn: {warning}")
    if errors:
        raise SystemExit("preflight failed:\n  - " + "\n  - ".join(errors))
    log("preflight ok")


def find_blender() -> Path:
    env = os.environ.get("BLENDER")
    if env and Path(env).is_file():
        return Path(env)
    which = shutil.which("blender")
    if which:
        return Path(which)
    roots = [
        Path(r"C:\Program Files\Blender Foundation"),
        Path(r"C:\Program Files (x86)\Blender Foundation"),
    ]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        found.extend(root.glob("Blender */blender.exe"))
    if not found:
        raise SystemExit(
            "Blender not found. Install Blender, or set BLENDER=C:\\path\\to\\blender.exe"
        )
    found.sort(key=lambda path: path.parent.name, reverse=True)
    return found[0]


def _norm(name: str) -> str:
    cleaned = name.split(":")[-1].split(".")[0]
    return "".join(ch for ch in cleaned.lower() if ch.isalnum())


def guess_slot(material: str) -> str:
    if material in GLSL_TO_SLOT:
        return GLSL_TO_SLOT[material]
    if material in CANON:
        return material
    key = _norm(material)
    for slot, words in SLOT_KEYWORDS.items():
        if any(word in key for word in words):
            return slot
    return "skin"


def guess_bone(group: str) -> str | None:
    if group in BONE_ALIASES.values() or group in (
        "Hips", "Spine", "Chest", "Neck", "Head",
    ):
        return group
    return BONE_ALIASES.get(_norm(group))


def joint_bone(joint: dict) -> str | None:
    """@return Canonical rig bone for one joint, preferring the file's own humanoid role."""
    if joint.get("role"):
        hit = guess_bone(joint["role"])
        if hit is not None:
            return hit
    return guess_bone(joint["name"])


def vrm_humanoid_roles(document: dict) -> dict[int, str]:
    """Canonical humanoid role per node, from the VRM extension.

    A VRM carries its own humanoid table, so an exporter is free to name the nodes whatever it
    likes - `upper_arm.R` out of Blender rather than `rightUpperArm`. Guessing from node names
    therefore reports a rig as unmapped when the file states the mapping outright. Both spec
    versions are read: 0.x keeps a list under `VRM`, 1.0 a dict under `VRMC_vrm`.
    @return Role by node index, empty when the file is a plain GLB.
    """
    extensions = document.get("extensions", {})
    roles: dict[int, str] = {}
    one = extensions.get("VRMC_vrm", {}).get("humanoid", {}).get("humanBones", {})
    if isinstance(one, dict):
        for role, entry in one.items():
            node = (entry or {}).get("node")
            if isinstance(node, int):
                roles[node] = role
    zero = extensions.get("VRM", {}).get("humanoid", {}).get("humanBones", [])
    if isinstance(zero, list):
        for entry in zero:
            node = (entry or {}).get("node")
            role = (entry or {}).get("bone")
            if isinstance(node, int) and isinstance(role, str):
                roles.setdefault(node, role)
    return roles


def inspect_glb(path: Path) -> dict:
    document, binary = read_glb(path)
    nodes = document.get("nodes", [])
    roles = vrm_humanoid_roles(document)
    # Parent per node, so a joint the humanoid table does not name can inherit from the nearest
    # ancestor it does. Hair and skirt chains are the reason: a VRM rigs them as spring bones,
    # Destiny has no such thing, and an ignored group leaves those vertices with no weight at all.
    parents: dict[int, int] = {}
    for index, node in enumerate(nodes):
        for child in node.get("children", []) or []:
            parents[child] = index
    # Each joint carries the node name the retarget will see as a vertex group, and the canonical
    # role when the file declares one. The role is what resolves; the name is what it resolves for.
    # Deduplicated by node: a model with several skins lists the shared joints in each of them,
    # and counting those repeats reports a rig several times larger than it is.
    joints = []
    seen: set[int] = set()
    for skin in document.get("skins", []):
        for index in skin.get("joints", []):
            if index in seen:
                continue
            seen.add(index)
            name = nodes[index].get("name", f"joint{index}")
            joints.append({"name": name, "role": roles.get(index),
                           "node": index, "parent": parents.get(index)})
    materials = []
    images = document.get("images", [])
    views = document.get("bufferViews", [])
    textures = document.get("textures", [])

    def image_info(tex_ref) -> dict | None:
        if not tex_ref:
            return None
        source = textures[tex_ref["index"]].get("source")
        if source is None:
            return None
        image = images[source]
        view = views[image["bufferView"]]
        start = view.get("byteOffset", 0)
        data = binary[start:start + view["byteLength"]]
        size = dimensions(data)
        return {
            "index": source,
            "name": image.get("name") or f"image{source:02d}",
            "mime": image.get("mimeType", ""),
            "width": size[0] if size else 0,
            "height": size[1] if size else 0,
            "bytes": len(data),
        }

    for material in document.get("materials", []):
        name = material.get("name", "?")
        pbr = material.get("pbrMetallicRoughness", {})
        materials.append({
            "name": name,
            "slot": guess_slot(name),
            "base": image_info(pbr.get("baseColorTexture")),
            "mr": image_info(pbr.get("metallicRoughnessTexture")),
            "metallic_factor": pbr.get("metallicFactor", 1.0),
            "roughness_factor": pbr.get("roughnessFactor", 1.0),
        })
    return {
        "path": str(path),
        "meshes": [mesh.get("name", "?") for mesh in document.get("meshes", [])],
        "joints": joints,
        "materials": materials,
        "images": len(images),
    }


def print_inspect(info: dict) -> None:
    log(f"GLB  {info['path']}")
    log(f"meshes  {', '.join(info['meshes']) or '(none)'}")
    log(f"joints  {len(info['joints'])}")
    unmapped = []
    for joint in info["joints"]:
        if joint_bone(joint) is None:
            unmapped.append(joint["name"])
    if unmapped:
        humanoid = any(joint.get("role") for joint in info["joints"])
        if humanoid:
            # These still reach the rig: the generated bone map walks each one up to the nearest
            # ancestor the humanoid table does name, so a hair chain rides the head rather than
            # losing its weights. Saying "ignored" here sent the last intake looking for a fault.
            log(f"{len(unmapped)} joints the humanoid table does not name; each inherits its "
                "nearest mapped ancestor:")
        else:
            log("unmapped joints (will be ignored unless you pass --bone-map):")
        for name in unmapped:
            log(f"  {name}")
    log("materials -> Destiny carrier slots (edit with --material-map):")
    for material in info["materials"]:
        base = material["base"]
        shape = f"{base['width']}x{base['height']}" if base else "no albedo"
        log(f"  {material['name']:<28} -> {material['slot']:<9} {shape}")


def work_dir(glb: Path) -> Path:
    dest = WORK / glb.stem
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def write_material_map(info: dict, dest: Path, overrides: dict[str, str] | None = None) -> Path:
    mapping = {}
    for material in info["materials"]:
        slot = (overrides or {}).get(material["name"], material["slot"])
        if slot == "skip":
            slot = "skin"
        mapping[material["name"]] = CANON.get(slot, slot)
    path = dest / "material_map.json"
    path.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    return path


def write_group_map(material_map: Path, dest: Path) -> Path:
    raw = json.loads(material_map.read_text(encoding="utf-8"))
    path = dest / "group_map.json"
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return path


def extract_textures(glb: Path, dest: Path) -> Path:
    out = dest / "textures"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, str(HERE / "glb_textures.py"), "--extract",
         "--glb", str(glb), "--out", str(out)],
        check=True,
        cwd=str(HERE),
    )
    return out


def _image_bytes(glb: Path, image_index: int) -> tuple[bytes, str]:
    document, binary = chunks(glb.read_bytes())
    image = document["images"][image_index]
    view = document["bufferViews"][image["bufferView"]]
    start = view.get("byteOffset", 0)
    data = binary[start:start + view["byteLength"]]
    mime = image.get("mimeType", "")
    suffix = ".png" if "png" in mime or data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    return data, suffix


def install_textures(glb: Path, info: dict, dest: Path, game_art: Path) -> dict[str, tuple[str, str]]:
    game_art.mkdir(parents=True, exist_ok=True)
    used: dict[str, tuple[str, str]] = {}
    for material in info["materials"]:
        slot = material["slot"]
        if slot not in SLOT_ALBEDO or slot in used:
            continue
        albedo_name, mr_name = SLOT_ALBEDO[slot]
        if material["base"]:
            data, suffix = _image_bytes(glb, material["base"]["index"])
            if suffix != ".png":
                albedo_name = Path(albedo_name).with_suffix(suffix).name
            (dest / albedo_name).write_bytes(data)
            shutil.copy2(dest / albedo_name, game_art / albedo_name)
        if material["mr"] and mr_name != "-":
            data, suffix = _image_bytes(glb, material["mr"]["index"])
            if suffix != ".png":
                mr_name = Path(mr_name).with_suffix(suffix).name
            (dest / mr_name).write_bytes(data)
            shutil.copy2(dest / mr_name, game_art / mr_name)
        else:
            mr_name = "-"
        used[slot] = (albedo_name, mr_name)
        log(f"  atlas {slot}: {albedo_name} / {mr_name}")
    return used


def retarget_tables() -> tuple[dict[str, int], int, int]:
    """Reads `retarget_mesh.py`'s own bone tables rather than keeping a second copy of them.

    Parsed with `ast` so a renamed or moved table fails loudly here instead of quietly
    disagreeing with the retarget that actually runs.
    @return BONE_MAP, SPINE_UPPER, CHEST_BONE.
    """
    import ast

    tree = ast.parse((HERE / "retarget_mesh.py").read_text(encoding="utf-8"))
    found: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in {
            "BONE_MAP", "SPINE_UPPER", "CHEST_BONE"
        }:
            found[target.id] = ast.literal_eval(node.value)
    missing = {"BONE_MAP", "SPINE_UPPER", "CHEST_BONE"} - found.keys()
    if missing:
        raise SystemExit(f"retarget_mesh.py no longer defines {', '.join(sorted(missing))}")
    return found["BONE_MAP"], found["SPINE_UPPER"], found["CHEST_BONE"]


def write_bone_map(info: dict, dest: Path) -> Path | None:
    """Turns the file's humanoid table into the group -> joint map the retarget consumes.

    Resolving the role is not enough on its own: the retarget matches Blender **vertex groups**,
    which carry the exporter's node names (`upper_leg.L`), not the canonical roles. So the roles
    are used here to write the mapping out against the names the retarget will actually see.

    @return Path to the written map, or None for a plain GLB that declares no humanoid.
    """
    if not any(joint.get("role") for joint in info["joints"]):
        return None
    bones, spine_upper, chest_bone = retarget_tables()
    by_role = {joint["role"]: joint["name"] for joint in info["joints"] if joint.get("role")}
    groups: dict[str, int] = {}
    payload: dict[str, object] = {}

    for joint in info["joints"]:
        role = joint.get("role")
        if not role:
            continue
        canonical = guess_bone(role)
        if canonical is None or canonical == "Chest":
            continue
        if canonical in bones:
            groups[joint["name"]] = bones[canonical]
        elif canonical.endswith(("_L", "_R")):
            # Fingers fold onto the wrist. The retarget's own finger indices sit above the body
            # ceiling and the chest draw has never posed them without needles, so the documented
            # safe default is what gets written.
            groups[joint["name"]] = 21 if canonical.endswith("_L") else 22

    # The retarget splits one chest group between two joints by height, which exists to fake an
    # UpperChest the source does not have. A VRM that declares both needs no split at all.
    # Settled before inheritance runs, so a chain hanging off the chest can descend from it.
    chest = by_role.get("chest")
    upper = by_role.get("upperChest")
    if chest and upper:
        groups[chest] = spine_upper
        groups[upper] = chest_bone
    elif chest or upper:
        payload["chest_group"] = chest or upper

    # Everything the humanoid table does not name - hair, skirts, extra finger bases - inherits
    # the nearest ancestor that is mapped. A spring chain hanging off the head then rides the head
    # rigidly, which is the most Destiny's skeleton can do; an ignored group would instead leave
    # those vertices with no weight at all. The chest-split group is deliberately not a source,
    # because it has no single joint index to inherit.
    by_node = {joint["node"]: joint for joint in info["joints"] if joint.get("node") is not None}
    resolved = {node: groups[joint["name"]]
                for node, joint in by_node.items() if joint["name"] in groups}
    inherited = 0
    for joint in info["joints"]:
        if joint["name"] in groups or joint.get("node") is None:
            continue
        walk = joint.get("parent")
        # Bounded by the joint count, so a malformed hierarchy cannot loop.
        for _ in range(len(by_node) + 1):
            if walk is None:
                break
            if walk in resolved:
                groups[joint["name"]] = resolved[walk]
                inherited += 1
                break
            walk = by_node[walk]["parent"] if walk in by_node else None
    if inherited:
        log(f"  {inherited} unnamed joints inherit their nearest mapped ancestor")

    # The pose step is a second, separate namespace: it looks bones up in the *armature* by
    # canonical name ("Left arm"), while `groups` is keyed by vertex group. An exporter that names
    # its bones `upper_arm.L` satisfies neither by accident, so the translation is written out too.
    payload["bones"] = {
        canonical: joint["name"]
        for joint in info["joints"]
        if joint.get("role") and (canonical := guess_bone(joint["role"])) and canonical != "Chest"
    }
    payload["groups"] = groups
    out = dest / "bone_map.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log(f"bone map: {len(groups)} groups from the file's humanoid table -> {out.name}")
    if "chest_group" in payload:
        log(f"  chest group {payload['chest_group']!r} splits {spine_upper}/{chest_bone} by height")
    elif chest and upper:
        log(f"  chest {chest!r} -> {spine_upper}, upperChest {upper!r} -> {chest_bone}")
    return out


def run_retarget(glb: Path, dest: Path, blender: Path, material_map: Path,
                 bone_map: Path | None) -> Path:
    out = dest / "character_body.obj"
    command = [
        str(blender), "--background", "--python", str(HERE / "retarget_mesh.py"), "--",
        "65535", str(out), "--keep-seams", "--fingers",
        "--glb", str(glb),
        "--material-map", str(material_map),
    ]
    if bone_map and bone_map.is_file():
        command.extend(["--bone-map", str(bone_map)])
    log("retarget: " + " ".join(command))
    subprocess.run(command, check=True, cwd=str(HERE))
    if not out.is_file():
        raise SystemExit(f"retarget did not write {out}")
    return out


def run_cut(body: Path) -> tuple[Path, Path]:
    chest = body.with_name(body.stem + "_nohands.obj")
    hands = body.with_name(body.stem.replace("body", "hands") if "body" in body.stem
                           else body.stem + "_hands")
    if hands == chest:
        hands = body.with_name("character_hands.obj")
    subprocess.run(
        [sys.executable, str(HERE / "cut_hands.py"),
         "--mesh", str(body),
         "--chest-out", str(chest),
         "--hands-out", str(hands)],
        check=True,
        cwd=str(HERE),
    )
    return chest, hands


def parts_from_groups(chest: Path, hands: Path, atlases: dict[str, tuple[str, str]]) -> list[str]:
    groups = json.loads(chest.with_name(chest.stem + "_groups.json").read_text(encoding="utf-8"))
    names = [CANON.get(name, name) for name in groups["slots"]]
    face = groups["face_material"]
    lines = ["# name start count albedo material   (written by bring_guardian.py)"]
    at = 0
    # Walk in the names-list order, same as inject_scatterhorn.order_by_group.
    for index, name in enumerate(names):
        count = sum(1 for item in face if item == index)
        if not count:
            continue
        slot = GLSL_TO_SLOT.get(name, "skin")
        albedo, material = atlases.get(slot, SLOT_ALBEDO[slot])
        lines.append(f"{slot} {at * 3} {count * 3} {albedo} {material}")
        at += count
    hand_groups = json.loads(hands.with_name(hands.stem + "_groups.json").read_text(encoding="utf-8"))
    hand_count = int(hand_groups["triangles"]) * 3
    albedo, material = atlases.get("skin", SLOT_ALBEDO["hands"])
    lines.append(f"hands 0 {hand_count} {albedo} {material}")
    return lines


def write_parts(lines: list[str], dest: Path, game_art: Path) -> Path:
    text = "\n".join(lines) + "\n"
    local = dest / "custom_parts.txt"
    local.write_text(text, encoding="ascii")
    game_art.mkdir(parents=True, exist_ok=True)
    (game_art / "custom_parts.txt").write_text(text, encoding="ascii")
    log(f"parts table -> {game_art / 'custom_parts.txt'}")
    for line in lines:
        if not line.startswith("#"):
            log(f"  {line}")
    return local


def run_inject(chest: Path, hands: Path, group_map: Path, dry_run: bool) -> None:
    command = [
        sys.executable, str(HERE / "inject_scatterhorn.py"),
        "--hands-on-gauntlets",
        "--mesh", str(chest),
        "--hands", str(hands),
        "--group-map", str(group_map),
    ]
    if dry_run:
        command.append("--dry-run")
    log("inject: " + " ".join(command))
    subprocess.run(command, check=True, cwd=str(HERE))


def run_pipeline(glb: Path, *, inject: bool, dry_run: bool,
                 material_overrides: dict[str, str] | None = None,
                 bone_map: Path | None = None,
                 game_art: Path = ART,
                 snapshot: bool = False) -> Path:
    if not glb.is_file():
        raise SystemExit(f"no GLB at {glb}")
    preflight(glb=glb, inject=inject and not dry_run)
    dest = work_dir(glb)
    log(f"intake work folder: {dest}")
    info = inspect_glb(glb)
    print_inspect(info)
    if material_overrides:
        for material in info["materials"]:
            if material["name"] in material_overrides:
                material["slot"] = material_overrides[material["name"]]
    material_map = write_material_map(info, dest, material_overrides)
    group_map = write_group_map(material_map, dest)
    extract_textures(glb, dest)
    atlases = install_textures(glb, info, dest, game_art)
    blender = find_blender()
    log(f"blender: {blender}")
    # A hand-written map always wins; otherwise the file's own humanoid table is used, which is
    # the difference between a VRM keeping its legs and forearms and losing them.
    body = run_retarget(glb, dest, blender, material_map,
                        bone_map or write_bone_map(info, dest))
    chest, hands = run_cut(body)
    lines = parts_from_groups(chest, hands, atlases)
    write_parts(lines, dest, game_art)
    if inject or dry_run:
        if inject and not dry_run:
            log("Destiny must be closed. Writing packages.")
        run_inject(chest, hands, group_map, dry_run=dry_run or not inject)
    if snapshot and inject and not dry_run:
        from known_good import save
        save(f"intake {glb.stem}: hands-on-gauntlets", GAME / "packages")
    remember_glb(glb)
    log("intake finished. Launch destiny2.exe — Warlock, Scatterhorn chest.")
    log("Look at character select, a destination, and first-person. Snapshot if it looks right.")
    return dest


def main() -> None:
    if flag("--ui"):
        from bring_guardian_ui import main as ui_main
        ui_main()
        return
    glb = resolve_glb()
    if flag("--inspect"):
        at = sys.argv.index("--inspect")
        if at + 1 < len(sys.argv) and not sys.argv[at + 1].startswith("--"):
            glb = Path(sys.argv[at + 1])
        if glb is None or not glb.is_file():
            raise SystemExit(
                "pass --inspect path.glb  (this tool does not ship a character)"
            )
        print_inspect(inspect_glb(glb))
        if flag("--preflight"):
            preflight(glb=glb, inject=False)
        return
    if flag("--preflight") and not flag("--dry-run") and not flag("--inject"):
        preflight(glb=glb if glb is not None and glb.is_file() else None, inject=False)
        return
    if glb is None or not glb.is_file():
        raise SystemExit(
            "pass --glb path.glb  (or --ui). Intake does not ship a character — "
            "bring your own model. Optional: set SUNRISE_GLB."
        )
    overrides_path = option("--material-map")
    overrides = json.loads(Path(overrides_path).read_text(encoding="utf-8")) if overrides_path else None
    bone = Path(option("--bone-map")) if option("--bone-map") else None
    run_pipeline(
        glb,
        inject=flag("--inject"),
        dry_run=flag("--dry-run"),
        material_overrides=overrides,
        bone_map=bone,
        snapshot=flag("--snapshot"),
    )


if __name__ == "__main__":
    main()
