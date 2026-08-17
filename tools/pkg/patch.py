"""
Replaces the bytes behind one package entry, in place, preserving everything else.

## Why patch rather than rebuild

A from-scratch writer has to reproduce every header field, and the version-38 header carries several
that are not understood — the hash-shaped words at `0x0BC`..`0x0CC` and `0x0D8`..`0x0E8` among them.
Emitting those wrong yields a file the game rejects for reasons that are near-impossible to
attribute. Patching sidesteps the whole problem: the original header, both tables and every other
body stay exactly as shipped, and only the records describing the replaced entry change.

## What it does

The new body is appended past the end of the file and the entry's own block record is repointed at
it, rewritten as a plain block — neither compressed nor encrypted. That is legal because block flags
are per-record and the reader honours them per block, so a plain block sitting among encrypted ones
decodes without any key. This is what makes the whole approach work without touching the game's
proprietary key table: writing never needs it.

## Limits, all enforced rather than assumed

- The replacement must fit one block (`0x40000`), because growing the block table would move every
  byte after it.
- The entry must own every block it spans. Entries are packed several to a block, so repointing a
  shared block would corrupt its neighbours. `plan_patch` refuses when another entry is in the way.
- Only the file's own patch level is written. Bodies belonging to other patch files are left alone.
"""
from __future__ import annotations

import shutil
import struct
from dataclasses import dataclass
from pathlib import Path

from tigerpkg import (
    BLOCK_RECORD_SIZE,
    BLOCK_SIZE,
    ENTRY_RECORD_SIZE,
    OFF_BLOCK_COUNT,
    OFF_PATCH_ID,
    Package,
    PackageError,
)

# Appended bodies start on this boundary, matching the alignment shipped bodies already sit on.
BODY_ALIGNMENT = 16
# Every one of the 2,202 shipped packages satisfies both of these exactly: 0x164 is the real file
# size, and the file ends in a 0x800 trailer whose start is recorded at 0x160. A written file that
# breaks the relationship is rejected by the patchable registrar with -86, while a byte-identical
# copy that preserves it registers cleanly - which is how the trailer was identified.
OFF_TRAILER = 0x160
OFF_FILE_SIZE = 0x164
TRAILER_SIZE = 0x800
# Smallest plaintext a block is assumed to carry when its real size cannot be measured. This is
# Oodle's decode step, the granularity Sunrise itself searches on.
MIN_ASSUMED_PLAINTEXT = 0x4000


@dataclass(frozen=True)
class Plan:
    """A checked description of one entry replacement."""

    entry_index: int
    block_index: int
    old_size: int
    new_size: int
    spans: list[int]


def encode_block_info(start_block: int, start_offset: int, size: int) -> int:
    """@return The packed `blockInfo` word for one entry placement."""
    if start_block > 0x3FFF:
        raise PackageError(f"start block {start_block} exceeds the 14-bit field")
    if start_offset % 16:
        raise PackageError(f"start offset {start_offset} is not 16-byte aligned")
    if (start_offset >> 4) > 0x3FFF:
        raise PackageError(f"start offset {start_offset} exceeds the 14-bit field")
    return (size << 28) | (((start_offset >> 4) & 0x3FFF) << 14) | start_block


def spanned_blocks(pkg: Package, entry_index: int) -> list[int]:
    """
    Lists the blocks one entry's bytes occupy.

    Plaintext sizes are only known for plain blocks, so a span crossing a compressed or encrypted
    block cannot be measured here and is refused rather than guessed.
    """
    entry = pkg.entries[entry_index]
    spans: list[int] = []
    covered = 0
    index = entry.start_block
    while covered < entry.size:
        if index >= pkg.header.block_count:
            raise PackageError(f"entry {entry_index} runs past the block table")
        block = pkg.blocks[index]
        if block.compressed or block.encrypted:
            raise PackageError(
                f"entry {entry_index} crosses non-plain block {index}; its plaintext size is not "
                "recorded, so the span cannot be measured offline"
            )
        skip = entry.start_offset if index == entry.start_block else 0
        covered += max(0, block.size - skip)
        spans.append(index)
        index += 1
    return spans


