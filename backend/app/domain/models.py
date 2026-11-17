"""Structured representation of an exam, activity or review.

This intermediate model sits between extraction and rendering. It is designed for
fidelity: text is kept as runs with their original formatting, question and
alternative labels are stored exactly as written (never renumbered), every part
remembers where it came from (provenance) and what was extracted (``original_runs``)
so manual corrections stay visible, and doubts are recorded as issues instead of
being silently "fixed".
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def new_id(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_hex(5)}"


def utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- text


class Run(BaseModel):
    """A span of text with uniform formatting. ``\n`` inside text is a line break."""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    sup: bool = False
    sub: bool = False

    def same_style(self, other: Run) -> bool:
        return (self.bold, self.italic, self.underline, self.sup, self.sub) == (
            other.bold,
            other.italic,
            other.underline,
            other.sup,
            other.sub,
        )


def runs_text(runs: list[Run]) -> str:
    return "".join(r.text for r in runs)


def merge_runs(runs: list[Run]) -> list[Run]:
    """Join adjacent runs with the same formatting and drop empty ones."""
    merged: list[Run] = []
    for run in runs:
        if not run.text:
            continue
        if merged and merged[-1].same_style(run):
            merged[-1] = merged[-1].model_copy(update={"text": merged[-1].text + run.text})
        else:
            merged.append(run.model_copy())
    return merged


# ------------------------------------------------------------------- provenance


Method = Literal["docx", "pdf_text", "ocr", "paste", "manual", "crop"]


class Provenance(BaseModel):
    source_id: str | None = None
    page: int | None = None  # 1-based page in the original
    # Normalised (0..1) box on the page image: x0, y0, x1, y1
    bbox: tuple[float, float, float, float] | None = None
    method: Method = "manual"
    confidence: float | None = None  # OCR mean confidence (0-100)


IssueSeverity = Literal["info", "warning", "error"]


class Issue(BaseModel):
    """Something the reviewer should look at. Never auto-resolved."""

    id: str = Field(default_factory=lambda: new_id("i"))
    code: str
    severity: IssueSeverity = "warning"
    message: str
    resolved: bool = False
    detail: dict[str, Any] | None = None


# ------------------------------------------------------------------------ parts


class AnswerSpace(BaseModel):
    # lines: ruled lines · box: bordered box · blank: empty space (e.g. for calculations)
    kind: Literal["none", "lines", "box", "blank"] = "none"
    lines: int = Field(5, ge=1, le=40)
    height_cm: float = Field(4.0, ge=1.0, le=25.0)


class CellContent(BaseModel):
    runs: list[Run] = Field(default_factory=list)
    asset_id: str | None = None
    width_cm: float | None = None


class TableCell(BaseModel):
    content: list[CellContent] = Field(default_factory=list)
    colspan: int = 1
    vmerge: Literal["restart", "continue"] | None = None


PartKind = Literal["paragraph", "alternative", "image", "table", "crop", "answer_space"]
Align = Literal["left", "center", "right", "justify"]


class Part(BaseModel):
    id: str = Field(default_factory=lambda: new_id("p"))
    kind: PartKind = "paragraph"
    runs: list[Run] = Field(default_factory=list)
    # Snapshot of what was extracted. ``None`` means the part was created by the user.
    original_runs: list[Run] | None = None
    label: str | None = None  # alternative label exactly as written: "a)", "(B)", "( )"
    original_label: str | None = None
    align: Align | None = None
    asset_id: str | None = None  # image / crop
    width_cm: float | None = None  # natural width of an image in the original
    width_pct: int = Field(100, ge=10, le=100)  # user scale relative to the natural width
    rows: list[list[TableCell]] | None = None  # table
    answer: AnswerSpace | None = None  # answer_space parts (answer lines inside a question)
    provenance: Provenance = Field(default_factory=Provenance)
    issues: list[Issue] = Field(default_factory=list)


# ----------------------------------------------------------------------- blocks


BlockKind = Literal["original_header", "instructions", "support_text", "question", "free", "page_break"]


class Block(BaseModel):
    id: str = Field(default_factory=lambda: new_id("b"))
    kind: BlockKind = "free"
    number: str | None = None  # question label exactly as written: "1)", "01.", "Questão 3 -"
    original_number: str | None = None
    parts: list[Part] = Field(default_factory=list)
    answer_space: AnswerSpace = Field(default_factory=AnswerSpace)
    # Layout overrides (shown as "diagramação" changes)
    page_break_before: bool = False
    allow_split: bool = False
    alternatives_inline: bool = False
    issues: list[Issue] = Field(default_factory=list)
    reviewed: bool = False


# ------------------------------------------------------------------- settings


DocType = Literal["prova", "atividade", "revisao"]

DOC_TYPE_LABELS: dict[str, str] = {"prova": "Prova", "atividade": "Atividade", "revisao": "Revisão"}


class ExamMeta(BaseModel):
    doc_type: DocType = "prova"
    title: str = ""
    disciplina: str = ""
    unidade: str = ""
    ano: str = ""
    turma: str = ""
    turno: str = ""
    professor: str = ""
    data: str = ""  # free text as typed (dd/mm/aaaa)
    valor: str = ""
    data_entrega: str = ""


class Margins(BaseModel):
    top: float = Field(1.5, ge=0.8, le=4)
    bottom: float = Field(1.5, ge=0.8, le=4)
    left: float = Field(1.8, ge=0.8, le=4)
    right: float = Field(1.8, ge=0.8, le=4)


class LayoutSettings(BaseModel):
    preset: Literal["padrao", "economico", "ampliado", "personalizado"] = "padrao"
    margins_cm: Margins = Field(default_factory=Margins)
    font_family: str = "Arial"
    font_size: float = Field(12, ge=10, le=20)
    line_spacing: float = Field(1.15, ge=1.0, le=2.0)
    question_spacing_pt: int = Field(10, ge=0, le=36)
    columns: Literal[1, 2] = 1
    column_separator: bool = True
    answer_line_spacing_cm: float = Field(0.8, ge=0.5, le=1.5)
    page_numbers: bool = True
    footer_text: str = ""
    grayscale: bool = True
    bold_question_numbers: bool = True
    justify_text: bool = False


class HeaderConfig(BaseModel):
    # template: school header; original: keep the header found in the file; both; none
    mode: Literal["template", "original", "both", "none"] = "template"
    template_id: str = "padrao-madre"


class ChangeEntry(BaseModel):
    id: str = Field(default_factory=lambda: new_id("c"))
    at: datetime = Field(default_factory=utcnow)
    kind: Literal["content", "layout", "structure"]
    summary: str
    block_id: str | None = None


class Exam(BaseModel):
    meta: ExamMeta = Field(default_factory=ExamMeta)
    blocks: list[Block] = Field(default_factory=list)
    layout: LayoutSettings = Field(default_factory=LayoutSettings)
    header: HeaderConfig = Field(default_factory=HeaderConfig)
    changes: list[ChangeEntry] = Field(default_factory=list)
    document_issues: list[Issue] = Field(default_factory=list)


# ---------------------------------------------------------------------- project


SourceKind = Literal["docx", "pdf", "image"]


class SourcePage(BaseModel):
    index: int  # 1-based
    image: str | None = None  # preview file name inside pages/
    full_image: str | None = None  # full-resolution processed image (OCR pages), used for crops
    width: int | None = None
    height: int | None = None
    method: Literal["pending", "text", "ocr", "render", "failed"] = "pending"
    note: str | None = None


class SourceFile(BaseModel):
    id: str = Field(default_factory=lambda: new_id("s"))
    name: str  # display name (sanitised)
    kind: SourceKind
    stored_as: str
    size: int
    pages: list[SourcePage] = Field(default_factory=list)
    extracted: bool = False


JobStatus = Literal["idle", "running", "done", "failed"]


class JobState(BaseModel):
    id: str = Field(default_factory=lambda: new_id("j"))
    kind: Literal["extract", "render"] = "extract"
    status: JobStatus = "idle"
    stage: str = ""
    message: str = ""
    progress: float = 0.0
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SendRecord(BaseModel):
    at: datetime = Field(default_factory=utcnow)
    mode: Literal["outbox", "smtp"]
    to: str
    render_id: str


class Project(BaseModel):
    id: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    revision: int = 0
    start_mode: Literal["upload", "paste", "blank"] = "upload"
    exam: Exam = Field(default_factory=Exam)
    sources: list[SourceFile] = Field(default_factory=list)
    job: JobState | None = None
    last_render_id: str | None = None
    sent: list[SendRecord] = Field(default_factory=list)
