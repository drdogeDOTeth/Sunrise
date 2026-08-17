"""
Reads Sunrise's log file while the game still holds it open, and pulls out what matters.

The in-game panel keeps only a bounded history — raising the client channel to `info` floods it with
the game's own retail logging and evicts the startup lines within seconds, which is exactly where
package registration reports. The file sink keeps everything, but the game holds the handle, so the
file has to be opened with shared read/write access rather than the ordinary path.

Usage: python readlog.py [pattern]
"""
from __future__ import annotations

import io
import os
import re
import sys

LOG = r"C:\Sunrise\bin\x64\Sunrise\logs\sunrise.log"
DEFAULT_PATTERN = r"package|pkg|registration|patchable|assert|level=(warn|error)"


def read_shared(path: str) -> str:
    """@return The file's contents, tolerating the writer holding it open."""
    handle = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        with io.FileIO(handle, closefd=False) as raw:
            return raw.readall().decode("utf-8", errors="replace")
    finally:
        os.close(handle)


pattern = re.compile(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATTERN, re.IGNORECASE)
text = read_shared(LOG)
lines = text.splitlines()

print(f"{LOG}\n{len(text):,} bytes, {len(lines):,} lines\n")

hits = [line for line in lines if pattern.search(line)]
print(f"--- {len(hits):,} matching lines ---")
for line in hits[:60]:
    print(f"  {line}")

verdict = [line for line in lines if "stage=registration" in line]
assertion = [line for line in lines if "atchable package registration" in line]
print("\n--- verdict ---")
for line in verdict:
    print(f"  {line}")
if assertion:
    print(f"  REJECTED: the game still refuses the written package")
    for line in assertion[:3]:
        print(f"    {line}")
else:
    print("  no 'Patchable package registration failed' assert in this run")
