"""Joins, welds and triangulates the custom character into one mesh for topology injection.

Welding is the whole trick here: the GLB's 61,908 vertices collapse to 23,512 once duplicated seam
vertices are merged, which drops it under mesh 2's 31,480-vertex budget with room to spare. That
budget matters because the model's second vertex buffer - normals, UVs and the skinning, since the
position buffer is only stride 8 - is not dumped, and the game indexes it per vertex. Staying at or
below the original count means it can be inherited untouched instead of dumped and rebuilt.

Shape keys are removed first: Blender refuses to apply modifiers to a mesh that has them.
"""
import sys
import bpy

TARGET = int(sys.argv[sys.argv.index("--") + 1])
OUT = sys.argv[sys.argv.index("--") + 2]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=r"C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb")

meshes = [o for o in bpy.data.objects if o.type == 'MESH']
for o in bpy.data.objects:
    o.select_set(False)
for o in meshes:
    o.select_set(True)
    if o.data.shape_keys:
        o.shape_key_clear()
bpy.context.view_layer.objects.active = meshes[0]
bpy.ops.object.join()
joined = bpy.context.view_layer.objects.active

bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.remove_doubles(threshold=0.0001)
bpy.ops.mesh.quads_convert_to_tris(quad_method='BEAUTY', ngon_method='BEAUTY')
bpy.ops.object.mode_set(mode='OBJECT')
print(f"joined+welded: {len(joined.data.vertices):,} verts, {len(joined.data.polygons):,} tris")

# glTF import can leave a leftover modifier; collapse must be first or the ratio misses.
while joined.modifiers:
    bpy.ops.object.modifier_apply(modifier=joined.modifiers[0].name)

passes = 0
while len(joined.data.vertices) > TARGET:
    passes += 1
    if passes > 8:
        raise RuntimeError(
            f"still {len(joined.data.vertices):,} verts after 8 collapses; target {TARGET:,}")
    ratio = max(0.05, TARGET / max(len(joined.data.vertices), 1) * 0.92)
    modifier = joined.modifiers.new(name="dec", type='DECIMATE')
    modifier.decimate_type = 'COLLAPSE'
    modifier.ratio = ratio
    bpy.ops.object.modifier_apply(modifier="dec")
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.quads_convert_to_tris(quad_method='BEAUTY', ngon_method='BEAUTY')
    bpy.ops.object.mode_set(mode='OBJECT')
    print(f"decimated: {len(joined.data.vertices):,} verts, {len(joined.data.polygons):,} tris")
if len(joined.data.vertices) <= TARGET:
    print(f"under budget ({TARGET:,})")

mesh = joined.data
matrix = joined.matrix_world
with open(OUT, "w") as fh:
    for v in mesh.vertices:
        p = matrix @ v.co
        # Blender Z-up -> Destiny (X forward, Y left-right, Z up); matches inject_mesh.to_destiny
        fh.write(f"v {-p.y:.6f} {p.x:.6f} {p.z:.6f}\n")
    for poly in mesh.polygons:
        if len(poly.vertices) == 3:
            a, b, c = poly.vertices
            fh.write(f"f {a + 1} {b + 1} {c + 1}\n")
print(f"wrote {OUT}: {len(mesh.vertices):,} verts, {len(mesh.polygons):,} tris")
