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


BOLD_FILES: dict[str, list[str]] = {
    "Arial": ["arialbd.ttf", "LiberationSans-Bold.ttf"],
    "Calibri": ["calibrib.ttf", "Carlito-Bold.ttf"],
    "Times New Roman": ["timesbd.ttf", "LiberationSerif-Bold.ttf"],
    "Verdana": ["verdanab.ttf"],
    "Century Gothic": ["GOTHICB.TTF", "gothicb.ttf"],
    "Comic Sans MS": ["comicbd.ttf"],
}


def _font_dirs() -> list[Path]:
    if sys.platform == "win32":
        return [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts"]


@lru_cache
def _font_index() -> dict[str, Path]:
    index: dict[str, Path] = {}
    for base in _font_dirs():
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix.lower() in (".ttf", ".otf"):
                index.setdefault(path.name.lower(), path)
    return index


@lru_cache
def _truetype(family: str, bold: bool):
    """The installed font file for a family (the metric-compatible one on Linux), or None."""
    from PIL import ImageFont

    names = BOLD_FILES.get(family, []) if bold else next((f for n, f, _ in CANDIDATES if n == family), [])
    index = _font_index()
    for name in names:
        path = index.get(name.lower())
        if path is not None:
            try:
                return ImageFont.truetype(str(path), size=1000)
            except OSError:
                continue
    return None


def text_width_cm(text: str, family: str, size_pt: float, bold: bool = False) -> float:
    """Printed width of a single line of text, used to decide if alternatives fit side by side.

    Uses the real font metrics when the font file is available, otherwise a conservative
    estimate (average glyph width of 0.6 em), so the result errs on the side of "does not fit".
    """
    font = _truetype(family, bold) or (_truetype("Arial", bold) if family != "Arial" else None)
    if font is not None:
        width_em = font.getlength(text) / 1000
    else:
        width_em = 0.6 * len(text)
    return width_em * size_pt / 72 * 2.54


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
