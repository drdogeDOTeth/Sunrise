"""
Reader for Destiny 2 Tiger packages (header version 38). **Incomplete — see below.**

The format is transcribed from Sunrise's own reader under
`Sunrise/src/middleware/content/packages/reader/`. Reading is implemented before writing because it
is checkable: a parse agreeing with thousands of shipped files is evidence the layout is understood,
whereas a writer cannot be judged until the game either loads its output or does not.

## Known limitation: there is more than one header layout

Running `verify_all.py` over 2,202 shipped packages parses every file without raising, but the
results expose a gap rather than confirming success: 58,776 distinct block-flag values (there should
be a handful) and 1,698 files whose block bodies point past EOF. Both are the signature of reading
past a table into unrelated bytes.

The cause is that the same logical fields sit at different offsets in different files. Of the 2,202
packages, 1,852 carry the entry-table offset at `0x110` — the offset Sunrise's `HeaderOffsets`
documents — while 350 carry it at `0x0B8` and leave `0x110` zero. The two are mutually exclusive.
The header's high word at `0x00` is 2 for both, so it does not discriminate them, and the
discriminator has not been identified.

This is consistent with Sunrise's reader being correct for the packages Sunrise actually opens.
It reads what it needs and is never pointed at the rest, so its offsets were never wrong for it.
A repacker has no such luxury.

Fixing this needs the discriminator identified before the writer is worth starting, because writing
a header in the wrong variant produces a file the game rejects for reasons that will be very hard to
attribute.

Block bodies are left compressed here. Decompression needs the game's own oo2core DLL and is the
caller's problem; `Block.flags` says whether it is required.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_VERSION = 38
HEADER_SIZE = 0x180
BLOCK_SIZE = 0x40000
ENTRY_TABLE_ADJUSTMENT = 96
BLOCK_TABLE_GAP = 32
ENTRY_RECORD_SIZE = 16
BLOCK_RECORD_SIZE = 48

TAG_BASE = 0x80800000
TAG_ENTRY_BITS = 13
TAG_ENTRY_MASK = (1 << TAG_ENTRY_BITS) - 1

# Header field offsets, all little-endian.
OFF_VERSION = 0x00
OFF_PACKAGE_ID = 0x04
OFF_PATCH_ID = 0x20
OFF_ENTRY_COUNT = 0xB4
OFF_BLOCK_COUNT = 0xD0
OFF_ENTRY_TABLE = 0x110

FLAG_COMPRESSED = 0x1
FLAG_ENCRYPTED = 0x2
FLAG_ALTERNATE_KEY = 0x4


class PackageError(Exception):
    """Raised when a file is not a supported Tiger package."""


@dataclass(frozen=True)
class Header:
    version: int
    package_id: int
    patch_id: int
    entry_count: int
    block_count: int
    entry_table: int
    block_table: int


@dataclass(frozen=True)
class Entry:
    """One entry-table row, with its block placement already decoded."""

    index: int
    reference: int
    type_info: int
    block_info: int

    @property
    def start_block(self) -> int:
        return self.block_info & 0x3FFF

    @property
    def start_offset(self) -> int:
        return ((self.block_info >> 14) & 0x3FFF) << 4

    @property
    def size(self) -> int:
        return self.block_info >> 28

    @property
    def entry_type(self) -> int:
        return (self.type_info >> 9) & 0x7F

    @property
    def entry_subtype(self) -> int:
        return (self.type_info >> 6) & 0x7


@dataclass(frozen=True)
class Block:
    """One block-table row. `offset`/`size` address the body inside this file."""

    index: int
    offset: int
    size: int
    patch_id: int
    flags: int
    opaque: bytes
    tag: bytes

    @property
    def compressed(self) -> bool:
        return bool(self.flags & FLAG_COMPRESSED)

    @property
    def encrypted(self) -> bool:
        return bool(self.flags & FLAG_ENCRYPTED)

    @property
    def alternate_key(self) -> bool:
        return bool(self.flags & FLAG_ALTERNATE_KEY)


def parse_header(raw: bytes) -> Header:
    """Parses the public header prefix, refusing anything but version 38."""
    if len(raw) < HEADER_SIZE:
        raise PackageError(f"file shorter than a header: {len(raw)} bytes")

    (version,) = struct.unpack_from("<H", raw, OFF_VERSION)
    if version != SUPPORTED_VERSION:
        raise PackageError(f"unsupported header version {version}")

    (package_id,) = struct.unpack_from("<H", raw, OFF_PACKAGE_ID)
    (patch_id,) = struct.unpack_from("<H", raw, OFF_PATCH_ID)
    (entry_count,) = struct.unpack_from("<I", raw, OFF_ENTRY_COUNT)
    (block_count,) = struct.unpack_from("<I", raw, OFF_BLOCK_COUNT)
    (entry_table_raw,) = struct.unpack_from("<I", raw, OFF_ENTRY_TABLE)

    entry_table = entry_table_raw + ENTRY_TABLE_ADJUSTMENT
    block_table = entry_table + entry_count * ENTRY_RECORD_SIZE + BLOCK_TABLE_GAP

    if entry_count == 0 or block_count == 0:
        raise PackageError("header declares no entries or no blocks")

    return Header(
        version=version,
        package_id=package_id,
        patch_id=patch_id,
        entry_count=entry_count,
        block_count=block_count,
        entry_table=entry_table,
        block_table=block_table,
    )


class Package:
    """One opened .pkg file and its decoded tables."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        raw = self.path.read_bytes()
        self._raw = raw
        self.header = parse_header(raw)
        self.entries = self._read_entries()
        self.blocks = self._read_blocks()

    def _read_entries(self) -> list[Entry]:
        head = self.header
        end = head.entry_table + head.entry_count * ENTRY_RECORD_SIZE
        if end > len(self._raw):
            raise PackageError("entry table runs past end of file")
        out = []
        for i in range(head.entry_count):
            at = head.entry_table + i * ENTRY_RECORD_SIZE
            reference, type_info, block_info = struct.unpack_from("<IIQ", self._raw, at)
            out.append(Entry(i, reference, type_info, block_info))
        return out

    def _read_blocks(self) -> list[Block]:
        head = self.header
        end = head.block_table + head.block_count * BLOCK_RECORD_SIZE
        if end > len(self._raw):
            raise PackageError("block table runs past end of file")
        out = []
        for i in range(head.block_count):
            at = head.block_table + i * BLOCK_RECORD_SIZE
            offset, size, patch_id, flags = struct.unpack_from("<IIHH", self._raw, at)
            opaque = self._raw[at + 12 : at + 32]
            tag = self._raw[at + 32 : at + 48]
            out.append(Block(i, offset, size, patch_id, flags, opaque, tag))
        return out

    def tag_for(self, entry_index: int) -> int:
        """@return The tag handle the game uses to address one entry of this package."""
        return TAG_BASE | (self.header.package_id << TAG_ENTRY_BITS) | entry_index

    def block_body(self, index: int) -> bytes:
        """@return One block's raw (still compressed/encrypted) body bytes."""
        block = self.blocks[index]
        return self._raw[block.offset : block.offset + block.size]

    def check(self) -> list[str]:
        """@return Structural complaints, empty when the file is internally consistent."""
        problems = []
        size = len(self._raw)
        for block in self.blocks:
            if block.offset + block.size > size:
                problems.append(f"block {block.index} body runs past EOF")
            if block.size > BLOCK_SIZE and block.compressed is False:
                problems.append(f"block {block.index} uncompressed body exceeds {BLOCK_SIZE:#x}")
        for entry in self.entries:
            if entry.start_block >= self.header.block_count:
                problems.append(f"entry {entry.index} names block {entry.start_block}, out of range")
        return problems

    def __repr__(self) -> str:
        h = self.header
        return (
            f"<Package {self.path.name} id=0x{h.package_id:04X} patch={h.patch_id} "
            f"entries={h.entry_count} blocks={h.block_count}>"
        )
