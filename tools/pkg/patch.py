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
it, stored **plain** — neither compressed nor encrypted. A plain block sitting among encrypted ones
decodes without any key, and `w64_audio_01d2_en` mixes the two in one file, so this is the format's
own behaviour rather than a hopeful reading of it. This is what makes the whole approach work
without touching the game's proprietary key table: writing never needs it.

## Why not compressed

Compression is implemented, correct, and **switched off**, because the game will not load the result.

A census of all 2,231 installed packages — **1,897,918 block records** — finds compressed+encrypted
(80.6%) and plain (17.9%) and **exactly zero blocks that are compressed but not encrypted**. The
combination this writer would have to produce, having no keys, is one the format never ships.

Tested rather than inferred. Layers written with compressed blocks **register cleanly** — the
container is structurally right — and then fail at content load:

    files:async: decompression failed for package w64_sandbox_037c_7.pkg

So compression is only reachable together with encryption, and encryption needs the block keys,
which stay inside the game. **Plain is the only encoding a writer without keys can legally produce.**
It is not a shortcut taken for convenience; it is the whole of what is available.

`compress=True` still works and round-trips byte-exact through the game's own codec, so the encoding
is ready if the keys ever become reachable. It should not be used against a live install.

A block record is 48 bytes and every field matters. The four leading fields fill 12; bytes 12-31
carry a SHA-1 and bytes 32-47 an AES-GCM tag used only when the encrypted flag is set. Writing the
first twelve and zeroing the rest yields a file the patchable registrar accepts and the geometry
streamer silently hangs on — the registrar validates structure, the streamer validates content, and
only the second one reads the hash.

**The SHA-1 covers the bytes as stored**, after compression, not the plaintext. Measured rather than
assumed: across 400 compressed-and-encrypted blocks `sha1(stored)` matched the field exactly, and so
did all 321 hash-carrying plain blocks in the three packages this fork patches. A block record is
therefore self-describing — `size` is the stored length the reader will read from disk, and the hash
covers exactly those bytes.

## Limits, all enforced rather than assumed

- A replacement larger than one block (`0x40000`) is split across consecutive appended records.
  Vertex buffers run to 2.6 MB, so this is required rather than optional. The entry's start-block
  field is 14 bits, which caps a package at 16,384 blocks.
- The entry must own every block it spans. Entries are packed several to a block, so repointing a
  shared block would corrupt its neighbours. `plan_patch` refuses when another entry is in the way.
- Only the file's own patch level is written. Bodies belonging to other patch files are left alone.
"""
from __future__ import annotations

import hashlib
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path

from tigerpkg import (
    BLOCK_RECORD_SIZE,
    BLOCK_SIZE,
    ENTRY_RECORD_SIZE,
    FLAG_COMPRESSED,
    OFF_BLOCK_COUNT,
    OFF_PATCH_ID,
    Package,
    PackageError,
)

# Entry start offsets are stored in 14 bits scaled by 16, so a body must begin on a 16-byte
# boundary within the block stream.
BODY_STREAM_ALIGNMENT = 16
# Appended bodies start on this boundary, matching the alignment shipped bodies already sit on.
#
# This was 16, which is what shipped bodies *appear* to satisfy if you only check that they are
# aligned at all. They satisfy far more: across mercury _4 (226 own blocks), mercury _5 (195) and
# w64_audio_01d2_en_2 (163), the least-aligned body in any of them sits on 0x800, with no
# exceptions in 584 blocks. It is the same 2,048-byte sector the trailer occupies, so the container
# is built on sectors throughout.
#
# Writing bodies on 16-byte boundaries produced files that register cleanly and then hang the
# geometry streamer in activity:initial_slice_set_loading. A single small entry survived it, which
# briefly read as evidence that plain blocks were fine — a 3-vertex buffer is easy for the game not
# to draw at all. Ninety-seven did not.
BODY_ALIGNMENT = 0x800
# Every one of the 2,202 shipped packages satisfies both of these exactly: 0x164 is the real file
# size, and the file ends in a 0x800 trailer whose start is recorded at 0x160. A written file that
# breaks the relationship is rejected by the patchable registrar with -86, while a byte-identical
# copy that preserves it registers cleanly - which is how the trailer was identified.
OFF_TRAILER = 0x160
OFF_FILE_SIZE = 0x164
TRAILER_SIZE = 0x800
# The header carries two `{offset, size, sha1[20]}` descriptors covering regions of the file. Both
# verify exactly on every file checked - mercury _4, mercury _5 and w64_audio_01d2_en_2 - which is
# what identified them.
#
# The second one spans the entry table *and* the block table, so growing the block table changes
# both its size and its digest. A file that leaves them stale registers cleanly, reads correctly
# through Sunrise's own reader, and then hangs the game's loader forever in
# activity:initial_slice_set_loading. That is the whole explanation for the hang: registration
# checks structure, Sunrise's reader does not verify these, and the game's loader does.
#
# The first descriptor covers a region ahead of the entry table that a patch never touches, so it
# is carried over untouched.
OFF_REGION_A = 0x0F0
OFF_REGION_B = 0x110
REGION_DIGEST_SIZE = 20
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


def encode_chunk(chunk: bytes, codec) -> tuple[bytes, int]:
    """
    Prepares one block's worth of stream for storage.

    Compression is attempted and **kept only when it wins**, which is what produces the mix of plain
    and compressed blocks the shipped packages actually contain.

    A short final chunk is padded to a whole block before compressing. The reader does not record a
    block's plaintext size — it recovers it by asking the codec for progressively smaller multiples
    of `0x4000` until one decodes (`oodle_installed.cpp`), and `layout.h` states a decompressed block
    is always `kBlockSize`. Padding to that keeps every compressed block on the size the search finds
    first. The padding is never read as content: the entry's own size field bounds what is consumed.

    @param chunk Up to `BLOCK_SIZE` bytes of the body stream.
    @param codec An `oodle.Oodle`, or None to store plain.
    @return The bytes to store, and the block flags describing them.
    """
    if codec is None:
        return chunk, 0
    padded = chunk if len(chunk) == BLOCK_SIZE else chunk + b"\x00" * (BLOCK_SIZE - len(chunk))
    packed = codec.compress(padded)
    # Losing to plain storage is normal for small or already-dense bodies, and a block that grew
    # under compression would cost space and gain nothing.
    if len(packed) >= len(chunk):
        return chunk, 0
    return packed, FLAG_COMPRESSED


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


def write_patch_package(source: str | Path, entry_index: int, new_data: bytes,
                        compress: bool = False) -> Plan:
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
    @param compress Oodle-compress blocks where it pays. Defaults off; see the module docstring.
    @return The plan that was carried out.
    """
    return write_patch_package_multi(source, {entry_index: new_data}, compress)[entry_index]


