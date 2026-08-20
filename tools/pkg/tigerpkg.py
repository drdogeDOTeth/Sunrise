"""
Reader for Destiny 2 Tiger packages (header version 38).

The format is transcribed from Sunrise's own reader under
`Sunrise/src/middleware/content/packages/reader/`. Reading is implemented before writing because it
is checkable: a parse agreeing with thousands of shipped files is evidence the layout is understood,
whereas a writer cannot be judged until the game either loads its output or does not.

## Two header layouts

Shipped packages carry the table offsets in one of two places, and the installed set contains both:

| layout | entry table  | block table   | packages |
|--------|--------------|---------------|----------|
| A      | `0x110` + 96 | derived       | 1,830    |
| B      | `0x0B8` + 96 | `0x0D4` + 96  | 333      |

The two are mutually exclusive and `0x110` discriminates them: layout B leaves it zero. Sunrise's
`HeaderOffsets` documents layout A only, which is correct for the packages Sunrise opens — it reads
what it needs and is never pointed at the rest. A repacker has no such luxury, so both are handled
here.

Layout A has no explicit block-table offset, so it is derived the way Sunrise derives it:
`entryTable + entryCount * 16 + 32`.

## Patch files

One package is a family of files, `<stem>_0.pkg` through `<stem>_N.pkg`. Every block record names
the patch id of the file holding its body, so most records in a patch file point at a sibling. A
block offset is only meaningful against the file its own `patch_id` selects, which is why `Package`
needs its directory rather than just its own bytes.

Block bodies may be Oodle-compressed, AES-GCM encrypted, or both. `oodle.py` handles compression
against the game's installed codec; encrypted blocks need keys Sunrise recovers from the running
game and are out of reach here.
"""
from __future__ import annotations

import re
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

# Header field offsets shared by both layouts, all little-endian.
OFF_VERSION = 0x00
OFF_PACKAGE_ID = 0x04
OFF_PATCH_ID = 0x20
OFF_ENTRY_COUNT = 0xB4
OFF_BLOCK_COUNT = 0xD0

# Layout-specific table offsets.
OFF_ENTRY_TABLE_A = 0x110
OFF_ENTRY_TABLE_B = 0x0B8
OFF_BLOCK_TABLE_B = 0x0D4

FLAG_COMPRESSED = 0x1
FLAG_ENCRYPTED = 0x2
FLAG_ALTERNATE_KEY = 0x4

PATCH_NAME_RE = re.compile(r"^(?P<stem>.+)_(?P<patch>\d+)\.pkg$", re.IGNORECASE)


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
    layout: str


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
        # Sunrise narrows this to uint32_t, and padding rows rely on the truncation to stay sane.
        return (self.block_info >> 28) & 0xFFFFFFFF

    @property
    def entry_type(self) -> int:
        return (self.type_info >> 9) & 0x7F

    @property
    def entry_subtype(self) -> int:
        return (self.type_info >> 6) & 0x7


@dataclass(frozen=True)
class Block:
    """One block-table row. `offset`/`size` address the body inside the file `patch_id` names."""

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
    """Parses the public header prefix, picking the layout by whether `0x110` is populated."""
    if len(raw) < HEADER_SIZE:
        raise PackageError(f"file shorter than a header: {len(raw)} bytes")

    (version,) = struct.unpack_from("<H", raw, OFF_VERSION)
    if version != SUPPORTED_VERSION:
        raise PackageError(f"unsupported header version {version}")

    (package_id,) = struct.unpack_from("<H", raw, OFF_PACKAGE_ID)
    (patch_id,) = struct.unpack_from("<H", raw, OFF_PATCH_ID)
    (entry_count,) = struct.unpack_from("<I", raw, OFF_ENTRY_COUNT)
    (block_count,) = struct.unpack_from("<I", raw, OFF_BLOCK_COUNT)
    if entry_count == 0 or block_count == 0:
        raise PackageError("header declares no entries or no blocks")

    (entry_table_a,) = struct.unpack_from("<I", raw, OFF_ENTRY_TABLE_A)
    if entry_table_a != 0:
        layout = "A"
        entry_table = entry_table_a + ENTRY_TABLE_ADJUSTMENT
        block_table = entry_table + entry_count * ENTRY_RECORD_SIZE + BLOCK_TABLE_GAP
    else:
        layout = "B"
        (entry_table_b,) = struct.unpack_from("<I", raw, OFF_ENTRY_TABLE_B)
        (block_table_b,) = struct.unpack_from("<I", raw, OFF_BLOCK_TABLE_B)
        if entry_table_b == 0 or block_table_b == 0:
            raise PackageError("neither header layout carries a usable entry-table offset")
        entry_table = entry_table_b + ENTRY_TABLE_ADJUSTMENT
        block_table = block_table_b + ENTRY_TABLE_ADJUSTMENT

    return Header(
        version=version,
        package_id=package_id,
        patch_id=patch_id,
        entry_count=entry_count,
        block_count=block_count,
        entry_table=entry_table,
        block_table=block_table,
        layout=layout,
    )


