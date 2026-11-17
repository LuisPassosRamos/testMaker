"""Fonts offered in the layout step: a short, school-friendly list filtered to what is installed.

The PDF converter can only use fonts present on the machine, so offering a missing
font would silently change the printed result.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

# Linux servers use metric-compatible open fonts under the familiar names (same line breaks).
LINUX_ALIASES = {
    "Arial": ["Arial", "Liberation Sans"],
    "Times New Roman": ["Times New Roman", "Liberation Serif"],
    "Calibri": ["Calibri", "Carlito"],
}

CANDIDATES: list[tuple[str, list[str], str]] = [
    ("Arial", ["arial.ttf", "LiberationSans-Regular.ttf"], "Clara e econômica"),
    ("Calibri", ["calibri.ttf", "Carlito-Regular.ttf"], "Moderna, padrão do Word"),
    ("Times New Roman", ["times.ttf", "LiberationSerif-Regular.ttf"], "Clássica, com serifa"),
    ("Verdana", ["verdana.ttf"], "Bem legível para crianças"),
    ("Century Gothic", ["GOTHIC.TTF", "gothic.ttf"], "Arredondada, comum na alfabetização"),
    ("Comic Sans MS", ["comic.ttf"], "Informal, para os pequenos"),
]


@lru_cache
def available_fonts() -> list[dict[str, str]]:
    found: set[str] = set()
    if sys.platform == "win32":
        dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
        files = {p.name.lower() for d in dirs if d.exists() for p in d.iterdir()}
        for name, file_names, _ in CANDIDATES:
            if any(f.lower() in files for f in file_names):
                found.add(name)
    elif shutil.which("fc-list"):
        try:
            listing = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            listing = ""
        families = {f.strip().lower() for line in listing.splitlines() for f in line.split(",")}
        for name, _, _ in CANDIDATES:
            if any(alias.lower() in families for alias in LINUX_ALIASES.get(name, [name])):
                found.add(name)
    if not found:
        found.add("Arial")
    return [{"name": name, "description": desc} for name, _, desc in CANDIDATES if name in found]
