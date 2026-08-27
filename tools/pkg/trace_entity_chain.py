"""
Walks an entity to its model offline, and writes the request for whatever the walk cannot reach.

## The problem this removes

Finding a named character's mesh is a chain of hops, and every hop that lands on an encrypted body
costs a whole game launch:

    SEntity  ->  entity resource  ->  SEntityModel  ->  buffer headers  ->  raw buffers

`resolve.py` already turns any tag into its class, size and package from the plain entry tables, and
`scan_entity_tags.py` reads the tags out of one dumped blob. What was missing is the walk itself -
doing those two things in a loop, remembering which blobs are already dumped, and stopping to say
exactly what the next launch must ask for. Done by hand for Zavala it took two launches and one of
them asked for the wrong package, because the resource and the model it names live in *different*
packages and only the second one matters for a patch.

## What the walk knows

**A model is recognised by class, not by guesswork.** `resolve()` reports `0x808073A5`
(`SEntityModel`) straight from the entry table, so the terminal condition is checkable offline. That
is how Zavala's body model was found: nothing in the entity blob had that class, but a resource the
entity named had a reference that did.

**Model-naming classes corroborate rather than decide.** Several classes name the same resource -
`80807314`, `808072B8`, `808072C0` on a thrall; those three plus `80807273` and `808072CB` on
Zavala. Agreement between them ranks candidates, but the class check above is what confirms one.
Ranking is reported so a wrong turn is visible rather than silent.

## Sizes are free; only the stride costs a launch

A buffer header's own `reference` field in the entry table is the tag of the raw buffer, so **the
buffer and its byte size resolve offline**. What needs the header *body* is the stride, and stride
is what turns bytes into a vertex count - the number that decides whether a swap needs a decimate,
since the injector may never exceed the target's vertex count.

**Headers do not live in the model's package.** Zavala's model is in `globals_0238`; its headers are
in `globals_01fe`, `globals_01cf` and `globals_03ab`. A pass that bulk-requested every header in the
model's own package therefore dumped 746 tags and not one of the twelve that were wanted. So the
walk collects header tags from the parsed model and requests exactly those.

`--headers <package>` still exists for the bulk case - it is the right move when the model is not
dumped yet and the headers are known to share its package - but it is not the default, because for
Zavala the default would have been wrong.

Usage:
    python trace_entity_chain.py zavala               # by name, from EntityNames.json
    python trace_entity_chain.py 0x80BC8F1E           # by tag
    python trace_entity_chain.py zavala --request     # emit the next request.txt block
    python trace_entity_chain.py zavala --request --headers globals_0238
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from resolve import DUMP, INDEX, TAG_BASE, TAG_MAX, references_of, resolve
from tigerpkg import TAG_ENTRY_BITS, Package, PackageError

NAMES = Path(r"C:\Sunrise\bin\x64\Sunrise\EntityNames.json")

SENTITY = 0x80809C0F
ENTITY_RESOURCE = 0x80809C36
SENTITY_MODEL = 0x808073A5

#: Classes observed naming the resource that leads to a model. Agreement between them ranks a
#: candidate; it never confirms one - the class check on the referenced tag does that.
MODEL_NAMING = {0x80807273, 0x808072B8, 0x808072C0, 0x808072CB, 0x80807314}
#: Class ids and tag handles share a numeric range and are told apart by where they fall in it.
#: Every Charm class id seen in this install is below 0x80810000 (`808073A5`, `80809C0F`) and every
#: tag is above it (`80BC8F1E`, `815B88A8`). Do not reuse `resolve.INLINE_MAX` (0x80801000) here:
#: that threshold separates inline values from tags, and classes such as `0x808072B8` sit above it,
#: which silently emptied this table and made every entity look like it named no model at all.
CLASS_MAX = 0x80810000
#: package_dump.cpp bounds one run at this many requests.
REQUEST_LIMIT = 1024

VERTEX_HEADER_SIZE = 12
INDEX_HEADER_SIZE = 24


def entity_names() -> dict[str, list[str]]:
    """@return Tag hex -> names, or empty when the artifact is absent."""
    if not NAMES.is_file():
        return {}
    return json.loads(NAMES.read_text(encoding="utf-8", errors="replace")).get("entities", {})


def find_by_name(needle: str) -> list[tuple[int, str]]:
    """
    @param needle Substring matched case-insensitively against every known entity name.
    @return (tag, name) for each match, entities only.
    """
    out = []
    for hex_tag, names in entity_names().items():
        for name in names:
            if needle.lower() in name.lower():
                out.append((int(hex_tag, 16), name))
                break
    return sorted(out)


def dumped(tag: int) -> Path | None:
    """@return Path of this tag's dumped body, or None when the game has not written it."""
    path = DUMP / f"tag_{tag:08X}.bin"
    return path if path.is_file() else None


