"""Draw a replacement for the title-screen subtitle, matching Destiny's lockup style.

The main menu's "SHADOWKEEP" is a **1110x61** texture — identified by the flat-colour probe, not
guessed (`docs/MENU_SKIN.md`). The hook binds any image over it at draw time by size, so a
replacement only has to be the right dimensions.

**Proportions are measured, not invented.** Comparing the untouched title screen against the probe
pass: the glyphs occupy about **560 px of the quad's 1110** (half its width, centred) and about
**34 px of its 61** (cap height). Filling the whole texture instead would render the word at twice
the original's width.

Two details that matter at this size:

* **Tracking is solved, not eyeballed.** Total width is the sum of glyph advances plus (n-1) gaps,
  so fixing the target width fixes the gap exactly. Destiny's lockup is defined by its wide
  tracking, and guessing a letter-spacing value gets the length wrong.
* **Centre on the ink, not the font metrics.** All-caps text carries no descender, so metric
  centring rides ~10 px high in a box only 61 px tall.

Usage:
    python make_title_texture.py VOIDWALKER
    python make_title_texture.py "NEW LIGHT" --out title.png --width 1110 --height 61
    python make_title_texture.py VOIDWALKER --preview      # also write a dark-ground preview

Then point the hook at it, with Destiny closed or not — this needs no relaunch of anything but the
game itself:

    C:\\Sunrise\\bin\\x64\\Sunrise\\menu_probe.txt:
        1110 61 voidwalker.png
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - guidance beats a traceback
    print("needs Pillow:  python -m pip install pillow")
    raise SystemExit(2)

# Serif faces present on a stock Windows install, closest first to the lockup's refined serif.
FONTS = ("constan.ttf", "cambria.ttc", "georgia.ttf", "BOOKOS.TTF", "times.ttf")
FONT_DIR = Path("C:/Windows/Fonts")


def option(name: str, fallback: str = "") -> str:
    flag = f"--{name}"
    if flag in sys.argv:
        at = sys.argv.index(flag)
        if at + 1 < len(sys.argv):
            return sys.argv[at + 1]
    return fallback


def pick_font(cap_height: int):
    """@return A font whose CAP height is at least `cap_height`, and its name."""
    for name in FONTS:
        path = FONT_DIR / name
        if not path.is_file():
            continue
        for size in range(cap_height, cap_height * 3):
            font = ImageFont.truetype(str(path), size)
            box = font.getbbox("H")
            if box[3] - box[1] >= cap_height:
                return font, name
    raise SystemExit(f"no usable serif font found in {FONT_DIR}")


def main() -> int:
    words = [a for a in sys.argv[1:] if not a.startswith("--")]
    # Skip values that belong to flags.
    for name in ("out", "width", "height", "target", "cap"):
        value = option(name)
        if value in words:
            words.remove(value)
    if not words:
        print(__doc__)
        return 2
    text = " ".join(words).upper()

    width = int(option("width", "1110"))
    height = int(option("height", "61"))
    # Defaults hold the measured ratios when a different texture size is asked for.
    target = int(option("target", str(round(width * 0.505))))
    cap = int(option("cap", str(round(height * 0.557))))
    out = Path(option("out", f"{text.split()[0].lower()}.png"))

    font, chosen = pick_font(cap)

    def render(dy: float) -> Image.Image:
        image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)
        advances = [draw.textlength(c, font=font) for c in text]
        gap = (target - sum(advances)) / max(len(text) - 1, 1)
        x = (width - target) / 2
        for char, advance in zip(text, advances):
            draw.text((x, dy), char, font=font, fill=(255, 255, 255, 255))
            x += advance + gap
        return image, gap

    first, gap = render(0)
    ink = first.getbbox()
    if ink is None:
        print("nothing rendered")
        return 1
    image, _ = render((height - (ink[3] - ink[1])) / 2 - ink[1])

    image.save(out)
    final = image.getbbox()
    print(f"'{text}'  {width}x{height}  font {chosen} {font.size}pt  tracking {gap:.1f}px")
    print(f"  ink x {final[0]}..{final[2]} ({final[2] - final[0]}px, target {target})")
    print(f"  ink y {final[1]}..{final[3]}  margins {final[1]} top / {height - final[3]} bottom")
    print(f"  wrote {out}")

    if "--preview" in sys.argv:
        preview = Image.new("RGB", (width, height), (18, 16, 20))
        preview.paste(image, (0, 0), image)
        name = out.with_name(out.stem + "_preview.png")
        preview.save(name)
        print(f"  wrote {name} (dark ground, as it reads in game)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
