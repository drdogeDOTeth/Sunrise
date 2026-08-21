"""Read the tags the game resolved for each model, straight out of the log. No launch, no guess.

`model_class_trace` logs an `r9tags=` list on every `SEntityModel` lookup - **every tag the game
resolved while building that model**, from process start, with no F8 window needed. That list is
the model's real dependency set, and it is already in `sunrise.log` from the last launch.

This finds the lookups that name the Scatterhorn chest models or the two carrier materials our
mesh draws through, then classifies every other tag in those lookups. Any `entry_type` 40 texture
in there is a texture the game bound for our body - which is the question four painting sweeps
were trying to answer by elimination.

Usage:
    python trace_model_tags.py                     # the live log
    python trace_model_tags.py --all               # every lookup, not just ours
    python trace_model_tags.py path\\to\\sunrise.log
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

from bone_probe import INDEX, entry_of
from paint_textures import TEXTURE_BODY_TYPE, TEXTURE_HEADER_TYPE, already_painted, ours
from tigerpkg import Package, TAG_BASE, TAG_ENTRY_BITS

LOG = Path(r"C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log")
TRACED = Path(__file__).with_name("traced_textures.json")
LOOKUP_RE = re.compile(r"ev=model_class_trace stage=lookup seq=(?P<seq>\d+) "
                       r"class=0x(?P<klass>[0-9A-Fa-f]+).*?r9tags=(?P<tags>[0-9A-Fa-fx,]+)")
# The trace logs two stages and three tag fields, and matching only one shape reads a third of it.
# `stage=lookup` carries `r9tags`; `stage=resource` carries a `tag=` naming the resource being
# loaded plus a `paytags=` list of its payload. Scanning every hex tag on every line of the event
# cannot miss a field the way a single anchored pattern did.
TRACE_LINE = "ev=model_class_trace"
TAG_LISTS_RE = re.compile(r"(?:r9tags|paytags)=([0-9A-Fa-fx,]+)")
SINGLE_TAG_RE = re.compile(r"\btag=0x([0-9A-Fa-f]+)")

# The chest models our mesh is injected into, and the materials its two carrier parts draw with.
OURS = {
    0x80EFA1CA: "chest model A", 0x80EFA1A9: "chest model B",
    0x80EF98DB: "carrier part 10 material", 0x80EF8C3C: "carrier part 32 material",
}
CLASS_NAMES = {
    0x808073A5: "SEntityModel", 0x80809C36: "entity resource", 0x808071E8: "material",
    0x80809C0F: "SEntity", 0x8080744A: "entity parent", 0x80807140: "technique",
    0x80808F49: "skeleton/anim", 0x808071F3: "dye body", 0x808071CD: "dye stub",
}

_opened: dict[Path, Package] = {}


def package_for(tag: int) -> Package | None:
    path = INDEX.get((tag - TAG_BASE) >> TAG_ENTRY_BITS)
    if path is None:
        return None
    if path not in _opened:
        _opened[path] = Package(path)
    return _opened[path]


def is_texture(tag: int, body) -> bool:
    """@return True when `tag` is a texture body paired with a 40-byte type-32 header."""
    if body.entry_type != TEXTURE_BODY_TYPE or body.size < 16:
        return False
    header = entry_of(body.reference)
    if header is None or header.size != 40 or header.entry_type != TEXTURE_HEADER_TYPE:
        return False
    return header.reference == tag


def describe(tag: int) -> tuple[str, str]:
    """@return `(kind, detail)` for one resolved tag."""
    if tag in CLASS_NAMES:
        return "class id", CLASS_NAMES[tag]
    entry = entry_of(tag)
    if entry is None:
        return "unknown", "not an entry in any installed package"
    path = INDEX[(tag - TAG_BASE) >> TAG_ENTRY_BITS]
    if is_texture(tag, entry):
        package = package_for(tag)
        painted = package is not None and ours(package, entry) and already_painted(package, entry)
        return "TEXTURE", (f"{entry.size:>12,} B  {path.stem}  "
                           f"{'painted' if painted else '** NEVER PAINTED **'}")
    name = CLASS_NAMES.get(entry.reference, f"class 0x{entry.reference:08X}")
    return f"type {entry.entry_type}", f"{entry.size:>12,} B  {path.stem}  {name}"


def main() -> None:
    arguments = [value for value in sys.argv[1:] if not value.startswith("--")]
    log = Path(arguments[0]) if arguments else LOG
    if not log.is_file():
        raise SystemExit(f"no log at {log}")

    lookups = []
    for number, line in enumerate(log.read_text(encoding="utf-8", errors="replace").splitlines()):
        if TRACE_LINE not in line:
            continue
        tags = []
        for group in TAG_LISTS_RE.findall(line):
            tags += [int(value, 16) for value in group.split(",") if value.strip()]
        tags += [int(value, 16) for value in SINGLE_TAG_RE.findall(line)]
        if not tags:
            continue
        anchored = LOOKUP_RE.search(line)
        klass = int(anchored["klass"], 16) if anchored else 0
        lookups.append((number, klass, tags))
    if not lookups:
        raise SystemExit(f"no model_class_trace events in {log.name}")
    print(f"{len(lookups):,} model_class_trace events with tags in {log.name}, "
          f"{sum(len(tags) for _n, _k, tags in lookups):,} tag references\n")

    wanted = lookups if "--all" in sys.argv else [
        row for row in lookups if any(tag in OURS for tag in row[2])]
    if not wanted:
        print("No lookup names our chest models or carrier materials.\n"
              "Falling back to every texture the game resolved during any model build - still a\n"
              "measured list, not a guess.\n")
        seen: dict[int, int] = collections.Counter()
        for _seq, _klass, tags in lookups:
            for tag in tags:
                if describe(tag)[0] == "TEXTURE":
                    seen[tag] += 1
        by_package: dict[str, list[tuple[int, int, bool]]] = collections.defaultdict(list)
        for tag in seen:
            entry = entry_of(tag)
            path = INDEX[(tag - TAG_BASE) >> TAG_ENTRY_BITS]
            package = package_for(tag)
            painted = package is not None and ours(package, entry) and already_painted(package, entry)
            by_package[path.stem].append((tag, entry.size, painted))
        print(f"{len(seen)} distinct textures resolved during model builds:\n")
        unpainted = []
        for stem in sorted(by_package):
            rows = sorted(by_package[stem])
            fresh = [row for row in rows if not row[2]]
            unpainted += [(tag, size, stem) for tag, size, _p in fresh]
            print(f"  {stem:<28} {len(rows):>3} textures, {len(fresh):>3} never painted")
        print(f"\n{len(unpainted)} textures were resolved by the game and have NEVER been painted.")
        for tag, size, stem in sorted(unpainted)[:40]:
            print(f"    0x{tag:08X}  {size:>12,} B  {stem}")
        if len(unpainted) > 40:
            print(f"    ... {len(unpainted) - 40} more")

        TRACED.write_text(json.dumps(
            [{"tag": f"0x{tag:08X}", "size": size, "package": stem}
             for tag, size, stem in sorted(unpainted)], indent=2), encoding="utf-8")
        print(f"\nwrote {TRACED}")
        print("Paint exactly these:  python paint_textures.py --traced")
        return

    print(f"{len(wanted)} lookup(s) name something of ours:\n")
    textures: dict[int, int] = {}
    for seq, klass, tags in wanted:
        mine = [f"{OURS[tag]} 0x{tag:08X}" for tag in tags if tag in OURS]
        print(f"=== seq {seq}  class 0x{klass:08X}  ({', '.join(mine)}) ===")
        for tag in tags:
            kind, detail = describe(tag)
            marker = "  <-- OURS" if tag in OURS else ""
            print(f"    0x{tag:08X}  {kind:<9} {detail}{marker}")
            if kind == "TEXTURE":
                textures[tag] = textures.get(tag, 0) + 1
        print()

    if not textures:
        print("No textures in those lookups: the model's dependency set does not name one.")
        return
    print(f"{len(textures)} distinct textures bound alongside our models:")
    for tag in sorted(textures):
        print(f"    0x{tag:08X}  {describe(tag)[1]}")


if __name__ == "__main__":
    main()
