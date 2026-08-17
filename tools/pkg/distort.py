"""
Rewrites every vertex buffer of one destination package, so the change is visible in game.

This is the end-to-end proof: decode a format, write new data in it, and see the game render it.
Class `0x80807173` is the one class actually decoded — a 48-byte `{size, count, stride}` header
followed by `count` 16-byte packed vertices — so it is the only honest place to try this.

## Why no dump is needed first

The element count follows from the entry size alone, `(size - 48) / 16`, which is readable from the
plain entry table. So a correctly-shaped replacement can be built entirely offline, and the whole
test costs one launch rather than one to dump and another to patch.

## Why this should not crash

The vertex count and stride are preserved exactly, so every buffer is the size the game expects and
index buffers still address valid vertices. Only the packed position bytes change. The mesh is drawn
wrong, which is the point; nothing reads out of bounds.

Undo with `python gametest.py --undo` — the receipt format is shared deliberately, so there is one
undo path rather than two.

Usage:
    python distort.py --list                     # candidate packages, write nothing
    python distort.py [--package <name>] [--amount 0.5]
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from patch import write_patch_package_multi
from tigerpkg import Package, PackageError

PACKAGES = Path(r"C:\Sunrise\packages")
RECEIPT = Path(__file__).with_name("gametest_receipt.json")
VERTEX_BUFFER_CLASS = 0x80807173
HEADER_SIZE = 48
STRIDE = 16
CACHE_GLOBS = (
    (Path(r"C:\Sunrise"), "cache_phr_*.dat"),
    (Path(r"C:\Sunrise\bin\x64\Sunrise\cache"), "content_manifest.bin"),
)


def option(name: str, fallback):
    return type(fallback)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else fallback


amount = option("--amount", 0.5)
wanted = option("--package", "")


def patch_index(path: Path) -> int:
    tail = path.stem.rsplit("_", 1)[1]
    return int(tail) if tail.isdigit() else -1


newest: dict[str, Path] = {}
for path in PACKAGES.glob("*.pkg"):
    stem = path.stem.rsplit("_", 1)[0]
    if stem not in newest or patch_index(path) > patch_index(newest[stem]):
        newest[stem] = path

candidates = []
for path in sorted(newest.values()):
    try:
        pkg = Package(path)
    except PackageError:
        continue
    buffers = [
        e for e in pkg.entries
        if e.reference == VERTEX_BUFFER_CLASS
        and e.size > HEADER_SIZE
        and (e.size - HEADER_SIZE) % STRIDE == 0
        and e.start_block < pkg.header.block_count
    ]
    if buffers:
        candidates.append((len(buffers), sum(e.size for e in buffers), path, pkg, buffers))

candidates.sort(key=lambda row: -row[0])
if not candidates:
    raise SystemExit("no package carries decodable vertex buffers")

print(f"{'package':46s} {'buffers':>8} {'vertices':>12} {'bytes':>12}")
for count, total, path, _pkg, buffers in candidates[:10]:
    verts = sum((e.size - HEADER_SIZE) // STRIDE for e in buffers)
    print(f"  {path.name:44s} {count:8,} {verts:12,} {total:12,}")

if "--list" in sys.argv:
    raise SystemExit(0)

if wanted:
    targets = [row for row in candidates if wanted.lower() in row[2].name.lower()]
    if not targets:
        raise SystemExit(f"no candidate package matching {wanted!r}")
else:
    # Which destination is reachable depends on the mod's activity list, not on what is installed.
    # Patching several means the test shows wherever the player can actually get to, instead of
    # spending a launch discovering the one patched package was somewhere unreachable.
    targets = candidates[: option("--count", 5)]

if RECEIPT.is_file():
    raise SystemExit(f"{RECEIPT.name} exists; run 'python gametest.py --undo' first")

written_names: list[str] = []
for count, total, source, pkg, buffers in targets:
    replacements: dict[int, bytes] = {}
    vertices = 0
    for entry in buffers:
        n = (entry.size - HEADER_SIZE) // STRIDE
        body = bytearray(struct.pack("<QQQ", entry.size, n, STRIDE))
        body.extend(b"\x00" * (HEADER_SIZE - len(body)))
        for i in range(n):
            # Positions are normalised int16. A sweeping wave replaces the real silhouette with an
            # obviously synthetic one, which reads as a deliberate change rather than as corruption.
            # The remaining packed fields are left zero; they affect shading, not silhouette.
            wave = int(32767 * amount * ((i % 97) / 97.0 * 2.0 - 1.0))
            body.extend(struct.pack("<8h", wave, -wave, wave // 2, 0, 0, 0, 0, 0))
        if len(body) != entry.size:
            raise SystemExit(f"built {len(body)} bytes for entry {entry.index}, wanted {entry.size}")
        replacements[entry.index] = bytes(body)
        vertices += n

    plans = write_patch_package_multi(source, replacements)
    written = source.with_name(f"{pkg.stem}_{pkg.header.patch_id + 1}.pkg")
    blocks = sum(len(p.spans) for p in plans.values())

    check = Package(written)
    introduced = set(check.check()) - set(pkg.check())
    bad = introduced or any(
        check.read_entry(index) != expected for index, expected in replacements.items()
    )
    if bad:
        written.unlink()
        for complaint in sorted(introduced)[:3]:
            print(f"  {complaint}")
        raise SystemExit(f"{written.name} failed verification; removed, nothing else installed")

    written_names.append(written.name)
    print(f"\n{written.name}: {written.stat().st_size:,} bytes")
    print(f"  {len(replacements)} buffers, {vertices:,} vertices, {blocks} block records")
    print(f"  verified byte-exact")

spare = Path(__file__).parent / "cache_backup"
spare.mkdir(exist_ok=True)
moved = 0
for directory, pattern in CACHE_GLOBS:
    for cache in directory.glob(pattern):
        cache.replace(spare / cache.name)
        moved += 1
print(f"\ncleared {moved} validation cache file(s)")

RECEIPT.write_text(json.dumps({"written": written_names, "mode": "distort"}, indent=2))
print(f"Installed {len(written_names)} patch files:")
for name in written_names:
    print(f"  {name}")
print("\nTravel to any of those destinations. Undo with: python gametest.py --undo")
