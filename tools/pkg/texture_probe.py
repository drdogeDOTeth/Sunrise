"""Find Destiny's textures, and the hop from a material to the one it samples.

Textures are easy to *find* and were never the hard part: entry tables are plain, so scanning
for entries of 5,586,944 B (2048x2048 BC7 with a mip chain) and 2,793,472 B (its half) locates
them offline, and each one's `reference` names its header. `sandbox_0699` alone holds 112.

**A texture is a 40-byte header entry and a data entry that reference each other.** Confirmed by
the pairing across all 55: `0x80EFAD60` (40 B) <-> `0x80EFAD63` (5,586,944 B), and so on for
every one of them. That 40 is the same size as the entries a material points at, which settles
what those are:

    +0x048    40-byte texture header -> its data   (materials sample here)
    +0x2C8    40-byte texture header -> its data
    +0x4D0..   8-byte stub -> 52-byte entry in globals_0238 / environments_0235

So materials **do** name their textures directly. The surprise is the size: our two carrier
materials sample data entries of only 2-8 KB, not the 5.6 MB maps sitting in the same packages.

**That is a consequence of our own inject, not a Destiny quirk.** Carrier parts 10 and 32 were
originally minor pieces of the robe, so they carry minor detail materials, and forcing all 46,068
of our triangles through them means the whole body wears a trim texture. When the mesh is split
into five per-material parts, each part's material tag is ours to choose - pick materials that
sample the big maps.

The 8-byte -> 52-byte pairs in `globals_0238` / `environments_0235` are shared across materials
and repeat within one material (part 10 references the same two five times), so they are far more
likely to be samplers or fallbacks than the robe's skin.

This writes the request that confirms the format: the four data entries our carriers sample, the
shared 52-byte pairs, and one full texture header **with its 5.6 MB body**, so the header layout
and the pixel format both get decoded in the same launch.

Usage:
    python texture_probe.py             # list what is there
    python texture_probe.py --request   # write dump/request.txt, then launch once
"""
from __future__ import annotations

import sys
from pathlib import Path

from bone_probe import INDEX, dumped, entry_of
from tigerpkg import Package, TAG_BASE, TAG_ENTRY_BITS

REQUEST = Path(r"C:\Sunrise\bin\x64\Sunrise\dump\request.txt")
# 2048x2048 BC7 plus its mip chain, and the same at half. Both are exact across the install.
TEXTURE_SIZES = (5_586_944, 2_793_472)
FAMILIES = ("037c", "037d", "0698", "0699")

# What the two carrier materials point at, from material_probe.py.
SLOT_TABLES = (0x81532C61, 0x81532B04, 0x81532693, 0x81532C5F)
SHARED_HEADERS = (0x80C70B8E, 0x80C70B8D, 0x80C6B566, 0x80C6B565)
# Adjacent to part 32's material stub 0x80EFAD59 - the most likely Scatterhorn texture pair.
SAMPLE_TEXTURE = (0x80EFAD60, 0x80EFAD63)


def textures_in(package_id: int, path: Path) -> list[tuple[int, int, int]]:
    """@return `(tag, size, reference)` for every texture-sized entry in one package."""
    pkg = Package(path)
    out = []
    for index, entry in enumerate(pkg.entries):
        if entry.size in TEXTURE_SIZES:
            out.append((TAG_BASE + (package_id << TAG_ENTRY_BITS) + index,
                        entry.size, entry.reference))
    return out


def main() -> None:
    total = 0
    for package_id, path in sorted(INDEX.items()):
        if not any(family in path.stem for family in FAMILIES):
            continue
        found = textures_in(package_id, path)
        total += len(found)
        print(f"{path.stem}: {len(found)} texture-sized entries")
        for tag, size, reference in found[:4]:
            header = entry_of(reference)
            print(f"    0x{tag:08X}  {size:>12,} B  header 0x{reference:08X}"
                  + (f" ({header.size} B)" if header else " (not an entry)"))
    print(f"\n{total} textures across the Scatterhorn packages")

    print("\nwhat the carrier materials point at:")
    for tag in SLOT_TABLES + SHARED_HEADERS + SAMPLE_TEXTURE:
        entry = entry_of(tag)
        body = dumped(tag)
        print(f"  0x{tag:08X}  {entry.size:>12,} B  ref 0x{entry.reference:08X}"
              + (f"  [dumped {len(body):,}]" if body else "  [not dumped]"))

    if "--request" not in sys.argv:
        print("\nRe-run with --request to write the dump request.")
        return

    wanted = SLOT_TABLES + SHARED_HEADERS + SAMPLE_TEXTURE
    lines = ["# Texture hop: material slot tables, shared headers, and one full texture.",
             "# None of these are entries we rewrite, so dumping them is safe.", ""]
    lines += [f"tag 0x{tag:08X}" for tag in sorted(set(wanted))]
    REQUEST.write_text("\r\n".join(lines), encoding="utf-8")
    megabytes = sum(entry_of(tag).size for tag in set(wanted)) / 1e6
    print(f"\nwrote {len(set(wanted))} requests ({megabytes:.1f} MB) to {REQUEST}")
    print("Launch once, reach the character screen, quit, then re-run this tool.")


if __name__ == "__main__":
    main()
