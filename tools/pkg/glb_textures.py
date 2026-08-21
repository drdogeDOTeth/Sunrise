"""Pull the custom model's own textures out of the GLB, and report what they are.

Everything extracted from Destiny so far has been geometry - position, texcoord and index buffers.
No texture ever came out of a package, and the material dependency set says why: three shaders, a
sampler, and nothing else. The art we actually intend to put on the body has been sitting in the
GLB the whole time.

A GLB is a 12-byte header then chunks: JSON, then BIN. Images live in the BIN at a `bufferView`,
tagged with a mime type, so no Blender and no glTF library are needed to get them out - and the
PNG/JPEG headers give width and height without an image library either.

What matters for injection is that a Destiny texture is **fixed-size**: the entry keeps its byte
count and the 40-byte header keeps its width, height and format. So each atlas has to be re-encoded
to whatever dimensions the slot it replaces already uses. This reports what we have to work with.

Usage:
    python glb_textures.py                       # list what is in the GLB
    python glb_textures.py --extract             # write them to objs/textures/
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

GLB = Path(r"C:\Chiliz\Destiny2SunriseCharacters\void_4003GasMask.glb")
OUT = Path(__file__).with_name("objs") / "textures"
GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942


def chunks(raw: bytes) -> tuple[dict, bytes]:
    """@return The glTF JSON and the binary chunk."""
    magic, version, _length = struct.unpack_from("<III", raw, 0)
    if magic != GLB_MAGIC:
        raise SystemExit(f"not a GLB: magic 0x{magic:08X}")
    document, binary, at = None, b"", 12
    while at + 8 <= len(raw):
        size, kind = struct.unpack_from("<II", raw, at)
        body = raw[at + 8: at + 8 + size]
        if kind == CHUNK_JSON:
            document = json.loads(body.decode("utf-8"))
        elif kind == CHUNK_BIN:
            binary = body
        at += 8 + size + (-size % 4)
    if document is None:
        raise SystemExit("GLB has no JSON chunk")
    print(f"glTF {version}, {len(raw) / 1e6:.1f} MB, binary chunk {len(binary) / 1e6:.1f} MB")
    return document, binary


def dimensions(data: bytes) -> tuple[int, int] | None:
    """@return `(width, height)` read from a PNG or JPEG header, without an image library."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        # IHDR is always the first chunk, and its width and height are the first two big-endian
        # uint32s of its payload.
        return struct.unpack_from(">II", data, 16)
    if data[:2] == b"\xff\xd8":
        at = 2
        while at + 9 < len(data):
            if data[at] != 0xFF:
                at += 1
                continue
            marker, size = data[at + 1], struct.unpack_from(">H", data, at + 2)[0]
            # SOF0..SOF15 carry the frame size; DHT/DAC/RST and friends never do.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack_from(">HH", data, at + 5)
                return width, height
            at += 2 + size
    return None


def main() -> None:
    if not GLB.is_file():
        raise SystemExit(f"no GLB at {GLB}")
    document, binary = chunks(GLB.read_bytes())

    views = document.get("bufferViews", [])
    images = document.get("images", [])
    materials = document.get("materials", [])

    # Which material uses which image, so an atlas can be matched to the mesh group that wears it.
    used: dict[int, list[str]] = {}
    for material in materials:
        name = material.get("name", "?")
        pbr = material.get("pbrMetallicRoughness", {})
        slots = {
            "baseColour": pbr.get("baseColorTexture"),
            "metalRough": pbr.get("metallicRoughnessTexture"),
            "normal": material.get("normalTexture"),
            "occlusion": material.get("occlusionTexture"),
            "emissive": material.get("emissiveTexture"),
        }
        for slot, reference in slots.items():
            if reference is None:
                continue
            texture = document["textures"][reference["index"]]
            source = texture.get("source")
            if source is not None:
                used.setdefault(source, []).append(f"{name}.{slot}")

    print(f"\n{len(images)} images, {len(materials)} materials\n")
    if "--extract" in sys.argv:
        OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for index, image in enumerate(images):
        view = views[image["bufferView"]]
        start = view.get("byteOffset", 0)
        data = binary[start: start + view["byteLength"]]
        total += len(data)
        size = dimensions(data)
        mime = image.get("mimeType", "?")
        wearers = ", ".join(used.get(index, [])) or "(unused)"
        shape = f"{size[0]}x{size[1]}" if size else "?"
        print(f"  [{index:>2}] {shape:<12} {len(data) / 1e6:>6.2f} MB  {mime:<12} {wearers}")
        if "--extract" in sys.argv:
            suffix = ".png" if "png" in mime else ".jpg"
            name = image.get("name") or f"image{index:02d}"
            path = OUT / f"{index:02d}_{name}{suffix}"
            path.write_bytes(data)
    print(f"\n{total / 1e6:.1f} MB of texture data")
    if "--extract" in sys.argv:
        print(f"extracted to {OUT}")
    else:
        print("Re-run with --extract to write them out.")


if __name__ == "__main__":
    main()
