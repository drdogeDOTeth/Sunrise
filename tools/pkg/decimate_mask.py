"""Decimate the GasMask mesh so it fits the smallest dumped helmets (~1171 verts)."""
import bpy

src = r"C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb"
dst = r"C:\Users\Round\OneDrive\Desktop\Destiny2ProjectSunrise\Sunrise\tools\pkg\GasMask_1100.glb"
target = 1100

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
obj = next(
    (o for o in bpy.data.objects if o.type == "MESH" and o.name.lower() == "gasmask"),
    None,
)
if obj is None:
    raise SystemExit("no mesh named GasMask")
bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
# Apply any import modifiers first so decimate is not stacked behind an armature.
for existing in list(obj.modifiers):
    bpy.ops.object.modifier_apply(modifier=existing.name)
before = len(obj.data.vertices)
verts = before
while verts > target:
    ratio = min(0.95, target / verts)
    mod = obj.modifiers.new(name="decimate", type="DECIMATE")
    mod.ratio = ratio
    bpy.ops.object.modifier_apply(modifier=mod.name)
    now = len(obj.data.vertices)
    if now >= verts:
        break
    verts = now
after = len(obj.data.vertices)
obj.name = "GasMask"
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True)
print(f"decimated {before} -> {after} verts ratio={ratio:.3f} -> {dst}")
