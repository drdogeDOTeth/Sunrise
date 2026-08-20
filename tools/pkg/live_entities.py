"""
Lists the `SEntityModel` handles a capture named outright, per field.

## Why this exists

`live_models.py --match` inverts *vertex buffer headers* back to models, which only works for models
already dumped. But a capture also carries handles that resolve directly to class `0x808073A5`, and
those need no dump at all to name - the entry table gives the class, and the tag *is* the model.

Separating the fields matters. `r9tags` is a per-instance window and is tight enough to act on;
`paytags` is a serialized reference table and proves reference, not identity.

Usage:
    python live_entities.py --log capture.log
    python live_entities.py --log capture.log --field r9tags
"""
from __future__ import annotations

import re
import struct
from collections import Counter, defaultdict
from pathlib import Path

from parse_models import INDEX, Model, lookup, option
from tigerpkg import TAG_BASE, TAG_ENTRY_BITS

ENTITY_MODEL = 0x808073A5
DUMPS = [Path(r"C:\Sunrise\bin\x64\Sunrise\dump"),
         Path(r"C:\Sunrise\bin\x64\Sunrise\dump_models")]

FIELD_RE = re.compile(
    r"\b(r9tags|paytags|dsttags)=((?:0x[0-9A-Fa-f]{8})(?:,0x[0-9A-Fa-f]{8})*)")

NAME = {pid: path.stem.rsplit("_", 1)[0].replace("w64_", "") for pid, path in INDEX.items()}


def where(tag: int) -> str:
    return NAME.get((tag - TAG_BASE) >> TAG_ENTRY_BITS, f"pkg?{(tag - TAG_BASE) >> TAG_ENTRY_BITS:04X}")


def dumped() -> dict[int, Path]:
    """@return Path of every dumped entry, keyed by tag."""
    out: dict[int, Path] = {}
    for folder in DUMPS:
        for path in folder.glob("tag_*.bin"):
            try:
                out.setdefault(int(path.stem[4:], 16), path)
            except ValueError:
                continue
    return out


def geometry(path: Path) -> tuple[int, float, float, int] | None:
    """@return `(mesh count, floor, top, position bytes)` for a dumped model, or None."""
    data = path.read_bytes()
    if len(data) < 0xA0:
        return None
    (declared,) = struct.unpack_from("<q", data, 0)
    if declared != len(data):
        return None
    model = Model(0, data)
    if not model.meshes or not model.scale:
        return None
    half = max(abs(value) for value in model.scale[:3])
    return (len(model.meshes),
            model.translation[2] - half,
            model.translation[2] + half,
            sum(mesh.position_bytes for mesh in model.meshes))


def main() -> None:
    log = Path(option("--log", r"C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log"))
    want = option("--field", "")
    if not log.is_file():
        raise SystemExit(f"no capture log at {log}")

    per_field: dict[str, Counter] = defaultdict(Counter)
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if "ev=model_class_trace" not in line:
            continue
        for field, blob in FIELD_RE.findall(line):
            for part in blob.split(","):
                tag = int(part, 16)
                entry = lookup(tag)
                if entry is not None and entry.reference == ENTITY_MODEL:
                    per_field[field][tag] += 1

    have = dumped()
    for field, counter in per_field.items():
        if want and field != want:
            continue
        print(f"\n=== {field}: {len(counter)} distinct SEntityModel handles "
              f"({sum(counter.values())} occurrences) ===")
        print(f"{'seen':>5} {'tag':>10} {'package':<18} {'meshes':>6} {'floor':>7} {'top':>7} "
              f"{'pos B':>9}  body?")
        for tag, count in counter.most_common():
            path = have.get(tag)
            info = geometry(path) if path else None
            if info is None:
                print(f"{count:5} 0x{tag:08X} {where(tag):<18} "
                      f"{'(not dumped)' if path is None else '(unparsed)':>32}")
                continue
            meshes, floor, top, positions = info
            body = 1.5 <= top <= 2.3 and -0.25 <= floor <= 0.30
            print(f"{count:5} 0x{tag:08X} {where(tag):<18} {meshes:6} {floor:7.3f} {top:7.3f} "
                  f"{positions:9,}  {'<<< BODY' if body else ''}")


if __name__ == "__main__":
    main()
