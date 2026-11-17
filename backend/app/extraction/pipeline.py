"""Extraction pipeline: sources -> items -> structured exam.

Direct extraction first (DOCX XML, PDF text layer); OCR only for pages without text.
Each source also gets page images so the reviewer can always see the original next
to the extracted content. A failing page or file never discards the rest: it becomes
an issue (and, when possible, a whole-page image) so the work can continue.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

from app.config import Settings
from app.domain.models import Issue, Project, Provenance, SourceFile, SourcePage
from app.extraction.docx_reader import DocxExtractor
from app.extraction.items import Item, MediaItem, PageBreakItem, TextItem
from app.extraction.ocr import OcrUnavailable, ocr_page, ocr_status
from app.extraction.pdf_reader import PdfTextExtractor, render_page
from app.importing.storage import ProjectStore
from app.rendering.converter import ConversionError, converter_status, docx_to_pdf
from app.structuring.segmenter import segment

log = logging.getLogger(__name__)

Progress = Callable[[str, float, str], None]  # stage, fraction (0-1), message
PREVIEW_DPI = 110
PREVIEW_MAX_WIDTH = 1400


def _save_preview(image: Image.Image, path: Path) -> tuple[int, int]:
    img = image.convert("RGB")
    if img.width > PREVIEW_MAX_WIDTH:
        ratio = PREVIEW_MAX_WIDTH / img.width
        img = img.resize((PREVIEW_MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)
    img.save(path, format="JPEG", quality=85, optimize=True)
    return img.width, img.height


def _norm_token(token: str) -> str:
    token = unicodedata.normalize("NFC", token).lower()
    return re.sub(r"[^\w]", "", token)


class ExtractionPipeline:
    def __init__(self, store: ProjectStore, settings: Settings):
        self.store = store
        self.settings = settings
        self.used_ocr = False

    # ------------------------------------------------------------------ run
    def run(self, pid: str, progress: Progress) -> None:
        project = self.store.load(pid)
        all_items: list[Item] = []
        doc_issues: list[Issue] = []
        sources = project.sources
        total = max(1, len(sources))
        for index, source in enumerate(sources):
            def sub_progress(stage: str, fraction: float, message: str, _i: int = index) -> None:
                progress(stage, (_i + min(max(fraction, 0.0), 1.0)) / total * 0.92, message)

            try:
                items, issues = self._extract_source(pid, source, sub_progress, multi=len(sources) > 1)
            except Exception as exc:
                log.warning("extraction failed for source %s: %s", source.id, exc.__class__.__name__)
                doc_issues.append(Issue(
                    code="source_failed", severity="error",
                    message=(f"Não foi possível ler “{source.name}”. O restante foi aproveitado. Você pode "
                             "tentar enviar o arquivo novamente (por exemplo, salvo como PDF) ou digitar o trecho."),
                ))
                continue
            source.extracted = True
            if all_items:
                all_items.append(PageBreakItem(prov=Provenance(source_id=source.id), new_source=True))
            all_items.extend(items)
            doc_issues.extend(issues)

        progress("structure", 0.95, "Organizando questões e alternativas…")
        result = segment(all_items)
        if self.used_ocr:
            doc_issues.insert(0, Issue(
                code="ocr_formatting", severity="warning",
                message=("Parte do conteúdo foi lida de imagem (OCR). Negrito e itálico não são reconhecidos "
                         "em fotos: reaplique onde for importante (ex.: “INCORRETA”, “EXCETO”)."),
            ))

        def apply(p: Project) -> None:
            p.sources = sources
            p.exam.blocks = result.blocks
            p.exam.document_issues = doc_issues + result.issues

        self.store.mutate(pid, apply)

    # --------------------------------------------------------------- sources
    def _extract_source(self, pid: str, source: SourceFile, progress: Progress, multi: bool):
        path = self.store.source_path(pid, source)
        if source.kind == "docx":
            return self._docx(pid, source, path, progress)
        if source.kind == "pdf":
            return self._pdf(pid, source, path, progress)
        return self._image(pid, source, path, progress, multi)

    def _save_crop(self, pid: str) -> Callable[[Image.Image], str]:
        return lambda img: self.store.save_asset_image(pid, img.convert("RGB"))

    def _docx(self, pid: str, source: SourceFile, path: Path, progress: Progress):
        progress("read", 0.1, f"Lendo o arquivo Word “{source.name}”…")
        extractor = DocxExtractor(str(path), source.id, lambda blob: self.store.save_asset_bytes(pid, blob))
        items = extractor.extract()
        issues = list(extractor.document_issues)

        progress("original", 0.5, "Preparando a visualização do original…")
        pages_dir = self.store.pages_dir(pid)
        source.pages = []
        if converter_status(self.settings).available:
            try:
                pdf_path = docx_to_pdf(path, pages_dir / f"{source.id}-original", self.settings)
                final_pdf = pages_dir / f"{source.id}-original.pdf"
                pdf_path.replace(final_pdf)
                self._render_pdf_pages(source, final_pdf, pages_dir)
                self._map_items_to_pages(items, final_pdf)
            except ConversionError:
                issues.append(Issue(
                    code="original_preview_failed", severity="info",
                    message="Não foi possível gerar a imagem do original para conferência; o conteúdo foi lido.",
                ))
        else:
            issues.append(Issue(
                code="original_preview_unavailable", severity="info",
                message="Instale o LibreOffice para ver o original do Word lado a lado (veja o README).",
            ))
        return items, issues

    def _render_pdf_pages(self, source: SourceFile, pdf_path: Path, pages_dir: Path) -> None:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            for i in range(len(pdf)):
                image = render_page(pdf, i, dpi=PREVIEW_DPI)
                name = f"{source.id}-p{i + 1}.jpg"
                w, h = _save_preview(image, pages_dir / name)
                source.pages.append(SourcePage(index=i + 1, image=name, width=w, height=h, method="render"))
        finally:
            pdf.close()

    def _map_items_to_pages(self, items: list[Item], pdf_path: Path) -> None:
        """Locate DOCX paragraphs in the rendered original so the reviewer can jump to them."""
        tokens: list[tuple[str, int, float, float, float, float]] = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for pno, page in enumerate(pdf.pages, 1):
                pw, ph = float(page.width), float(page.height)
                for w in page.extract_words(x_tolerance=1.5):
                    token = _norm_token(w["text"])
                    if token:
                        tokens.append((token, pno, w["x0"] / pw, w["top"] / ph, w["x1"] / pw, w["bottom"] / ph))
        pointer = 0
        last_page: int | None = None
        for item in items:
            if isinstance(item, TextItem):
                words = [t for t in (_norm_token(x) for x in item.text.split()) if t][:12]
                if not words:
                    continue
                probe = words[: min(3, len(words))]
                found = None
                for start in range(pointer, min(len(tokens), pointer + 6000)):
                    if all(start + k < len(tokens) and tokens[start + k][0] == probe[k] for k in range(len(probe))):
                        found = start
                        break
                if found is None:
                    item.prov.page = last_page
                    continue
                span = [t for t in tokens[found: found + len(words)] if t[1] == tokens[found][1]]
                item.prov.page = tokens[found][1]
                item.prov.bbox = (min(t[2] for t in span), min(t[3] for t in span),
                                  max(t[4] for t in span), max(t[5] for t in span))
                last_page = item.prov.page
                pointer = found + 1
            elif hasattr(item, "prov") and item.prov.page is None:
                item.prov.page = last_page

    def _pdf(self, pid: str, source: SourceFile, path: Path, progress: Progress):
        pages_dir = self.store.pages_dir(pid)
        items: list[Item] = []
        issues: list[Issue] = []
        source.pages = []
        extractor = PdfTextExtractor(str(path), source.id, self._save_crop(pid))
        pdf = pdfium.PdfDocument(str(path))
        try:
            with pdfplumber.open(str(path)) as plumber:
                total = len(plumber.pages)
                for i, page in enumerate(plumber.pages):
                    progress("read", i / total, f"Lendo a página {i + 1} de {total}…")
                    try:
                        result = extractor.extract_page(page, pdf, i)
                    except Exception as exc:
                        log.warning("pdf page %d failed: %s", i + 1, exc.__class__.__name__)
                        result = None
                    if result is not None and not result.needs_ocr:
                        preview = render_page(pdf, i, dpi=PREVIEW_DPI)
                        name = f"{source.id}-p{i + 1}.jpg"
                        w, h = _save_preview(preview, pages_dir / name)
                        source.pages.append(SourcePage(index=i + 1, image=name, width=w, height=h, method="text"))
                        items.extend(result.items)
                        issues.extend(result.issues)
                        continue
                    progress("ocr", (i + 0.3) / total,
                             f"Reconhecendo o texto da página {i + 1} de {total} (pode levar alguns segundos)…")
                    image = render_page(pdf, i, dpi=300)
                    page_items, page_issues, page = self._ocr_image(pid, source, image, i + 1)
                    source.pages.append(page)
                    items.extend(page_items)
                    issues.extend(page_issues)
        finally:
            pdf.close()
        return items, issues

    def _image(self, pid: str, source: SourceFile, path: Path, progress: Progress, multi: bool):
        progress("ocr", 0.2, f"Reconhecendo o texto da imagem “{source.name}” (pode levar alguns segundos)…")
        with Image.open(path) as img:
            image = img.convert("RGB")
        page_items, page_issues, page = self._ocr_image(pid, source, image, 1)
        source.pages = [page]
        return page_items, page_issues

    def _ocr_image(self, pid: str, source: SourceFile, image: Image.Image, page_no: int):
        pages_dir = self.store.pages_dir(pid)
        status = ocr_status(self.settings)
        name = f"{source.id}-p{page_no}.jpg"
        full = f"{source.id}-p{page_no}-full.png"
        if not status.available:
            image.save(pages_dir / full, format="PNG")
            w, h = _save_preview(image, pages_dir / name)
            asset = self.store.save_asset_image(pid, image)
            media = MediaItem(
                kind="crop", asset_id=asset, width_cm=18.0,
                prov=Provenance(source_id=source.id, page=page_no, bbox=(0, 0, 1, 1), method="crop"),
                issues=[Issue(code="ocr_unavailable", severity="error",
                              message=("O reconhecimento de texto (OCR) não está instalado; a página foi mantida "
                                       f"como imagem. {status.message}"))],
            )
            page = SourcePage(index=page_no, image=name, full_image=full, width=w, height=h, method="failed",
                              note="OCR indisponível")
            return [media], [], page
        self.used_ocr = True
        try:
            result = ocr_page(image, source_id=source.id, page_no=page_no, settings=self.settings,
                              save_crop=self._save_crop(pid))
        except (OcrUnavailable, RuntimeError) as exc:
            log.warning("ocr failed on page %d: %s", page_no, exc.__class__.__name__)
            image.save(pages_dir / full, format="PNG")
            w, h = _save_preview(image, pages_dir / name)
            asset = self.store.save_asset_image(pid, image)
            media = MediaItem(
                kind="crop", asset_id=asset, width_cm=18.0,
                prov=Provenance(source_id=source.id, page=page_no, bbox=(0, 0, 1, 1), method="crop"),
                issues=[Issue(code="ocr_failed", severity="error",
                              message="Não foi possível ler esta página; ela foi mantida como imagem.")],
            )
            return [media], [], SourcePage(index=page_no, image=name, full_image=full, width=w, height=h,
                                           method="failed")
        result.display_image.save(pages_dir / full, format="PNG")
        w, h = _save_preview(result.display_image, pages_dir / name)
        page = SourcePage(index=page_no, image=name, full_image=full, width=w, height=h, method="ocr",
                          note=f"confiança média {result.mean_confidence:.0f}%" if result.mean_confidence else None)
        return result.items, result.issues, page


def crop_from_source(store: ProjectStore, pid: str, source: SourceFile, page_no: int,
                     bbox: tuple[float, float, float, float]) -> Image.Image:
    """Cut a region of an original page at print quality (vector PDFs are re-rendered at 300 dpi)."""
    page = next((p for p in source.pages if p.index == page_no), None)
    if page is None:
        raise FileNotFoundError("page")
    pages_dir = store.pages_dir(pid)
    x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in bbox)
    if x1 - x0 < 0.01 or y1 - y0 < 0.005:
        raise ValueError("A área selecionada é pequena demais.")
    if page.full_image and (pages_dir / page.full_image).exists():
        with Image.open(pages_dir / page.full_image) as img:
            image = img.convert("RGB")
    else:
        pdf_path = (store.source_path(pid, source) if source.kind == "pdf"
                    else pages_dir / f"{source.id}-original.pdf")
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            image = render_page(pdf, page_no - 1, dpi=300)
        finally:
            pdf.close()
    w, h = image.size
    return image.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
