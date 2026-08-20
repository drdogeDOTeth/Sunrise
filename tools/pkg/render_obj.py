"""
Renders OBJ files to a shaded PNG contact sheet, so geometry can be *identified* rather than guessed.

Offline rendering is the one visual judgement in this project that is trustworthy. The in-game
Guardian draws as a translucent dissolve shell, which is why screenshots produced a false
identification; a model extracted to triangles and drawn here shows exactly what it is.

Two orthographic views, flat-shaded, drawn back to front. Destiny's x is **forward**, so the
(x,z) projection is the **side** and (y,z) - left to right - is the **front**. The labels were
the other way round until 2026-08-20; in a project that identifies content by looking at it, a
mislabelled axis is a real trap.

Usage:
    python render_obj.py out.png a.obj b.obj ...
    python render_obj.py sheet.png --dir objs
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

CELL = 340
MARGIN = 14
LABEL = 34
LIGHT = np.array([0.35, -0.80, 0.49])
LIGHT /= np.linalg.norm(LIGHT)


def read_obj(path: Path) -> tuple[np.ndarray, np.ndarray]:
    points: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("v "):
            parts = line.split()
            points.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif line.startswith("f "):
            parts = [chunk.split("/")[0] for chunk in line.split()[1:]]
            if len(parts) >= 3:
                faces.append((int(parts[0]) - 1, int(parts[1]) - 1, int(parts[2]) - 1))
    return np.asarray(points, dtype=float), np.asarray(faces, dtype=np.int64)


def view(image: Image.Image, origin: tuple[int, int], points: np.ndarray, faces: np.ndarray,
         horizontal: int, scale: float, centre: np.ndarray) -> None:
    """Draws one orthographic projection, painter's algorithm, into a CELL-sized cell."""
    draw = ImageDraw.Draw(image)
    if len(faces) == 0:
        draw.text((origin[0] + 8, origin[1] + 8), "no faces", fill=(150, 40, 40))
        return
    triangles = points[faces]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.where(lengths == 0, 1.0, lengths)
    shade = np.clip(np.abs(normals @ LIGHT), 0.10, 1.0) * 0.75 + 0.20

    depth_axis = 1 if horizontal == 0 else 0
    order = np.argsort(triangles[:, :, depth_axis].mean(axis=1))

    # Model space is z-up; screen y grows downward, so z is negated.
    flat = triangles[:, :, [horizontal, 2]] - centre[[horizontal, 2]]
    screen = np.empty_like(flat)
    screen[:, :, 0] = origin[0] + CELL / 2 + flat[:, :, 0] * scale
    screen[:, :, 1] = origin[1] + CELL / 2 - flat[:, :, 1] * scale

    for index in order:
        value = shade[index]
        colour = (int(value * 150), int(value * 172), int(value * 226))
        draw.polygon([tuple(point) for point in screen[index]], fill=colour)


def main() -> None:
    arguments = sys.argv[1:]
    if not arguments:
        raise SystemExit(__doc__)
    out = Path(arguments[0])
    if "--dir" in arguments:
        sources = sorted(Path(arguments[arguments.index("--dir") + 1]).glob("*.obj"))
    else:
        sources = [Path(name) for name in arguments[1:] if name.endswith(".obj")]
    if not sources:
        raise SystemExit("no OBJ files given")

    columns = len(sources)
    image = Image.new("RGB", (columns * (2 * CELL + MARGIN) + MARGIN, CELL + LABEL + 2 * MARGIN),
                      (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for index, source in enumerate(sources):
        points, faces = read_obj(source)
        left = MARGIN + index * (2 * CELL + MARGIN)
        top = MARGIN + LABEL
        if len(points) == 0:
            draw.text((left + 8, MARGIN), f"{source.stem}: empty", fill=(150, 40, 40))
            continue
        low, high = points.min(axis=0), points.max(axis=0)
        span = high - low
        centre = (low + high) / 2.0
        scale = (CELL - 24) / max(span[[0, 2]].max(), span[[1, 2]].max(), 1e-6)
        draw.text((left + 4, MARGIN),
                  f"{source.stem}   {len(points):,} verts  {len(faces):,} tris   "
                  f"{span[0]:.2f} x {span[1]:.2f} x {span[2]:.2f} m",
                  fill=(20, 20, 20))
        view(image, (left, top), points, faces, 0, scale, centre)
        view(image, (left + CELL, top), points, faces, 1, scale, centre)
        draw.rectangle([left, top, left + 2 * CELL, top + CELL], outline=(210, 210, 210))
        draw.text((left + 6, top + CELL - 16), "side", fill=(90, 90, 90))
        draw.text((left + CELL + 6, top + CELL - 16), "front", fill=(90, 90, 90))
    image.save(out)
    print(f"wrote {out}  ({image.size[0]}x{image.size[1]})")


if __name__ == "__main__":
    main()
