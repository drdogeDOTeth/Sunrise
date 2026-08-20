"""
Blanks every dumped investment entity model.

Torso blanks and person-sized wraps did not move the default undersuit. Empty equipment slots
make the client fall back to race/gender/class defaults; this probe asks whether that fallback
mesh even lives in investment_01d3 / 0361. If the tunic survives, it does not.

Usage:
    python probe_investment.py --dry-run
    python probe_investment.py
    python inject_mesh.py --undo
"""
from __future__ import annotations

import sys
from pathlib import Path

from inject_mesh import blank_model, entry_index_of, package_of, write_all
from parse_models import models
from tigerpkg import TAG_BASE, TAG_ENTRY_BITS

INVESTMENT = {0x01D3, 0x0361}


def main() -> None:
    chosen = [
        model for model in models
        if ((model.tag - TAG_BASE) >> TAG_ENTRY_BITS) in INVESTMENT
    ]
    if not chosen:
        raise SystemExit("no investment models in dump_models")
    by_package: dict[Path, dict[int, bytes]] = {}
    for model in chosen:
        by_package.setdefault(package_of(model.tag), {})[entry_index_of(model.tag)] = (
            blank_model(model)
        )
    print(f"{len(chosen)} investment models across {len(by_package)} packages")
    for path, replacements in sorted(by_package.items()):
        print(f"  {path.name}: {len(replacements)} entries")
    if "--dry-run" in sys.argv:
        return
    write_all(by_package, "")
    request = Path(r"C:\Sunrise\bin\x64\Sunrise\dump\request.txt")
    request.write_text("# blanked so a launch cannot re-dump through an installed patch\r\n",
                       encoding="utf-8")
    print(f"blanked {request}")


if __name__ == "__main__":
    main()
