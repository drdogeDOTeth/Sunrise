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
python tools\pkg\inspect_entry.py 80B363C9  # annotate one, field by field
```

## The reference graph, resolved without decrypting anything

A tag encodes its package id and entry index directly, so `tools/pkg/resolve.py` looks up the class
and size behind any reference from plain entry tables. Only bodies need the game.

```powershell
python tools\pkg\resolve.py --refs 80B363C9   # what one dumped blob points at
python tools\pkg\resolve.py --graph           # every dumped blob, plus a class census
```

`0x80B363C9` — a 52-byte `0x80806E28` — resolves to:

| target | class | size | package |
|---|---|---|---|
| `0x80B363C2` | `0x80806E2C` | 1,944 B | sandbox |
| `0x80B364F4` `0x80B363C5` `0x80B363C8` `0x80B3645E` | `0x808071E8` | 2.4-4.1 KB | sandbox |
| `0x80C70B97` | `0x80806E2E` | **32 B** | globals |
| `0x80F5746B` | `0x808071E8` | 1,120 B | globals |

One header, several same-class members, and a tiny shared record from `globals`. `0x808073A5`
records have the same shape, pointing at four or five `0x808071E8` blobs of 5-6 KB each.

### Two corrections this forced

**`0x808071E8` is not packed buffer data.** Sampled entries are **94-96% zeros**, and two of them —
3,248 B and 2,384 B — both have their last non-zero byte at *exactly* offset 1115. Identical tails
across different sizes means a structure with a large reserved region, not vertex or index data.
The earlier "index buffer or sparse table" reading was too generous to the buffer theory.

**`reference` is not always a class id.** Entries of 12-24 bytes resolve to "classes" that are
themselves tag-shaped (`0x80BC2BDA`, `0x80B800B7`), so small stubs use the field as a pointer. That
is why `classes.py` reports 1,061,209 distinct values: the genuinely structured classes are the few
hundred with high counts, and the long tail is pointers being counted as types.

### Most-referenced classes across the sample

| class | references | note |
|---|---|---|
| `0x80809C54` | 99 | 88-112 B, in `globals` — small, shared, heavily pointed at |
| `0x80806E28` | 55 | the 52-byte manifest |
| `0x808071E8` | 21 | the mostly-zero structure above |
| `0x80809C36` | 9 | 17-24 KB dense reference table |

`0x80809C54` is the strongest untouched lead: something referenced 99 times, only ~100 bytes, and
living in `globals` rather than beside the art is what a shared format or material descriptor looks
like.

## Not yet known

Which class is geometry, which is texture, and how any of them are laid out. The next step is to
follow `0x80806E28`'s references outward and dump what they point at, since a record that names its
dependencies is a better entry point than a large blob that does not.
