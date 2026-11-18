"""Flat sequence of content items produced by every extractor.

DOCX, PDF and OCR extractors all emit the same items in reading order. The
structuring step then groups them into header, instructions, support texts and
questions, so segmentation rules are written once for every input format.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.models import Issue, Provenance, Run, TableCell, runs_text


@dataclass
class TextItem:
    runs: list[Run]
    prov: Provenance
    issues: list[Issue] = field(default_factory=list)
    # Label produced by Word automatic numbering ("1.", "a)") — not part of the text.
    list_label: str | None = None
    list_level: int = 0
    align: str | None = None
    style: str | None = None
    page_break_before: bool = False
    from_page_header: bool = False  # came from the Word page header (usually the school header)

    @property
    def text(self) -> str:
        return runs_text(self.runs)


@dataclass
class MediaItem:
    kind: str  # "image" | "crop" | "table"
    prov: Provenance
    asset_id: str | None = None
    width_cm: float | None = None
    rows: list[list[TableCell]] | None = None
    issues: list[Issue] = field(default_factory=list)
    from_page_header: bool = False
    table_borders: bool = True  # tables: grid lines visible in the original


@dataclass
class AnswerLinesItem:
    """Blank answer lines found in the original (underscores or drawn rules)."""

    count: int
    prov: Provenance
    blank: bool = False  # True for runs of empty paragraphs (space without lines)


@dataclass
class PageBreakItem:
    prov: Provenance
    new_source: bool = False  # first item of another uploaded file (it may repeat a header)


Item = TextItem | MediaItem | AnswerLinesItem | PageBreakItem
