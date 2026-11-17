"""Direct text extraction from digital PDFs (pdfplumber), page by page.

Pages with almost no text layer are reported as "needs OCR" so the pipeline can
send them to Tesseract. Bold/italic come from font names, underline from thin rules
drawn under words, superscript/subscript from size and baseline. Embedded images,
vector drawings and ruled tables are preserved as crops rendered from the page
itself (pypdfium2), so nothing is redrawn or re-generated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

from app.domain.models import Issue, Provenance
from app.extraction.items import AnswerLinesItem, Item, MediaItem, TextItem
from app.extraction.layout import Line, Word, lines_to_paragraphs, order_page_lines
from app.structuring.patterns import PAGE_NUMBER_RE, UNDERSCORE_LINE_RE

log = logging.getLogger(__name__)

RENDER_DPI = 200


@dataclass
class Region:
    bbox: tuple[float, float, float, float]  # x0, top, x1, bottom (PDF points)
    kind: str  # image | drawing | table
    answer_lines: int = 0


@dataclass
class PdfPageResult:
    page: int
    needs_ocr: bool
    items: list[Item] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def render_page(pdf: pdfium.PdfDocument, index: int, dpi: int = RENDER_DPI) -> Image.Image:
    page = pdf[index]
    try:
        bitmap = page.render(scale=dpi / 72)
        return bitmap.to_pil().convert("RGB")
    finally:
        page.close()


def _style_from_font(fontname: str) -> tuple[bool, bool]:
    name = (fontname or "").split("+")[-1].lower()
    bold = any(k in name for k in ("bold", "black", "heavy", "semibold", "demi", ",b"))
    italic = any(k in name for k in ("italic", "oblique", ",i"))
    return bold, italic


def _merge_boxes(boxes: list[tuple[float, float, float, float]], pad: float) -> list[tuple[float, float, float, float]]:
    merged = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out: list[list[float]] = []
        for box in merged:
            for other in out:
                if not (box[0] > other[2] + pad or box[2] < other[0] - pad or box[1] > other[3] + pad
                        or box[3] < other[1] - pad):
                    other[0], other[1] = min(other[0], box[0]), min(other[1], box[1])
                    other[2], other[3] = max(other[2], box[2]), max(other[3], box[3])
                    changed = True
                    break
            else:
                out.append(box)
        merged = out
    return [tuple(b) for b in merged]  # type: ignore[misc]


class PdfTextExtractor:
    def __init__(self, path: str, source_id: str, save_crop):
        self.path = path
        self.source_id = source_id
        self.save_crop = save_crop  # (pil_image) -> asset_id
        self._fraction_tops: list[float] = []

    def page_count(self) -> int:
        with pdfplumber.open(self.path) as pdf:
            return len(pdf.pages)

    def extract_page(self, plumber_page, pdfium_doc: pdfium.PdfDocument, index: int) -> PdfPageResult:
        page_no = index + 1
        width, height = float(plumber_page.width), float(plumber_page.height)
        chars = [c for c in plumber_page.chars if c.get("text", "").strip()]
        page_area = width * height
        image_area = sum(
            max(0.0, (im["x1"] - im["x0"]) * (im["bottom"] - im["top"])) for im in plumber_page.images
        )
        if len(chars) < 25 and (image_area > page_area * 0.3 or len(chars) == 0):
            return PdfPageResult(page=page_no, needs_ocr=True)

        result = PdfPageResult(page=page_no, needs_ocr=False)
        hrules = self._horizontal_rules(plumber_page)
        regions = self._figure_regions(plumber_page, hrules, width, height)

        words = self._words(plumber_page, hrules)
        # Words inside a preserved region belong to the crop, not to the text flow.
        inside: list[Word] = []
        flow: list[Word] = []
        for w in words:
            cx, cy = (w.x0 + w.x1) / 2, (w.top + w.bottom) / 2
            region = next((r for r in regions if r.bbox[0] <= cx <= r.bbox[2] and r.bbox[1] <= cy <= r.bbox[3]), None)
            (inside if region and region.kind != "answer_lines" else flow).append(w)

        flow = self._drop_page_artifacts(flow, height, result)
        text_x0 = min((w.x0 for w in flow), default=0.0)
        text_x1 = max((w.x1 for w in flow), default=width)
        lines, two_columns = order_page_lines(flow, page_no, width, text_x0, text_x1)
        if two_columns:
            result.issues.append(
                Issue(
                    code="two_columns",
                    severity="warning",
                    message=(
                        f"A página {page_no} estava em duas colunas. Lemos a coluna da esquerda e depois a da "
                        "direita — confira se a ordem das questões ficou certa."
                    ),
                )
            )

        paragraphs = lines_to_paragraphs(lines)
        anchors: list[tuple[float, int, Item]] = []
        for para in paragraphs:
            runs, joined = para.runs()
            text = "".join(r.text for r in runs)
            x0, y0, x1, y1 = para.bbox()
            prov = Provenance(
                source_id=self.source_id,
                page=page_no,
                bbox=(x0 / width, y0 / height, x1 / width, y1 / height),
                method="pdf_text",
            )
            if UNDERSCORE_LINE_RE.match(text):
                anchors.append((self._order_key(para.lines[0], two_columns), 0,
                                AnswerLinesItem(count=len(para.lines), prov=prov)))
                continue
            item = TextItem(runs=runs, prov=prov, align=para.align)
            if any(y0 - 2 <= top <= y1 + 2 for top in self._fraction_tops):
                item.issues.append(
                    Issue(
                        code="formula_linearised",
                        severity="warning",
                        message=("Havia uma fração ou fórmula empilhada, escrita aqui em uma linha (ex.: “3/4”). "
                                 "Compare com o original; se preferir, use “Recortar do original”."),
                    )
                )
            if joined:
                item.issues.append(
                    Issue(
                        code="hyphen_joined",
                        severity="info",
                        message="Palavras divididas no fim da linha foram reunidas: " + ", ".join(joined[:5]),
                        detail={"words": joined},
                    )
                )
            anchors.append((self._order_key(para.lines[0], two_columns), 0, item))

        scale = RENDER_DPI / 72
        page_image: Image.Image | None = None
        for region in regions:
            x0, top, x1, bottom = region.bbox
            prov = Provenance(
                source_id=self.source_id,
                page=page_no,
                bbox=(x0 / width, top / height, x1 / width, bottom / height),
                method="crop",
            )
            key_line = Line(words=[Word("", x0, top, x1, bottom)], page=page_no)
            key_line.column = self._region_column(region, lines)
            if region.kind == "answer_lines":
                anchors.append((self._order_key(key_line, two_columns), 1,
                                AnswerLinesItem(count=region.answer_lines, prov=prov)))
                continue
            if page_image is None:
                page_image = render_page(pdfium_doc, index)
            crop = page_image.crop((int(x0 * scale), int(top * scale), int(x1 * scale), int(bottom * scale)))
            asset = self.save_crop(crop)
            label = {"image": "imagem", "drawing": "figura", "table": "tabela"}[region.kind]
            media = MediaItem(kind="crop", prov=prov, asset_id=asset, width_cm=round((x1 - x0) / 72 * 2.54, 2))
            if region.kind == "table":
                media.issues.append(
                    Issue(
                        code="table_as_crop",
                        severity="warning",
                        message="Tabela preservada como imagem recortada do original (o texto dela não é editável).",
                    )
                )
            elif region.kind == "drawing":
                media.issues.append(
                    Issue(
                        code="drawing_as_crop",
                        severity="info",
                        message=f"Desenho preservado como {label} recortada do original.",
                    )
                )
            anchors.append((self._order_key(key_line, two_columns), 1, media))

        anchors.sort(key=lambda a: (a[0], a[1]))
        result.items = [a[2] for a in anchors]
        return result

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _order_key(line: Line, two_columns: bool) -> tuple[int, float]:
        # In two-column pages the left column (1) comes before the right column (2).
        column_rank = {0: 0, 1: 0, 2: 1}[line.column] if two_columns else 0
        return (column_rank, line.top)

    @staticmethod
    def _region_column(region: Region, lines: list[Line]) -> int:
        cx = (region.bbox[0] + region.bbox[2]) / 2
        candidates = [ln for ln in lines if ln.column in (1, 2)]
        if not candidates:
            return 0
        best = min(candidates, key=lambda ln: abs((ln.col_x0 + ln.col_x1) / 2 - cx))
        width = region.bbox[2] - region.bbox[0]
        if width > (best.col_x1 - best.col_x0) * 1.3:
            return 0
        return best.column

    def _horizontal_rules(self, page) -> list[tuple[float, float, float]]:
        rules = []
        for ln in page.lines:
            if abs(ln["top"] - ln["bottom"]) < 1.5:
                rules.append((ln["x0"], ln["x1"], (ln["top"] + ln["bottom"]) / 2))
        for rc in page.rects:
            if rc["height"] < 1.6 and rc["width"] > 4:
                rules.append((rc["x0"], rc["x1"], (rc["top"] + rc["bottom"]) / 2))
        return rules

    def _words(self, page, hrules) -> list[Word]:
        raw = page.extract_words(
            x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False, use_text_flow=False,
            extra_attrs=["fontname", "size"],
        )
        if not raw:
            return []
        sizes = sorted(w["size"] for w in raw)
        body_size = sizes[len(sizes) // 2]
        words: list[Word] = []
        for w in raw:
            bold, italic = _style_from_font(w.get("fontname", ""))
            words.append(Word(
                text=w["text"], x0=w["x0"], top=w["top"], x1=w["x1"], bottom=w["bottom"],
                bold=bold, italic=italic, size=w["size"],
            ))
        words, self._fraction_tops = linearise_fractions(words, hrules)
        mark_underlines(words, hrules)
        # Superscript / subscript: noticeably smaller and shifted relative to neighbours on the line.
        for word in words:
            if word.size < body_size * 0.8:
                neighbours = [
                    o for o in words
                    if o is not word and o.size >= body_size * 0.9 and abs(o.x1 - word.x0) < body_size * 1.5
                    and abs((o.top + o.bottom) / 2 - (word.top + word.bottom) / 2) < body_size
                ]
                if neighbours:
                    ref = neighbours[0]
                    centre = (word.top + word.bottom) / 2
                    if centre < (ref.top + ref.bottom) / 2 - body_size * 0.15:
                        word.sup = True
                    elif centre > (ref.top + ref.bottom) / 2 + body_size * 0.15:
                        word.sub = True
        return words

    def _figure_regions(self, page, hrules, width: float, height: float) -> list[Region]:
        regions: list[Region] = []
        min_side = 12.0
        for im in page.images:
            x0, top, x1, bottom = max(0, im["x0"]), max(0, im["top"]), min(width, im["x1"]), min(height, im["bottom"])
            if x1 - x0 >= min_side and bottom - top >= min_side:
                regions.append(Region((x0, top, x1, bottom), "image"))

        tables: list[Region] = []
        try:
            for table in page.find_tables():
                x0, top, x1, bottom = table.bbox
                if len(table.rows) >= 2:
                    tables.append(Region((x0, top, x1, bottom), "table"))
        except Exception:
            log.info("pdf: table detection failed on a page")
        regions.extend(tables)

        # Vector drawings (curves and non-rule shapes) clustered together.
        shapes: list[tuple[float, float, float, float]] = []
        for curve in page.curves:
            shapes.append((curve["x0"], curve["top"], curve["x1"], curve["bottom"]))
        for rc in page.rects:
            if rc["height"] >= 1.6 and rc["width"] >= 1.6 and rc["width"] < width * 0.95:
                shapes.append((rc["x0"], rc["top"], rc["x1"], rc["bottom"]))
        shapes = [s for s in shapes if not any(_inside(s, r.bbox) for r in regions)]
        for box in _merge_boxes(shapes, pad=6):
            if box[2] - box[0] >= 30 and box[3] - box[1] >= 30:
                regions.append(Region(box, "drawing"))

        # Groups of long parallel rules with nothing written on them are answer lines.
        long_rules = sorted(
            (r for r in hrules if r[1] - r[0] > width * 0.35
             and not any(reg.bbox[1] - 3 <= r[2] <= reg.bbox[3] + 3 and r[0] < reg.bbox[2] and r[1] > reg.bbox[0]
                         for reg in regions)),
            key=lambda r: r[2],
        )
        group: list[tuple[float, float, float]] = []
        for rule in long_rules + [(0, 0, 1e9)]:
            if group and (rule[2] - group[-1][2] > 40 or abs(rule[0] - group[0][0]) > 20):
                if len(group) >= 2:
                    x0 = min(r[0] for r in group)
                    x1 = max(r[1] for r in group)
                    box = (x0, group[0][2] - 14, x1, group[-1][2] + 2)
                    if not any(_inside(box, r.bbox) for r in regions):
                        regions.append(Region(box, "answer_lines", answer_lines=len(group)))
                group = []
            group.append(rule)
        return [r for r in regions if not any(o is not r and o.kind != "answer_lines" and _inside(r.bbox, o.bbox)
                                              for o in regions)]

    def _drop_page_artifacts(self, words: list[Word], height: float, result: PdfPageResult) -> list[Word]:
        """Remove page numbers printed in the top/bottom margins of the original layout."""
        margin_words = [w for w in words if w.top < height * 0.06 or w.bottom > height * 0.94]
        if not margin_words:
            return words
        from app.extraction.layout import group_lines

        drop: set[int] = set()
        for line in group_lines(margin_words, result.page):
            if PAGE_NUMBER_RE.match(line.text):
                drop.update(id(w) for w in line.words)
        if drop:
            result.issues.append(
                Issue(
                    code="page_number_removed",
                    severity="info",
                    message=f"O número de página do original (página {result.page}) foi removido; "
                    "a nova numeração é feita automaticamente.",
                )
            )
        return [w for w in words if id(w) not in drop]


def linearise_fractions(words: list[Word], rules: list[tuple[float, float, float]]) -> tuple[list[Word], list[float]]:
    """Replace stacked fractions (numerator / bar / denominator) by one inline word "a/b".

    Without this, numerator and denominator end up on separate lines and can even be
    attached to the wrong question. Returns the new word list and the y of each fraction.
    """
    used: set[int] = set()
    created: list[Word] = []
    tops: list[float] = []
    for rx0, rx1, y in rules:
        length = rx1 - rx0
        if length <= 0 or length > 80:
            continue

        def overlaps(w: Word, rx0: float = rx0, rx1: float = rx1) -> bool:
            return min(rx1, w.x1) - max(rx0, w.x0) > (w.x1 - w.x0) * 0.5

        above = [w for w in words if id(w) not in used and overlaps(w) and -1 <= y - w.bottom <= w.height * 0.6]
        below = [w for w in words if id(w) not in used and overlaps(w) and -1 <= w.top - y <= w.height * 0.6]
        if not above or not below:
            continue
        height = max(w.height for w in above + below)
        same_line = [w for w in words if id(w) not in used and w not in above and w not in below
                     and abs((w.top + w.bottom) / 2 - y) < height * 0.6]
        top = min((w.top for w in same_line), default=y - height / 2)
        bottom = max((w.bottom for w in same_line), default=y + height / 2)
        num = " ".join(w.text for w in sorted(above, key=lambda w: w.x0))
        den = " ".join(w.text for w in sorted(below, key=lambda w: w.x0))
        num = num if len(num) <= 2 else f"({num})"
        den = den if len(den) <= 2 else f"({den})"
        created.append(Word(text=f"{num}/{den}", x0=rx0, top=top, x1=rx1, bottom=bottom, size=above[0].size))
        used.update(id(w) for w in above + below)
        tops.append(top)
    if not created:
        return words, []
    return [w for w in words if id(w) not in used] + created, tops


def mark_underlines(words: list[Word], rules: list[tuple[float, float, float]]) -> None:
    """Flag words that sit on a rule drawn just below them.

    A rule only counts as an underline when words cover most of its length; long
    rules with empty space (answer lines, "Nome: ______") and fraction bars (text
    right below the rule) are ignored.
    """
    for rx0, rx1, y in rules:
        length = rx1 - rx0
        if length <= 0:
            continue
        if any(-1 <= w.top - y <= w.height * 0.4 and min(rx1, w.x1) - max(rx0, w.x0) > 0 for w in words):
            continue  # something written right below: a fraction bar or table rule, not an underline
        near = [w for w in words if w.bottom - w.height * 0.25 <= y <= w.bottom + w.height * 0.45]
        covered = 0.0
        for w in near:
            covered += max(0.0, min(rx1, w.x1) - max(rx0, w.x0))
        if covered < length * 0.7:
            continue
        for w in near:
            if min(rx1, w.x1) - max(rx0, w.x0) > (w.x1 - w.x0) * 0.7:
                w.underline = True


def _inside(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float], tol: float = 2) -> bool:
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and inner[2] <= outer[2] + tol
            and inner[3] <= outer[3] + tol)
