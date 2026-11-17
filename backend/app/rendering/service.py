"""Render pipeline: exam -> DOCX -> PDF -> page previews + checks.

Each render is stored under ``renders/<render_id>/`` where the id derives from the
exam content, so an unchanged exam is never rendered twice and the PDF a teacher
downloads is exactly the one whose pages were previewed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import pypdfium2 as pdfium
from pydantic import BaseModel

from app.config import Settings
from app.importing.storage import ProjectStore, write_json
from app.rendering.checks import CheckResult, run_checks
from app.rendering.converter import docx_to_pdf
from app.rendering.docx_writer import DocxRenderer, safe_filename
from app.templates_store import TemplateStore

Progress = Callable[[str, float, str], None]
PREVIEW_SCALE = 1.4  # ~100 dpi
KEEP_RENDERS = 4


class RenderResult(BaseModel):
    render_id: str
    revision: int
    pages: int
    checks: list[CheckResult]
    docx_name: str
    pdf_name: str
    created_at: float


def render_key(project_revision: int, exam_json: str, template_json: str) -> str:
    digest = hashlib.sha256((exam_json + template_json).encode("utf-8")).hexdigest()[:12]
    return f"r{digest}"


def render_project(store: ProjectStore, templates: TemplateStore, settings: Settings, pid: str,
                   progress: Progress) -> RenderResult:
    project = store.load(pid)
    exam = project.exam
    template = templates.get(exam.header.template_id)
    exam_json = exam.model_dump_json(exclude={"changes", "document_issues"})
    render_id = render_key(project.revision, exam_json, template.model_dump_json())
    out_dir = store.renders_dir(pid) / render_id
    result_file = out_dir / "result.json"
    if result_file.exists():
        return RenderResult.model_validate_json(result_file.read_text(encoding="utf-8"))

    out_dir.mkdir(parents=True, exist_ok=True)
    progress("docx", 0.1, "Montando o documento Word…")
    docx_path = out_dir / "prova.docx"
    DocxRenderer(exam, template, store.assets_dir(pid), templates.logo_path(template), out_dir / "work").render(
        docx_path)

    progress("pdf", 0.35, "Gerando o PDF para impressão…")
    pdf_tmp = docx_to_pdf(docx_path, out_dir / "convert", settings)
    pdf_path = out_dir / "prova.pdf"
    pdf_tmp.replace(pdf_path)
    shutil.rmtree(out_dir / "convert", ignore_errors=True)
    shutil.rmtree(out_dir / "work", ignore_errors=True)

    progress("preview", 0.75, "Preparando a prévia das páginas…")
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        count = len(pdf)
        for i in range(count):
            page = pdf[i]
            try:
                page.render(scale=PREVIEW_SCALE).to_pil().convert("RGB").save(
                    out_dir / f"page-{i + 1}.jpg", format="JPEG", quality=85)
            finally:
                page.close()
    finally:
        pdf.close()

    progress("checks", 0.9, "Conferindo se nada ficou de fora…")
    checks = run_checks(exam, str(pdf_path))
    result = RenderResult(
        render_id=render_id, revision=project.revision, pages=count, checks=checks,
        docx_name=safe_filename(exam, "docx"), pdf_name=safe_filename(exam, "pdf"), created_at=time.time(),
    )
    write_json(result_file, json.loads(result.model_dump_json()))
    store.mutate(pid, lambda p: setattr(p, "last_render_id", render_id), bump=False)
    _prune(store.renders_dir(pid), keep=render_id)
    return result


def load_render(store: ProjectStore, pid: str, render_id: str) -> RenderResult | None:
    if not render_id.startswith("r") or not render_id[1:].isalnum():
        return None
    path = store.renders_dir(pid) / render_id / "result.json"
    if not path.exists():
        return None
    return RenderResult.model_validate_json(path.read_text(encoding="utf-8"))


def _prune(renders_dir: Path, keep: str) -> None:
    dirs = sorted((d for d in renders_dir.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)
    for old in dirs[KEEP_RENDERS:]:
        if old.name != keep:
            shutil.rmtree(old, ignore_errors=True)
