r"""Check authored `settings.json` against the installed build before a launch, not after.

Nine emote items were granted into an emote bucket that had six rows free. `place_item` had nowhere
to put the last three, `resolve()` returned false, the snapshot never built, and the client said
**"could not connect to the Destiny 2 servers"** - a message that names the network and blames
nothing that was actually wrong. Every input needed to catch that sits in `build_data.bin` and the
settings file, offline, in under a second.

Checks, each one a rule the loadout resolver enforces at runtime by failing the whole snapshot:

- every `definition_hash` is installed
- an authored `plugs` array is exactly as long as the item's `ordinarySocketCount`
- per character, items per inventory bucket fit that bucket's `slotCount`
- `instance_soid` is unique across the whole file
- an item in an equipment slot has a non-negative `equipmentSlot` of its own

    python check_authored_state.py                     # the live settings
    python check_authored_state.py path\to\settings.json
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import build_cache

LIVE = Path(r"C:\Sunrise\bin\x64\Sunrise\settings.json")


def installed():
    """@return definition hash -> (bucket, sockets, equipment slot), and bucket -> slot count."""
    data, _version, _header, blocks, consts, arrays = build_cache.layout()
    bodies = build_cache.structs()
    detail = build_cache.field_map("ItemDetailRecord", bodies, consts, arrays)
    base, count, size, _record = blocks["itemDetails"]
    items = {}
    for index in range(count):
        row = base + index * size
        items[build_cache.read_field(data, row, detail["definitionHash"])] = (
            build_cache.read_field(data, row, detail["bucketId"]),
            build_cache.read_field(data, row, detail["ordinarySocketCount"]),
            build_cache.read_field(data, row, detail["equipmentSlot"]))
    capacity = {row[0]: row[3] for row in build_cache.rows(data, blocks, "inventoryBuckets",
                                                           "<BBHHbB")}
    return items, capacity


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else LIVE
    authored = json.loads(io.open(path, encoding="utf-8-sig").read())
    items, capacity = installed()
    problems: list[str] = []
    soids: dict[str, str] = {}

    for index, character in enumerate(authored["state"]["characters"]):
        rows = [(f"equipment.{name}", row) for name, row in character["equipment"].items()]
        rows += [("inventory", row) for row in character["inventory"]]
        used: dict[int, int] = {}
        for label, row in rows:
            where = f"character {index} {label} {row['definition_hash']}"
            soid = row["instance_soid"]
            if soid in soids:
                problems.append(f"{where}: instance_soid {soid} also used by {soids[soid]}")
            soids[soid] = where
            definition = int(row["definition_hash"], 16)
            if definition not in items:
                problems.append(f"{where}: not installed in this build")
                continue
            bucket, sockets, slot = items[definition]
            used[bucket] = used.get(bucket, 0) + 1
            plugs = row.get("plugs")
            # A null plugs field means native defaults, which imposes no length.
            if isinstance(plugs, list) and len(plugs) != sockets:
                problems.append(f"{where}: {len(plugs)} plug(s) but the item has {sockets} socket(s)")
            if label.startswith("equipment.") and slot < 0:
                problems.append(f"{where}: cannot be equipped, it has no equipment slot")
        for bucket, count in sorted(used.items()):
            limit = capacity.get(bucket)
            if limit is not None and count > limit:
                problems.append(f"character {index}: bucket {bucket} holds {count} item(s), "
                                f"capacity {limit}")

    print(f"{path.name}: {len(soids)} authored item(s) across "
          f"{len(authored['state']['characters'])} character(s)")
    for problem in problems:
        print(f"  FAIL  {problem}")
    if problems:
        raise SystemExit(f"{len(problems)} problem(s); the snapshot would fail to build")
    print("  every item installed, every bucket fits, every plug count matches")


if __name__ == "__main__":
    main()
