"""Fit an image to the title-screen backdrop quad, and stamp a logo on it.

The backdrop is bound at draw time by size (`docs/MENU_SKIN.md`). The hook hands the game whatever
file it finds, so the file's pixel dimensions are irrelevant on their own. What decides the result
is that **the quad samples a window out of the middle of the texture** and maps that window to a
screen rect wider than 16:9. Hand it a photo unprepared and the middle is cropped out and pulled
sideways.

## The window, measured

Solved by locating two features in both the baked file and a screenshot of it in game - the bright
ceiling light bar's ends horizontally, the bar and the lit ledge vertically:

    u  0.184 .. 0.807   (the middle 62% of the width)
    v  0.006 .. 0.926   (nearly the full height)
    screen rect 1920 x 883, sitting under the mirrored band that occupies the top 197 px

Four things confirm it rather than one. A logo baked at u=0.021 did not appear; all four corner
ticks did not appear; the crowd came back zoomed by exactly the predicted factor; and a circle
baked at the texture centre rendered as a 914x615 ellipse, against arcs measured at x=515 and 1415
versus 516 and 1431 predicted.

An earlier guess put the window at full UV, on the reasoning that a 16:9 source read as only
*slightly* stretched. That was wrong, and wrong in an instructive way: **"looks slightly off" is not
a measurement.** The marks are.

## What this does

Cover-crops the source to the *screen rect's* aspect - fill the screen, trim the overflow, keep
circles circular - and lays it into the UV window, so the crop is what reaches the screen. Anything
outside the window is never seen by this quad, and is filled with a blurred, darkened copy because
the second layer samples out there.

Logos and marks are positioned in **screen** coordinates and mapped back through the window, so
"top-left corner" means the corner of the screen, not of the file.

`--marks` overlays a faint circle and corner ticks. They are drawn pre-distorted, so a correct
render shows a true circle and four ticks at the screen corners. Anything else is the residual
error, and the ellipse's width:height is the size of it.

Usage:
    python make_menu_background.py menu_bg.jpg --logo doge_white.png --out title_bg.png
    python make_menu_background.py sky.jpg --logo-corner tr --logo-height 0.14 --marks
    python make_menu_background.py sky.jpg --window 0.19,0.81,0.0,0.93   # re-measured window

Then in `C:\\Sunrise\\bin\\x64\\Sunrise\\menu_probe.txt`:

    3030 940  title_bg.png
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
except ImportError:  # pragma: no cover - guidance beats a traceback
    print("needs Pillow:  python -m pip install pillow")
    raise SystemExit(2)

# Measured, not assumed - see the module docstring for how, and for what confirmed it.
WINDOW = (0.184, 0.807, 0.006, 0.926)
RECT = (1920, 883)
SLOT = (3030, 940)

CORNERS = {"tl": (0.0, 0.0), "tr": (1.0, 0.0), "bl": (0.0, 1.0), "br": (1.0, 1.0)}


def option(name: str, fallback: str = "") -> str:
    flag = f"--{name}"
    if flag in sys.argv:
        at = sys.argv.index(flag)
        if at + 1 < len(sys.argv):
            return sys.argv[at + 1]
    return fallback


def numbers(text: str, count: int) -> tuple[float, ...]:
    parts = tuple(float(p) for p in text.replace("x", ",").split(","))
    if len(parts) != count:
        raise SystemExit(f"expected {count} comma-separated numbers, got '{text}'")
    return parts


def extend_margins(canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Mirror the window's edges outward to fill the rest of the texture.

    **The margin is not dead space.** The title screen reads only the window, but the character
    select screen reads the *whole* texture with wrap, and puts the u=0/1 seam across the middle of
    the screen. A blurred dark fill out here is invisible on one screen and a black band down the
    centre of the other.

    Mirroring is what makes both work: it is seamless at the window boundary by construction, and
    it carries real picture rather than a hole. The source's left and right edges are dark walls,
    so the wrap seam lands somewhere already dark.
    """
    x0, y0, x1, y1 = box
    width, height = canvas.size

    if x0 > 0:  # left margin, reflected about the window's left edge
        take = min(x0, x1 - x0)
        strip = canvas.crop((x0, y0, x0 + take, y1)).transpose(Image.FLIP_LEFT_RIGHT)
        canvas.paste(strip, (x0 - take, y0))
    if x1 < width:  # right margin
        take = min(width - x1, x1 - x0)
        strip = canvas.crop((x1 - take, y0, x1, y1)).transpose(Image.FLIP_LEFT_RIGHT)
        canvas.paste(strip, (x1, y0))

    # Now the full width is filled, so the vertical margins mirror across all of it.
    if y0 > 0:
        take = min(y0, y1 - y0)
        strip = canvas.crop((0, y0, width, y0 + take)).transpose(Image.FLIP_TOP_BOTTOM)
        canvas.paste(strip, (0, y0 - take))
    if y1 < height:
        take = min(height - y1, y1 - y0)
        strip = canvas.crop((0, y1 - take, width, y1)).transpose(Image.FLIP_TOP_BOTTOM)
        canvas.paste(strip, (0, y1))


