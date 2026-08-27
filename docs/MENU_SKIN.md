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

## Fitting a background: the quad reads a window, not the whole texture

The hook hands the game whatever file it finds, so the file's pixel dimensions do not decide
anything on their own. What decides the result is that **the backdrop quad samples a window out of
the middle of the texture** and maps it to a screen rect wider than 16:9:

```
u  0.184 .. 0.807     the middle 62% of the width
v  0.006 .. 0.926     nearly the full height
screen rect 1920 x 883, under the band that occupies the top 197 px
```

Solved by locating two features in both the baked file and a screenshot of it in game — the bright
ceiling light bar's two ends horizontally, the bar and the lit ledge vertically.

**Four independent things confirm it**, which is the reason to trust it:

* a logo baked at u 0.021 did not appear on screen
* all four corner ticks did not appear
* the crowd came back zoomed by the predicted factor
* a circle baked at the texture centre rendered as a 914×615 ellipse, arcs measured at x 515 and
  1415 against 516 and 1431 predicted

So the fix is to **cover-crop to the screen rect's 2.174 and lay that into the window** — fill the
screen, trim ~18% off top and bottom, keep circles circular.

```bash
python make_menu_background.py menu_bg.jpg --logo doge_white.png --gamma 0.75
python make_menu_background.py sky.jpg --logo-corner tr --marks
python make_menu_background.py sky.jpg --window 0.19,0.81,0.0,0.93   # if re-measured
```

The window was confirmed a second time against the finished render, by locating three features in
both the baked file and the screenshot: predicted-vs-observed came out **0 px, 3 px and 12 px** on a
1080p screen.

**`--gamma`, not brightness, for a dark photo.** These backdrops carry blown-out practical lights,
so a linear multiplier drives those further into clipping while barely touching the subject. On the
crowd photo the dark band measured mean 0.248 with 43% of pixels under 0.15; `--gamma 0.75` lifts
that to 0.326 for 0.13 pp more clipped pixels. It is applied to the source before the logo goes on,
so a white mark never gets lifted along with the picture.

Logos and marks are positioned in **screen** coordinates and mapped back through the window, so
"top-left corner" means the corner of the screen rather than of the file.

**`--marks` turns a pretty screenshot into a measurement.** A faint circle and four corner ticks
ride on the finished background at ~20% opacity, drawn pre-distorted so a correct render shows a
true circle and four visible ticks. An ellipse's width:height is the residual error; a missing tick
says the window is still wrong on that edge. Drop the flag once it reads clean.

### The mistake worth not repeating

The first attempt put the window at full UV, reasoning that a 16:9 source read as only *slightly*
stretched and only a full-UV wide rect predicts "slightly". That reasoning was sound and the
premise was junk: **"looks slightly off" is not a measurement.** An earlier calibration pass had
already read the window as roughly the middle two-thirds, and it was talked away on the strength of
a feeling about a screenshot. Trust the grid, or trust the marks — not the impression.

### Where a logo can go

The colour pass tinted ten small quads at once. Every one drew centre or centre-right — the
rotating emblem behind the title, plus the Bungie glyph (276×276) and the "2" (112×158). **Nothing
draws in the top-left corner**, so a corner mark has no slot of its own and is composited into the
background instead. That costs nothing: the background is already being baked, and the logo then
travels with whichever theme is loaded.

### The character select screen is a different quad

It draws from the **1920×1200** slot, not the 3030×940 one, and reads the texture across its whole
width — offset by **0.502**, wrapping. **Three** textures share that size — BC7 (98), UNORM (28) and
BC7 sRGB (99) — and matching is by size, so all three get replaced.

Surveying both screens in one launch filled all 64 slots: 33 for the title screen, the rest for
character select, so the list is complete for neither and there may be more past the cap.

The offset was measured off the logo, which makes a good landmark because its position in the file
is known exactly: baked at u 0.028, it rendered at screen x 1010 of 1920, and its 140 texture px
arrived as 140 screen px — so the mapping is 1:1 with a half-width shift.

That shift puts the picture on screen cut in half and swapped, with the source's two dark side
walls butted together down the middle. **In a symmetric room that reads as a mirror**, which is what
it was mistaken for. `--roll 0.5` cancels it: the picture lands in order and the seam moves out to
the screen's own edges, where nothing can see it.

```bash
python make_menu_background.py sky.jpg --window 0,1,0,1 --rect 1920x1080 \
    --slot 1920x1200 --roll 0.5 --out select_bg.png
```

Worth knowing that the un-rolled version is not simply broken — the two walls meeting in the centre
make a column the character stands against, which reads as deliberate staging. Both are kept.

### The band across the top

The 1920x1200 layer draws over the top ~197 px of the title screen, and it **mirrors about the
centre of the screen** - it reads v 0.040..0.244 with u running 0.502 up to 1.0 at screen centre
and then reflecting back. So anything with left-right detail in its window arrives twice, once
backwards. A logo baked for character select showed up as two logos in the band, one of them
mirror-written, which is how the mirror was identified.

A mirrored quad can never show un-mirrored picture. What it *can* show is content with no
left-right detail, because that mirrors to itself invisibly:

```bash
python make_menu_background.py menu_bg.jpg --gamma 0.75 --band --slot 1920x1200     --out title_band.png
```

`--band` makes every row the mean colour of the picture's corresponding row - keeping the ceiling's
vertical falloff and glow, dropping exactly the horizontal detail the mirror would duplicate. It is
positioned by geometry rather than by eye: the backdrop shows `kept` of the source's height over
its rect, so the band's 197 px take the source rows immediately above the crop at the same scale,
and what runs off the top fades out. Simulated before launching, the join measured **4.6 of 255**.

### Telling two screens apart

The title band and character select both bind 1920x1200 and matching is by size, so one image had
to serve both - and they disagree: the band mirrors, character select wraps. Their **formats**
differ, though, and the survey reports the format of everything it names.

So a probe line takes an optional `@<dxgi format>` before its value:

```
1920 1200 @98 title_band.png     BC7        - title band
1920 1200 @28 title_band.png     UNORM      - title band
1920 1200 @99 select_bg.png      BC7 sRGB   - character select
```

Format 0 (omitted) still means "any", so every size-only rule keeps working. This is also what a
theme switcher will need, since it has to address each screen separately.

The 1920×1200 layer draws over the top ~197 px and samples near **u 0.94, mirrored** — which is what
was throwing reversed ceiling lights across the top of the screen when both layers were bound to
the same photo. Since that region is outside the backdrop's own window, the fitter fills it with a
blurred, darkened copy, and the band becomes a soft dark strip instead of a competing reflection.

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
