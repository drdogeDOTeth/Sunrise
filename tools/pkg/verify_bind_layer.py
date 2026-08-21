"""Read the shipped bind layer back out of the installed packages, as the game would.

The atlas bind hung character select twice: once with a stale `+0x00` self-length, and once after
that was fixed. Two things could still be wrong and they need separating *offline*, because each
guess costs a launch:

- the material surgery, which resizes a type-8 record whose other fields are undecoded;
- the atlas entries, which are 5,586,944 B / ~21 blocks against a previous verified maximum of
  798 KB across four.

This reads the newest installed patch of each package, pulls our seventeen entries out of the
block stream, and checks every invariant we know how to state. It reads the *shipped bytes*, not
the plan, so agreement is evidence rather than restatement.

Usage:
    python verify_bind_layer.py
"""
from __future__ import annotations

import hashlib
import re
import struct
from pathlib import Path

from tigerpkg import Package

PACKAGES = Path(r"C:\Sunrise\packages")
BLOCK_SIZE = 0x40000
TEXTURE_ARRAY_FIELD = 0x2D0
ARRAY_CLASS = 0x80809FBD
TEXTURE_BINDING = 0x80807211
ARRAY_HEADER = 0x14
ALBEDO_SLOT = 3

# What the layer claims to have written, from bind_material_textures.GROUPS.
MATERIALS = {
    0x80EF98DB: ("tank top", 0x80EF8811),
    0x80EFA1F7: ("skin, arms", 0x80EF881D),
    0x80EFA1DC: ("gas mask", 0x80EF89F1),
    0x81532AE0: ("twirl", 0x80EFAD60),
    0x81531EF0: ("necklace", 0x80EFB76B),
}
BODIES = {0x80EF880E: "tank top", 0x80EF881F: "skin, arms", 0x80EF89F3: "gas mask",
          0x80EFAD63: "twirl", 0x80EFB7B9: "necklace"}
HEADERS = {tag for _, tag in MATERIALS.values()}

TAG_BASE = 0x80800000
TAG_ENTRY_BITS = 13
TAG_ENTRY_MASK = (1 << TAG_ENTRY_BITS) - 1
NAME_RE = re.compile(r"^(?P<stem>.+)_(?P<patch>\d+)\.pkg$", re.IGNORECASE)


def newest_of_each() -> dict[int, Package]:
    """@return The highest-numbered installed patch of every package, keyed by package id."""
    best: dict[str, tuple[int, Path]] = {}
    for path in PACKAGES.glob("*.pkg"):
        match = NAME_RE.match(path.name)
        if match is None:
            continue
        patch = int(match["patch"])
        stem = match["stem"]
        if stem not in best or patch > best[stem][0]:
            best[stem] = (patch, path)
    out = {}
    for _, path in best.values():
        package = Package(path)
        out[package.header.package_id] = package
    return out


def locate(tag: int, opened: dict[int, Package]) -> tuple[Package, int]:
    package_id = (tag - TAG_BASE) >> TAG_ENTRY_BITS
    package = opened.get(package_id)
    if package is None:
        raise SystemExit(f"0x{tag:08X}: package 0x{package_id:04X} is not installed")
    return package, (tag - TAG_BASE) & TAG_ENTRY_MASK


