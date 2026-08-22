"""Fill dump/request.txt with armour-package materials that might already bind a texture.

2026-08-22: dumping *any* 1408 B twin pulled sandbox_0691 world materials. Those match the
mask's slot-3 layout but use vertex shader 0x80FEBAC3. Pointing chest parts at them made
tank/skin/twirl/necklace vanish; the mask (VS 0x81532C62) stayed. Next dump is materials
whose tags live in the armour families the character already loads.

Cap 1,024. Materials were never rewritten, so they are safe to dump.

Usage:
    python request_bindable_materials.py           # write request.txt
    python request_bindable_materials.py --dry-run
"""
from __future__ import annotations

import sys
from pathlib import Path

from bone_probe import DUMP, INDEX, PLAYABLE, dumped, entry_of, meshes_of
from tigerpkg import Package, TAG_BASE

REQUEST = Path(r"C:\Sunrise\bin\x64\Sunrise\dump\request.txt")
MATERIAL = 0x808071E8
STAGE_SIZE = 1032
MASK_SIZE = 1408           # 0x80EFA1DC
SPLIT_SIZES = {1312, 1408, 1584, 1616}  # the five chest carriers
CAP = 1024
ARMOR = ("sandbox_037c", "sandbox_037d", "sandbox_0698", "sandbox_0699", "sandbox_01fd")


def family_of(path: Path) -> str | None:
    stem = path.stem.rsplit("_", 1)[0]
    return stem if any(stem.endswith(name.split("_", 1)[1]) and name in f"w64_{stem}" or True
                       for name in ARMOR) else None


def armor_path(path: Path) -> bool:
    stem = path.stem.rsplit("_", 1)[0]
    return any(family in stem for family in ARMOR)


def collect_armor() -> dict[int, tuple[int, str]]:
    """@return `{tag: (size, package stem)}` for every material in the armour families."""
    found: dict[int, tuple[int, str]] = {}
    for path in INDEX.values():
        if not armor_path(path):
            continue
        pkg = Package(path)
        stem = path.stem.rsplit("_", 1)[0]
        for index, entry in enumerate(pkg.entries):
            if entry.reference != MATERIAL:
                continue
            tag = pkg.tag_for(index)
            found[tag] = (entry.size, stem)
    return found


def equipped_undumped() -> set[int]:
    wanted: set[int] = set()
    for _name, tag in PLAYABLE.items():
        body = dumped(tag)
        if body is None:
            continue
        for mesh in meshes_of(body):
            for at, *_ in mesh["parts"]:
                import struct
                (material,) = struct.unpack_from("<I", body, at)
                if material >= TAG_BASE and dumped(material) is None:
                    wanted.add(material)
    return wanted


def rank(tag: int, size: int, pkg: str, equipped: set[int]) -> tuple[int, int, int]:
    if dumped(tag) is not None:
        return (9, size, tag)
    if tag in equipped and size != STAGE_SIZE:
        return (0, -size, tag)
    if size == MASK_SIZE:
        return (1, tag, 0)
    if size in SPLIT_SIZES:
        return (2, abs(size - MASK_SIZE), tag)
    if size != STAGE_SIZE:
        return (3, abs(size - MASK_SIZE), tag)
    return (4, size, tag)


def main() -> None:
    import struct  # noqa: F401 — equipped_undumped imports locally; keep collect import-free
    sizes = collect_armor()
    equipped = equipped_undumped()
    already = [tag for tag in sizes if dumped(tag) is not None]
    candidates = [tag for tag in sizes if dumped(tag) is None]
    candidates.sort(key=lambda tag: rank(tag, sizes[tag][0], sizes[tag][1], equipped))
    chosen = candidates[:CAP]

    print(f"armour-family materials {len(sizes):,}  already dumped {len(already)}  "
          f"requesting {len(chosen)} / {len(candidates):,}")
    buckets = [
        ("equipped, not 1032 B", lambda t: t in equipped and sizes[t][0] != STAGE_SIZE),
        ("1408 B (gas-mask twin)", lambda t: sizes[t][0] == MASK_SIZE),
        ("other split sizes (1312/1584/1616)", lambda t: sizes[t][0] in SPLIT_SIZES - {MASK_SIZE}),
        ("other non-1032", lambda t: sizes[t][0] not in SPLIT_SIZES and sizes[t][0] != STAGE_SIZE),
        ("1032 B stubs", lambda t: sizes[t][0] == STAGE_SIZE),
    ]
    for label, test in buckets:
        n = sum(1 for tag in chosen if test(tag))
        if n:
            print(f"  {n:4}  {label}")

    lines = [
        "# Armour-family materials (037c/037d/0698/0699/01fd). Not 0691 world binders.",
        "# After launch: python scan_material_textures.py  — keep only VS 0x81532C62 / 0x81532C5B.",
        "",
    ]
    for tag in chosen:
        size, pkg = sizes[tag]
        flag = "equipped" if tag in equipped else pkg.rsplit("_", 1)[-1]
        extra = "  # 1408 like gas mask" if size == MASK_SIZE else ""
        lines.append(f"tag 0x{tag:08X}   # {size:,} B, {flag}{extra}")

    if "--dry-run" in sys.argv:
        print("dry-run, request.txt not written")
        return
    REQUEST.parent.mkdir(parents=True, exist_ok=True)
    REQUEST.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    print(f"wrote {len(chosen)} requests to {REQUEST}")
    print("Launch once, reach the character screen, quit. Then: python scan_material_textures.py")


if __name__ == "__main__":
    main()
