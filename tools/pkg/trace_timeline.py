"""
Buckets a capture's live model instances by *when* they appeared.

## Why this exists

Every earlier analysis treated a capture as one flat set of handles, which throws away the single
most discriminating fact available: the user walks through the game in *stages*. Login, character
select, the character screen and the world each build their own objects, and the log stamps every
event with `t=` milliseconds. Bucketing by time separates "loaded at character select" from
"loaded at login" without a single extra launch.

Usage:
    python trace_timeline.py --log capture.log                 # phase histogram
    python trace_timeline.py --log capture.log --from 30000 --to 60000   # packages in a window
    python trace_timeline.py --log capture.log --gap 3000      # tune phase splitting
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from parse_models import INDEX, lookup, option
from tigerpkg import TAG_BASE, TAG_ENTRY_BITS

ENTITY_MODEL = 0x808073A5

TIME_RE = re.compile(r"\bt=(\d+)")
R9_RE = re.compile(r"\br9=0x([0-9A-Fa-f]+)")
FIELD_RE = re.compile(
    r"\b(r9tags|paytags|dsttags)=((?:0x[0-9A-Fa-f]{8})(?:,0x[0-9A-Fa-f]{8})*)")

NAME = {pid: path.stem.rsplit("_", 1)[0].replace("w64_", "") for pid, path in INDEX.items()}


def where(tag: int) -> str:
    return NAME.get((tag - TAG_BASE) >> TAG_ENTRY_BITS, f"pkg?{(tag - TAG_BASE) >> TAG_ENTRY_BITS:04X}")


def events(log: Path) -> list[tuple[int, str, str, list[int]]]:
    """@return `(milliseconds, r9 key, field, tags)` for every tag window, in log order."""
    out: list[tuple[int, str, str, list[int]]] = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if "ev=model_class_trace" not in line:
            continue
        stamp = TIME_RE.search(line)
        if not stamp:
            continue
        when = int(stamp.group(1))
        key = R9_RE.search(line)
        for field, blob in FIELD_RE.findall(line):
            out.append((when, key.group(1) if key else "", field,
                        [int(part, 16) for part in blob.split(",")]))
    return out


def main() -> None:
    log = Path(option("--log", r"C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log"))
    if not log.is_file():
        raise SystemExit(f"no capture log at {log}")
    gap = int(option("--gap", "3000"))
    start = option("--from", "")
    stop = option("--to", "")

    rows = events(log)
    if not rows:
        raise SystemExit(f"no model_class_trace tag windows in {log}")

    if start or stop:
        low = int(start) if start else 0
        high = int(stop) if stop else 1 << 62
        chosen = [row for row in rows if low <= row[0] <= high]
        packages: Counter[str] = Counter()
        models: Counter[int] = Counter()
        for _, _, field, tags in chosen:
            for tag in tags:
                entry = lookup(tag)
                if entry is None:
                    continue
                packages[where(tag)] += 1
                if entry.reference == ENTITY_MODEL:
                    models[tag] += 1
        print(f"{len(chosen)} windows in t=[{low},{high}]")
        print("\n--- packages ---")
        for package, count in packages.most_common(20):
            print(f"{count:8,}  {package}")
        print(f"\n--- SEntityModel handles named in this window ({len(models)}) ---")
        for tag, count in models.most_common(40):
            print(f"{count:5}  0x{tag:08X}  {where(tag)}")
        return

    # Split into phases wherever the game goes quiet for `gap` milliseconds.
    phases: list[list[tuple[int, str, str, list[int]]]] = [[rows[0]]]
    for row in rows[1:]:
        if row[0] - phases[-1][-1][0] > gap:
            phases.append([])
        phases[-1].append(row)

    print(f"{len(rows):,} tag windows, {len(phases)} phases (gap > {gap} ms)\n")
    print(f"{'phase':>5} {'t start':>9} {'t end':>9} {'windows':>8} {'r9 keys':>8}  top packages")
    for index, phase in enumerate(phases):
        packages: Counter[str] = Counter()
        keys = {row[1] for row in phase if row[1]}
        for _, _, _, tags in phase:
            for tag in tags:
                if lookup(tag) is not None:
                    packages[where(tag)] += 1
        top = ", ".join(f"{name}:{count}" for name, count in packages.most_common(4))
        print(f"{index:5} {phase[0][0]:9,} {phase[-1][0]:9,} {len(phase):8,} {len(keys):8,}  {top}")


if __name__ == "__main__":
    main()
