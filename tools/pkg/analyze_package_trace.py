"""
Maps a live F8 package-read capture back to Tiger entries.

The game reads encrypted/compressed block bodies by file offset. Tiger's newest package table says
which logical blocks live at those offsets and which entries cross those blocks, so the trace can
identify candidate SEntityModel tags without guessing package families.

Usage:
    python analyze_package_trace.py
    python analyze_package_trace.py C:\\Sunrise\\bin\\x64\\Sunrise\\logs\\sunrise.log
"""
from __future__ import annotations

import collections
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from tigerpkg import BLOCK_SIZE, Package, PackageError

DEFAULT_LOG = Path(r"C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log")
PACKAGES = Path(r"C:\Sunrise\packages")
ENTITY_MODEL = 0x808073A5

TRACE_RE = re.compile(
    r"t=(?P<time>\d+) .*?ev=package_trace stage=read seq=(?P<seq>\d+) "
    r"api=\S+ file=(?P<file>\S+) "
    r"offset=0x(?P<offset>[0-9A-Fa-f]+) size=(?P<size>\d+) .*?"
    r"caller=0x(?P<caller>[0-9A-Fa-f]+)(?: .*?stack=(?P<stack>\S+))?"
)
PATCH_RE = re.compile(r"^(?P<stem>.+)_(?P<patch>\d+)\.pkg$", re.IGNORECASE)


@dataclass(frozen=True)
class Read:
    time: int
    sequence: int
    file: str
    offset: int
    size: int
    caller: int
    stack: str


def newest_packages() -> dict[str, Path]:
    """Returns lower-case package stem -> newest installed patch table."""
    newest: dict[str, tuple[int, Path]] = {}
    for path in PACKAGES.glob("*.pkg"):
        match = PATCH_RE.match(path.name)
        if match is None:
            continue
        stem = match["stem"].lower()
        patch = int(match["patch"])
        if stem not in newest or patch > newest[stem][0]:
            newest[stem] = (patch, path)
    return {stem: path for stem, (_patch, path) in newest.items()}


def parse_sessions(path: Path) -> list[list[Read]]:
    """Returns every completed F8 capture in log order."""
    sessions: list[list[Read]] = []
    active: list[Read] | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "ev=package_trace stage=capture result=started" in line:
            active = []
            continue
        if "ev=package_trace stage=capture result=stopped" in line:
            if active is not None:
                sessions.append(active)
            active = None
            continue
        match = TRACE_RE.search(line)
        if match is None or active is None:
            continue
        active.append(
            Read(
                time=int(match["time"]),
                sequence=int(match["seq"]),
                file=match["file"],
                offset=int(match["offset"], 16),
                size=int(match["size"]),
                caller=int(match["caller"], 16),
                stack=match["stack"] or "",
            )
        )
    return sessions


def entry_last_block(entry) -> int:
    """Returns the inclusive logical block index crossed by one entry."""
    occupied = entry.start_offset + entry.size
    return entry.start_block + max(0, occupied - 1) // BLOCK_SIZE


args = [argument for argument in sys.argv[1:] if not argument.startswith("--")]
log = Path(args[0]) if args else DEFAULT_LOG
session_index = int(sys.argv[sys.argv.index("--session") + 1]) if "--session" in sys.argv else -1
sessions = parse_sessions(log)
if not sessions:
    raise SystemExit(f"no completed package_trace captures in {log}")
try:
    reads = sessions[session_index]
except IndexError as error:
    raise SystemExit(f"capture {session_index} does not exist; log has {len(sessions)}") from error
if not reads:
    raise SystemExit(f"capture {session_index} contains no package reads")

newest = newest_packages()
opened: dict[str, Package | None] = {}
entry_hits: collections.Counter[tuple[int, int, int, str]] = collections.Counter()
block_hits: collections.Counter[tuple[str, int]] = collections.Counter()
callers: collections.Counter[int] = collections.Counter(read.caller for read in reads)
stacks: collections.Counter[str] = collections.Counter(read.stack for read in reads if read.stack)
unmapped = collections.Counter()
entries_by_block: dict[tuple[str, int], list] = {}
model_timeline: list[tuple[Read, set[int]]] = []
families: collections.Counter[str] = collections.Counter()

