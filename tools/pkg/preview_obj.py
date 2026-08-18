"""
Renders extracted OBJ files to a contact sheet, so a mesh can be identified by eye.

Naming a mesh is the one step no amount of table decoding settles here: the item -> model chain
runs through investment tables Charm does not decode below Witch Queen, so a model carries no name.
Its shape does. Six candidates rendered front, side and top is enough to tell a helmet from a
shoulder pad in one glance, which is what the bounding-box filter in  can only
guess at.

Orthographic, z-buffered by triangle depth, flat-shaded from the view axis. Destiny is Z-up.

Usage:
    python extract_mesh.py --helmets --out-dir out
    python preview_obj.py out sheet.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SIZE = 260
# Destiny is Z-up, so a front view is (x, z) with y as depth, and a side view is (y, z).
VIEWS = (("front", (0, 2, 1), (1, -1)), ("side", (1, 2, 0), (1, -1)), ("top", (0, 1, 2), (1, -1)))


def load(path: Path):
    vertices, faces = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("v "):
            vertices.append([float(value) for value in line.split()[1:4]])
        elif line.startswith("f "):
            faces.append([int(part.split("/")[0]) - 1 for part in line.split()[1:4]])
    return np.array(vertices, dtype=np.float64), np.array(faces, dtype=np.int32)


def render(vertices, faces, axes, flip):
    """Z-buffered flat shading, orthographic, lit from the front."""
    horizontal, vertical, depth = axes
    points = vertices[:, [horizontal, vertical]].copy()
    points[:, 0] *= flip[0]
    points[:, 1] *= flip[1]
    low, high = points.min(axis=0), points.max(axis=0)
    extent = max(high - low)
    if extent <= 0:
        return Image.new("L", (SIZE, SIZE), 20)
    scale = (SIZE - 24) / extent
    centre = (low + high) / 2
    screen = (points - centre) * scale + SIZE / 2

    tri = vertices[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths == 0] = 1
    normals /= lengths[:, None]
    shade = np.clip(np.abs(normals[:, depth]) * 200 + 40, 0, 255).astype(np.uint8)
    order = np.argsort(tri[:, :, depth].mean(axis=1))

    image = Image.new("L", (SIZE, SIZE), 18)
    draw = ImageDraw.Draw(image)
    for index in order:
        a, b, c = faces[index]
        draw.polygon([tuple(screen[a]), tuple(screen[b]), tuple(screen[c])],
                     fill=int(shade[index]))
    return image


paths = sorted(Path(sys.argv[1]).glob("*.obj"))
columns = len(VIEWS)
sheet = Image.new("RGB", (SIZE * columns + 150, SIZE * len(paths)), (12, 12, 14))
label = ImageDraw.Draw(sheet)
for row, path in enumerate(paths):
    vertices, faces = load(path)
    for column, (_name, axes, flip) in enumerate(VIEWS):
        sheet.paste(render(vertices, faces, axes, flip).convert("RGB"),
                    (150 + column * SIZE, row * SIZE))
    label.text((10, row * SIZE + SIZE // 2 - 16), path.stem.replace("model_", "0x"),
               fill=(230, 230, 235))
    label.text((10, row * SIZE + SIZE // 2), f"{len(vertices):,} v", fill=(150, 150, 160))
    label.text((10, row * SIZE + SIZE // 2 + 16), f"{len(faces):,} f", fill=(150, 150, 160))
sheet.save(sys.argv[2])
print(f"{len(paths)} models -> {sys.argv[2]}")
