"""Structure text pasted or typed by the teacher (no file involved).

The browser sends paragraphs as formatted runs (bold/italic/underline survive a paste
from Word or Google Docs). They go through the same segmentation rules as files.
"""

from __future__ import annotations

import unicodedata

from app.domain.models import Provenance, Run, merge_runs
from app.extraction.items import AnswerLinesItem, Item, TextItem
from app.structuring.patterns import UNDERSCORE_LINE_RE


def pasted_items(paragraphs: list[list[Run]]) -> list[Item]:
    items: list[Item] = []
    blank_run = 0
    for runs in paragraphs:
        runs = merge_runs([r.model_copy(update={"text": unicodedata.normalize("NFC", r.text)}) for r in runs])
        text = "".join(r.text for r in runs)
        if not text.strip():
            blank_run += 1
            continue
        if blank_run >= 3 and items:
            items.append(AnswerLinesItem(count=blank_run, prov=Provenance(method="paste"), blank=True))
        blank_run = 0
        if UNDERSCORE_LINE_RE.match(text):
            if items and isinstance(items[-1], AnswerLinesItem) and not items[-1].blank:
                items[-1].count += 1
            else:
                items.append(AnswerLinesItem(count=1, prov=Provenance(method="paste")))
            continue
        items.append(TextItem(runs=runs, prov=Provenance(method="paste")))
    return items


def plain_text_paragraphs(text: str) -> list[list[Run]]:
    return [[Run(text=line)] for line in text.replace("\r\n", "\n").split("\n")]
