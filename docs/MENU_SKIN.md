# Main menu skin

Replacing title-screen art at **draw time** — no package written, nothing on disk changed, and
reversible by deleting one file.

---

## What the title screen actually draws

Identified 2026-08-26 by the flat-colour probe: tint several candidate sizes different colours,
launch once, read it off a screenshot. Every guess resolved in a single pass.

| size | format | what it is |
|---|---|---|
| **1110×61** | RGBA8_UNORM | **"SHADOWKEEP"** — authored art, dumped and confirmed |
| 1050×114 | typeless | the "DESTINY 2" area — **a render target, empty when read** |
| 975×36 | typeless | "PRESS ⏎ TO PLAY" |
| 3030×940 | RGBA8_UNORM | main backdrop |
| 1920×1200 | **BC7** | second background layer behind it |
| 1024×1024 | **A8** | font glyph atlas |
| 276×276 | — | draws where the Bungie glyph sits, between DESTINY and 2 |
| 112×158 | — | draws where the "2" sits |

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

`menu_dump.txt` takes the same `<width> <height>` lines and saves each slot's **existing** content
to `menudump_<w>x<h>.png` before any probe swaps it — the way to read Bungie's own art rather than
overwrite it. Only 32-bit formats are dumped; BC7 slots come back empty.

---

## Fitting a background: the quad's aspect, not the file's

The hook hands the game whatever file it finds, and the quad maps UV 0..1 across it. So **the
file's pixel dimensions are irrelevant** and only one number matters: the quad's screen aspect.

That number was pinned by elimination, not measured with a ruler. The backdrop was bound to a
3840×2160 (16:9) photo and read as *slightly* stretched. Only one geometry produces "slightly":

| quad geometry | distortion on a 16:9 source |
|---|---|
| full UV, screen rect **2.157:1** | **1.21× horizontal — matches** |
| full UV, rect 16:9 | none, and some was visible |
| middle-67% crop, either rect | 1.50–1.82×, which would read as wrecked |

So the correction is a **cover crop to 2.157:1** — fill the screen, lose ~18% off top and bottom,
keep circles circular. Never a resize, which is what stretched it in the first place.

```bash
python make_menu_background.py menu_bg.jpg --logo doge_white.png --out title_bg.png
python make_menu_background.py sky.jpg --aspect 2.0 --logo-corner tr --marks
```

The output is written *at* the quad's aspect, so the PNG on disk looks exactly like the thing in
game — nothing is pre-warped and nothing has to be imagined.

**`--marks` turns a pretty screenshot into a measurement.** A faint circle and corner ticks ride on
the finished background at ~18% opacity. Circle round → the aspect is right. Circle an ellipse →
its width:height *is* the remaining correction, feed it back through `--aspect`. A corner tick
missing → the quad crops, and by how much. Drop the flag once it reads clean.

### Where a logo can go

The colour pass tinted ten small quads at once. Every one drew centre or centre-right — the
rotating emblem behind the title, plus the Bungie glyph (276×276) and the "2" (112×158). **Nothing
draws in the top-left corner**, so a corner mark has no slot of its own and is composited into the
background instead. That costs nothing: the background is already being baked, and the logo then
travels with whichever theme is loaded.

---

## Making a title texture

```bash
python make_title_texture.py VOIDWALKER --preview
```

1110×61 to match the original. Proportions are no longer estimated — dumping the slot gives
Bungie's own art to measure: white RGB with the glyphs in **alpha**, ink spanning **x 273..829
(556 px) and y 14..47 (33 px)**, coverage 0.178. The screenshot estimates that preceded the dump
(560 px wide, 34 px cap) were right to within 4 px and 1 px.

Filling the whole texture would render the word at twice the original's width.

```bash
python make_title_texture.py VOIDWALKER --target 556 --cap 33
```

Scoring every installed font against the dumped art by alpha IoU was **not** decisive — a 33 px cap
is too small to separate faces, and sans and serif scored alike around 0.5. Ink coverage and a 2×
side-by-side settled it instead: Constantia sits within 3 px of the original on a 1110 px texture.

Tracking is *solved*, not eyeballed — total width is the sum of glyph advances plus (n−1) gaps, so
fixing the target width fixes the gap. And centring is on the **ink bounding box**, not the font
metrics: all-caps text has no descender, so metric centring rides ~10 px high in a box 61 px tall.

---

## Answered: is the subtitle rasterised?

It was a fair worry — RGBA8_UNORM, 1 mip, a non-power-of-2 size, and a font atlas sitting in the
same survey all point at a surface built at runtime rather than a shipped asset. **It is shipped
art.** Dumping the slot returned finished SHADOWKEEP glyphs, and a bound replacement renders solid
rather than flickering.

The one that *is* built at runtime is **1050×114**: it dumps fully transparent, every channel zero,
in the typeless format render targets use. The DESTINY 2 lockup is composed into it during the
frame. Tinting it still works, because the probe replaces the shader resource at draw time — but
there is no authored image in there to read back.
