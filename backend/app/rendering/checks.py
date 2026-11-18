"""Automatic checks on the exported PDF.

They compare the PDF with the structured exam to catch problems before printing:
missing or duplicated questions/alternatives, wrong order, text outside the margins,
non-A4 pages, a question number stranded at the bottom of a page and questions that
continue on the next page. Results are shown to the user next to the preview.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pdfplumber
from pydantic import BaseModel

from app.domain.models import Exam, runs_text
from app.extraction.layout import Word, order_page_lines

A4 = (595.28, 841.89)
# School standard (study of the school's exams): at most 4 A4 pages for the student and at most
# 3 images per page, only one of them large (more than a third of the usable height).
MAX_PAGES = 4
MAX_IMAGES_PER_PAGE = 3
# Questions that depend on reading colours in an image ("Lata de lixo azul – plástico"). Words for the
# student's own colouring ("pinte", "colorir") are left out: the drawing is still printed for them.
_COLOR_WORDS = re.compile(
    r"\b(cor|cores|azul|azuis|vermelh[oa]s?|amarel[oa]s?|verde|verdes|laranja|roxo|roxa|rosa|marrom)\b"
    r"|\blegenda\b.{0,60}\b(colorir|pintar|pinte)\b|\b(colorir|pintar|pinte)\b.{0,60}\blegenda\b",
    re.IGNORECASE,
)


class CheckResult(BaseModel):
    code: str
    severity: str  # info | warning | error
    message: str
    page: int | None = None
    block_id: str | None = None


@dataclass
class _Token:
    text: str
    page: int
    top: float
    bottom: float
    x0: float
    x1: float


@dataclass
class _Located:
    block_id: str
    label: str
    index: int | None = None
    count: int = 0
    pages: list[int] = field(default_factory=list)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^\w]", "", text)


def _signature(words: list[str], size: int = 6) -> list[str]:
    return [w for w in (_norm(x) for x in words) if w][:size]


def _find_all(tokens: list[str], probe: list[str]) -> list[int]:
    if not probe:
        return []
    hits = []
    n = len(probe)
    for i in range(len(tokens) - n + 1):
        if tokens[i] == probe[0] and tokens[i:i + n] == probe:
            hits.append(i)
    return hits


def run_checks(exam: Exam, pdf_path: str) -> list[CheckResult]:
    results: list[CheckResult] = []
    layout = exam.layout
    tokens: list[_Token] = []
    page_bottoms: dict[int, float] = {}
    images_per_page: dict[int, list[float]] = {}  # heights of the images on each page
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        for pno, page in enumerate(pdf.pages, 1):
            w, h = float(page.width), float(page.height)
            image_bottom = 0.0
            for image in page.images:
                small_logo = pno == 1 and float(image["top"]) < h * 0.2 and float(image["width"]) < 85
                if not small_logo and float(image["width"]) > 20 and float(image["height"]) > 20:
                    images_per_page.setdefault(pno, []).append(float(image["height"]))
                    image_bottom = max(image_bottom, float(image["bottom"]))
            if abs(w - A4[0]) > 3 or abs(h - A4[1]) > 3:
                results.append(CheckResult(code="not_a4", severity="error", page=pno,
                                           message=f"A página {pno} não está no tamanho A4."))
            left = layout.margins_cm.left / 2.54 * 72
            right = w - layout.margins_cm.right / 2.54 * 72
            top = layout.margins_cm.top / 2.54 * 72
            footer_zone = h - layout.margins_cm.bottom / 2.54 * 72
            outside = 0
            body_bottom = 0.0
            body_words: list[Word] = []
            for word in page.extract_words(x_tolerance=1.5, y_tolerance=2):
                if word["top"] >= footer_zone - 2:
                    continue  # footer (page number) lives in the bottom margin by design
                if word["x0"] < left - 4 or word["x1"] > right + 4 or word["top"] < top - 4:
                    outside += 1
                body_bottom = max(body_bottom, word["bottom"])
                body_words.append(Word(word["text"], word["x0"], word["top"], word["x1"], word["bottom"]))
            # Read in the same order a student would (left column, then right column).
            lines, _ = order_page_lines(body_words, pno, w, left, right)
            for line in lines:
                for word in line.words:
                    for piece in word.text.split():
                        tokens.append(_Token(piece, pno, word.top, word.bottom, word.x0, word.x1))
            # Images below the last line of text also fill the page: a question followed by its
            # picture is not stranded at the bottom.
            page_bottoms[pno] = max(body_bottom, image_bottom if image_bottom < footer_zone else 0.0)
            if outside:
                results.append(CheckResult(code="outside_margin", severity="error", page=pno,
                                           message=f"Há conteúdo fora das margens na página {pno}."))
    normalised = [_norm(t.text) for t in tokens]
    index_map = [i for i, t in enumerate(normalised) if t]
    compact = [normalised[i] for i in index_map]

    questions = [b for b in exam.blocks if b.kind == "question"]
    located: list[_Located] = []
    for block in questions:
        label = (block.number or "").strip()
        first_text = next((runs_text(p.runs) for p in block.parts if p.kind == "paragraph" and runs_text(p.runs).strip()),
                          "")
        probe = _signature(label.split() + first_text.split(), 7)
        loc = _Located(block_id=block.id, label=label or "?")
        hits = _find_all(compact, probe) if len(probe) >= 2 else []
        loc.count = len(hits)
        if hits:
            loc.index = hits[0]
            loc.pages = [tokens[index_map[h]].page for h in hits]
        located.append(loc)

        if probe and len(probe) >= 2 and not hits:
            results.append(CheckResult(code="question_missing", severity="error", block_id=block.id,
                                       message=f"Não encontramos a questão {label} no PDF. Confira a prévia."))
        elif len(hits) > 1:
            results.append(CheckResult(code="question_duplicated", severity="warning", block_id=block.id,
                                       message=f"O início da questão {label} aparece {len(hits)} vezes no PDF."))

        # Alternatives: each must appear once, after the question start.
        start = loc.index or 0
        for part in block.parts:
            if part.kind != "alternative":
                continue
            alt_probe = _signature((part.label or "").split() + runs_text(part.runs).split(), 5)
            if len(alt_probe) < 2:
                continue
            alt_hits = [h for h in _find_all(compact, alt_probe) if h >= start]
            if not alt_hits:
                results.append(CheckResult(
                    code="alternative_missing", severity="error", block_id=block.id,
                    message=f"A alternativa “{part.label} {runs_text(part.runs)[:30]}” da questão {label} "
                            "não foi encontrada no PDF.",
                ))

    found = [loc for loc in located if loc.index is not None]
    for prev, cur in zip(found, found[1:], strict=False):
        if cur.index is not None and prev.index is not None and cur.index < prev.index:
            results.append(CheckResult(code="order_changed", severity="error", block_id=cur.block_id,
                                       message=f"A questão {cur.label} aparece antes da {prev.label} no PDF."))

    # Pagination quality: question number stranded at the bottom, questions crossing pages.
    for i, loc in enumerate(found):
        token = tokens[index_map[loc.index]]  # type: ignore[index]
        bottom = page_bottoms.get(token.page, 0)
        line_tokens = [t for t in tokens if t.page == token.page and abs(t.top - token.top) < 2]
        is_last_line = token.bottom >= bottom - 2 and all(t.bottom >= bottom - 14 for t in line_tokens)
        if is_last_line and token.page < max(page_bottoms):
            results.append(CheckResult(
                code="orphan_question_start", severity="warning", page=token.page, block_id=loc.block_id,
                message=f"A questão {loc.label} começa na última linha da página {token.page}. "
                        "Considere “Começar em nova página”.",
            ))
        nxt = found[i + 1] if i + 1 < len(found) else None
        end_page = tokens[index_map[nxt.index]].page if nxt and nxt.index is not None else None  # type: ignore[index]
        if end_page is not None and end_page > token.page + 1:
            results.append(CheckResult(
                code="question_spans_pages", severity="info", page=token.page, block_id=loc.block_id,
                message=f"A questão {loc.label} ocupa mais de uma página.",
            ))

    results += _school_standard_checks(exam, page_count, images_per_page, found, tokens, index_map)
    return results


def _school_standard_checks(exam: Exam, page_count: int, images_per_page: dict[int, list[float]],
                            found: list[_Located], tokens: list[_Token], index_map: list[int]) -> list[CheckResult]:
    """Limits from the study of the school's exams: pages, images per page, letter size, colours."""
    results: list[CheckResult] = []
    layout = exam.layout
    if page_count > MAX_PAGES:
        late = [loc.label for loc in found
                if loc.index is not None and tokens[index_map[loc.index]].page > MAX_PAGES]
        where = f" A partir da página {MAX_PAGES + 1} estão: {', '.join(late)}." if late else ""
        results.append(CheckResult(
            code="page_limit", severity="warning",
            message=f"A prova ficou com {page_count} páginas; o padrão da escola é de até {MAX_PAGES} páginas A4."
                    f"{where} Experimente o estilo “Econômico”, 2 colunas ou revise o espaço das respostas.",
        ))
    usable = (29.7 - layout.margins_cm.top - layout.margins_cm.bottom) / 2.54 * 72
    for pno, heights in sorted(images_per_page.items()):
        if len(heights) > MAX_IMAGES_PER_PAGE:
            results.append(CheckResult(
                code="images_per_page", severity="warning", page=pno,
                message=f"A página {pno} tem {len(heights)} imagens; o padrão da escola é de no máximo "
                        f"{MAX_IMAGES_PER_PAGE} por página.",
            ))
        large = sum(1 for height in heights if height > usable / 3)
        if large > 1:
            results.append(CheckResult(
                code="large_images_per_page", severity="info", page=pno,
                message=f"A página {pno} tem {large} imagens grandes; o padrão é no máximo uma grande por página.",
            ))

    year = re.match(r"\s*(\d)", exam.meta.ano)
    if year:
        n = int(year.group(1))
        minimum = 12 if n <= 5 else 11
        if layout.font_size < minimum:
            results.append(CheckResult(
                code="font_too_small", severity="warning",
                message=f"Para o {exam.meta.ano}, use letra de pelo menos {minimum} pt "
                        f"(está em {layout.font_size:g} pt).",
            ))
        elif n <= 2 and layout.font_size < 14:
            results.append(CheckResult(
                code="font_early_years", severity="info",
                message="Para 1º e 2º ano, a pesquisa da escola recomenda letra de 14 pt quando couber.",
            ))

    if layout.grayscale:
        for block in exam.blocks:
            if block.kind not in ("question", "support_text"):
                continue
            has_image = any(p.kind in ("image", "crop") for p in block.parts)
            text = " ".join(runs_text(p.runs) for p in block.parts if p.kind in ("paragraph", "alternative"))
            if has_image and _COLOR_WORDS.search(text):
                what = f"A questão {block.number}" if block.kind == "question" and block.number else "Um texto de apoio"
                results.append(CheckResult(
                    code="colour_in_grayscale", severity="warning", block_id=block.id,
                    message=f"{what} fala de cores e tem imagem, mas a impressão está em preto e branco. "
                            "Confira se a imagem continua compreensível ou imprima colorido.",
                ))
    return results
