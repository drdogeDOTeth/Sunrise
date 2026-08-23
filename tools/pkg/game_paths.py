"""Resolve the live Sunrise install on Windows or this Linux box."""
from __future__ import annotations

from pathlib import Path


def sunrise_root() -> Path:
    for candidate in (Path(r"C:\Sunrise"), Path("/home/howie/Sunrise")):
        if (candidate / "packages").is_dir() and (candidate / "destiny2.exe").is_file():
            return candidate
    return Path("/home/howie/Sunrise")


def packages_dir() -> Path:
    return sunrise_root() / "packages"


def artifact_dir() -> Path:
    return sunrise_root() / "bin" / "x64" / "Sunrise"


def dump_dir() -> Path:
    path = artifact_dir() / "dump"
    path.mkdir(parents=True, exist_ok=True)
    return path