def relight(image: Image.Image, gamma: float, saturation: float) -> Image.Image:
    """Lift a dark photo for a screen that sits behind white text.

    **Gamma, not brightness.** These backdrops already have blown-out practical lights in them, and
    a linear multiplier drives those further into clipping while barely touching the crowd. A gamma
    below 1 lifts the shadows and midtones and leaves the highlights where they are.

    Applied to the source, before the logo goes on, so a white mark is never dimmed with it.
    """
    if gamma != 1.0:
        curve = [round(255 * (i / 255) ** gamma) for i in range(256)]
        image = image.point(curve * len(image.getbands()))
    if saturation != 1.0:
        image = ImageEnhance.Color(image).enhance(saturation)
    return image


def cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """@return `image` cropped to width:height, then scaled to exactly that size.

    Cover, not fit: the frame is filled and the overflow trimmed evenly, so nothing distorts.
    """
    want, have = width / height, image.width / image.height
    if have > want:  # source is wider - trim the sides
        keep = round(image.height * want)
        box = ((image.width - keep) // 2, 0, (image.width - keep) // 2 + keep, image.height)
    else:  # source is taller - trim top and bottom
        keep = round(image.width / want)
        box = (0, (image.height - keep) // 2, image.width, (image.height - keep) // 2 + keep)
    return image.resize((width, height), Image.LANCZOS, box=box)


class Quad:
    """Maps screen coordinates to texture pixels through the measured UV window."""

    def __init__(self, window, rect, slot):
        self.u0, self.u1, self.v0, self.v1 = window
        self.rect_w, self.rect_h = rect
        self.slot_w, self.slot_h = slot
        self.x0 = self.u0 * self.slot_w
        self.y0 = self.v0 * self.slot_h
        self.box_w = (self.u1 - self.u0) * self.slot_w
        self.box_h = (self.v1 - self.v0) * self.slot_h

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (round(self.x0), round(self.y0),
                round(self.x0 + self.box_w), round(self.y0 + self.box_h))

    def point(self, sx: float, sy: float) -> tuple[float, float]:
        """@return Texture pixel for a screen pixel."""
        return (self.x0 + sx / self.rect_w * self.box_w,
                self.y0 + sy / self.rect_h * self.box_h)

    def size(self, sw: float, sh: float) -> tuple[float, float]:
        """@return Texture pixel size for a screen pixel size, pre-distorted."""
        return (sw / self.rect_w * self.box_w, sh / self.rect_h * self.box_h)


def stamp_logo(canvas: Image.Image, quad: Quad, logo: Image.Image, corner: str,
               height_frac: float, margin_frac: float) -> str:
    """Composite `logo` into a corner **of the screen**, sized as a fraction of screen height.

    A white mark on a photograph needs help to stay legible, so it carries a soft dark shadow.
    """
    ink = logo.getbbox()
    if ink is not None:
        logo = logo.crop(ink)

    tall = quad.rect_h * height_frac
    wide = logo.width * tall / logo.height
    pad = quad.rect_h * margin_frac
    fx, fy = CORNERS[corner]
    sx = fx * (quad.rect_w - wide - 2 * pad) + pad
    sy = fy * (quad.rect_h - tall - 2 * pad) + pad

    tw, th = quad.size(wide, tall)
    tx, ty = quad.point(sx, sy)
    logo = logo.resize((max(round(tw), 8), max(round(th), 8)), Image.LANCZOS)

    blur = max(round(min(logo.size) * 0.05), 2)
    shadow = Image.new("RGBA", (logo.width + blur * 6, logo.height + blur * 6), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 190), (blur * 3, blur * 3), logo)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(shadow, (round(tx) - blur * 3, round(ty) - blur * 3))
    canvas.alpha_composite(logo, (round(tx), round(ty)))
    return (f"{wide:.0f}x{tall:.0f} screen px at ({sx:.0f}, {sy:.0f}) "
            f"-> {logo.width}x{logo.height} texture px at ({tx:.0f}, {ty:.0f})")


def add_marks(canvas: Image.Image, quad: Quad) -> None:
    """Registration marks laid out in screen space, so a correct render shows a true circle.

    Deliberately low-contrast. These ride on a finished background, so the next screenshot doubles
    as a measurement without spoiling the look.
    """
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    ink, faint = (255, 255, 255, 52), (255, 255, 255, 30)
    line = max(round(quad.box_h * 0.003), 2)

    r = quad.rect_h * 0.34
    cx, cy = quad.point(quad.rect_w / 2, quad.rect_h / 2)
    rx, ry = quad.size(r, r)
    draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=ink, width=line)
    draw.line([(cx - rx, cy), (cx + rx, cy)], fill=faint, width=line)
    draw.line([(cx, cy - ry), (cx, cy + ry)], fill=faint, width=line)

    # Ticks inset from the screen corners, so a small error still leaves them on screen.
    arm_x, arm_y = quad.size(quad.rect_h * 0.05, quad.rect_h * 0.05)
    for fx, fy in CORNERS.values():
        inset_x = quad.rect_w * (0.02 if fx == 0 else 0.98)
        inset_y = quad.rect_h * (0.02 if fy == 0 else 0.98)
        x, y = quad.point(inset_x, inset_y)
        sx = -1 if fx else 1
        sy = -1 if fy else 1
        draw.line([(x, y), (x + sx * arm_x, y)], fill=ink, width=line * 2)
        draw.line([(x, y), (x, y + sy * arm_y)], fill=ink, width=line * 2)
    canvas.alpha_composite(layer)


