"""
Looks up item definition hashes in Sunrise's extracted build-data cache.

The item → model chain starts here: each definition carries an art-arrangement index. Charm will
not walk that index to an entity on Shadowkeep, but the index itself is already in
`build_data.bin` — no extra launch required.

Usage:
    python lookup_item.py 0xEA042965 0x231DBD19
    python lookup_item.py --helmets
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

CACHE = Path(r"C:\Sunrise\bin\x64\Sunrise\cache\build_data.bin")
NAMED, ITEM, COLL, MATSET, DETAIL = 140, 12, 76, 68, 294
UNSET = 0xFFFF


def load_details(path: Path = CACHE) -> list[dict]:
    data = path.read_bytes()
    if data[:8] != b"SUNRISEB":
        raise SystemExit(f"{path} is not a Sunrise build-data cache")
    counts = struct.unpack_from("<" + "I" * 22, data, 28)
    offset = 132 + counts[0] * NAMED + counts[1] * ITEM + counts[2] * COLL + counts[3] * MATSET
    rows = []
    for index in range(counts[4]):
        record = data[offset + index * DETAIL : offset + (index + 1) * DETAIL]
        rows.append({
            "idx": struct.unpack_from("<H", record, 0)[0],
            "bucket": record[2],
            "slot": struct.unpack_from("<b", record, 3)[0],
            "sockets": record[6],
            "plugs": struct.unpack_from("<12H", record, 13),
            "hash": struct.unpack_from("<I", record, 142)[0],
            "gear": struct.unpack_from("<H", record, 146)[0],
            "arr": struct.unpack_from("<4H", record, 148),
        })
    return rows


def fmt_arr(values: tuple[int, ...]) -> str:
    return ",".join("-" if value == UNSET else str(value) for value in values)


def show(row: dict) -> None:
    print(
        f"0x{row['hash']:08X}  idx={row['idx']:<5}  bucket={row['bucket']:<3}  "
        f"slot={row['slot']:<3}  gear={'-' if row['gear'] == UNSET else row['gear']:<5}  "
        f"arr={fmt_arr(row['arr'])}"
    )


def main() -> None:
    rows = load_details()
    by_hash = {row["hash"]: row for row in rows}
    if "--helmets" in sys.argv:
        helmets = [row for row in rows if row["bucket"] == 3 and row["slot"] == 1]
        print(f"{len(helmets)} helmet items (bucket 3, slot 1)\n")
        for row in helmets:
            show(row)
        return
    tags = [int(argument, 0) for argument in sys.argv[1:] if argument.startswith("0x")]
    if not tags:
        raise SystemExit(__doc__)
    for tag in tags:
        row = by_hash.get(tag)
        if row is None:
            print(f"0x{tag:08X}  not in this install")
            continue
        show(row)
        for plug in row["plugs"][: row["sockets"]]:
            if plug == UNSET:
                continue
            owned = next((item for item in rows if item["idx"] == plug), None)
            if owned is None:
                print(f"  plug idx={plug}  missing")
            else:
                print(
                    f"  plug 0x{owned['hash']:08X}  idx={owned['idx']}  "
                    f"bucket={owned['bucket']}  arr={fmt_arr(owned['arr'])}"
                )


if __name__ == "__main__":
    main()
