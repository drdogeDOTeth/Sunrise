"""Draw a calibration target for a menu texture slot, so UV mapping is measured, not guessed.

Binding an image to a menu slot stretches it, because the quad's screen footprint and the
texture's aspect are unrelated. Correcting that needs two numbers nobody can read off a
screenshot of a photograph: **which part of the texture reaches the screen**, and **what scale it
arrives at**. A photograph hides both. A grid shows them.

What is on the target and why:

* **Concentric circles at the centre** — a circle is the only shape whose distortion is
  unambiguous. Whatever ellipse comes back gives the x:y scale ratio directly.
* **A labelled grid** (A1, B2, ...) — tells you which region of the texture maps where, and
  whether the quad samples the whole 0..1 range or a crop of it.
* **Coloured corner wedges** — reveal a flip, a rotation, or a corner falling offscreen.
* **An edge ruler in percent** — read the crop straight off the screenshot.
* **The slot size printed large** — so two layers bound at once are never confused.

Usage:
    python make_calibration.py 3030 940 --out calib_a.png
    python make_calibration.py 1920 1200 --out calib_b.png

Then in `menu_probe.txt`:

    3030 940  calib_a.png
    1920 1200 calib_b.png

Launch, screenshot the title screen, and the correction falls out of the picture.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    print("needs Pillow:  python -m pip install pillow")
    raise SystemExit(2)

CORNERS = (((0, 0), (255, 60, 60)), ((1, 0), (60, 255, 60)),
           ((0, 1), (60, 120, 255)), ((1, 1), (255, 220, 40)))


def option(name: str, fallback: str = "") -> str:
    flag = f"--{name}"
    if flag in sys.argv:
        at = sys.argv.index(flag)
        if at + 1 < len(sys.argv):
            return sys.argv[at + 1]
    return fallback


def font_of(size: int):
    for name in ("consolab.ttf", "arialbd.ttf", "arial.ttf"):
        path = Path("C:/Windows/Fonts") / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def main() -> int:
    numbers = [a for a in sys.argv[1:] if a.isdigit()]
    if len(numbers) < 2:
        print(__doc__)
        return 2
    width, height = int(numbers[0]), int(numbers[1])
    out = Path(option("out", f"calib_{width}x{height}.png"))
    cells = int(option("cells", "12"))

    image = Image.new("RGBA", (width, height), (12, 12, 16, 255))
    draw = ImageDraw.Draw(image)

    # Grid. Alternating fill keeps cell boundaries readable even when heavily scaled.
    step_x = width / cells
    rows = max(int(round(cells * height / width)), 2)
    step_y = height / rows
    label = font_of(max(int(min(step_x, step_y) * 0.34), 10))
    for row in range(rows):
        for col in range(cells):
            x0, y0 = col * step_x, row * step_y
            shade = 46 if (row + col) % 2 else 26
            draw.rectangle([x0, y0, x0 + step_x, y0 + step_y], fill=(shade, shade, shade + 8, 255),
                           outline=(90, 90, 100, 255))
            draw.text((x0 + step_x * 0.5, y0 + step_y * 0.5),
                      f"{chr(65 + row % 26)}{col + 1}", font=label,
                      fill=(190, 190, 200, 255), anchor="mm")

    # Circles: the aspect readout. Radius steps in units of the SHORT side, so a correct render
    # shows circles and any stretch shows the ratio directly.
    unit = min(width, height) / 2
    for k in (0.25, 0.5, 0.75, 0.95):
        r = unit * k
        draw.ellipse([width / 2 - r, height / 2 - r, width / 2 + r, height / 2 + r],
                     outline=(255, 90, 200, 255), width=max(int(unit * 0.012), 2))

    # Corner wedges, each a different colour, so a flip or an offscreen corner is obvious.
    size = min(width, height) * 0.16
    for (cx, cy), colour in CORNERS:
        x = cx * width
        y = cy * height
        sx = -1 if cx else 1
        sy = -1 if cy else 1
        draw.polygon([(x, y), (x + sx * size, y), (x, y + sy * size)], fill=colour + (255,))

    # Percent ruler along the top and left edges: read the sampled crop off the screenshot.
    tick = font_of(max(int(min(width, height) * 0.03), 11))
    for pct in range(0, 101, 10):
        x = width * pct / 100
        draw.line([(x, 0), (x, height * 0.035)], fill=(255, 255, 255, 255), width=3)
        if 0 < pct < 100:
            draw.text((x, height * 0.055), f"{pct}", font=tick, fill=(255, 255, 255, 255),
                      anchor="ma")
        y = height * pct / 100
        draw.line([(0, y), (width * 0.02, y)], fill=(255, 255, 255, 255), width=3)

    # The slot size, large, so two layers bound at once cannot be mixed up.
    big = font_of(max(int(min(width, height) * 0.16), 24))
    draw.text((width / 2, height * 0.5), f"{width}x{height}", font=big,
              fill=(255, 255, 255, 235), anchor="mm",
              stroke_width=max(int(min(width, height) * 0.01), 2), stroke_fill=(0, 0, 0, 255))

    image.save(out)
    print(f"wrote {out}  {width}x{height}  grid {cells}x{rows}  aspect {width / height:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
