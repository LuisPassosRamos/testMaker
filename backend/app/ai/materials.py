"""Text (and a few page images) from the teacher's material: lesson plan, book pages, notebook photos.

Word and text PDFs give their text directly. Photos and scanned pages are read with OCR when
it is installed, and a few of them also go to the model as images, because handwriting and
book layouts are read better by the model than by OCR.
"""

from __future__ import annotations

import io
import logging

import pdfplumber
import pypdfium2 as pdfium
from docx import Document
from PIL import Image

from app.config import Settings
from app.domain.models import Project, SourceFile
from app.extraction.items import TextItem
from app.extraction.ocr import ocr_page, ocr_status
from app.extraction.pdf_reader import render_page
from app.importing.storage import ProjectStore

log = logging.getLogger(__name__)

MAX_PDF_PAGES = 15
MAX_IMAGES = 4
IMAGE_SIDE = 1600


def _jpeg(image: Image.Image) -> bytes:
    img = image.convert("RGB")
    img.thumbnail((IMAGE_SIDE, IMAGE_SIDE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=80, optimize=True)
    return out.getvalue()


def _ocr_text(image: Image.Image, source: SourceFile, page_no: int, settings: Settings) -> str:
    if not ocr_status(settings).available:
        return ""
    try:
        result = ocr_page(image, source_id=source.id, page_no=page_no, settings=settings, save_crop=lambda _img: "")
    except Exception as exc:  # OCR is a bonus here: the image itself still goes to the model
        log.warning("material OCR failed: %s", exc.__class__.__name__)
        return ""
    return "\n".join(item.text for item in result.items if isinstance(item, TextItem))


def _docx_text(path) -> str:
    doc = Document(str(path))
    lines = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(dict.fromkeys(cells)))
    return "\n".join(lines)


def collect_materials(store: ProjectStore, pid: str, project: Project, settings: Settings,
                      progress=None) -> tuple[list[tuple[str, str]], list[tuple[str, bytes]]]:
    """[(file name, text)] and up to MAX_IMAGES [(mime, jpeg bytes)] of pages without a text layer."""
    texts: list[tuple[str, str]] = []
    images: list[tuple[str, bytes]] = []
    materials = [s for s in project.sources if s.role == "material"]
    for index, source in enumerate(materials):
        if progress:
            progress("read", 0.05 + 0.15 * index / max(1, len(materials)), f"Lendo “{source.name}”…")
        path = store.source_path(pid, source)
        try:
            if source.kind == "docx":
                texts.append((source.name, _docx_text(path)))
            elif source.kind == "pdf":
                pieces: list[str] = []
                pdf = pdfium.PdfDocument(str(path))
                try:
                    with pdfplumber.open(str(path)) as plumber:
                        for i, page in enumerate(plumber.pages[:MAX_PDF_PAGES]):
                            text = (page.extract_text() or "").strip()
                            if len(text) >= 40:
                                pieces.append(text)
                                continue
                            image = render_page(pdf, i, dpi=200)
                            pieces.append(_ocr_text(image, source, i + 1, settings))
                            if len(images) < MAX_IMAGES:
                                images.append(("image/jpeg", _jpeg(image)))
                finally:
                    pdf.close()
                texts.append((source.name, "\n\n".join(p for p in pieces if p)))
            else:
                with Image.open(path) as img:
                    image = img.convert("RGB")
                texts.append((source.name, _ocr_text(image, source, 1, settings)))
                if len(images) < MAX_IMAGES:
                    images.append(("image/jpeg", _jpeg(image)))
        except Exception as exc:
            log.warning("material %s could not be read: %s", source.id, exc.__class__.__name__)
            texts.append((source.name, ""))
    return texts, images