def class_tag_pairs(data: bytes) -> dict[int, set[int]]:
    """
    Reads the resource table's class/tag pairs out of a dumped entity body.

    Every class id in the table is immediately followed by the tag it introduces, which is what
    makes the table readable without knowing any record's length. Scanning is 4-byte aligned on
    purpose: an unaligned scan of the same blob reports hundreds of tags that are really halves of
    adjacent fields, which is how a first attempt at this produced 446 "references" for Zavala.

    @param data Decrypted entity body.
    @return Class id -> the tags it introduces.
    """
    pairs: dict[int, set[int]] = {}
    for at in range(0, len(data) - 7, 4):
        class_id, tag = struct.unpack_from("<II", data, at)
        if 0x80800000 <= class_id < CLASS_MAX <= tag <= TAG_MAX:
            pairs.setdefault(class_id, set()).add(tag)
    return pairs


def models_referenced(tag: int) -> list[int]:
    """@return Tags referenced by this dumped blob whose class is SEntityModel."""
    if not dumped(tag):
        return []
    found = []
    for ref in references_of(f"{tag:08X}"):
        got = resolve(ref)
        if got and got[0] == SENTITY_MODEL and ref not in found:
            found.append(ref)
    return found


def rank_candidates(pairs: dict[int, set[int]]) -> list[tuple[int, int]]:
    """
    @param pairs Class/tag table from an entity body.
    @return (tag, number of model-naming classes that agree), best first.
    """
    votes: dict[int, int] = {}
    for class_id, tags in pairs.items():
        if class_id in MODEL_NAMING:
            for tag in tags:
                votes[tag] = votes.get(tag, 0) + 1
    return sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))


def describe(tag: int) -> str:
    """@return One line naming a tag's class, size and package, plus whether it is dumped."""
    got = resolve(tag)
    if got is None:
        return f"0x{tag:08X}  unresolved"
    class_id, size, _family, name = got
    label = {SENTITY: "SEntity", ENTITY_RESOURCE: "resource",
             SENTITY_MODEL: "SEntityModel"}.get(class_id, f"class {class_id:08X}")
    mark = "dumped" if dumped(tag) else "NOT DUMPED"
    return f"0x{tag:08X}  {label:<13} {size:>9,} B  {name:<24} {mark}"


def walk(entity: int) -> tuple[list[int], list[int]]:
    """
    Follows one entity as far as the dumps allow.

    @param entity SEntity tag to start from.
    @return (model tags found, tags that must be dumped before the walk can continue).
    """
    print(f"entity   {describe(entity)}")
    body = dumped(entity)
    if not body:
        print("\n  The entity body itself is not dumped. Request it and launch once.")
        return [], [entity]

    # A model may be named by the entity directly; enemies reach it through a resource instead.
    direct = models_referenced(entity)
    if direct:
        print("\n  names an SEntityModel directly:")
        for tag in direct:
            print(f"    {describe(tag)}")
        return direct, [t for t in direct if not dumped(t)]

    pairs = class_tag_pairs(body.read_bytes())
    ranked = rank_candidates(pairs)
    if not ranked:
        print("\n  No model-naming class in this entity's table. Widen MODEL_NAMING, or the "
              "model is reached by a route this tool does not know.")
        return [], []

    print(f"\n  {len(pairs)} classes introduce tags; model candidates by agreement:")
    for tag, votes in ranked[:6]:
        print(f"    {votes} classes agree  {describe(tag)}")

    models: list[int] = []
    missing: list[int] = []
    print()
    for tag, _votes in ranked:
        if not dumped(tag):
            missing.append(tag)
            continue
        for model in models_referenced(tag):
            if model not in models:
                models.append(model)
                print(f"  resource 0x{tag:08X} -> MODEL {describe(model)}")
    if models:
        missing += [m for m in models if not dumped(m)]
    return models, missing


