"""Name the textures the game actually reads while drawing a Guardian. No guessing.

Four sweeps have painted 1,300-odd textures across twelve packages to find the body's albedo by
elimination, and every one was a guess about *where* to look. The game already tells us: the
package-read hook records every file read with its offset, and Tiger's block table says which
entry lives at that offset. Filter those entries to textures and the answer is a list, not a
hypothesis.

**Capture procedure** - the character is built at login, so a capture that starts at the character
screen sees nothing unless something forces a rebuild. Clicking between the three characters does:

    1. Launch, wait for SELECT CHARACTER.
    2. Press **F8** to open the capture window.
    3. Click Hunter, then Titan, then Warlock, pausing on each until it has drawn.
    4. Press **F8** again to close it.
    5. Quit.

Then `python trace_textures.py`. It archives the log first, because the log rotates one deep and
a second launch destroys the capture.

What comes back is every texture entry read during the window, marked with whether one of our
sweeps already painted it. A texture that was read but never painted is the thing four sweeps
were looking for.

Usage:
    python trace_textures.py                     # newest capture in the live log
    python trace_textures.py --session 0         # an earlier capture in the same log
    python trace_textures.py path\\to\\sunrise.log
"""
from __future__ import annotations

import collections
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from bone_probe import INDEX, entry_of
from paint_textures import TEXTURE_BODY_TYPE, TEXTURE_HEADER_TYPE, already_painted, ours
from tigerpkg import BLOCK_SIZE, Package, PackageError, TAG_BASE, TAG_ENTRY_BITS

LOG = Path(r"C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log")
ARCHIVE = Path(__file__).with_name("trace_archive")
TRACED = Path(__file__).with_name("traced_textures.json")
PACKAGES = Path(r"C:\Sunrise\packages")

TRACE_RE = re.compile(
    r"t=(?P<time>\d+) .*?ev=package_trace stage=read seq=(?P<seq>\d+) "
    r"api=\S+ file=(?P<file>\S+) "
    r"offset=0x(?P<offset>[0-9A-Fa-f]+) size=(?P<size>\d+)"
)
PATCH_RE = re.compile(r"^(?P<stem>.+)_(?P<patch>\d+)\.pkg$", re.IGNORECASE)
STARTED = "ev=package_trace stage=capture result=started"
STOPPED = "ev=package_trace stage=capture result=stopped"


def archive(log: Path) -> Path:
    """
    Copies the log aside before anything else.

    The log rotates one deep, so the next launch overwrites the capture that cost a launch to take.
    Copying first makes re-analysis free and means a mistake here is never expensive.
    """
    ARCHIVE.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    kept = ARCHIVE / f"sunrise-{stamp}.log"
    shutil.copy2(log, kept)
    print(f"archived {log.name} -> {kept}")
    return kept


def captures(log: Path) -> list[list[tuple[str, int, int]]]:
    """@return Each completed F8 window as a list of `(file, offset, size)` reads."""
    out: list[list[tuple[str, int, int]]] = []
    active: list[tuple[str, int, int]] | None = None
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if STARTED in line:
            active = []
            continue
        if STOPPED in line:
            if active is not None:
                out.append(active)
            active = None
            continue
        if active is None:
            continue
        match = TRACE_RE.search(line)
        if match is not None:
            active.append((match["file"], int(match["offset"], 16), int(match["size"])))
    return out


def entries_by_block(package: Package) -> dict[int, list[int]]:
    """@return Logical block index -> the entry indices that cross it."""
    table: dict[int, list[int]] = collections.defaultdict(list)
    for index, entry in enumerate(package.entries):
        if entry.size == 0:
            continue
        last = entry.start_block + max(0, entry.start_offset + entry.size - 1) // BLOCK_SIZE
        for block in range(entry.start_block, min(last, package.header.block_count - 1) + 1):
            table[block].append(index)
    return table


def is_texture(package_id: int, index: int, body) -> bool:
    """@return True when this entry is a texture body paired with a 40-byte type-32 header."""
    if body.entry_type != TEXTURE_BODY_TYPE or body.size < 16:
        return False
    header = entry_of(body.reference)
    if header is None or header.size != 40 or header.entry_type != TEXTURE_HEADER_TYPE:
        return False
    return header.reference == TAG_BASE + (package_id << TAG_ENTRY_BITS) + index


