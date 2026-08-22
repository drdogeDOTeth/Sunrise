"""
Undo a paint layer by writing a new top layer that points its entry rows back at the original blocks.

**Nothing was ever destroyed.** A paint layer *appends* blocks to the cumulative block table and
repoints entry rows at them; the original block records keep their `(patch_id, offset, size)` and
stay reachable. In `w64_sandbox_037c_23` the table grew 463 -> 613 and every original block was still
present, unchanged. So the paint is undone by rewriting the row, not by recovering bytes:

    entry 2060   painted (463, 0, 2793472)   ->   original (269, 38096, 2793472)

This matters because the alternatives do not work:

- **Deleting the layer file is not an option.** Later layers interleave their blocks with the paint
  layer's, so `037c_28` needs 150 blocks out of `_23`. Remove `_23` and the game spins forever at
  character select, asking for blocks that are not installed. Layers truncate from the top only.
- **Rewriting the bodies is not an option.** The originals are AES-GCM encrypted and the block keys
  stay inside the game, so their plaintext is not available offline and never will be here.
- **Knowing the texture format is not required.** Repointing a row restores the original encrypted
  body byte-for-byte, whatever its format was. That side-steps the whole BC1-vs-BC7 ambiguity that
  cannot be resolved offline, because texture headers are encrypted too.

The written file stores **no bodies of its own** - zero new blocks. It is a copy of the newest layer
with the patch id bumped, some entry rows rewritten, and the region-B digest recomputed. The entry
table sits inside region B and outside region A (verified: entry table `0x1860..0x21860`, region B
`0x1800..0x27020`, region A `0x1000..0x1060`), so region B is the only digest a row edit invalidates
- which is exactly what `write_patch_package_multi` already relies on.

Choosing the row to restore is a walk, not a subtraction. An entry may have been painted more than
once, so reverting to "the layer below the newest paint" can just hand back an older paint. This
walks the installed layers newest-to-oldest and takes the first row whose blocks are owned by no
condemned patch id.

Usage:
    python revert_entry_rows.py --package w64_sandbox_037c --painted-by 23,26
    python revert_entry_rows.py --package w64_sandbox_037c --painted-by 23,26 --write
    python revert_entry_rows.py --all-paint-sweeps          # every package, the known bad ids
    python revert_entry_rows.py --package ... --painted-by 23 --types 40
"""
from __future__ import annotations

import hashlib
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from patch import (BLOCK_RECORD_SIZE, ENTRY_RECORD_SIZE, OFF_FILE_SIZE, OFF_PATCH_ID, OFF_REGION_B,
                   OFF_TRAILER, REGION_DIGEST_SIZE, TRAILER_SIZE)
from tigerpkg import BLOCK_SIZE, Package, PackageError

PACKAGES = Path("C:/Sunrise/packages")
# The offset of the 8-byte blockInfo word within an entry record, as `write_patch_package_multi`
# writes it. The first 8 bytes are the reference and type fields and must be carried over unchanged.
BLOCK_INFO_AT = 8


@dataclass(frozen=True)
class Revert:
    entry: int
    from_row: tuple[int, int, int]     # (first block, offset in block, size) currently in force
    to_row: tuple[int, int, int]       # what it will be set back to
    to_layer: int                      # the patch id whose table supplied `to_row`
    blob: bytes                        # the 8 raw bytes to write


def layers_of(stem: str, directory: Path) -> list[tuple[int, Path]]:
    """@return Every installed layer of one package as `(patch id, path)`, oldest first."""
    out = []
    for path in directory.glob(f"{stem}_*.pkg"):
        tail = path.stem[len(stem) + 1:]
        if tail.isdigit():
            out.append((int(tail), path))
    return sorted(out)


