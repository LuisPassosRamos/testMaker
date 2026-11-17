"""DOCX -> PDF conversion.

LibreOffice (headless) is the primary engine: free, available on Windows and in a
Linux container, so local previews match a future hosted deployment. Microsoft Word
(COM automation) is a Windows-only fallback. Conversions are serialised — LibreOffice
does not like concurrent runs on one profile — and time-limited.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_WINDOWS_SOFFICE = [
    Path(r"C:\Program Files\LibreOffice\program\soffice.com"),
    Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
    Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
]


class ConversionError(Exception):
    pass


@dataclass
class ConverterStatus:
    available: bool
    engine: str | None
    message: str


def find_libreoffice(settings: Settings) -> str | None:
    if settings.libreoffice_path and Path(settings.libreoffice_path).exists():
        return settings.libreoffice_path
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _WINDOWS_SOFFICE:
        if candidate.exists():
            return str(candidate)
    return None


def _word_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"):
            return True
    except OSError:
        return False


def converter_status(settings: Settings) -> ConverterStatus:
    if settings.converter in ("auto", "libreoffice") and find_libreoffice(settings):
        return ConverterStatus(True, "libreoffice", "LibreOffice")
    if settings.converter in ("auto", "word") and _word_available():
        return ConverterStatus(True, "word", "Microsoft Word")
    return ConverterStatus(
        False, None, "Nenhum conversor de PDF encontrado. Instale o LibreOffice (veja o README)."
    )


def docx_to_pdf(docx_path: Path, out_dir: Path, settings: Settings) -> Path:
    status = converter_status(settings)
    if not status.available:
        raise ConversionError(status.message)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with _LOCK:
        if status.engine == "libreoffice":
            pdf = _convert_libreoffice(docx_path, out_dir, settings)
        else:
            pdf = _convert_word(docx_path, out_dir)
    log.info("converted docx->pdf with %s in %.1fs", status.engine, time.perf_counter() - started)
    return pdf


def _convert_libreoffice(docx_path: Path, out_dir: Path, settings: Settings) -> Path:
    soffice = find_libreoffice(settings)
    assert soffice
    profile = (settings.data_dir / "lo-profile").resolve()
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        soffice,
        f"-env:UserInstallation={profile.as_uri()}",
        "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard",
        "--convert-to", "pdf:writer_pdf_Export",
        "--outdir", str(out_dir.resolve()),
        str(docx_path.resolve()),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=settings.conversion_timeout_s, creationflags=creationflags,
            env=dict(os.environ),
        )
    except subprocess.TimeoutExpired as exc:
        raise ConversionError("A conversão para PDF demorou demais. Tente novamente.") from exc
    pdf = out_dir / (docx_path.stem + ".pdf")
    if proc.returncode != 0 or not pdf.exists():
        log.warning("libreoffice failed (code %s)", proc.returncode)
        raise ConversionError("O LibreOffice não conseguiu gerar o PDF deste documento.")
    return pdf


def _convert_word(docx_path: Path, out_dir: Path) -> Path:
    import pythoncom
    import win32com.client

    pdf = (out_dir / (docx_path.stem + ".pdf")).resolve()
    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(docx_path.resolve()), ConfirmConversions=False, ReadOnly=True,
                                  AddToRecentFiles=False, Visible=False)
        try:
            doc.ExportAsFixedFormat(str(pdf), 17)  # wdExportFormatPDF
        finally:
            doc.Close(False)
    except Exception as exc:
        raise ConversionError("O Microsoft Word não conseguiu gerar o PDF deste documento.") from exc
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()
    if not pdf.exists():
        raise ConversionError("O Microsoft Word não gerou o PDF.")
    return pdf
