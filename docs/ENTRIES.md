# Package entries: what the decrypted blobs look like

Everything here comes from 24 entries dumped out of the running game with
`items::packages::dump_if_requested()`, sampled from the `sandbox` family because that is where
weapon and armour art lives. Class ids and sample tags were chosen offline first — see below.

Nothing here is a decoded format yet. It is the structure that is visible without one.

## Most of this needed no game at all

Entry tables are plain file data even where every block body is encrypted, and an entry record's
`reference` field **is** the class id the reader reports for that tag. So the whole class census can
be taken from disk with no keys:

```powershell
python tools\pkg\classes.py        # 1,061,209 classes over 2,695,944 entries
python tools\pkg\make_request.py   # picks real sample tags from one family
```

Only the bodies need the running game. Guessing class ids in game would have cost a launch each.

## Two conventions visible in every blob

**Entries begin with their own size**, as a little-endian `u64` at offset 0. Confirmed on every
sampled entry: `0x668`→1,640, `0x890`→2,192, `0x5EB0`→24,240, `0xC64`→3,172, `0x2010`→8,208.
The one class that does not is `0x80806E28`, which starts straight into tag handles.

**Arrays are `{u64 count, u64 offset}` pairs.** In `0x80806E2C`:

```
 8: 1     16: 336
24: 2     32: 368
40: 1     48: 384
80: 330   88: 376
96: 59   104: 712
```

Counts and offsets alternate, and the offsets rise. Whether an offset is absolute or relative to its
own field position is **not yet settled** — relative to the field puts the four arrays at 352, 400,
432 and 464, which is consistent, but that has not been checked against actual array contents.

**Low tag-shaped values are inline type markers, not references.** `0x80800006`, `0x80800009`,
`0x8080000A`, `0x80800090` and friends recur across unrelated blobs, so they mark embedded structure
types. Only values well above `0x80801000` point at other entries. `analyze.py` splits them on that
boundary.

## Class candidates, on evidence rather than assumption

| class | size | entropy | tag refs | reading |
|---|---|---|---|---|
| `0x80808F49` | 8-26 KB | **7.0-7.5** | 10-18 | Near-random payload behind a short header of offsets and a length. Packed pixel or bytecode data. Also heavy in `cinematics`, which argues for animation over textures. |
| `0x80806E2C` | 1.6-1.9 KB | 4.1 | 12 | 31-37% plausible floats, `{count, offset}` arrays. Geometry, transforms or bounds. |
| `0x80806E28` | **52 B** | 3.2-4.3 | 3-7 | Eight consecutive tag handles then small fields. A manifest tying other blobs together. |
| `0x80809C36` | 17-24 KB | 2.2 | 580-808 | Dense reference table. |
| `0x80809C0F` | **exactly 3,172 B** | 3.3 | 185 | Fixed-size across every sample, and all three share the reference `0x811C9DC5`. A fixed struct pointing at something common. |
| `0x808071E8` | 2-3 KB | **0.4-1.8** | 5-12 | Very low entropy, so heavily zeroed or repetitive. Index buffer or a sparse table. |

`0x80806E28` is the most useful of these despite being the smallest: at 52 bytes it is nearly
decodable by hand, and its references reach a `0x80806E2C` and two `0x808071E8` blobs — the shape a
mesh record would have if it named a vertex layout and its buffers.

## Tools

```powershell
python tools\pkg\analyze.py           # characterise every dumped entry
python tools\pkg\inspect.py 80B363C9  # annotate one, field by field
```

## Not yet known

Which class is geometry, which is texture, and how any of them are laid out. The next step is to
follow `0x80806E28`'s references outward and dump what they point at, since a record that names its
dependencies is a better entry point than a large blob that does not.
