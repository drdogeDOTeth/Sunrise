"""Fit an image to the title-screen backdrop quad, and stamp a logo on it.

The backdrop is bound at draw time by size (`docs/MENU_SKIN.md`). The hook hands the game whatever
file it finds, so **the file's pixel dimensions are irrelevant** — the quad maps UV 0..1 across
whatever it is given. What matters is one number: **the quad's screen aspect**. Give it a 16:9
photo and a wider quad pulls it sideways.

That number was not guessed. A 16:9 source (3840x2160) on this quad read as *slightly* stretched,
and only one geometry produces "slightly":

    rect 2.157:1, full UV   ->  1.21x horizontal   <- matches
    rect 16:9,    full UV   ->  none               <- would have looked perfect
    middle-67% crop, either ->  1.50x .. 1.82x     <- would have looked wrecked

So the correction is a **cover crop to the quad's aspect**, never a resize: fill the screen, lose a
little off the top and bottom, keep circles circular.

Because the file's own aspect is what lands on screen, this writes the output *at the quad's
aspect*. Nothing is pre-warped, so the PNG on disk looks exactly like the thing in game.

## Confirming the aspect rather than trusting it

`--marks` overlays a faint circle and corner ticks at low opacity. One screenshot then settles it:

* circle round -> the aspect is right
* circle an ellipse -> its width:height **is** the remaining correction, feed it to `--aspect`
* a corner tick missing -> the quad crops, and by how much

Usage:
    python make_menu_background.py menu_bg.jpg --out title_bg.png
    python make_menu_background.py menu_bg.jpg --logo doge_white.png --marks
    python make_menu_background.py sky.jpg --aspect 2.0 --logo-corner tr --logo-height 0.14

Then in `C:\\Sunrise\\bin\\x64\\Sunrise\\menu_probe.txt`:

    3030 940  title_bg.png
    1920 1200 title_bg.png
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:  # pragma: no cover - guidance beats a traceback
    print("needs Pillow:  python -m pip install pillow")
    raise SystemExit(2)

# Measured, not assumed - see the module docstring for how it was pinned down.
QUAD_ASPECT = 2.157
OUT_WIDTH = 2880

CORNERS = {"tl": (0.0, 0.0), "tr": (1.0, 0.0), "bl": (0.0, 1.0), "br": (1.0, 1.0)}


def option(name: str, fallback: str = "") -> str:
    flag = f"--{name}"
    if flag in sys.argv:
        at = sys.argv.index(flag)
        if at + 1 < len(sys.argv):
            return sys.argv[at + 1]
    return fallback


def cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """@return `image` cropped to width:height, then scaled to exactly that size.

    Cover, not fit: the frame is filled and the overflow is trimmed evenly, so nothing distorts.
    """
    want = width / height
    have = image.width / image.height
    if have > want:  # source is wider - trim the sides
        keep = round(image.height * want)
        box = ((image.width - keep) // 2, 0, (image.width - keep) // 2 + keep, image.height)
    else:  # source is taller - trim top and bottom
        keep = round(image.width / want)
        box = (0, (image.height - keep) // 2, image.width, (image.height - keep) // 2 + keep)
    return image.resize((width, height), Image.LANCZOS, box=box)


def stamp_logo(canvas: Image.Image, logo: Image.Image, corner: str,
               height_frac: float, margin_frac: float) -> tuple[int, int, int, int]:
    """Composite `logo` into a corner of `canvas`, sized as a fraction of canvas height.

    A white mark on a photograph needs help to stay legible, so it carries a soft dark shadow.
    @return The placed box, for reporting.
    """
    ink = logo.getbbox()
    if ink is not None:
        logo = logo.crop(ink)
    tall = max(round(canvas.height * height_frac), 8)
    wide = max(round(logo.width * tall / logo.height), 8)
    logo = logo.resize((wide, tall), Image.LANCZOS)

    fx, fy = CORNERS[corner]
    pad = round(canvas.height * margin_frac)
    x = round(fx * (canvas.width - wide - 2 * pad)) + pad
    y = round(fy * (canvas.height - tall - 2 * pad)) + pad

    blur = max(round(tall * 0.05), 2)
    shadow = Image.new("RGBA", (wide + blur * 6, tall + blur * 6), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 190), (blur * 3, blur * 3), logo)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(shadow, (x - blur * 3, y - blur * 3))
    canvas.alpha_composite(logo, (x, y))
    return x, y, wide, tall


def add_marks(canvas: Image.Image) -> None:
    """Faint registration marks: a circle for aspect, corner ticks for crop.

    Deliberately low-contrast. These ride on a finished background so the next screenshot doubles
    as a measurement without spoiling the look.
    """
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    ink = (255, 255, 255, 46)
    line = max(round(canvas.height * 0.0025), 2)

    r = canvas.height * 0.32
    cx, cy = canvas.width / 2, canvas.height / 2
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ink, width=line)
    draw.line([(cx - r, cy), (cx + r, cy)], fill=(255, 255, 255, 26), width=line)
    draw.line([(cx, cy - r), (cx, cy + r)], fill=(255, 255, 255, 26), width=line)

    arm = round(canvas.height * 0.05)
    for fx, fy in CORNERS.values():
        x = round(fx * (canvas.width - 1))
        y = round(fy * (canvas.height - 1))
        sx = -1 if fx else 1
        sy = -1 if fy else 1
        draw.line([(x, y), (x + sx * arm, y)], fill=ink, width=line * 2)
        draw.line([(x, y), (x, y + sy * arm)], fill=ink, width=line * 2)
    canvas.alpha_composite(layer)


def main() -> int:
    plain = [a for a in sys.argv[1:] if not a.startswith("--")]
    for name in ("out", "aspect", "width", "logo", "logo-corner", "logo-height", "logo-margin"):
        value = option(name)
        if value in plain:
            plain.remove(value)
    if not plain:
        print(__doc__)
        return 2

    source = Path(plain[0])
    if not source.is_file():
        print(f"no such image: {source}")
        return 1

    aspect = float(option("aspect", str(QUAD_ASPECT)))
    width = int(option("width", str(OUT_WIDTH)))
    height = max(round(width / aspect), 2)
    out = Path(option("out", "title_bg.png"))

    image = Image.open(source).convert("RGBA")
    had = image.width / image.height
    canvas = cover(image, width, height)
    print(f"{source.name}  {image.width}x{image.height} (aspect {had:.3f})")
    edge = "top/bottom" if had < aspect else "the sides"
    lost = 1 - min(had, aspect) / max(had, aspect)
    print(f"  cover-cropped to {width}x{height} (aspect {aspect:.3f}) - "
          f"{100 * lost:.1f}% trimmed off {edge}")

    logo_path = option("logo")
    if logo_path:
        logo_file = Path(logo_path)
        if not logo_file.is_file():
            print(f"no such logo: {logo_file}")
            return 1
        corner = option("logo-corner", "tl")
        if corner not in CORNERS:
            print(f"--logo-corner must be one of {', '.join(CORNERS)}")
            return 1
        box = stamp_logo(canvas, Image.open(logo_file).convert("RGBA"), corner,
                         float(option("logo-height", "0.12")),
                         float(option("logo-margin", "0.045")))
        print(f"  {logo_file.name} at {corner}: {box[2]}x{box[3]} px at ({box[0]}, {box[1]})"
              f" = {100 * box[0] / width:.1f}%, {100 * box[1] / height:.1f}% of the screen")

    if "--marks" in sys.argv:
        add_marks(canvas)
        print("  registration marks on: circle = aspect, corner ticks = crop")

    canvas.convert("RGB").save(out, quality=95)
    print(f"  wrote {out}  ({out.stat().st_size / 1024:.0f} KB)"
          if out.exists() else f"  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