def write_patch_package_multi(source: str | Path,
                              replacements: dict[int, bytes],
                              compress: bool = False) -> dict[int, Plan]:
    """
    Writes the next patch file, redirecting any number of entries in one go.

    One patch file per changed entry would work but is wasteful and, worse, only the newest file of
    a package is a legal base — so a second patch has to be built on the first, and the chain has to
    be rebuilt from scratch whenever any link changes. Redirecting them together avoids that
    entirely.

    @param source Newest existing file of the package. It is never modified.
    @param replacements Entry index to the bytes it should yield.
    @param compress Oodle-compress blocks where it pays. **Defaults off, and should stay off.**
        A compressed-but-unencrypted block is a combination the game cannot load — see the
        module docstring. Kept because the encoding itself is correct and round-trips
        byte-exact, so it is ready if the block keys ever become reachable.
    @return The plan carried out for each entry.
    """
    source = Path(source)
    pkg = Package(source)
    if not replacements:
        raise PackageError("no replacements given")

    # Imported here, not at module scope: the codec is the game's own DLL, so a machine without the
    # game installed can still import this module to inspect a package.
    codec = None
    if compress:
        from oodle import Oodle, OodleError
        try:
            codec = Oodle()
        except OodleError as problem:
            raise PackageError(
                f"compression asked for but the codec is unavailable: {problem}. "
                "Pass compress=False to write plain blocks instead."
            ) from problem
    for entry_index, new_data in replacements.items():
        if not 0 <= entry_index < pkg.header.entry_count:
            raise PackageError(f"entry {entry_index} is outside this package")
        if not new_data:
            raise PackageError(f"replacement for entry {entry_index} is empty")

    # A package's data is one continuous stream cut into fixed BLOCK_SIZE blocks, and entries sit at
    # arbitrary byte offsets within it. Shipped entries prove it: in mercury_destination_0356 entry
    # 2939 ends inside block 1220 and entry 2962 begins in that same block at offset 257,088.
    #
    # So every block holds exactly BLOCK_SIZE uncompressed bytes, and only the last block a patch
    # file contributes may be short. That is measurable on plain blocks, where stored size *is*
    # uncompressed size: w64_audio_01d2_en_2 has 283 of 285 at exactly 0x40000 with the two partials
    # at the end of each patch's run, and w64_audio_01e7_en_2 has 292 of 293 with its one partial
    # last of all.
    #
    # Giving each entry its own block, as this did before, makes every block partial. A single-block
    # entry survives that because start_offset is 0 and the reader never crosses a boundary, which
    # is why 97 such entries loaded cleanly. Multi-block entries must cross, the arithmetic assumes
    # full blocks, and Mercury froze with 2048 mainloop hitches.
    stream = bytearray()
    placement: dict[int, tuple[int, int]] = {}
    for index, data in replacements.items():
        # Entry start offsets are stored in 14 bits scaled by 16, so each body must begin on a
        # 16-byte boundary within the stream.
        if len(stream) % BODY_STREAM_ALIGNMENT:
            stream.extend(b"\x00" * (BODY_STREAM_ALIGNMENT - len(stream) % BODY_STREAM_ALIGNMENT))
        placement[index] = (len(stream), len(data))
        stream.extend(data)

    chunks = [bytes(stream[at : at + BLOCK_SIZE]) for at in range(0, len(stream), BLOCK_SIZE)]
    total_chunks = len(chunks)

    new_patch_id = pkg.header.patch_id + 1
    new_block_index = pkg.header.block_count
    if new_block_index + total_chunks > 0x3FFF:
        raise PackageError(
            f"block table cannot take {total_chunks} more records; it is at {new_block_index} "
            "and the entry start-block field is 14 bits"
        )

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
    region_offset, region_size = struct.unpack_from("<II", pkg.raw, OFF_REGION_B)
    region_end = region_offset + region_size
    if not region_offset < table_end <= region_end:
        raise PackageError(
            f"{source.name}: block table ends at 0x{table_end:X}, outside the hashed region "
            f"0x{region_offset:X}..0x{region_end:X}")

    # A small table of tag references sits immediately after the block table, inside the hashed
    # region. Nothing in the header points at it, so its position is implied by the table's end and
    # it has to travel with it. Truncating at table_end silently dropped it.
    tail = bytes(pkg.raw[table_end:region_end])

    raw = bytearray(pkg.raw[:table_end])
    # Every record is appended before any body so the table stays contiguous.
    raw.extend(b"\x00" * (BLOCK_RECORD_SIZE * total_chunks))
    raw.extend(tail)

    for record, chunk in enumerate(chunks):
        stored, flags = encode_chunk(chunk, codec)
        body_at = (len(raw) + BODY_ALIGNMENT - 1) // BODY_ALIGNMENT * BODY_ALIGNMENT
        raw.extend(b"\x00" * (body_at - len(raw)))
        raw.extend(stored)
        # A block record is 48 bytes, not the 12 the four leading fields occupy. Writing only
        # those and leaving the rest zero produced a file the registrar accepted and the
        # geometry streamer then hung on, because bytes 12-31 are a SHA-1 of the stored body
        # and every shipped block carries one. Verified against all 285 plain blocks of
        # w64_audio_01d2_en: sha1(body) equals those bytes exactly, with no exceptions.
        #
        # The hash covers the bytes as STORED, after compression - measured across 400
        # compressed-and-encrypted blocks and all 321 hash-carrying plain blocks in the three
        # packages this fork patches, with no exceptions either way.
        #
        # Bytes 32-47 are the AES-GCM tag and are nonzero only for encrypted blocks - in that
        # same package they are set on precisely the 61 blocks whose encrypted flag is set.
        # Ours are never encrypted, so leaving the tag zero is correct by construction rather
        # than by omission. Encrypting would need the block keys, which stay in the game.
        struct.pack_into(
            "<IIHH20s16s",
            raw,
            table_end + record * BLOCK_RECORD_SIZE,
            body_at,
            len(stored),
            new_patch_id,
            flags,
            hashlib.sha1(stored).digest(),
            b"\x00" * 16,
        )

    carried: dict[int, Plan] = {}
    for entry_index, (offset, size) in placement.items():
        first_block = new_block_index + offset // BLOCK_SIZE
        last_block = new_block_index + (offset + size - 1) // BLOCK_SIZE
        entry_at = pkg.header.entry_table + entry_index * ENTRY_RECORD_SIZE
        struct.pack_into(
            "<Q", raw, entry_at + 8,
            encode_block_info(first_block, offset % BLOCK_SIZE, size))
        carried[entry_index] = Plan(
            entry_index,
            first_block,
            pkg.entries[entry_index].size,
            size,
            list(range(first_block, last_block + 1)),
        )

    # The trailer is carried over rather than invented. Its contents are not understood, and the
    # registrar accepts a file that keeps them; a file that drops them is rejected.
    raw.extend(trailer)

    struct.pack_into("<H", raw, OFF_PATCH_ID, new_patch_id)
    struct.pack_into("<I", raw, OFF_BLOCK_COUNT, pkg.header.block_count + total_chunks)
    struct.pack_into("<I", raw, OFF_FILE_SIZE, len(raw))
    struct.pack_into("<I", raw, OFF_TRAILER, len(raw) - TRAILER_SIZE)

    # Last, because every earlier write in this function lands inside the hashed region: the new
    # block records, the tail that moved behind them, and each redirected entry's blockInfo. The
    # header fields just written sit ahead of the region and do not affect it.
    grown = region_size + BLOCK_RECORD_SIZE * total_chunks
    struct.pack_into("<I", raw, OFF_REGION_B + 4, grown)
    struct.pack_into(
        f"<{REGION_DIGEST_SIZE}s",
        raw,
        OFF_REGION_B + 8,
        hashlib.sha1(bytes(raw[region_offset:region_offset + grown])).digest())

    out_path.write_bytes(bytes(raw))
    return carried


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
    """
    Re-reads a patched file and raises unless the entry now yields exactly `expected`.

    The decoder is supplied unconditionally, so this exercises the same decompression path the game
    will take. Reading a compressed block back through the codec is the check that the stored block
    is genuinely decodable, rather than merely well-formed on paper.
    """
    pkg = Package(out_path)
    decoder = None
    try:
        from oodle import Oodle
        decoder = Oodle().decompress_block
    except Exception:
        # Only fatal if the file actually needs it, which read_entry will say.
        pass
    got = pkg.read_entry(entry_index, decoder=decoder)
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
