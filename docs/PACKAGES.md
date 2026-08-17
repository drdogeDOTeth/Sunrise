# Tiger packages: what is known, and what it means for custom art

Everything here was established against the 2,202 packages of this Shadowkeep install
(`86657.20.08.23`) using `tools/pkg/`. Where something is inferred rather than observed, it says so.

## Header version 38 has two layouts

Sunrise's `reader/layout.h` documents one set of table offsets. The installed set uses two, and they
are mutually exclusive:

| layout | entry table  | block table   | packages |
|--------|--------------|---------------|----------|
| A      | `0x110` + 96 | derived        | 1,852    |
| B      | `0x0B8` + 96 | `0x0D4` + 96   | 350      |

**`0x110` discriminates them** — layout B leaves it zero. Layout A has no explicit block-table
offset, so it is derived the way Sunrise derives it: `entryTable + entryCount * 16 + 32`.

Sunrise's offsets are not wrong. They are right for the packages Sunrise opens, and it is never
pointed at the others, so the gap never surfaced. A repacker has no such luxury.

Other confirmed header fields: version `0x00`, package id `0x04`, patch id `0x20`, entry count
`0xB4`, block count `0xD0`, body end `0x160`, file size `0x164`. The hash-shaped words at
`0x0BC`..`0x0CC` and `0x0D8`..`0x0E8` are **not** understood, which is why the writer inherits them
rather than computing them.

## One package is a family of files

`<stem>_0.pkg` through `<stem>_N.pkg`. Every block record carries the patch id of the file holding
its body, so most records in a patch file point at a sibling. **A block offset is only meaningful
against the file its own `patch_id` names** — validating offsets against the file being read
reports failures that are an artifact of the check, not of the data. This was the single biggest
source of false alarms while transcribing the format.

## Almost everything is encrypted

Across 1,857,244 block records:

| flags    | share | meaning                |
|----------|-------|------------------------|
| `0x0003` | 80.1% | compressed + encrypted |
| `0x0000` | 18.2% | plain                  |
| `0x0007` |  0.0% | + alternate key        |

**There is no `0x0001`.** Every compressed block in the install is also encrypted. The plain 18%
is almost entirely audio (97% plain) and video (82%); the families holding geometry are not:

| family                      | plain blocks | of total  |
|-----------------------------|--------------|-----------|
| `sandbox`                   | 148          | 451,943   |
| `environments`              | 597          | 417,605   |
| `activities`                | 52           | 109,172   |
| `investment_globals_client` | 88           | 55,876    |

### What that settles

Block keys live in a table Sunrise locates in the running game and reads at use time.
`client/targets/game/packages.h` is explicit that the bytes are proprietary and are "never copied
into State, cache, capture or logs". This fork keeps that property: **the keys are not exported to
disk.**

That costs nothing, because:

- **Writing never needs them.** Block flags are per-record and `load_block` decrypts only when
  `kEncrypted` is set, so a plain block sitting among encrypted ones decodes without any key. The
  round-trip test relies on exactly this.
- **Reading reference art does not need them exported either.** Sunrise already decrypts in-process,
  so a dump feature inside the mod produces the reference bytes with the keys staying where upstream
  put them.

## Writing: use the format's own update mechanism

Two approaches were tried. The first — repointing an existing block record in place — **does not
work**, and the round-trip test is what proved it. Entries pack several to a block and spill across
block boundaries, so a block is nearly always shared; repointing one silently corrupted a
neighbouring entry that began in an earlier block and ran into this one. Checking start blocks alone
misses that case entirely.

The approach that works is the one Bungie's own updates use: **write the next patch file.**

`tools/pkg/patch.py: write_patch_package()` copies the newest existing file through the end of its
block table, appends one new block record, stores exactly one body, and repoints the target entry at
it. Every other block record keeps its original patch id and therefore still resolves to the older
file. Nothing existing is disturbed, so no neighbour can be corrupted. Header fields that are not
understood are inherited verbatim.

Current limits, all enforced rather than assumed:

- The replacement must fit one block (`0x40000`).
- Bodies are written plain — neither compressed nor encrypted.
- Block indices are a 14-bit field, capping a package at 16,384 blocks.

## Every package ends in a 0x800 trailer

All 2,202 shipped packages satisfy both of these **exactly**:

- `0x164` holds the real file size.
- File size minus the value at `0x160` is `0x800`. The last 2,048 bytes are a trailer, and `0x160`
  records where it starts.

This is not decoration. A written patch file that breaks the relationship is rejected by the game's
patchable registrar with result `-86`, logged through Sunrise's assert hook as
`Patchable package registration failed with result  (-86)`. `write_patch_package` therefore carries
the source file's trailer over verbatim — its contents are not understood, and they do not need to
be, because the registrar accepts a file that keeps them.

### How that was established

By bisection, because guessing was going to be expensive:

1. A patch file with a redirected entry was rejected with `-86`.
2. A **no-op** patch file — a byte-identical copy of its source with only the patch id at `0x20`
   changed — registered **cleanly**. That ruled out an intrinsic header signature or an
   expected-file-set check, and pinned the cause on the table edits.
3. Comparing the two against the shipped set produced the `0x800` invariant above.

`write_noop_patch_package()` exists to repeat step 2 whenever a rejection needs re-bisecting.

### The registrar caches its verdict

The game validates a package set **only when it changes**, then caches the result and skips
revalidation. A second launch after a rejected file appeared came up clean and briefly looked like a
pass; nothing had been re-checked. Clearing `C:\Sunrise\cache_phr_*.dat` and
`bin\x64\Sunrise\cache\content_manifest.bin` forces a genuine re-check, and `gametest.py` now does
that automatically on every run. A launch without it proves nothing.

## What -86 is not

`-86` is `0xFFFFFFAA`, a sibling of the `-93` and `-89` results
`client/hooks/package_trust/package_trust_bypass.cpp` already defeats. Forcing it the same way does
**not** work: `B8 AA FF FF FF` (`mov eax, -86`) does not appear anywhere in the unpacked image, so
the value arrives in a variable rather than as an immediate. The assert text reads
`failed with result  (-86)` — the doubled space is consistent with a `"%s (%d)"` format whose first
argument is empty.

Note also that the site cannot be found by scanning `destiny2.exe` on disk. It is VMProtect-packed:
there is a `.vmp0` section, `.text` reports entropy 8.00, and no marker strings survive. A file scan
finds zero sites for `-93` and `-89` too, both of which resolve fine at runtime.

The bypass added here is deliberately **non-fatal** for that reason — `-93` and `-89` are
load-bearing and proven, and a pattern that fails to resolve must not take them down.

## Verified so far

```powershell
cd tools\pkg
python verify_all.py        # parses all 2,202 packages
python roundtrip_test.py    # writes a patch file and reads the whole package back
```

- All 2,202 packages parse. 24 still contain suspect block records; 16 of those are video patch
  files and 8 are single odd records in otherwise clean files. Nothing needed for art is affected.
- Entry extraction is proven end to end against plain packages, including multi-block spans and
  cross-patch-file resolution.
- The Oodle binding round-trips through the game's own `oo2core_3_win64.dll`.
- A written patch file reads back with the redirected entry byte-exact and **all 722 other entries
  byte-identical**.

## Not yet verified

**The game has not loaded a written package.** Everything above is proven against our own reader.
Sunrise reads packages for its datagen, but the renderer is the game's own loader, and whether it
accepts a hand-written patch file is untested. That is the next real milestone, and it is the one
that can still invalidate the approach.
