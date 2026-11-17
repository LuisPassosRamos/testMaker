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
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            w, h = float(page.width), float(page.height)
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
            page_bottoms[pno] = body_bottom
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
    return results
