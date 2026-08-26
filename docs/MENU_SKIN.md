# Main menu skin

Replacing title-screen art at **draw time** — no package written, nothing on disk changed, and
reversible by deleting one file.

---

## What the title screen actually draws

Identified 2026-08-26 by the flat-colour probe: tint several candidate sizes different colours,
launch once, read it off a screenshot. Every guess resolved in a single pass.

| size | format | what it is |
|---|---|---|
| **1110×61** | RGBA8_UNORM | **"SHADOWKEEP"** |
| 1050×114 | typeless | "DESTINY 2" |
| 975×36 | typeless | "PRESS ⏎ TO PLAY" |
| 3030×940 | RGBA8_UNORM | main backdrop |
| 1920×1200 | **BC7** | second background layer behind it |
| 1024×1024 | **A8** | font glyph atlas |

Two of those readings settle questions on their own:

* **The background is an image, not a video.** BC7 is block-compressed authored art, and render
  targets are never BC-compressed. There was no need to go near the 20.6 GB of Bink video.
* **A 1024×1024 A8 texture is a font atlas** — alpha-only. The menu rasterises text, so some wide
  quads are drawn strings rather than shipped art. Sizes appearing in *both* UNORM and TYPELESS
  (772×98, 144×134, 112×158) are written-then-sampled, i.e. dynamic UI.

---

## Using it

`C:\Sunrise\bin\x64\Sunrise\menu_probe.txt`, one rule per line — `<width> <height> <RRGGBB | file>`:

```
3030 940 menu_bg.jpg
1920 1200 menu_bg.jpg
1110 61 voidwalker.png
```

A dot in the third token means a filename, resolved beside `settings.json` and decoded by WIC, so
jpg and png both work. Otherwise it is a flat colour — which is the identification mode.

Delete the file to revert. No rebuild either way.

**Matching is by size, not by pointer.** A UI texture is recreated between frames, so its address
is not stable while its dimensions are.

**Probing is confined to draws the character never claims**, so an identification pass cannot
disturb the Guardian, and the saved shader resources are restored immediately after each draw
because the game keeps drawing with those slots.

`menu_survey.txt` (presence = on) lists every distinct texture a screen binds, with dimensions,
format and aspect. That is how a *new* screen gets identified; the probe is how a candidate gets
confirmed.

---

## Making a title texture

```bash
python make_title_texture.py VOIDWALKER --preview
```

1110×61 to match the original. Proportions are measured off the screenshots rather than invented:
glyphs occupy **~560 px of the quad's 1110** (half its width, centred) and **~34 px of its 61**.
Filling the whole texture would render the word at twice the original's width.

Tracking is *solved*, not eyeballed — total width is the sum of glyph advances plus (n−1) gaps, so
fixing the target width fixes the gap. And centring is on the **ink bounding box**, not the font
metrics: all-caps text has no descender, so metric centring rides ~10 px high in a box 61 px tall.

---

## Known unknown

The SHADOWKEEP texture is RGBA8_UNORM, 1 mip, at an odd non-power-of-2 size — which looks more like
a surface allocated at runtime than a shipped asset, and there is a font atlas in the survey. **If
the game re-rasterises that text every frame, a bound image may be overwritten rather than shown.**
Binding a 1110×61 image and looking is the cheap test; if it flickers or does not appear, the text
is rasterised and changing it means finding the string instead.