def parse_model(tag: int) -> list[int]:
    """
    Prints one dumped SEntityModel's mesh table and reports the buffer headers it names.

    Layout is `parse_models.py`'s, repeated here only to report buffer tags during a walk. A
    DynamicArray is a count then a self-relative offset, and the data sits at
    `offset_field_position + value + 0x10`; every offset is bounds-checked, because getting the
    +0x10 wrong lands inside the previous record rather than failing loudly.

    A header's own `reference` field in the entry table is the tag of the raw buffer, so the buffer
    and its size are reported here with no dump at all. Only the **stride** needs the header body,
    and stride is what turns a byte count into a vertex count.

    @param tag Dumped SEntityModel.
    @return Buffer-header tags this model names, in mesh order.
    """
    data = dumped(tag).read_bytes()
    count, rel = struct.unpack_from("<qq", data, 0x10)
    at = 0x10 + 8 + rel + 0x10
    if not (0 < count < 256 and 0 <= at and at + count * 0x88 <= len(data)):
        print(f"  model 0x{tag:08X}: mesh array not at 0x10 in this record "
              f"(count={count}, data would start 0x{at:X} of 0x{len(data):X})")
        return []
    scale = struct.unpack_from("<4f", data, 0x50)
    print(f"  model 0x{tag:08X}: {count} meshes, scale {scale[0]:.4f}")
    headers: list[int] = []
    for i in range(count):
        rec = at + i * 0x88
        positions, texcoords, weights, _unused, indices = struct.unpack_from("<IIIII", data, rec)
        parts, _ = struct.unpack_from("<qq", data, rec + 0x18)
        print(f"    mesh {i}: {parts} parts")
        for role, header in (("positions", positions), ("texcoords", texcoords),
                             ("weights", weights), ("indices", indices)):
            if not TAG_BASE <= header <= TAG_MAX:
                continue           # 0xFFFFFFFF where a mesh carries no such buffer
            if header not in headers:
                headers.append(header)
            print(f"      {role:<10} {describe_buffer(header)}")
    return headers


def describe_buffer(header: int) -> str:
    """
    @param header Vertex (12 B) or index (24 B) buffer header tag.
    @return Its raw buffer, size, and the vertex count when the stride is known.

    The stride lives in the header body. When that body is dumped the count is exact; when it is
    not, this says so rather than assuming a stride - Zavala's three body meshes do **not** share
    one (98,352 B divides by 16 exactly, 98,936 B does not).
    """
    got = resolve(header)
    if got is None:
        return f"0x{header:08X}  unresolved"
    raw_tag, header_size, _family, name = got
    raw = resolve(raw_tag)
    if raw is None:
        return f"hdr 0x{header:08X} {header_size} B in {name}  (raw buffer unresolved)"
    out = (f"hdr 0x{header:08X} {header_size:>2} B in {name:<24} "
           f"-> buffer 0x{raw_tag:08X} {raw[1]:>9,} B in {raw[3]}")
    body = dumped(header)
    if body and header_size == VERTEX_HEADER_SIZE:
        _size, stride, _type = struct.unpack_from("<IHH", body.read_bytes(), 0)
        if stride:
            exact = "" if raw[1] % stride == 0 else "  (NOT a whole number - stride suspect)"
            out += f"   stride {stride} => {raw[1] // stride:,} verts{exact}"
    elif not body:
        out += "   stride unknown, header not dumped"
    return out


