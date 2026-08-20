"""
Renders OBJ point clouds side by side as orthographic silhouettes, to judge a wrap before installing.

`preview_obj.py` renders a contact sheet to identify a mesh. This answers a different question: is a
*transformed* mesh still the right shape? A wrap that collapsed, inverted, fitted to the wrong axis,
or kept the source's pose is unmistakable in a silhouette and completely invisible in the summary
statistics the wrap prints. One launch costs minutes; this costs a second.

Two panels per input - front (Y left-right) and side (X front-back), Z always up, in Destiny space.

## Two details that decide whether the picture tells the truth

- **Faces filter the vertices.** An extract that emits every vertex but only LOD-0 faces renders as
  point soup, because the cheap LOD copies sit inside the main body and thicken every silhouette.
  When a file has faces, only vertices some face references are drawn.
- **One shared scale for both axes.** Fitting each axis independently would normalise away exactly
  the squash or stretch being looked for.

Usage:
    python compare_silhouettes.py body.obj wrapped.obj out.png
    python compare_silhouettes.py a.obj b.obj c.obj sheet.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

WIDTH, HEIGHT = 340, 620
PAD = 18
BACKGROUND = (16, 18, 22)
INK = (120, 220, 255)


def read_obj(path: Path) -> np.ndarray:
    """@return Vertices, restricted to those a face references when the file has faces."""
    points: list[tuple[float, float, float]] = []
    used: set[int] = set()
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if line.startswith("v "):
            _, x, y, z = line.split()[:4]
            points.append((float(x), float(y), float(z)))
        elif line.startswith("f "):
            for token in line.split()[1:]:
                index = int(token.split("/")[0])
                used.add(index - 1 if index > 0 else len(points) + index)
    array = np.asarray(points, dtype=np.float64)
    if not used:
        return array
    keep = np.fromiter(sorted(used), dtype=np.int64)
    return array[keep[(keep >= 0) & (keep < len(array))]]


def panel(points: np.ndarray, horizontal: int) -> Image.Image:
    """@param horizontal Axis drawn left-right; Z is always up."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    if len(points) == 0:
        return image
    across = points[:, horizontal]
    up = points[:, 2]
    span = max(across.max() - across.min(), up.max() - up.min(), 1e-9)
    scale = (min(WIDTH, HEIGHT) - 2 * PAD) / span
    centre = (across.min() + across.max()) / 2
    x = ((across - centre) * scale + WIDTH / 2).astype(np.int32)
    y = (HEIGHT - PAD - (up - up.min()) * scale).astype(np.int32)
    keep = (x >= 0) & (x < WIDTH) & (y >= 0) & (y < HEIGHT)
    buffer = np.asarray(image).copy()
    buffer[y[keep], x[keep]] = INK
    return Image.fromarray(buffer)


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    sources = [Path(argument) for argument in sys.argv[1:-1]]
    output = Path(sys.argv[-1])

    panels: list[Image.Image] = []
    for path in sources:
        points = read_obj(path)
        if len(points) == 0:
            raise SystemExit(f"{path} has no vertices")
        print(f"{path.name}: {len(points):,} points  "
              f"x {points[:, 0].min():.2f}..{points[:, 0].max():.2f}  "
              f"y {points[:, 1].min():.2f}..{points[:, 1].max():.2f}  "
              f"z {points[:, 2].min():.2f}..{points[:, 2].max():.2f}")
        panels.append(panel(points, 1))
        panels.append(panel(points, 0))

    sheet = Image.new("RGB", (WIDTH * len(panels), HEIGHT), BACKGROUND)
    for index, image in enumerate(panels):
        sheet.paste(image, (index * WIDTH, 0))
    sheet.save(output)
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