def main() -> int:
    plain = [a for a in sys.argv[1:] if not a.startswith("--")]
    for name in ("out", "window", "rect", "slot", "logo", "logo-corner", "logo-height",
                 "logo-margin", "gamma", "saturation"):
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

    window = numbers(option("window", ",".join(str(v) for v in WINDOW)), 4)
    rect = tuple(int(v) for v in numbers(option("rect", "%dx%d" % RECT), 2))
    slot = tuple(int(v) for v in numbers(option("slot", "%dx%d" % SLOT), 2))
    quad = Quad(window, rect, slot)
    out = Path(option("out", "title_bg.png"))

    gamma = float(option("gamma", "1.0"))
    saturation = float(option("saturation", "1.0"))
    image = relight(Image.open(source).convert("RGB"), gamma, saturation).convert("RGBA")
    had = image.width / image.height
    want = rect[0] / rect[1]
    print(f"{source.name}  {image.width}x{image.height} (aspect {had:.3f})")
    if gamma != 1.0 or saturation != 1.0:
        print(f"  relit: gamma {gamma} (shadows lifted, highlights held), "
              f"saturation {saturation}")
    print(f"  quad window u {window[0]:.3f}..{window[1]:.3f}  v {window[2]:.3f}..{window[3]:.3f}"
          f"  -> screen {rect[0]}x{rect[1]} (aspect {want:.3f})")

    canvas = Image.new("RGBA", slot, (0, 0, 0, 255))
    box = quad.box
    # Crop at the SCREEN's aspect, then squeeze into the window's pixel box. The quad undoes that
    # squeeze on the way to the screen. Cropping at the box's own aspect instead only looks right
    # while the two happen to agree, which they do not for every slot.
    fitted = cover(image, *rect).resize((box[2] - box[0], box[3] - box[1]), Image.LANCZOS)
    canvas.paste(fitted, (box[0], box[1]))
    extend_margins(canvas, box)
    edge = "top/bottom" if had < want else "the sides"
    lost = 1 - min(had, want) / max(had, want)
    print(f"  cover-cropped to {want:.3f} ({100 * lost:.1f}% trimmed off {edge}) and laid into"
          f" the window at {box[0]},{box[1]} {box[2] - box[0]}x{box[3] - box[1]} px")

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
        where = stamp_logo(canvas, quad, Image.open(logo_file).convert("RGBA"), corner,
                           float(option("logo-height", "0.13")),
                           float(option("logo-margin", "0.05")))
        print(f"  {logo_file.name} {corner}: {where}")

    if "--marks" in sys.argv:
        add_marks(canvas, quad)
        print("  marks on: a true circle and four corner ticks mean the window is right")

    canvas.convert("RGB").save(out)
    print(f"  wrote {out}  {slot[0]}x{slot[1]}  ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