def span_of(pkg: Package, entry: int) -> tuple[int, int]:
    """@return `(first block, block count)` an entry's bytes are cut across."""
    record = pkg.entries[entry]
    end = record.start_offset + record.size
    return record.start_block, max(1, -(-end // BLOCK_SIZE))


def owners(pkg: Package, entry: int) -> set[int]:
    """@return The patch ids holding the bodies an entry currently resolves through."""
    first, count = span_of(pkg, entry)
    return {pkg.blocks[b].patch_id for b in range(first, min(first + count, len(pkg.blocks)))}


def row_blob(pkg: Package, entry: int) -> bytes:
    """@return The raw 8-byte blockInfo word from one package's entry table."""
    at = pkg.header.entry_table + entry * ENTRY_RECORD_SIZE + BLOCK_INFO_AT
    return bytes(pkg.raw[at:at + 8])


def row_of(pkg: Package, entry: int) -> tuple[int, int, int]:
    record = pkg.entries[entry]
    return record.start_block, record.start_offset, record.size


def blocks_agree(new: Package, old: Package, entry: int) -> bool:
    """
    Confirms the blocks an old row names still mean the same thing in the newest table.

    Block indices are prefix-stable because layers append, but that is an observation rather than a
    guarantee, and restoring a row against a table where index N moved would point an entry at
    unrelated bytes. Checked rather than assumed.
    """
    first, count = span_of(old, entry)
    if first + count > len(new.blocks):
        return False
    for b in range(first, first + count):
        a, c = new.blocks[b], old.blocks[b]
        if (a.patch_id, a.offset, a.size) != (c.patch_id, c.offset, c.size):
            return False
    return True


def plan(stem: str, bad: set[int], directory: Path,
         types: set[int] | None) -> tuple[Package, list[Revert], list[str]]:
    """
    Works out which rows to restore and to what, without writing anything.

    @return `(newest package, reverts, complaints)`.
    """
    layers = layers_of(stem, directory)
    if not layers:
        raise PackageError(f"no installed layers named {stem}_*.pkg in {directory}")
    newest = Package(layers[-1][1])
    installed = {pid for pid, _ in layers}
    for pid in sorted(bad):
        if pid not in installed:
            raise PackageError(
                f"{stem}: patch {pid} is not installed, so its rows are not in force. Restore it "
                "first - reverting rows requires the layer you are undoing to be present.")

    # Only entries the condemned layers still own. An entry painted by 23 and later overwritten by a
    # good layer needs no help, and rewriting it would undo the good layer instead.
    condemned = []
    for entry, record in enumerate(newest.entries):
        if record.size == 0:
            continue
        if types is not None and getattr(record, "entry_type", None) not in types:
            continue
        if owners(newest, entry) & bad:
            condemned.append(entry)

    older = [(pid, Package(path)) for pid, path in layers[:-1]]
    reverts: list[Revert] = []
    complaints: list[str] = []
    for entry in condemned:
        for pid, old in reversed(older):
            if entry >= len(old.entries) or old.entries[entry].size == 0:
                continue
            if owners(old, entry) & bad:
                continue                      # this layer shows a paint too; keep walking back
            if not blocks_agree(newest, old, entry):
                complaints.append(
                    f"{stem} entry {entry}: patch {pid} names blocks that moved in the newest "
                    "table; refusing to restore this row")
                break
            reverts.append(Revert(entry, row_of(newest, entry), row_of(old, entry), pid,
                                  row_blob(old, entry)))
            break
        else:
            complaints.append(
                f"{stem} entry {entry}: no installed layer holds a row free of {sorted(bad)}; "
                "the original predates the oldest installed layer")
    return newest, reverts, complaints


def write(newest: Package, reverts: list[Revert], trim: bool = True) -> Path:
    """
    Writes the next patch file: bumped id, restored rows, recomputed region digest.

    Zero blocks are added, so the block count is unchanged and the trailer is carried over untouched
    - the registrar rejects a file that drops it.

    @param trim Drop the body area. A copy of the layer beneath carries all of its bodies, but every
        block record still names *its* patch id, so the loader reads them from that file and this
        copy is never touched - `0699_9` was 186 MB of bytes nothing can reach. Verified before
        truncating: this file owns **zero** blocks, so nothing in the package resolves through its
        body area. Everything the loader does read lives at or before `region_end`, because the
        writer lays out header, entry table, block table, tail, then bodies, then trailer.
    """
    if not reverts:
        raise PackageError("nothing to revert")
    new_id = newest.header.patch_id + 1
    out = newest.path.with_name(f"{newest.stem}_{new_id}.pkg")
    if out.exists():
        raise PackageError(f"{out.name} already exists; {newest.path.name} is not the newest layer")

    region_offset, region_size = struct.unpack_from("<II", newest.raw, OFF_REGION_B)
    table = newest.header.entry_table
    table_end = table + newest.header.entry_count * ENTRY_RECORD_SIZE
    if not (region_offset <= table and table_end <= region_offset + region_size):
        raise PackageError(
            f"{newest.path.name}: entry table 0x{table:X}..0x{table_end:X} is not inside hashed "
            f"region 0x{region_offset:X}..0x{region_offset + region_size:X}")

    raw = bytearray(newest.raw)
    for item in reverts:
        at = table + item.entry * ENTRY_RECORD_SIZE + BLOCK_INFO_AT
        raw[at:at + 8] = item.blob
    struct.pack_into("<H", raw, OFF_PATCH_ID, new_id)

    if trim:
        owned = [b for b in newest.blocks if b.patch_id == new_id]
        if owned:
            raise PackageError(
                f"{out.name} would own {len(owned)} blocks; refusing to trim a file whose bodies "
                "something resolves through")
        region_end = region_offset + region_size
        trailer = bytes(raw[-TRAILER_SIZE:])
        raw = bytearray(raw[:region_end]) + trailer
        struct.pack_into("<I", raw, OFF_FILE_SIZE, len(raw))
        struct.pack_into("<I", raw, OFF_TRAILER, len(raw) - TRAILER_SIZE)

    # Last, after every edit that lands inside the region. The patch id, file size and trailer
    # offset all sit ahead of the region and do not affect it; truncation happens after it.
    struct.pack_into(
        f"<{REGION_DIGEST_SIZE}s", raw, OFF_REGION_B + 8,
        hashlib.sha1(bytes(raw[region_offset:region_offset + region_size])).digest())
    out.write_bytes(bytes(raw))
    return out


def verify(out: Path, reverts: list[Revert]) -> None:
    """Re-opens the written file and raises unless every row now reads back as intended."""
    pkg = Package(out)
    for item in reverts:
        got = row_of(pkg, item.entry)
        if got != item.to_row:
            raise PackageError(
                f"{out.name} entry {item.entry} reads back {got}, expected {item.to_row}")
    pkg.check()
    # Every block a restored row names must still have its body installed, or the entry is a hole.
    live = {p.name for p in out.parent.glob("*.pkg")}
    for item in reverts:
        first, count = span_of(pkg, item.entry)
        for b in range(first, first + count):
            host = pkg.patch_path(pkg.blocks[b].patch_id).name
            if host not in live:
                raise PackageError(
                    f"{out.name} entry {item.entry} resolves through {host}, which is not installed")


def types_written_by(path: Path, patch_id: int) -> dict[int, int]:
    """@return Histogram of entry types whose bodies this layer supplied."""
    pkg = Package(path)
    mine = {i for i, block in enumerate(pkg.blocks) if block.patch_id == patch_id}
    if not mine:
        return {}
    out: dict[int, int] = {}
    for entry, record in enumerate(pkg.entries):
        if record.size == 0:
            continue
        first, count = span_of(pkg, entry)
        if any(first <= b < first + count for b in mine):
            kind = getattr(record, "entry_type", -1)
            out[kind] = out.get(kind, 0) + 1
    return out


def detect_sweeps(directory: Path, since: float) -> dict[str, set[int]]:
    """
    Finds every layer that is a flat-colour paint sweep, by what it writes.

    A sweep writes texture bodies and nothing else - `{40: N}`. Every mesh group writes type-8
    records alongside paired 40/32. That separated all 26 write-groups of 2026-08-20 exactly, and
    deriving it beats a hardcoded table that goes stale the moment another layer is written.

    @param since Only consider layers modified after this timestamp - ours, not shipped.
    """
    found: dict[str, set[int]] = {}
    for path in sorted(directory.glob("*.pkg")):
        tail = path.stem.rsplit("_", 1)
        if len(tail) != 2 or not tail[1].isdigit():
            continue
        if path.stat().st_mtime <= since:
            continue
        stem, patch_id = tail[0], int(tail[1])
        try:
            kinds = types_written_by(path, patch_id)
        except PackageError:
            continue
        if kinds and set(kinds) == {40}:
            found.setdefault(stem, set()).add(patch_id)
    return found


def main() -> None:
    args = sys.argv[1:]
    write_it = "--write" in args
    directory = PACKAGES
    types: set[int] | None = {40}
    for arg in args:
        if arg.startswith("--packages="):
            directory = Path(arg.split("=", 1)[1])
        if arg.startswith("--types="):
            value = arg.split("=", 1)[1]
            types = None if value == "any" else {int(x) for x in value.split(",")}

    jobs: dict[str, set[int]] = {}
    if "--all-paint-sweeps" in args:
        # 2026-08-18 predates our first written layer and postdates the install, so it separates
        # ours from shipped without relying on "has plain blocks" - video and audio packages
        # legitimately contain plain blocks.
        import datetime
        since = datetime.datetime(2026, 8, 18).timestamp()
        jobs = detect_sweeps(directory, since)
        if not jobs:
            raise SystemExit(f"no paint sweeps detected in {directory}")
        print(f"detected {sum(len(v) for v in jobs.values())} sweep layer(s) "
              f"across {len(jobs)} package(s)\n")
    else:
        stem = next((a.split("=", 1)[1] for a in args if a.startswith("--package=")), None)
        ids = next((a.split("=", 1)[1] for a in args if a.startswith("--painted-by=")), None)
        if not stem or not ids:
            raise SystemExit(__doc__)
        jobs[stem] = {int(x) for x in ids.split(",")}

    total, problems = 0, 0
    for stem, bad in sorted(jobs.items()):
        try:
            newest, reverts, complaints = plan(stem, bad, directory, types)
        except PackageError as error:
            print(f"{stem}: {error}")
            problems += 1
            continue
        print(f"{stem}: newest is {newest.path.name}; {len(reverts)} rows to restore "
              f"from patch id(s) {sorted(bad)}")
        for item in reverts[:4]:
            print(f"    entry {item.entry:>5}  {item.from_row} -> {item.to_row}  (from _{item.to_layer})")
        if len(reverts) > 4:
            print(f"    ... {len(reverts) - 4} more")
        for line in complaints:
            print(f"    !! {line}")
        problems += len(complaints)
        total += len(reverts)
        if write_it and reverts and not complaints:
            out = write(newest, reverts, trim="--no-trim" not in args)
            verify(out, reverts)
            print(f"    wrote and verified {out.name} ({out.stat().st_size:,} B)")
        elif write_it and complaints:
            print("    refusing to write while any row is unresolved")

    print()
    print(f"{total} rows across {len(jobs)} package(s); {problems} problem(s)")
    if not write_it:
        print("Nothing written. Re-run with --write.")


if __name__ == "__main__":
    main()