for read in reads:
    match = PATCH_RE.match(read.file)
    if match is None:
        unmapped[(read.file, "filename")] += 1
        continue
    stem = match["stem"].lower()
    patch_id = int(match["patch"])
    if stem not in opened:
        source = newest.get(stem)
        try:
            opened[stem] = Package(source) if source else None
        except PackageError:
            opened[stem] = None
    package = opened[stem]
    if package is None:
        unmapped[(read.file, "package")] += 1
        continue
    families[stem] += 1

    read_end = read.offset + max(read.size, 1)
    touched = [
        block.index
        for block in package.blocks
        if block.patch_id == patch_id
        and block.offset < read_end
        and block.offset + block.size > read.offset
    ]
    if not touched:
        unmapped[(read.file, "offset")] += 1
        continue
    for block_index in touched:
        block_hits[(stem, block_index)] += 1
        cache_key = (stem, block_index)
        if cache_key not in entries_by_block:
            entries_by_block[cache_key] = [
                entry
                for entry in package.entries
                if entry.size != 0
                and entry.start_block <= block_index <= entry_last_block(entry)
            ]
        model_tags: set[int] = set()
        for entry in entries_by_block[cache_key]:
            tag = package.tag_for(entry.index)
            key = (tag, entry.reference, entry.size, stem)
            entry_hits[key] += 1
            if entry.reference == ENTITY_MODEL:
                model_tags.add(tag)
        if model_tags:
            model_timeline.append((read, model_tags))

selected = session_index if session_index >= 0 else len(sessions) + session_index
print(f"capture: {selected + 1}/{len(sessions)} with {len(reads):,} package reads from {log}")
print(f"mapped:  {sum(block_hits.values()):,} block touches across {len(block_hits):,} blocks")
if unmapped:
    reasons = ", ".join(reason for (_file, reason), _count in unmapped.most_common(3))
    print(f"unmapped: {sum(unmapped.values()):,} reads ({reasons})")

print("\n--- package families by read count ---")
for stem, count in families.most_common(30):
    print(f"{count:6,}  {stem}")

print("\n--- SEntityModel candidates (class 0x808073A5) ---")
models = [(count, key) for key, count in entry_hits.items() if key[1] == ENTITY_MODEL]
models.sort(reverse=True)
if not models:
    print("  none in the captured blocks")
else:
    print(f"{'hits':>6}  {'tag':>10}  {'bytes':>9}  package")
    for count, (tag, _reference, size, stem) in models[:100]:
        print(f"{count:6,}  0x{tag:08X}  {size:9,}  {stem}")

print("\n--- model-bearing read timeline ---")
first_time = reads[0].time
if not model_timeline:
    print("  no read touched a block containing an SEntityModel")
else:
    print(f"{'seq':>6}  {'+ms':>7}  {'models':>6}  package file / candidate tags")
    for read, tags in model_timeline[:250]:
        shown = ",".join(f"0x{tag:08X}" for tag in sorted(tags)[:12])
        suffix = ",..." if len(tags) > 12 else ""
        print(
            f"{read.sequence:6,}  {read.time - first_time:7,}  {len(tags):6,}  "
            f"{read.file}  {shown}{suffix}"
        )

print("\n--- all structured entry candidates ---")
print(f"{'hits':>6}  {'tag':>10}  {'class/ref':>10}  {'bytes':>9}  package")
for (tag, reference, size, stem), count in entry_hits.most_common(100):
    print(f"{count:6,}  0x{tag:08X}  0x{reference:08X}  {size:9,}  {stem}")

print("\n--- loader caller RVAs ---")
for caller, count in callers.most_common(20):
    print(f"{count:6,}  destiny2.exe+0x{caller:X}")
if stacks:
    print("\n--- game stack chains ---")
    for stack, count in stacks.most_common(20):
        print(f"{count:6,}  {stack}")
