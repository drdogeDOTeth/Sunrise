"""
Snapshot and restore the exact set of patch layers that is known to work in game.

**Why this exists.** 2026-08-21 was lost to one bad layer among ninety-nine, and most of the cost
was not finding it - it was repeatedly losing track of which files were live. Layers live in three
places (`packages/`, `packages/_vanilla_test/`, `packages/_bad_ui_01a3/`), a wrong set produces
symptoms indistinguishable from a content bug, and pulling a layer out of the middle of a stack
spins the game at character select forever with no error. A prose description of "the good config"
in a handoff document is not enough; this records it as data and puts it back exactly.

The snapshot is the **live set**, by name. Restoring means: every file in the snapshot is live, and
every other layer of ours is in the attic. Files are only ever moved, never deleted, and the tool
refuses to run while the game holds them open.

    python known_good.py --save "96 layers: orbit + Tower + five-part split"
    python known_good.py --check      # is the live set exactly the snapshot?
    python known_good.py --restore    # make it so
    python known_good.py --list       # what snapshots exist

Snapshots are JSON in `tools/pkg/known_good/`, committed with the repo, so the recipe survives the
machine.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PACKAGES = Path("C:/Sunrise/packages")
ATTIC = "_vanilla_test"
STORE = Path(__file__).with_name("known_good")
# The install finished 2026-08-16 and our first layer is 2026-08-19, so this separates ours from
# shipped. "Has plain blocks" would not - video and audio packages legitimately contain them.
INSTALLED_BEFORE = datetime(2026, 8, 18)


def game_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq destiny2.exe"],
                             capture_output=True, text=True, timeout=20).stdout
        return "destiny2.exe" in out
    except Exception:
        return False


def ours(directory: Path) -> list[Path]:
    """@return Every layer file *we* wrote in one directory, by write date."""
    return sorted(p for p in directory.glob("*.pkg")
                  if datetime.fromtimestamp(p.stat().st_mtime) > INSTALLED_BEFORE)


def unique_in(directory: Path, name: str) -> Path:
    """
    @param directory Destination directory.
    @param name Preferred file name.
    @return A free path in `directory`, suffixed only if the name is already taken.

    Two different builds of one layer can both need parking - the attic already holds a copy under
    that name and the live file is a different one. Overwriting would destroy the only copy of
    whichever it hit, and this tool never deletes.
    """
    target = directory / name
    if not target.exists():
        return target
    stem = target.stem
    for index in range(1, 1000):
        candidate = directory / f"{stem}.{index}{target.suffix}"
        if not candidate.exists():
            return candidate
    raise SystemExit(f"cannot find a free name for {name} in {directory}")


def all_known(packages: Path) -> dict[str, list[Path]]:
    """@return Every copy of every layer of ours anywhere under `packages`, by file name.

    A name is not unique. Thirteen of v25's layers exist in two or more attics with *different*
    contents, because a reverted experiment keeps the name it reverted. Returning only the first
    copy found made a restore depend on directory order: `_vanilla_test` sorts last, so once every
    layer had been moved there, a stale copy in `_reverted*` won and the restore silently rebuilt
    the wrong set. Callers pick by recorded size instead - see `resolve`.
    """
    found: dict[str, list[Path]] = {}
    for directory in [packages, *(d for d in packages.iterdir() if d.is_dir())]:
        for path in ours(directory):
            found.setdefault(path.name, []).append(path)
    return found


def resolve(entry: dict, candidates: list[Path], packages: Path) -> Path | None:
    """
    Picks the copy of one layer that the snapshot actually recorded.
    @param entry Snapshot entry, carrying the recorded byte size.
    @param candidates Every copy of that name on disk.
    @param packages Live package directory.
    @return The matching copy, preferring one already live, or None when none matches.
    """
    exact = [p for p in candidates if p.stat().st_size == entry["size"]]
    if not exact:
        return None
    # A copy already in place is the one to keep: moving an identical file achieves nothing and
    # only risks a collision. Otherwise the canonical attic wins over the reverted experiments.
    for preferred in (packages, packages / ATTIC):
        for path in exact:
            if path.parent == preferred:
                return path
    return exact[0]


def save(note: str, packages: Path) -> Path:
    live = ours(packages)
    STORE.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = STORE / f"{stamp}.json"
    out.write_text(json.dumps({
        "saved": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "count": len(live),
        "live": [{"name": p.name, "size": p.stat().st_size,
                  "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
                 for p in live],
    }, indent=2))
    print(f"saved {len(live)} live layers -> {out.name}\n  {note}")
    return out


def newest_snapshot() -> Path:
    snaps = sorted(STORE.glob("*.json"))
    if not snaps:
        raise SystemExit(f"no snapshots in {STORE}")
    return snaps[-1]


def check(packages: Path, snapshot: Path) -> bool:
    want = json.loads(snapshot.read_text())
    expected = {e["name"]: e for e in want["live"]}
    have = {p.name: p for p in ours(packages)}
    missing = sorted(set(expected) - set(have))
    extra = sorted(set(have) - set(expected))
    wrong = [n for n in set(expected) & set(have)
             if have[n].stat().st_size != expected[n]["size"]]
    print(f"snapshot {snapshot.name}: {want['count']} layers - {want['note']}")
    print(f"  live now: {len(have)}")
    for n in missing:
        print(f"  MISSING (should be live): {n}")
    for n in extra:
        print(f"  EXTRA (should be in the attic): {n}")
    for n in wrong:
        print(f"  WRONG SIZE: {n} is {have[n].stat().st_size:,}, expected {expected[n]['size']:,}")
    ok = not (missing or extra or wrong)
    print("  MATCHES" if ok else "  DOES NOT MATCH")
    return ok


def restore(packages: Path, snapshot: Path) -> None:
    if game_running():
        raise SystemExit("destiny2 is running; close it before moving package files")
    want = {e["name"]: e for e in json.loads(snapshot.read_text())["live"]}
    known = all_known(packages)
    # Resolve the whole set before moving anything, so a snapshot that cannot be rebuilt exactly
    # leaves the install untouched rather than half-converted.
    chosen: dict[str, Path] = {}
    unresolved: list[str] = []
    for name, entry in want.items():
        match = resolve(entry, known.get(name, []), packages)
        if match is None:
            unresolved.append(f"{name} ({len(known.get(name, []))} copies, none {entry['size']:,}B)")
        else:
            chosen[name] = match
    if unresolved:
        raise SystemExit(
            f"{len(unresolved)} layer(s) cannot be resolved to the recorded content:\n  "
            + "\n  ".join(unresolved[:8])
            + "\nRefusing to restore a partial set.")
    attic = packages / ATTIC
    attic.mkdir(exist_ok=True)
    out_count = in_count = 0
    # Out first: a layer moving to the attic frees its name, which matters when a stack was rebuilt.
    # A live file whose content is not the recorded one has to go too, or it holds the name against
    # the copy that should take it.
    for path in ours(packages):
        if chosen.get(path.name) == path:
            continue
        shutil.move(str(path), str(unique_in(attic, path.name)))
        out_count += 1
    for name, source in sorted(chosen.items()):
        if source.parent != packages:
            shutil.move(str(source), str(packages / name))
            in_count += 1
    print(f"moved {out_count} out, {in_count} in")
    check(packages, snapshot)


def main() -> None:
    args = sys.argv[1:]
    packages = next((Path(a.split("=", 1)[1]) for a in args if a.startswith("--packages=")),
                    PACKAGES)
    pick = next((a.split("=", 1)[1] for a in args if a.startswith("--snapshot=")), None)
    if "--save" in args:
        note = next((args[i + 1] for i, a in enumerate(args)
                     if a == "--save" and i + 1 < len(args) and not args[i + 1].startswith("--")),
                    "unlabelled")
        save(note, packages)
    elif "--list" in args:
        for path in sorted(STORE.glob("*.json")):
            data = json.loads(path.read_text())
            print(f"  {path.name}  {data['count']:>3} layers  {data['note']}")
    elif "--restore" in args:
        restore(packages, STORE / pick if pick else newest_snapshot())
    elif "--check" in args:
        sys.exit(0 if check(packages, STORE / pick if pick else newest_snapshot()) else 1)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
