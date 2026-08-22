"""Pack the five GLB albedos into the one bound 512 slot, and scale UVs into tiles.

The chest-native mask material (`0x80EFA1DC`) is the only binder that draws on this
body. Off-chest steals vanish. So every split part already names that material
(`assign_chest_mask.py` / `037d_26`), and the five unique atlases have to share the
one buffer it samples: `0x80B3611D` (512² BC7 in `019b_8`).

That is the same atlas trick as a RedM/FiveM clothing YTD: one texture, UV islands
in tiles. Destiny will not let us grow this slot. Skin takes 384²; the other four
are already near-flat and keep 128². Tank unused-field is filled so it cannot
paint a white vest. Skin saturation is boosted so a dye-tile PS still has hue.

Writes, and only these:

- pixels of `0x80B3611D` (mip 0+1). Not a flat colour. Header untouched.
- primary U/V of the two chest texcoord buffers. Normals, tangents, positions,
  weights, index ranges and material tags stay as they are.

Do not pass --small. 020e_9's 21,872 B write was suit_t5 (`0x80C1D3CB`), not this
texture's other half.

Usage:
    python pack_chest_atlas.py --dry-run
    python pack_chest_atlas.py --write    # Destiny closed
"""
from __future__ import annotations

import json
import struct
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

from inject_mesh import entry_index_of, write_all
from inject_scatterhorn import (
    CHESTS,
    PACKED_MAX,
    TEXCOORD_STRIDE,
    UV_SCALE,
    UV_TRANSLATION,
)
from paint_albedo import mip_chain
from parse_models import Model
from tigerpkg import Package
from verify_bind_layer import locate, newest_of_each

HERE = Path(__file__).resolve().parent
TEXTURES = HERE / "objs" / "textures"
GROUPS_PATH = HERE / "character_body_groups.json"
PREVIEW = TEXTURES / "_packed_512.png"
# 0-1 UVs from the five-part split, before any tile scale. Live 0698_25 is already
# tiled; reading it again would double-scale. Always start from this layer.
UV_SOURCE = Path(r"C:\Sunrise\packages\w64_sandbox_0698_24.pkg")

HEADER = 0x80C1D3CD
BUFFER = 0x80B3611D
# Proven against the dumped header and the live entry. Headers stay encrypted;
# we do not touch them, and we do not need the dump to paint the plain body.
ATLAS = 512
FORMAT_TOTAL = 349_552

# Skin is the character. Tank/mask/twirl/necklace are already near-flat, so they
# keep 128² and skin takes 384². Destiny V / PNG / DirectX: origin top-left.
TILES = {
    "GLSLShader13": (0, 0, 384, 384),       # skin, arms, legs
    "GLSLShader85": (384, 0, 128, 128),     # tank
    "GLSLShader66": (384, 128, 128, 128),   # gas mask
    "GLSLShader22": (384, 256, 128, 128),   # twirl
    "GLSLShader60": (384, 384, 128, 128),   # necklace
}
IMAGES = {
    "GLSLShader85": "01_BlackTankTopshader_BaseColor.png",
    "GLSLShader13": "10_SkinTats_BaseColor.png",
    "GLSLShader66": "04_GasMaskshader_BaseColor.png",
    "GLSLShader22": "13_Twirlshader_BaseColor.png",
    "GLSLShader60": "07_Plancha_BaseColor.png",
}


def index_offsets() -> dict[int, str]:
    """@return `{index offset: GLB material name}` in the order the injector sorted faces."""
    raw = json.loads(GROUPS_PATH.read_text(encoding="utf-8"))
    counts = Counter(raw["face_material"])
    at = 0
    out: dict[int, str] = {}
    for index, name in enumerate(raw["slots"]):
        tris = int(counts.get(index, 0))
        if not tris:
            continue
        out[at] = name
        at += tris * 3
    return out


def tile_pad(box: tuple[int, int, int, int]) -> int:
    """@return Gutter in pixels. 8px on the 384 tile, 4px on the 128s."""
    return 4 if min(box[2], box[3]) <= 128 else 8


def fill_light_background(image: Image.Image) -> Image.Image:
    """Replace the tank atlas's unused light-gray field with the island colour.

    Those leftover texels were the white vest: tank UVs that miss an island
    sampled the sheet background. Blender never shows that field.
    """
    arr = np.asarray(image.convert("RGBA")).copy()
    rgb = arr[:, :, :3].astype(np.float32)
    lum = rgb.mean(axis=2)
    background = lum > 140
    island = rgb[~background]
    if not island.size or not background.any():
        return image.convert("RGBA")
    fill = np.median(island, axis=0)
    arr[background, :3] = fill
    print(f"    tank unused field -> island RGB {tuple(int(c) for c in fill)} "
          f"({background.mean() * 100:.0f}% of sheet)")
    return Image.fromarray(arr)


