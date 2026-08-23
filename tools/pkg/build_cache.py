"""Read `build_data.bin` by deriving its layout from the source, not by hardcoding it.

`lookup_item.py` hardcoded record sizes and a fixed payload offset, and upstream's format bump to
version 44 silently broke it: every lookup answered "not in this install", including for the
weapon and helmet the character visibly has equipped. A reader that guesses a layout fails
*quietly*, which is the worst way to fail, so this one derives the layout from
`src/state/build_data/cache/records/format.h` and then **proves** it: header + the sum of every
domain's rows must equal the file size exactly. If the format moves again, this raises instead of
lying.

Struct sizes come from parsing the packed record definitions and resolving their `kConstant`
array bounds out of the headers that declare them.

Usage:
    python build_cache.py                      # layout, validated, plus domain row counts
    python build_cache.py 0x7979AE7D           # what is this definition hash?
    python build_cache.py --slot emote         # every item in one equipment slot
    python build_cache.py --buckets            # bucket id histogram
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent / "Sunrise" / "src" / "state" / "build_data"
FORMAT = SRC / "cache" / "records" / "format.h"
CACHE = Path(r"C:\Sunrise\bin\x64\Sunrise\cache\build_data.bin")

SCALARS = {
    "std::uint8_t": ("B", 1), "std::int8_t": ("b", 1),
    "std::uint16_t": ("H", 2), "std::int16_t": ("h", 2),
    "std::uint32_t": ("I", 4), "std::int32_t": ("i", 4),
    "std::uint64_t": ("Q", 8), "std::int64_t": ("q", 8),
    "char": ("c", 1), "bool": ("?", 1), "float": ("f", 4),
}
# Written by cache_payload_writer.cpp, in this order, one block per header count.
DOMAINS = [
    ("named", "NamedRecord"), ("items", "ItemRecord"),
    ("collectibles", "CollectibleRecord"),
    ("materialRequirementSets", "MaterialRequirementSetRecord"),
    ("itemDetails", "ItemDetailRecord"),
    ("socketPlugRules", "SocketPlugRuleRecord"),
    ("socketPlugPools", "SocketPlugPoolRecord"),
    ("socketPlugMembers", "SocketPlugMemberRecord"),
    ("inventoryBuckets", "InventoryBucketRecord"),
    ("socketEntryLists", "SocketEntryListRecord"),
    ("socketEntryTables", "SocketEntryTableRecord"),
    ("abilityBuckets", "AbilityBucketRecord"),
    ("progressions", "ProgressionRecord"),
    ("scenarios", "ScenarioRecord"),
    ("rosterGroups", "RosterGroupRecord"),
    ("spawnStems", "SpawnStemRecord"),
    ("spawnNameHashes", "SpawnNameHashRecord"),
    ("spawnPoints", "SpawnPointRecord"),
    ("hashNames", "HashNameRecord"),
    ("vendorIndex", "VendorIndexRecord"),
    ("vendorDefinitions", "VendorDefinitionRecord"),
    ("vendorSaleRows", "VendorSaleRowRecord"),
    ("vendorInstalledRows", "VendorInstalledRowRecord"),
]


def constants() -> dict[str, list[tuple[str, str]]]:
    """@return Bare constant name -> [(declaring path, value expression)].

    Expressions, not values, because a bound is often derived: `scenarios::kBubbleMaskBytes` is
    `kBubbleCapacity / 8`. And a list, not one entry, because the same bare name exists in several
    namespaces with **different** values - `kBubbleMaskBytes` is 8 under scenarios and 32 under
    spawn_sets. Taking the wrong one cost 96 bytes per scenario row and was invisible until the
    file-size check failed.
    """
    found: dict[str, list[tuple[str, str]]] = {}
    pattern = re.compile(
        r"inline\s+constexpr\s+[\w:]+(?:\s*<[^>]*>)?\s+(k\w+)\s*(?:\{|=)\s*([^;{}]+?)\s*\}?\s*;")
    for path in list(SRC.rglob("*.h")) + list((SRC.parent / "content").rglob("*.h")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, expression in pattern.findall(text):
            found.setdefault(name, []).append((str(path), expression))
    return found


def resolve(bound: str, consts, arrays=None, seen=()) -> int | None:
    """@return The value of a possibly namespace-qualified, possibly derived constant.

    A qualified name that has no declaration under its own namespace **raises**. Falling back to a
    same-named constant from elsewhere is what produced a wrong row size here once already.
    """
    parts = bound.split("::")
    bare = parts[-1]
    if bare in seen:
        raise SystemExit(f"constant {bare} refers to itself")
    candidates = consts.get(bare, [])
    if not candidates:
        return None
    if len(parts) > 1:
        namespace = parts[-2]
        narrowed = [(path, expr) for path, expr in candidates
                    if f"\\{namespace}\\" in path or path.endswith(f"\\{namespace}.h")]
        if not narrowed:
            raise SystemExit(
                f"{bound} has no declaration under a '{namespace}' path, but {bare} exists "
                f"elsewhere. Refusing to guess - a wrong bound silently corrupts every row.")
        candidates = narrowed
    values = {evaluate(expr, consts, arrays or {}, seen + (bare,)) for _path, expr in candidates}
    if len(values) > 1:
        raise SystemExit(f"{bound} is declared with conflicting values {sorted(values)}; "
                         "qualify it or the row size is a guess")
    return values.pop()


def evaluate(expression: str, consts, arrays, seen=()) -> int:
    """@return An integer constant expression: literals, other constants, `.size()`, arithmetic."""
    text = expression.replace("'", "").strip()
    for token in sorted(set(re.findall(r"[A-Za-z_][\w:]*(?:\.size\(\))?", text)), key=len,
                        reverse=True):
        if token.endswith(".size()"):
            name = token[: -len(".size()")].split("::")[-1]
            if name not in arrays:
                raise SystemExit(f"no constexpr array {name} to take .size() of")
            value = arrays[name]
        else:
            value = resolve(token, consts, arrays, seen)
            if value is None:
                raise SystemExit(f"unknown constant {token} in '{expression}'")
        text = text.replace(token, str(value))
    if not re.fullmatch(r"[0-9+\-*/()\s]+", text):
        raise SystemExit(f"cannot evaluate constant expression '{expression}' (became '{text}')")
    return int(eval(text))  # noqa: S307 - input is digits and operators only, checked above


def constexpr_arrays() -> dict[str, int]:
    """@return `kName` -> element count, for constexpr std::array constants like kCacheMagic."""
    found: dict[str, int] = {}
    pattern = re.compile(
        r"inline\s+constexpr\s+std::array\s*<\s*[\w:]+\s*,\s*(\d+)\s*>\s*(k\w+)")
    for path in list(SRC.rglob("*.h")) + list((SRC.parent / "content").rglob("*.h")):
        for count, name in pattern.findall(path.read_text(encoding="utf-8", errors="replace")):
            found.setdefault(name, int(count))
    return found


def structs() -> dict[str, str]:
    """@return Struct name -> body text, for every struct declared in format.h."""
    text = FORMAT.read_text(encoding="utf-8", errors="replace")
    return {name: body for name, body in
            re.findall(r"struct\s+(\w+)\s*(?:final\s*)?\{(.*?)\n\};", text, re.S)}


def split_template(text: str) -> list[str]:
    """@return Top-level comma-separated arguments of `a<b<c,d>,e>`, brackets respected."""
    parts, depth, current = [], 0, ""
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
            continue
        current += char
    parts.append(current.strip())
    return parts


def bound_value(expr: str, consts, arrays: dict[str, int]) -> int:
    """@return An array bound: a literal, a constant, `kThing.size()`, or a product of those."""
    expr = expr.strip()
    if "*" in expr:
        product = 1
        for term in expr.split("*"):
            product *= bound_value(term, consts, arrays)
        return product
    if expr.endswith(".size()"):
        name = expr[: -len(".size()")].split("::")[-1]
        if name not in arrays:
            raise SystemExit(f"no constexpr array {name} to take .size() of")
        return arrays[name]
    if expr.isdigit():
        return int(expr)
    value = resolve(expr, consts)
    if value is None:
        raise SystemExit(f"unknown array bound {expr}")
    return value


def size_of_type(kind: str, bodies, consts, arrays, seen=()) -> int:
    kind = kind.strip()
    if kind in SCALARS:
        return SCALARS[kind][1]
    if kind.startswith("std::array"):
        inner = kind[kind.index("<") + 1: kind.rindex(">")]
        element, bound = split_template(inner)
        return size_of_type(element, bodies, consts, arrays, seen) * bound_value(
            bound, consts, arrays)
    bare = kind.split("::")[-1]
    if bare in bodies:
        return size_of(bare, bodies, consts, arrays, seen)
    raise SystemExit(f"unknown type {kind}")


def size_of(name: str, bodies: dict[str, str], consts, arrays, seen=()) -> int:
    """@return Packed byte size of one record struct.

    Fields are split on `;`, not on newlines: a default member initializer routinely wraps across
    lines, and a line-based parser silently drops those fields. Every field must parse - an
    unrecognised one raises rather than contributing zero, because a silently short row is
    exactly the failure this whole reader exists to avoid.
    """
    if name in seen:
        raise SystemExit(f"recursive struct {name}")
    body = re.sub(r"//[^\n]*|/\*.*?\*/", "", bodies[name], flags=re.S)
    total = 0
    for field in body.split(";"):
        field = " ".join(field.split())
        if not field:
            continue
        field = re.sub(r"\s*(?:\{.*\}|=.*)$", "", field)      # drop the default initializer
        match = re.match(r"(.+?)\s+(\w+)$", field)
        if not match:
            raise SystemExit(f"cannot parse field '{field}' of {name}")
        total += size_of_type(match.group(1), bodies, consts, arrays, seen + (name,))
    return total


def layout():
    consts = constants()
    bodies = structs()
    arrays = constexpr_arrays()
    header_size = size_of("Header", bodies, consts, arrays)
    data = CACHE.read_bytes()
    if data[:8] != b"SUNRISEB":
        raise SystemExit(f"{CACHE} is not a Sunrise build-data cache")
    version = struct.unpack_from("<I", data, 8)[0]
    counts = struct.unpack_from("<23I", data, 28)

    blocks = {}
    offset = header_size
    for (key, record), count in zip(DOMAINS, counts):
        size = size_of(record, bodies, consts, arrays)
        blocks[key] = (offset, count, size, record)
        offset += count * size

    if offset != len(data):
        raise SystemExit(
            f"layout does not add up: computed {offset:,} bytes, file is {len(data):,}. "
            f"format.h says version {resolve('kCacheFormatVersion', consts, arrays)}, file says {version}. "
            "The reader is wrong, or the format moved - fix it rather than trusting a lookup.")
    return data, version, header_size, blocks, consts, arrays


def field_map(name: str, bodies, consts, arrays) -> dict[str, tuple[int, str, int, int]]:
    """@return Field name -> (offset, element type, element size, element count).

    Same walk as `size_of`, keeping the running offset. Deriving offsets keeps every decode in
    step with the header instead of pinning magic numbers that rot at the next format bump.
    """
    body = re.sub(r"//[^\n]*|/\*.*?\*/", "", bodies[name], flags=re.S)
    offset = 0
    out: dict[str, tuple[int, str, int, int]] = {}
    for field in body.split(";"):
        field = " ".join(field.split())
        if not field:
            continue
        field = re.sub(r"\s*(?:\{.*\}|=.*)$", "", field)
        match = re.match(r"(.+?)\s+(\w+)$", field)
        if not match:
            raise SystemExit(f"cannot parse field '{field}' of {name}")
        kind, member = match.group(1), match.group(2)
        count = 1
        element = kind
        if kind.startswith("std::array"):
            inner = kind[kind.index("<") + 1: kind.rindex(">")]
            element, bound = split_template(inner)
            count = bound_value(bound, consts, arrays)
        element_size = size_of_type(element, bodies, consts, arrays)
        out[member] = (offset, element, element_size, count)
        offset += element_size * count
    return out


def read_field(data, base, spec):
    offset, element, size, count = spec
    code = SCALARS.get(element, ("B", 1))[0]
    values = struct.unpack_from(f"<{count}{code}", data, base + offset)
    return values[0] if count == 1 else values


def rows(data, blocks, key, fields):
    offset, count, size, _ = blocks[key]
    for index in range(count):
        yield struct.unpack_from(fields, data, offset + index * size)


def main() -> None:
    data, version, header_size, blocks, consts, arrays = layout()
    print(f"{CACHE.name}: format {version} (source says {resolve('kCacheFormatVersion', consts, arrays)}), "
          f"header {header_size} B, {len(data):,} B total - layout validated")

    if "--buckets" in sys.argv or len(sys.argv) == 1:
        print(f"\n{'domain':<26} {'rows':>8} {'row bytes':>10}")
        for key, (offset, count, size, record) in blocks.items():
            print(f"{key:<26} {count:>8,} {size:>10}")

    bodies = structs()
    detail = field_map("ItemDetailRecord", bodies, consts, arrays)
    detail_base, detail_count, detail_size, _ = blocks["itemDetails"]

    def detail_row(index: int):
        return detail_base + index * detail_size

    if "--bucket" in sys.argv:
        bucket = int(sys.argv[sys.argv.index("--bucket") + 1], 0)
        print(f"\nitems in bucket {bucket}:")
        shown = 0
        for index in range(detail_count):
            base = detail_row(index)
            if read_field(data, base, detail["bucketId"]) != bucket:
                continue
            shown += 1
            print(f"  0x{read_field(data, base, detail['definitionHash']):08X}  "
                  f"slot {read_field(data, base, detail['equipmentSlot']):>3}  "
                  f"sockets {read_field(data, base, detail['ordinarySocketCount']):>2}  "
                  f"socketList {read_field(data, base, detail['socketEntryListIndex']):>5}  "
                  f"art {read_field(data, base, detail['gearArtIndex']):>5}")
        print(f"  {shown} item(s)")

    for argument in sys.argv[1:]:
        if not argument.lower().startswith("0x"):
            continue
        target = int(argument, 16)
        for index in range(detail_count):
            base = detail_row(index)
            if read_field(data, base, detail["definitionHash"]) != target:
                continue
            print(f"\n0x{target:08X} detail:")
            for key in ("bucketId", "equipmentSlot", "instancedDefinition", "ordinarySocketState",
                        "ordinarySocketCount", "socketEntryListIndex", "gearArtIndex"):
                print(f"  {key:<22} {read_field(data, base, detail[key])}")
            plugs = read_field(data, base, detail["initialPlugIndices"])
            print(f"  initialPlugIndices     {[p for p in plugs if p != 0xFFFF][:12]}")
            break

    wanted = [int(a, 16) for a in sys.argv[1:] if a.lower().startswith("0x")]
    if wanted:
        # ItemRecord: definitionHash u32, definitionIndex u16, bucketId u8, tier u8, ...
        found = {}
        for row in rows(data, blocks, "items", "<IHBB"):
            if row[0] in wanted:
                found[row[0]] = row
        print()
        for hash_value in wanted:
            row = found.get(hash_value)
            if row is None:
                print(f"0x{hash_value:08X}  not among the {blocks['items'][1]:,} installed items")
            else:
                print(f"0x{hash_value:08X}  index {row[1]}  bucket {row[2]}  tier {row[3]}")


if __name__ == "__main__":
    main()