def package_headers(stem: str) -> list[int]:
    """
    @param stem Package stem such as `globals_0238`, with or without the `w64_` prefix.
    @return Tags of every 12- or 24-byte entry in it, which is every buffer header it can hold.
    """
    stem = stem if stem.startswith("w64_") else f"w64_{stem}"
    for package_id, path in INDEX.items():
        if path.stem.rsplit("_", 1)[0] != stem:
            continue
        try:
            pkg = Package(path)
        except PackageError:
            return []
        return [TAG_BASE + (package_id << TAG_ENTRY_BITS) + e.index
                for e in pkg.entries
                if e.size in (VERTEX_HEADER_SIZE, INDEX_HEADER_SIZE)]
    return []


def emit_request(missing: list[int], headers: list[int]) -> None:
    """Prints a request.txt block, refusing to exceed what one launch can serve."""
    total = len(missing) + len(headers)
    print(f"\n# --- next request.txt block: {len(missing)} blocked tags"
          f"{f' + {len(headers)} buffer headers' if headers else ''} = {total} ---")
    if total > REQUEST_LIMIT:
        print(f"# REFUSING: {total} exceeds the {REQUEST_LIMIT} one launch serves. Narrow it, "
              f"or a partial dump will look like a complete one.")
        return
    for tag in missing:
        got = resolve(tag)
        note = f"  # {got[3]} {got[1]:,} B" if got else ""
        print(f"tag 0x{tag:08X}{note}")
    for tag in headers:
        got = resolve(tag)
        print(f"tag 0x{tag:08X}  # {got[1]} B header" if got else f"tag 0x{tag:08X}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2

    target = args[0]
    if target.lower().startswith("0x"):
        entities = [(int(target, 16), "")]
    else:
        entities = find_by_name(target)
        if not entities:
            print(f"no entity name contains {target!r}")
            return 1
        print(f"{len(entities)} entities match {target!r}:")
        for tag, name in entities:
            print(f"  0x{tag:08X}  {name}")
        print()

    all_models: list[int] = []
    all_missing: list[int] = []
    all_headers: list[int] = []
    for tag, name in entities:
        print(f"===== {name or f'0x{tag:08X}'} =====")
        models, missing = walk(tag)
        for model in models:
            if not dumped(model):
                continue
            for header in parse_model(model):
                # Headers do not live with their model - Zavala's model is in globals_0238 while
                # its headers are in globals_01fe, 01cf and 03ab - so they are collected from the
                # parse rather than assumed to share the model's package.
                if header not in all_headers:
                    all_headers.append(header)
                if not dumped(header) and header not in missing:
                    missing.append(header)
        all_models += [m for m in models if m not in all_models]
        all_missing += [m for m in missing if m not in all_missing]
        print()

    if all_models:
        print("models found:")
        for model in all_models:
            print(f"  {describe(model)}")
        # An injection rewrites the model record, the headers AND the raw buffers, and those sit in
        # different packages - Zavala's model is in globals_0238 while his buffers are in 01fe, 01cf
        # and 03ab. Reporting only the model's package would send someone to probe one package and
        # then write to four.
        packages: set[str] = set()
        for tag in all_models + all_headers:
            got = resolve(tag)
            if got:
                packages.add(got[3].rsplit("_", 1)[0])
                raw = resolve(got[0])
                if raw:
                    packages.add(raw[3].rsplit("_", 1)[0])
        print(f"\n{len(packages)} packages must each accept a layer for this swap:")
        for name in sorted(packages):
            print(f"  {name}")
        print("Probe every one with noop_patch_probe.py before writing geometry: a package that "
              "rejects our layers hangs the world load whatever the bytes say.")

    if "--request" in sys.argv:
        headers: list[int] = []
        if "--headers" in sys.argv:
            headers = package_headers(sys.argv[sys.argv.index("--headers") + 1])
        emit_request(all_missing, headers)
    elif all_missing:
        print(f"\n{len(all_missing)} tags block the walk. Re-run with --request to emit them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
