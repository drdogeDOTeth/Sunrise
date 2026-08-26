"""Copy a whole working character off this machine, so a snapshot can actually restore it.

**`known_good.py` is a manifest, not a backup.** Its snapshots record name, size and mtime and then
*move* files between `packages/` and the attic. That recovers a wrong live *set*; it cannot recover
a byte. Delete or overwrite a `.pkg` and `--restore` reports MATCHES against files that no longer
hold what they held. This copies the bytes.

Three things have to travel together, because each is separately unrecoverable:

  1. **the layer chain** - every `.pkg` in the snapshot, not just the character ones. Layers
     truncate only: pull one from the middle and everything above it dangles and spins at
     character select. The chain is the unit.
  2. **the profile** - `characters/<name>/` (parts.txt + PNGs). Nothing in git holds these, and a
     part table is exact `(start, count)` pairs against that specific geometry.
  3. **the source model** - the .glb the geometry was built from. With it, the character is
     rebuildable; without it, the layers are the only copy that will ever exist.

Destination defaults to OneDrive, off this disk, because "not lost" means surviving the machine.

    python backup_config.py --snapshot=20260825-132926.json --name v25 \
        --profile v25 --source "C:\\Chiliz\\Destiny2SunriseCharacters\\void_4003GasMask.glb"
    python backup_config.py --verify --name v25      # re-hash every file against the backup
    python backup_config.py --list

Re-runnable: a file whose size and hash already match is skipped, so a repeat costs a read.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAME = Path(os.environ.get("SUNRISE_GAME", r"C:\Sunrise"))
PACKAGES = GAME / "packages"
ART = GAME / "bin" / "x64" / "Sunrise"
STORE = HERE / "known_good"
VAULT = Path(os.environ.get("SUNRISE_BACKUP",
                            str(Path.home() / "OneDrive" / "Destiny2SunriseBackups")))


def option(name: str, fallback: str = "") -> str:
    for arg in sys.argv:
        if arg.startswith(f"--{name}="):
            return arg.split("=", 1)[1]
    if f"--{name}" in sys.argv:
        at = sys.argv.index(f"--{name}")
        if at + 1 < len(sys.argv) and not sys.argv[at + 1].startswith("--"):
            return sys.argv[at + 1]
    return fallback


def digest(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def copy_into(source: Path, dest: Path, ledger: dict, label: str) -> tuple[int, int, int]:
    """@return (copied, skipped, bytes) - skipping anything already identical."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = ledger.get(label)
    if dest.is_file() and have and dest.stat().st_size == source.stat().st_size:
        if digest(source) == have:
            return (0, 1, 0)
    shutil.copy2(source, dest)
    ledger[label] = digest(dest)
    return (1, 0, source.stat().st_size)


def main() -> int:
    name = option("name")
    if "--list" in sys.argv:
        if not VAULT.is_dir():
            print(f"no vault at {VAULT}")
            return 1
        for entry in sorted(VAULT.iterdir()):
            manifest = entry / "manifest.json"
            if manifest.is_file():
                data = json.loads(manifest.read_text())
                print(f"  {entry.name:16s} {data['count']:3d} files  "
                      f"{data['bytes'] / 1024 / 1024:8.1f} MB  {data['saved'][:19]}  "
                      f"{data.get('note', '')}")
        return 0

    if not name:
        print("--name is required (e.g. --name v25)")
        return 2
    root = VAULT / name
    manifest_path = root / "manifest.json"
    previous = json.loads(manifest_path.read_text())["files"] if manifest_path.is_file() else {}

    if "--verify" in sys.argv:
        if not manifest_path.is_file():
            print(f"no backup named {name} at {root}")
            return 2
        bad = 0
        for label, want in previous.items():
            path = root / label
            if not path.is_file():
                print(f"  MISSING  {label}")
                bad += 1
            elif digest(path) != want:
                print(f"  CHANGED  {label}")
                bad += 1
        print(f"\n{len(previous)} files checked, {bad} bad")
        return 1 if bad else 0

    snapshot = option("snapshot")
    if not snapshot:
        print("--snapshot= is required")
        return 2
    live = json.loads((STORE / snapshot).read_text(encoding="utf-8"))["live"]

    ledger: dict[str, str] = dict(previous)
    copied = skipped = total = 0
    missing: list[str] = []

    for entry in live:
        source = PACKAGES / entry["name"]
        if not source.is_file():
            missing.append(entry["name"])
            continue
        c, s, b = copy_into(source, root / "packages" / entry["name"], ledger,
                            f"packages/{entry['name']}")
        copied += c; skipped += s; total += b

    profile = option("profile")
    if profile:
        folder = ART / "characters" / profile
        for item in sorted(folder.glob("*")) if folder.is_dir() else []:
            if item.is_file():
                c, s, b = copy_into(item, root / "profile" / item.name, ledger,
                                    f"profile/{item.name}")
                copied += c; skipped += s; total += b

    source_model = option("source")
    if source_model:
        model = Path(source_model)
        if model.is_file():
            c, s, b = copy_into(model, root / "source" / model.name, ledger,
                                f"source/{model.name}")
            copied += c; skipped += s; total += b
        else:
            missing.append(str(model))

    # The manifest travels with the bytes, so the backup can be checked without this repo.
    root.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "saved": __import__("datetime").datetime.now().isoformat(),
        "name": name,
        "snapshot": snapshot,
        "note": option("note", f"{name}: {len(live)} layers"),
        "count": len(ledger),
        "bytes": sum((root / label).stat().st_size for label in ledger if (root / label).is_file()),
        "files": ledger,
    }, indent=2), encoding="utf-8")

    print(f"backup '{name}' -> {root}")
    print(f"  copied {copied}, unchanged {skipped}, {total / 1024 / 1024:.1f} MB written")
    print(f"  manifest lists {len(ledger)} files")
    if missing:
        print(f"  NOT FOUND ({len(missing)}):")
        for item in missing[:10]:
            print(f"    {item}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
