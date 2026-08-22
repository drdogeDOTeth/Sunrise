"""Headless: import the custom GLB and render what Blender actually shows.

Does not write packages. Does not touch the mesh we already injected.

Usage:
    blender --background --python preview_glb_blender.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy

GLB = Path(r"C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb")
OUT = Path(__file__).with_name("objs") / "textures" / "_glb_blender_preview.png"


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if not GLB.is_file():
        raise SystemExit(f"no GLB at {GLB}")
    bpy.ops.import_scene.gltf(filepath=str(GLB))

    meshes = [o for o in bpy.data.objects if o.type == "MESH" and o.name != "Icosphere"]
    loose = [o for o in bpy.data.objects if o.type == "MESH" and o.name == "Icosphere"]
    for obj in loose:
        obj.hide_render = True
        obj.hide_viewport = True
    print(f"imported {len(meshes)} meshes from {GLB.name}")
    for obj in meshes:
        print(f"  {obj.name}: {len(obj.data.vertices):,} verts, "
              f"mats {[s.material.name if s.material else None for s in obj.material_slots]}")

    for mat in bpy.data.materials:
        if not mat.use_nodes or not mat.node_tree:
            continue
        images = []
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                images.append(f"{node.image.name} {tuple(node.image.size)}")
        print(f"  material {mat.name}: {images or 'no images'}")

    # Frame every mesh.
    for obj in bpy.data.objects:
        obj.select_set(obj.type == "MESH")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

    # World-space box of the kept meshes so the camera actually frames them.
    import mathutils
    low = mathutils.Vector((1e9, 1e9, 1e9))
    high = mathutils.Vector((-1e9, -1e9, -1e9))
    for obj in meshes:
        for corner in obj.bound_box:
            world = obj.matrix_world @ mathutils.Vector(corner)
            low.x, low.y, low.z = min(low.x, world.x), min(low.y, world.y), min(low.z, world.z)
            high.x, high.y, high.z = max(high.x, world.x), max(high.y, world.y), max(high.z, world.z)
    center = (low + high) * 0.5
    span = (high - low).length
    print(f"  bbox {tuple(round(c, 3) for c in low)} .. {tuple(round(c, 3) for c in high)}  span {span:.2f}")

    bpy.ops.object.empty_add(type="PLAIN_AXES", location=center)
    target = bpy.context.active_object
    target.name = "LookAt"
    cam_loc = center + mathutils.Vector((span * 0.55, -span * 0.85, span * 0.15))
    bpy.ops.object.camera_add(location=cam_loc)
    cam = bpy.context.active_object
    track = cam.constraints.new(type="TRACK_TO")
    track.target = target
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"
    bpy.context.scene.camera = cam

    light_data = bpy.data.lights.new(name="Key", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("Key", light_data)
    bpy.context.scene.collection.objects.link(light)
    light.location = (2.0, -1.5, 4.0)

    fill_data = bpy.data.lights.new(name="Fill", type="SUN")
    fill_data.energy = 1.0
    fill = bpy.data.objects.new("Fill", fill_data)
    bpy.context.scene.collection.objects.link(fill)
    fill.rotation_euler = (0.8, 0.2, 2.4)

    scene = bpy.context.scene
    # Workbench + TEXTURE is the honest "show the albedo" view — no Principled
    # lighting crush. EEVEE Next is a fallback if this build renamed Workbench.
    for engine in ("BLENDER_WORKBENCH", "BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            print(f"render engine {engine}")
            break
        except TypeError:
            continue
    if scene.render.engine == "BLENDER_WORKBENCH":
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "TEXTURE"
    scene.render.resolution_x = 768
    scene.render.resolution_y = 1024
    scene.render.filepath = str(OUT)
    scene.render.image_settings.file_format = "PNG"
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (0.82, 0.82, 0.84, 1.0)
        bg.inputs[1].default_value = 0.6

    OUT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    # Blender injects its own argv; keep this import-safe when run under bpy.
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