def main() -> None:
    arguments = [value for value in sys.argv[1:] if not value.startswith("--")]
    log = Path(arguments[0]) if arguments else LOG
    if not log.is_file():
        raise SystemExit(f"no log at {log}")
    if log == LOG:
        log = archive(log)

    windows = captures(log)
    if not windows:
        raise SystemExit(
            f"no completed F8 capture in {log.name}.\n"
            "Launch, reach SELECT CHARACTER, press F8, click through all three characters, "
            "press F8 again, then quit."
        )
    which = int(sys.argv[sys.argv.index("--session") + 1]) if "--session" in sys.argv else -1
    reads = windows[which]
    print(f"{len(windows)} capture(s); using #{which} with {len(reads):,} reads\n")

    by_stem: dict[str, Package | None] = {}
    stem_id: dict[str, int] = {}
    block_index: dict[str, dict[int, list[int]]] = {}
    for package_id, path in INDEX.items():
        stem_id[path.stem.rsplit("_", 1)[0].lower()] = package_id

    hits: collections.Counter[int] = collections.Counter()
    families: collections.Counter[str] = collections.Counter()
    unmapped = 0
    for name, offset, size in reads:
        # The hook logs whatever path the game passed, which may be absolute. Match on the leaf.
        match = PATCH_RE.match(Path(name.replace("\\", "/")).name)
        if match is None:
            unmapped += 1
            continue
        stem, patch_id = match["stem"].lower(), int(match["patch"])
        families[stem] += 1
        if stem not in by_stem:
            newest = next((p for pid, p in INDEX.items()
                           if p.stem.rsplit("_", 1)[0].lower() == stem), None)
            try:
                by_stem[stem] = Package(newest) if newest else None
            except PackageError:
                by_stem[stem] = None
            if by_stem[stem] is not None:
                block_index[stem] = entries_by_block(by_stem[stem])
        package = by_stem[stem]
        if package is None:
            unmapped += 1
            continue
        end = offset + max(size, 1)
        package_id = stem_id.get(stem, package.header.package_id)
        for block in package.blocks:
            if block.patch_id != patch_id or block.offset >= end or block.offset + block.size <= offset:
                continue
            for index in block_index[stem].get(block.index, ()):
                hits[TAG_BASE + (package_id << TAG_ENTRY_BITS) + index] += 1

    print("reads by package:")
    for stem, count in families.most_common(12):
        print(f"    {stem:<30} {count:>6,}")
    if unmapped:
        print(f"    ({unmapped:,} reads could not be mapped)")

    textures = []
    for tag, count in hits.items():
        body = entry_of(tag)
        package_id = (tag - TAG_BASE) >> TAG_ENTRY_BITS
        index = (tag - TAG_BASE) & 0x1FFF
        if body is None or not is_texture(package_id, index, body):
            continue
        path = INDEX[package_id]
        package = by_stem.get(path.stem.rsplit("_", 1)[0].lower()) or Package(path)
        painted = ours(package, body) and already_painted(package, body)
        textures.append((count, tag, body.size, path.stem, painted))

    textures.sort(reverse=True)
    total = sum(size for _c, _t, size, _s, _p in textures)
    print(f"\n{len(textures)} TEXTURES READ while the Guardians were drawn, "
          f"{total / 1e6:,.0f} MB in total")
    print(f"    {'reads':>6}  {'tag':<12} {'bytes':>12}  painted  package")
    for count, tag, size, stem, painted in textures[:25]:
        print(f"    {count:>6}  0x{tag:08X}  {size:>12,}  "
              f"{'yes' if painted else '** NO **'}  {stem}")
    if len(textures) > 25:
        print(f"    ... {len(textures) - 25} more")

    # A read covers a whole block and every entry sharing that block is credited, so the read count
    # is a ranking, not a proof. Writing the whole measured set out lets the painter take a --top
    # slice of it: the body is a large surface whose maps are read repeatedly, so it ranks high.
    limit = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else len(textures)
    keep = textures[:limit]
    TRACED.write_text(json.dumps(
        [{"tag": f"0x{tag:08X}", "size": size, "package": stem, "reads": count}
         for count, tag, size, stem, _painted in keep], indent=2), encoding="utf-8")
    print(f"\nwrote {len(keep)} textures ({sum(r[2] for r in keep) / 1e6:,.0f} MB) to {TRACED}")
    print("Paint them:  python paint_textures.py --traced")


if __name__ == "__main__":
    main()