def prepare_image(name: str, image: Image.Image) -> Image.Image:
    """@return The GLB albedo, pushed so the dye-tile PS still has a hue to keep."""
    image = image.convert("RGBA")
    if name == "GLSLShader85":
        image = fill_light_background(image)
    # 2026-08-22 look-alike probe: sat 2.4 + contrast 1.25 pushed luma over a
    # dye threshold and the legs went white. Do not boost. This PS is a luma
    # gate (high = cloth white, low = suit black), not an RGB albedo.
    return image


def paste_tile(atlas: Image.Image, source: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Resize `source` into `box`, with a per-tile gutter against mip bleed."""
    x, y, width, height = box
    pad = tile_pad(box)
    inner_w, inner_h = width - 2 * pad, height - 2 * pad
    if inner_w < 4 or inner_h < 4:
        raise SystemExit(f"tile {box} is smaller than pad={pad}")
    rgba = source.convert("RGBA")
    atlas.paste(rgba.resize((width, height), Image.LANCZOS), (x, y))
    atlas.paste(rgba.resize((inner_w, inner_h), Image.LANCZOS), (x + pad, y + pad))


def pack_atlas() -> Image.Image:
    """@return A 512² RGBA atlas with one GLB albedo per tile."""
    atlas = Image.new("RGBA", (ATLAS, ATLAS), (8, 8, 8, 255))
    for name, box in TILES.items():
        path = TEXTURES / IMAGES[name]
        if not path.is_file():
            raise SystemExit(f"missing {path.name}; run: python glb_textures.py --extract")
        image = prepare_image(name, Image.open(path))
        print(f"  tile {name:<14} {image.size[0]}x{image.size[1]} -> {box}")
        paste_tile(atlas, image, box)
    TEXTURES.mkdir(parents=True, exist_ok=True)
    atlas.save(PREVIEW)
    print(f"  preview {PREVIEW}")
    return atlas


def quantise(values: np.ndarray) -> np.ndarray:
    packed = np.round((values - UV_TRANSLATION) / UV_SCALE * PACKED_MAX)
    return np.clip(packed, -32767, 32767).astype(np.int16)


def uv_transform(box: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
    """@return `(scale_u, scale_v, offset_u, offset_v)` mapping 0..1 into the padded tile."""
    x, y, width, height = box
    pad = tile_pad(box)
    return (
        (width - 2 * pad) / ATLAS,
        (height - 2 * pad) / ATLAS,
        (x + pad) / ATLAS,
        (y + pad) / ATLAS,
    )


def original_uvs(tag: int) -> bytes:
    """@return The stride-24 texcoord body from `0698_24` (0-1 UVs, not tiled)."""
    if not UV_SOURCE.is_file():
        raise SystemExit(f"missing {UV_SOURCE.name}; need the pre-tile UV layer")
    return Package(UV_SOURCE).read_entry(entry_index_of(tag))


def remap_buffer(uv_body: bytes, indices: np.ndarray, parts, offsets: dict[int, str]) -> bytes:
    """@return `uv_body` with primary UVs scaled into tiles. Other stride-24 fields copied."""
    if len(uv_body) % TEXCOORD_STRIDE:
        raise SystemExit(f"texcoord body {len(uv_body)} B is not stride {TEXCOORD_STRIDE}")
    words = np.frombuffer(uv_body, dtype=np.int16).reshape(-1, TEXCOORD_STRIDE // 2).copy()
    u = words[:, 0].astype(np.float64) / PACKED_MAX * UV_SCALE + UV_TRANSLATION
    v = words[:, 1].astype(np.float64) / PACKED_MAX * UV_SCALE + UV_TRANSLATION
    owner = np.full(len(words), -1, dtype=np.int32)
    names = list(TILES)
    name_id = {name: index for index, name in enumerate(names)}

    for _material, _primitive, offset, count, lod in parts:
        if lod != 0 or not count:
            continue
        name = offsets.get(offset)
        if name is None:
            raise SystemExit(
                f"live index offset {offset:,} is not a five-part-split range; "
                "restore 100421 and re-run assign_chest_mask.py")
        verts = np.unique(indices[offset: offset + count])
        clash = owner[verts] >= 0
        if clash.any():
            raise SystemExit(f"{name} shares {int(clash.sum())} vertices with another part")
        owner[verts] = name_id[name]
        print(f"    {name:<14} {len(verts):5,} verts  uv "
              f"u {u[verts].min():.3f}..{u[verts].max():.3f}  "
              f"v {v[verts].min():.3f}..{v[verts].max():.3f}")

    missed = int((owner < 0).sum())
    if missed:
        print(f"    {missed} verts in no drawn part; UVs left alone")

    for name, box in TILES.items():
        scale_u, scale_v, offset_u, offset_v = uv_transform(box)
        mask = owner == name_id[name]
        u[mask] = np.clip(u[mask], 0.0, 1.0) * scale_u + offset_u
        v[mask] = np.clip(v[mask], 0.0, 1.0) * scale_v + offset_v
        print(f"    {name:<14} -> tile {box}  "
              f"u {u[mask].min():.3f}..{u[mask].max():.3f}  "
              f"v {v[mask].min():.3f}..{v[mask].max():.3f}")

    words[:, 0] = quantise(u)
    words[:, 1] = quantise(v)
    out = words.tobytes()
    if len(out) != len(uv_body):
        raise SystemExit(f"texcoord size changed {len(uv_body)} -> {len(out)}")
    return out


def add(work: dict[Path, dict[int, bytes]], packages, tag: int, data: bytes) -> None:
    pkg, index = locate(tag, packages)
    live = pkg.read_entry(index)
    if len(live) != len(data):
        raise SystemExit(
            f"0x{tag:08X} live {len(live):,} B, replacement {len(data):,} B; "
            "refusing a resize")
    work.setdefault(pkg.path, {})[index] = data
    print(f"  queue 0x{tag:08X}  {pkg.path.name} entry {index}  {len(data):,} B")


def main() -> None:
    write = "--write" in sys.argv
    print("=== pack five GLB albedos into 0x80B3611D, scale UVs per part ===")
    offsets = index_offsets()
    print("index ranges from groups.json:")
    for offset, name in offsets.items():
        print(f"  {name:<14} starts at index {offset:,}")

    print("\npacking atlas")
    atlas = pack_atlas()

    packages = newest_of_each()
    buf_pkg, buf_index = locate(BUFFER, packages)
    buf_size = buf_pkg.entries[buf_index].size
    print(f"\nbuffer 0x{BUFFER:08X}  {buf_pkg.path.name} entry {buf_index}  {buf_size:,} B")
    if buf_size != 327_680:
        raise SystemExit(f"expected mip0+1 327,680 B, live is {buf_size:,}")

    blob = mip_chain(atlas, ATLAS, ATLAS, FORMAT_TOTAL)
    print(f"  encoded {len(blob):,} B, writing first {buf_size:,} (mip 0+1). small mips left alone.")

    work: dict[Path, dict[int, bytes]] = {}
    add(work, packages, BUFFER, blob[:buf_size])

    seen_uv: dict[int, bytes] = {}
    for tag in CHESTS:
        pkg, index = locate(tag, packages)
        model = Model(tag, pkg.read_entry(index))
        mesh = model.meshes[0]
        scale = struct.unpack_from("<2f", model.data, 0x70)
        translation = struct.unpack_from("<2f", model.data, 0x78)
        if scale != (UV_SCALE, UV_SCALE) or translation != (UV_TRANSLATION, UV_TRANSLATION):
            raise SystemExit(
                f"0x{tag:08X} texcoord frame is {scale}/{translation}, not 0.5/0.5")
        print(f"\n0x{tag:08X}  uv 0x{mesh.texcoord_buffer:08X}  "
              f"idx 0x{mesh.index_buffer:08X}")
        idx_pkg, idx_i = locate(mesh.index_buffer, packages)
        indices = np.frombuffer(idx_pkg.read_entry(idx_i), dtype=np.uint16)
        uv_body = original_uvs(mesh.texcoord_buffer)
        remapped = remap_buffer(uv_body, indices, mesh.parts, offsets)
        seen_uv[mesh.texcoord_buffer] = remapped
        add(work, packages, mesh.texcoord_buffer, remapped)

    print(f"\n{sum(len(v) for v in work.values())} entries across {len(work)} packages")
    for path, replacements in sorted(work.items()):
        print(f"  {path.name}: {len(replacements)} entries")
    if not write:
        print("dry-run; nothing written. Re-run with --write (Destiny closed).")
        return
    write_all(work, "")
    print("wrote. Next launch: character select. Skin should be the GLB green + tattoos "
          "(384 tile, boosted). Tank black, no white vest. Mask dark. "
          "Still brown with no green -> the dye PS ate hue; restore "
          "python known_good.py --restore --snapshot=20260822-135112.json")


if __name__ == "__main__":
    main()