def reachable_blocks(pkg: Package, entry_index: int) -> range:
    """
    Bounds the blocks one entry can touch, without needing every block's plaintext size.

    An entry that crosses a compressed or encrypted block cannot be measured exactly, so its reach
    is over-estimated using the smallest plaintext a block can plausibly carry. Over-estimating
    costs a rejected candidate; under-estimating corrupts a neighbour silently, which is the failure
    this exists to prevent.
    """
    entry = pkg.entries[entry_index]
    try:
        spans = spanned_blocks(pkg, entry_index)
        return range(spans[0], spans[-1] + 1)
    except PackageError:
        reach = -(-entry.size // MIN_ASSUMED_PLAINTEXT) + 1
        return range(entry.start_block, min(entry.start_block + reach, pkg.header.block_count))


def plan_patch(pkg: Package, entry_index: int, new_size: int) -> Plan:
    """Checks that one entry can be replaced, and refuses with a reason when it cannot."""
    if not 0 <= entry_index < pkg.header.entry_count:
        raise PackageError(f"entry {entry_index} is outside this package")
    if new_size == 0:
        raise PackageError("replacement is empty")
    if new_size > BLOCK_SIZE:
        raise PackageError(
            f"replacement is {new_size:,} bytes, over the {BLOCK_SIZE:,}-byte single-block limit"
        )

    entry = pkg.entries[entry_index]
    spans = spanned_blocks(pkg, entry_index)
    owned = set(spans)

    # Entries pack several to a block and spill across block boundaries, so a neighbour can occupy
    # one of these blocks without starting in it. Checking start blocks alone misses exactly that.
    for other in pkg.entries:
        if other.index == entry_index or other.size == 0:
            continue
        if other.start_block >= pkg.header.block_count:
            continue
        if owned.isdisjoint(reachable_blocks(pkg, other.index)):
            continue
        raise PackageError(
            f"entry {other.index} occupies block "
            f"{sorted(owned & set(reachable_blocks(pkg, other.index)))[0]} too; "
            "repointing it would corrupt that entry"
        )

    for index in spans:
        block = pkg.blocks[index]
        if block.patch_id != pkg.header.patch_id:
            raise PackageError(
                f"block {index} belongs to patch {block.patch_id}, not this file "
                f"({pkg.header.patch_id}); patch it in its own file instead"
            )

    return Plan(entry_index, entry.start_block, entry.size, new_size, spans)


def write_patch_package(source: str | Path, entry_index: int, new_data: bytes) -> Plan:
    """
    Writes the next patch file of one package, redirecting a single entry to new bytes.

    This is the format's own update mechanism rather than a workaround. A patch file carries the
    full entry and block tables, but its block records keep the patch ids of the files that already
    hold their bodies, so only genuinely new data is stored. The replaced entry is pointed at a
    freshly appended block record, which means no existing block is disturbed and no neighbouring
    entry can be corrupted — the objection that sinks in-place repointing does not apply here.

    Every header field that is not understood is inherited verbatim from the source file.

    @param source Newest existing file of the package. It is never modified.
    @param entry_index Entry to redirect.
    @param new_data Bytes the entry should yield.
    @return The plan that was carried out.
    """
    source = Path(source)
    pkg = Package(source)
    if not 0 <= entry_index < pkg.header.entry_count:
        raise PackageError(f"entry {entry_index} is outside this package")
    if not new_data:
        raise PackageError("replacement is empty")
    if len(new_data) > BLOCK_SIZE:
        raise PackageError(
            f"replacement is {len(new_data):,} bytes, over the {BLOCK_SIZE:,}-byte block limit"
        )

    new_patch_id = pkg.header.patch_id + 1
    new_block_index = pkg.header.block_count
    if new_block_index > 0x3FFF:
        raise PackageError(f"block table is full at {new_block_index} records")

    # Writing over a sibling would silently discard shipped bodies that other records still point
    # at, and the damage would only surface as missing content much later.
    out_path = source.with_name(f"{pkg.stem}_{new_patch_id}.pkg")
    if out_path.exists():
        raise PackageError(
            f"{out_path.name} already exists, so {source.name} is not the newest file of this "
            "package; base the patch on the newest one instead"
        )

    if len(pkg.raw) < TRAILER_SIZE:
        raise PackageError(f"{source.name} is too small to carry a trailer")
    trailer = pkg.raw[-TRAILER_SIZE:]

    table_end = pkg.header.block_table + pkg.header.block_count * BLOCK_RECORD_SIZE
    raw = bytearray(pkg.raw[:table_end])

    # One more block record, describing the only body this file actually stores.
    raw.extend(b"\x00" * BLOCK_RECORD_SIZE)
    body_at = (len(raw) + BODY_ALIGNMENT - 1) // BODY_ALIGNMENT * BODY_ALIGNMENT
    raw.extend(b"\x00" * (body_at - len(raw)))
    raw.extend(new_data)
    # The trailer is carried over rather than invented. Its contents are not understood, and the
    # registrar accepts a file that keeps them; a file that drops them is rejected.
    raw.extend(trailer)

    struct.pack_into("<IIHH", raw, table_end, body_at, len(new_data), new_patch_id, 0)
    struct.pack_into("<H", raw, OFF_PATCH_ID, new_patch_id)
    struct.pack_into("<I", raw, OFF_BLOCK_COUNT, pkg.header.block_count + 1)

    entry_at = pkg.header.entry_table + entry_index * ENTRY_RECORD_SIZE
    struct.pack_into("<Q", raw, entry_at + 8, encode_block_info(new_block_index, 0, len(new_data)))

    struct.pack_into("<I", raw, OFF_FILE_SIZE, len(raw))
    struct.pack_into("<I", raw, OFF_TRAILER, len(raw) - TRAILER_SIZE)

    out_path.write_bytes(bytes(raw))
    return Plan(entry_index, new_block_index, pkg.entries[entry_index].size, len(new_data), [])


def write_noop_patch_package(source: str | Path) -> Path:
    """
    Writes the next patch file as an exact copy of its source, with only the patch id bumped.

    This exists to bisect the registrar's -86 rejection. The file changes nothing: it stores no
    bodies of its own, and every block record keeps the patch id of the file that already holds its
    body, so the package resolves to exactly the same bytes it did before. The only differences from
    a shipped file are the patch id at 0x20 and, consequently, its name.

    If the game accepts this, the container layout is understood and the rejection is caused by the
    table edits a real patch makes. If the game rejects it, the check is intrinsic to the header —
    a signature or an expected-file-set test — and no amount of care with the tables will pass it.
    Those two outcomes need completely different work, which is why it is worth one launch to tell
    them apart.

    @param source Newest existing file of the package. It is never modified.
    @return The file written.
    """
    source = Path(source)
    pkg = Package(source)
    new_patch_id = pkg.header.patch_id + 1
    out_path = source.with_name(f"{pkg.stem}_{new_patch_id}.pkg")
    if out_path.exists():
        raise PackageError(f"{out_path.name} already exists; {source.name} is not the newest file")

    raw = bytearray(pkg.raw)
    struct.pack_into("<H", raw, OFF_PATCH_ID, new_patch_id)
    out_path.write_bytes(bytes(raw))
    return out_path


def verify_patch(out_path: str | Path, entry_index: int, expected: bytes) -> None:
    """Re-reads a patched file and raises unless the entry now yields exactly `expected`."""
    pkg = Package(out_path)
    got = pkg.read_entry(entry_index)
    if got != expected:
        raise PackageError(
            f"entry {entry_index} read back {len(got):,} bytes, expected {len(expected):,}"
        )
    problems = pkg.check()
    if problems:
        raise PackageError(f"patched file has {len(problems)} structural complaints: {problems[0]}")


def copy_family(source: str | Path, destination: str | Path) -> Path:
    """Copies one package and its patch siblings into a directory, so a test never risks the game."""
    source = Path(source)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    pkg = Package(source)
    for sibling in source.parent.glob(f"{pkg.stem}_*.pkg"):
        shutil.copy2(sibling, destination / sibling.name)
    return destination / source.name
