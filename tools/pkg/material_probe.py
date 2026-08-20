"""Find the materials the custom body draws through, and what they reference.

Geometry is finished; the body still wears Scatterhorn's albedo. A part's first four bytes are
its material tag, so the materials the injected body actually samples are readable offline -
the entry *tables* are never encrypted, even where the bodies are.

Material bodies are encrypted, so decoding one needs a dump, and this writes the request for it.
Materials are not entries we rewrite, so dumping them is safe even though they sit in packages
we patch - the rule is about tags whose bodies we replaced.

Usage:
    python material_probe.py
    python material_probe.py --request      # write dump/request.txt, then launch once
"""
from __future__ import annotations

import collections
import struct
import sys
from pathlib import Path

from bone_probe import PACKAGES, dumped, entry_of, meshes_of

REQUEST = Path(r"C:\Sunrise\bin\x64\Sunrise\dump\request.txt")
# The carrier parts inject_scatterhorn.py rewrites; their materials are what the body samples.
CARRIERS = (10, 32)
BODIES = {"chest_a": 0x80EFA1CA, "chest_b": 0x80EFA1A9}
MATERIAL_CLASS = 0x80807140


def package_name(tag: int) -> str:
    handle = tag - 0x80800000
    package_id = handle >> 13
    for path in PACKAGES.glob("*.pkg"):
        with open(path, "rb") as fh:
            head = fh.read(0x180)
        if len(head) >= 0x180 and struct.unpack_from("<H", head, 0x04)[0] == package_id:
            return path.stem.rsplit("_", 1)[0]
    return f"package 0x{package_id:04X}"


def describe(tag: int, label: str) -> None:
    entry = entry_of(tag)
    if entry is None:
        print(f"    {label} 0x{tag:08X}: not in any entry table")
        return
    body = dumped(tag)
    print(f"    {label} 0x{tag:08X}  class 0x{entry.reference:08X}  {entry.size:>9,} B  "
          f"{package_name(tag)}"
          + (f"  [dumped {len(body):,} B]" if body else "  [not dumped]"))


def main() -> None:
    wanted: set[int] = set()
    everything: set[int] = set()
    for name, tag in BODIES.items():
        data = dumped(tag)
        if data is None:
            print(f"{name}: not dumped")
            continue
        mesh = meshes_of(data)[0]
        print(f"\n=== {name} 0x{tag:08X} ===")
        materials: collections.Counter[int] = collections.Counter()
        for index, (at, _primitive, _offset, _count, lod) in enumerate(mesh["parts"]):
            (material,) = struct.unpack_from("<I", data, at)
            if index in CARRIERS:
                print(f"  carrier part {index} (lod {lod}) material 0x{material:08X}")
                wanted.add(material)
            materials[material] += 1
            everything.add(material)
        print(f"  {len(materials)} distinct materials across {len(mesh['parts'])} parts")
        for material, count in materials.most_common():
            mark = "  <-- carrier" if material in wanted else ""
            describe(material, f"x{count:<3}")
            if mark:
                print(f"        {mark.strip()}")

    print("\n=== what the carrier materials reference ===")
    children: set[int] = set()
    for material in sorted(wanted):
        body = dumped(material)
        print(f"\n  material 0x{material:08X}:")
        if body is None:
            print("    not dumped - request it and launch once")
            continue
        # Every dword in tag range is a candidate reference; the 208-byte material is small
        # enough that listing them all is cheaper than guessing which offsets matter.
        for at in range(0, len(body) - 3, 4):
            (value,) = struct.unpack_from("<I", body, at)
            if 0x80800000 <= value < 0x81FFFFFF:
                describe(value, f"+0x{at:03X}")
                children.add(value)

    if "--request" not in sys.argv:
        print("\nRe-run with --request to write the dump request.")
        return

    # Every material on the model, not just the two carriers: one launch is the cost either way,
    # and the neighbours say which texture slot means what by varying against each other.
    requested = sorted(everything | children)
    lines = ["# Materials the custom body samples, and whatever they reference.",
             "# Safe to dump: we never rewrite these entries, only the buffers beside them.", ""]
    for tag in requested:
        lines.append(f"tag 0x{tag:08X}")
    REQUEST.write_text("\r\n".join(lines), encoding="utf-8")
    print(f"\nwrote {len(requested)} requests to {REQUEST}")
    print("Launch once, reach the character screen, quit, then re-run this tool.")


if __name__ == "__main__":
    main()
