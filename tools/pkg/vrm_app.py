"""
Sunrise VRM Character Manager & Injector App.

Interactive CLI & GUI Application for managing, converting, and injecting
VRM 0.x / 1.0 and GLB models into Destiny 2 / Sunrise.

Features:
- Convert .vrm / .glb models into Sunrise custom character profiles
- List all installed character models in Sunrise
- Inject custom character packages into Destiny 2 game files
- View metadata, bone mappings, and texture channels
- Switch active default model
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vrm_injector import (
    VrmConverter,
    VrmDocument,
    VrmError,
    deploy_profile,
    inject_full_body,
    profile_id_from_name,
)

DEFAULT_SUNRISE_DIR = Path(r"C:\Sunrise")
DEFAULT_MODELS_DIR = DEFAULT_SUNRISE_DIR / "bin" / "x64" / "Sunrise" / "models"


def get_models_dir() -> Path:
    if DEFAULT_MODELS_DIR.exists():
        return DEFAULT_MODELS_DIR
    local_models = Path(__file__).parent / "models"
    local_models.mkdir(parents=True, exist_ok=True)
    return local_models


def list_installed_models(models_dir: Path) -> list[dict]:
    models = []
    if not models_dir.exists():
        return models

    for item in models_dir.iterdir():
        if item.is_dir():
            manifest_file = item / "manifest.json"
            if manifest_file.is_file():
                try:
                    data = json.loads(manifest_file.read_text(encoding="utf-8"))
                    data["folder"] = str(item)
                    models.append(data)
                except Exception as err:
                    print(f"Warning: Failed to read {manifest_file}: {err}")
    return models


def print_banner() -> None:
    print("=" * 68)
    print("       SUNRISE VRM CHARACTER MANAGER & INJECTOR APP")
    print("=" * 68)


def interactive_cli() -> None:
    print_banner()
    models_dir = get_models_dir()

    while True:
        print(f"\nModels Directory: {models_dir}")
        installed = list_installed_models(models_dir)
        print(f"\nInstalled VRM Profiles ({len(installed)}):")
        if not installed:
            print("  (None installed yet)")
        else:
            for idx, mod in enumerate(installed):
                name = mod.get("name", "Unknown")
                author = mod.get("author", "Unknown")
                parts = len(mod.get("parts", []))
                verts = mod.get("vertex_count", 0)
                print(f"  [{idx + 1}] {name} (Author: {author} | {verts:,} verts | {parts} parts)")

        print("\nOptions:")
        print("  1. Convert a VRM / GLB into a full-body profile")
        print("  2. Inspect a VRM / GLB file metadata")
        print("  3. View details of an installed profile")
        print("  4. Inject a profile as the playable Warlock (Destiny must be closed)")
        print("  5. Exit")

        choice = input("\nSelect an option [1-5]: ").strip()

        if choice == "1":
            file_str = input("Enter path to .vrm / .glb file: ").strip().strip('"').strip("'")
            path = Path(file_str)
            if not path.is_file():
                print(f"Error: File does not exist: {path}")
                continue
            name_override = input(f"Enter profile name (press Enter for '{path.stem}'): ").strip()
            profile_name = name_override or path.stem

            try:
                print("\nParsing VRM document and building a full-body Warlock profile...")
                doc = VrmDocument.load(path)
                out_dir = models_dir / profile_id_from_name(profile_name)
                converter = VrmConverter(doc)
                manifest = converter.build_profile(profile_name, out_dir)
                deploy_profile(out_dir, manifest["id"])
                print(f"\nConverted '{manifest.get('name')}' "
                      f"({manifest.get('vertex_count', 0):,} verts, "
                      f"{len(manifest.get('parts', []))} parts).")
                print("This is a full character, not a helmet. Close Destiny and pick option 4 to inject it.")
            except Exception as e:
                print(f"\nError converting model: {e}")

        elif choice == "2":
            file_str = input("Enter path to .vrm / .glb file: ").strip().strip('"').strip("'")
            path = Path(file_str)
            if not path.is_file():
                print(f"Error: File does not exist: {path}")
                continue
            try:
                doc = VrmDocument.load(path)
                print("\n--- VRM Info ---")
                print(f"Title: {doc.meta.get('title', 'N/A')}")
                print(f"Author: {doc.meta.get('author', 'N/A')}")
                print(f"Version: {doc.vrm_version.upper()}")
                print(f"Humanoid Bones: {len(doc.human_bones)}")
                print(f"Meshes: {len(doc.json.get('meshes', []))}")
                print(f"Materials: {len(doc.json.get('materials', []))}")
                print(f"Textures: {len(doc.json.get('images', []))}")
            except Exception as e:
                print(f"\nError reading file: {e}")

        elif choice == "3":
            if not installed:
                print("No models installed.")
                continue
            sel = input(f"Enter model number [1-{len(installed)}]: ").strip()
            try:
                idx = int(sel) - 1
                if 0 <= idx < len(installed):
                    mod = installed[idx]
                    print(json.dumps(mod, indent=2))
                else:
                    print("Invalid selection.")
            except ValueError:
                print("Invalid input.")

        elif choice == "4":
            if not installed:
                print("No models installed. Convert one first.")
                continue
            sel = input(f"Select profile to inject as the playable Warlock [1-{len(installed)}]: ").strip()
            try:
                idx = int(sel) - 1
                if 0 <= idx < len(installed):
                    mod = installed[idx]
                    src_dir = Path(mod["folder"])
                    obj_path = src_dir / "character_body.obj"
                    confirm = input(
                        f"Inject '{mod.get('name')}' onto the Scatterhorn chest and blank the rest "
                        "of the armour? Destiny must be closed. [y/N]: "
                    ).strip().lower()
                    if confirm != "y":
                        print("Cancelled.")
                        continue
                    inject_full_body(obj_path)
                    print(f"\nInstalled '{mod.get('name')}' as the playable Warlock.")
                    print("Launch the game, then pick this profile in the Player menu so the draw hook matches.")
                else:
                    print("Invalid selection.")
            except (ValueError, VrmError, OSError) as err:
                print(f"Inject failed: {err}")

        elif choice == "5" or choice.lower() in ("q", "quit", "exit"):
            print("Exiting.")
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="Sunrise VRM Manager App")
    parser.add_argument("--list", action="store_true", help="List installed models")
    parser.add_argument("--convert", type=Path, help="Convert a .vrm or .glb file")
    parser.add_argument("--name", type=str, default="", help="Custom name for converted profile")

    args = parser.parse_args()

    models_dir = get_models_dir()

    if args.list:
        installed = list_installed_models(models_dir)
        print(f"Installed Models in {models_dir}:")
        for m in installed:
            print(f" - {m.get('name')} (id: {m.get('id')})")
        return

    if args.convert:
        doc = VrmDocument.load(args.convert)
        profile_name = args.name or args.convert.stem
        out_dir = models_dir / profile_id_from_name(profile_name)
        converter = VrmConverter(doc)
        manifest = converter.build_profile(profile_name, out_dir)
        deploy_profile(out_dir, manifest["id"])
        print(f"Saved full-body profile to {out_dir}")
        return

    interactive_cli()


if __name__ == "__main__":
    main()