class Package:
    """One opened .pkg file, its decoded tables, and access to its patch siblings."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        match = PATCH_NAME_RE.match(self.path.name)
        if match is None:
            raise PackageError(f"filename does not carry a patch index: {self.path.name}")
        self.stem = match["stem"]

        raw = self.path.read_bytes()
        self._raw = raw
        self.header = parse_header(raw)
        self.entries = self._read_entries()
        self.blocks = self._read_blocks()
        self._sibling_cache: dict[int, bytes | None] = {self.header.patch_id: raw}
        self._size_cache: dict[int, int | None] = {self.header.patch_id: len(raw)}

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

    @property
    def raw(self) -> bytes:
        """@return This file's bytes, as read."""
        return self._raw

    def patch_path(self, patch_id: int) -> Path:
        """@return The sibling file holding bodies for one patch id, which may not exist."""
        return self.path.with_name(f"{self.stem}_{patch_id}.pkg")

    def _patch_bytes(self, patch_id: int) -> bytes | None:
        if patch_id not in self._sibling_cache:
            sibling = self.patch_path(patch_id)
            self._sibling_cache[patch_id] = sibling.read_bytes() if sibling.is_file() else None
        return self._sibling_cache[patch_id]

    def _patch_size(self, patch_id: int) -> int | None:
        """@return A patch file's size without reading it, or None when it is not installed."""
        if patch_id not in self._size_cache:
            sibling = self.patch_path(patch_id)
            self._size_cache[patch_id] = sibling.stat().st_size if sibling.is_file() else None
        return self._size_cache[patch_id]

    def tag_for(self, entry_index: int) -> int:
        """@return The tag handle the game uses to address one entry of this package."""
        # TAG_BASE is a numeric bias, not a bit-field prefix. OR happens to agree
        # with addition for low package ids, but aliases ids once their shifted
        # value overlaps bit 23. Live traces prove package 0x06DC is addressed as
        # 0x815Bxxxx, not 0x80DBxxxx.
        return TAG_BASE + (self.header.package_id << TAG_ENTRY_BITS) + entry_index

    def block_body(self, index: int) -> bytes:
        """@return One block's stored body, read from whichever patch file holds it."""
        block = self.blocks[index]
        host = self._patch_bytes(block.patch_id)
        if host is None:
            raise PackageError(
                f"block {index} lives in patch {block.patch_id}, "
                f"but {self.patch_path(block.patch_id).name} is not installed"
            )
        body = host[block.offset : block.offset + block.size]
        if len(body) != block.size:
            raise PackageError(f"block {index} body is truncated in patch {block.patch_id}")
        return body

    def read_entry(self, index: int, decoder=None) -> bytes:
        """
        Assembles one entry from the block stream.

        @param index Entry-table row.
        @param decoder Callable taking a compressed block body and returning its plaintext, needed
            only when the entry crosses a compressed block.
        @return The entry's bytes.
        """
        entry = self.entries[index]
        if entry.size == 0:
            raise PackageError(f"entry {index} is empty")

        out = bytearray()
        block_index = entry.start_block
        while len(out) < entry.size:
            if block_index >= self.header.block_count:
                raise PackageError(f"entry {index} runs past the block table")
            block = self.blocks[block_index]
            if block.encrypted:
                raise PackageError(
                    f"entry {index} crosses encrypted block {block_index}; "
                    "block keys are only available inside the running game"
                )
            body = self.block_body(block_index)
            if block.compressed:
                if decoder is None:
                    raise PackageError(
                        f"entry {index} crosses compressed block {block_index} and no decoder "
                        "was supplied"
                    )
                body = decoder(body)
            skip = entry.start_offset if block_index == entry.start_block else 0
            if skip > len(body):
                raise PackageError(f"entry {index} starts past the end of block {block_index}")
            take = min(len(body) - skip, entry.size - len(out))
            if take == 0:
                raise PackageError(f"entry {index} made no progress at block {block_index}")
            out += body[skip : skip + take]
            block_index += 1
        return bytes(out)

    def check(self) -> list[str]:
        """@return Structural complaints, empty when the file is internally consistent."""
        problems = []
        for block in self.blocks:
            host = self._patch_size(block.patch_id)
            if host is None:
                problems.append(f"block {block.index} names uninstalled patch {block.patch_id}")
            elif block.offset + block.size > host:
                problems.append(f"block {block.index} body runs past its patch file")
            if block.flags & ~(FLAG_COMPRESSED | FLAG_ENCRYPTED | FLAG_ALTERNATE_KEY):
                problems.append(f"block {block.index} carries unknown flags {block.flags:#06x}")
        for entry in self.entries:
            if entry.start_block >= self.header.block_count:
                problems.append(f"entry {entry.index} names block {entry.start_block}, out of range")
        return problems

    def __repr__(self) -> str:
        h = self.header
        return (
            f"<Package {self.path.name} layout={h.layout} id=0x{h.package_id:04X} "
            f"patch={h.patch_id} entries={h.entry_count} blocks={h.block_count}>"
        )