def block_span(package: Package, index: int) -> tuple[int, int]:
    """@return `(first block, block count)` the entry's bytes are cut across."""
    entry = package.entries[index]
    first = entry.start_block
    end = entry.start_offset + entry.size
    return first, max(1, -(-end // BLOCK_SIZE))


def texture_array(data: bytes) -> tuple[int, int] | None:
    count, offset = struct.unpack_from("<qq", data, TEXTURE_ARRAY_FIELD)
    if count <= 0:
        return None
    marker = TEXTURE_ARRAY_FIELD + 8 + offset - 4
    if not 0 <= marker <= len(data) - ARRAY_HEADER:
        return -1, count
    (seen,) = struct.unpack_from("<I", data, marker)
    its_count, _, element, _ = struct.unpack_from("<4I", data, marker + 4)
    if seen != ARRAY_CLASS or element != TEXTURE_BINDING or its_count != count:
        return -2, count
    return marker, count


def check_blocks(package: Package, first: int, count: int) -> list[str]:
    """Verify each block record against its own body: size, host file, and the SHA-1 at +12."""
    problems = []
    for i in range(first, first + count):
        if i >= len(package.blocks):
            problems.append(f"block {i} is past the block table ({len(package.blocks)})")
            break
        block = package.blocks[i]
        try:
            body = package.block_body(i)
        except Exception as error:  # noqa: BLE001 - report, do not abort the sweep
            problems.append(f"block {i}: {error}")
            continue
        if block.flags:
            problems.append(f"block {i} is not plain: flags {block.flags:#06x}")
        digest = hashlib.sha1(body).digest()
        if block.opaque != digest[:20]:
            got = block.opaque.hex()[:16]
            problems.append(f"block {i} SHA-1 mismatch (record {got}..., "
                            f"body {digest.hex()[:16]}...)")
        if block.offset % 0x800:
            problems.append(f"block {i} body at {block.offset:#x} is not 0x800-aligned")
    return problems


def main() -> None:
    opened = newest_of_each()
    print(f"{len(opened)} packages, newest patch of each\n")

    verdicts: list[str] = []

    print("=== materials (type 8) ===")
    for tag, (what, wanted_header) in MATERIALS.items():
        package, index = locate(tag, opened)
        entry = package.entries[index]
        try:
            data = package.read_entry(index)
        except Exception as error:  # noqa: BLE001
            print(f"0x{tag:08X} {what:<12} UNREADABLE: {error}")
            verdicts.append(f"0x{tag:08X} unreadable")
            continue
        first, span = block_span(package, index)
        (declared,) = struct.unpack_from("<I", data, 0)
        note = "ok" if declared == len(data) else f"MISMATCH (declared {declared:,})"
        found = texture_array(data)
        if found is None:
            bind = "NULL ARRAY"
        elif found[0] < 0:
            bind = f"UNRESOLVABLE ({found[0]})"
        else:
            marker, count = found
            slots = dict(struct.unpack_from("<2I", data, marker + ARRAY_HEADER + i * 8)
                         for i in range(count))
            got = slots.get(ALBEDO_SLOT)
            bind = (f"slot {ALBEDO_SLOT} -> 0x{got:08X}" if got == wanted_header
                    else f"WRONG slot {ALBEDO_SLOT} -> {got and f'0x{got:08X}'}")
        print(f"0x{tag:08X} {what:<12} {package.path.name:<26} entry {index:>5}  "
              f"type {entry.entry_type:>2}  {len(data):>7,} B  blocks {first}+{span}")
        print(f"           self-length {note};  {bind}")
        if declared != len(data) or "ok" not in note or bind.startswith(("NULL", "WRONG", "UNRES")):
            verdicts.append(f"0x{tag:08X} {note} / {bind}")
        for problem in check_blocks(package, first, span):
            print(f"           BLOCK: {problem}")
            verdicts.append(f"0x{tag:08X} {problem}")

    print("\n=== texture headers (type 32) and bodies (type 40) ===")
    for tag in sorted(HEADERS | set(BODIES)):
        package, index = locate(tag, opened)
        entry = package.entries[index]
        first, span = block_span(package, index)
        kind = "header" if tag in HEADERS else f"body ({BODIES[tag]})"
        try:
            data = package.read_entry(index)
            read = f"{len(data):,} B"
        except Exception as error:  # noqa: BLE001
            read = f"UNREADABLE: {error}"
            verdicts.append(f"0x{tag:08X} unreadable")
            data = b""
        print(f"0x{tag:08X} {kind:<18} {package.path.name:<26} entry {index:>5}  "
              f"type {entry.entry_type:>2}  {read}  blocks {first}+{span}")
        if data and entry.entry_type == 32 and len(data) == 40:
            width, height = struct.unpack_from("<HH", data, 0x0E)
            fmt = struct.unpack_from("<I", data, 4)[0]
            print(f"           {width}x{height}, format {fmt}, {data[0x17]} mips")
        for problem in check_blocks(package, first, span):
            print(f"           BLOCK: {problem}")
            verdicts.append(f"0x{tag:08X} {problem}")

    print("\n=== structural check of every touched package ===")
    touched = {locate(tag, opened)[0].path.name: locate(tag, opened)[0]
               for tag in list(MATERIALS) + list(BODIES) + list(HEADERS)}
    for name, package in sorted(touched.items()):
        problems = package.check()
        print(f"{name:<28} {len(package.entries):>6,} entries  {len(package.blocks):>5,} blocks  "
              f"{len(problems)} complaints")
        for problem in problems[:5]:
            print(f"    {problem}")
            verdicts.append(f"{name}: {problem}")

    print()
    if verdicts:
        print(f"{len(verdicts)} FAILURES:")
        for line in verdicts:
            print(f"  {line}")
    else:
        print("Every invariant this script knows how to state holds in the shipped bytes.")


if __name__ == "__main__":
    main()
