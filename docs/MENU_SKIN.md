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
| 112×158 | RGBA8_UNORM | **the "2" of DESTINY 2** — authored art, dumped and confirmed |
| 276×276 | RGBA8_UNORM | draws where the Bungie glyph sits, but **alpha peaks at 46** — a glow, not the glyph |

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
rotating emblem behind the title, plus two that land on the lockup itself. **Nothing draws in the
top-left corner**, so a corner mark has no slot of its own and is composited into the background
instead.

Dumping those two settles what they are: **112×158 is the "2"**, finished white-on-alpha art. The
276×276 sits where the Bungie glyph does but its alpha peaks at 46 of 255, so it is a glow behind
the glyph rather than the glyph — putting a mark there would render at 18% opacity. If the lockup
itself is ever the target, the glyph is a slot still to be found.

Compositing the logo into the background costs nothing — the background is already being baked, and the logo then travels with whichever theme is loaded.

### The character select screen is a different quad

It draws from the **1920×1200** slot, not the 3030×940 one, and reads the texture across its whole
width — offset by **0.502**, wrapping. **Three** textures share that size — BC7 (98), UNORM (28) and
BC7 sRGB (99). Matching was by size alone at the time, so all three were replaced together; see *Telling screens
apart* below for what separates them now.

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

### Telling screens apart: format, then order

Four screens bind 1920×1200 and matching was by size alone, so one image had to serve all of them.
Two mechanisms separate them, and each was chosen only after the cheaper one was shown to fail.

**Format**, where it differs. A probe line takes an optional `@<dxgi format>` before its value:

```
1920 1200 @28 title_band.png     UNORM     - title screen's top band
1920 1200 @98 boot_bg.png        BC7       - boot screen and character-select loading
1920 1200 @99 select_bg.png      BC7 sRGB  - character select
```

Format 0 (omitted) still means "any", so every size-only rule keeps working.

**Order**, where format does not differ. The boot screen and the screen that loads into character
select are the same UI element at two moments — same size, same format. The draw is not a way out
either: the hit log shows UI index ranges **drift** as the menu repacks its buffer (the title screen
alone used 132, 138, 144, 150, 156, 162, 180, 186, 192…), so keying on `startIndex` would be
building on sand.

What is stable is sequence. In the hit log the boot screen's format-98 draws are hits **1 and 2**,
and the title backdrop is hit **9** — the boot screen always draws before the title screen exists,
and the loading screen always after. So a line takes an optional `!boot` or `!after`:

```
1920 1200 @98 !boot  boot_bg.png
1920 1200 @98 !after load_bg.png
```

The phase flips the first time a 3030×940 texture is bound, read from the game's own description
rather than our replacement, and never clears.

**`stage=probe_hit` in the log is what made this tractable** — it names every distinct
(size, format, draw, phase) a probe lands on, once each. Its cap started at 32 and filled before
character select was ever reached, losing exactly the screen under study; it is 96 now.