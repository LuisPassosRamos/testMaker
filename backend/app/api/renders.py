"""Rendering (DOCX -> PDF -> preview), downloads and sending to the print inbox."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import Services, api_error, load_project, services
from app.domain.models import JobState, SendRecord
from app.jobs import JobBusy
from app.mail.mailer import MailError, PrintRequest, build_message, send_message
from app.rendering.converter import converter_status
from app.rendering.service import RenderResult, load_render, render_key, render_project

router = APIRouter(prefix="/api/projects", tags=["render"])


class RenderStatus(BaseModel):
    current_key: str
    render: RenderResult | None
    up_to_date: bool


class SendRequest(BaseModel):
    render_id: str
    request: PrintRequest


def _current_key(svc: Services, pid: str) -> str:
    project = load_project(svc, pid)
    template = svc.templates.get(project.exam.header.template_id)
    exam_json = project.exam.model_dump_json(exclude={"changes", "document_issues"})
    return render_key(project.revision, exam_json, template.model_dump_json())


@router.post("/{pid}/render", response_model=JobState)
def start_render(pid: str, svc: Services = Depends(services)) -> JobState:
    load_project(svc, pid)
    status = converter_status(svc.settings)
    if not status.available:
        raise api_error(503, "no_converter", status.message)
    try:
        return svc.jobs.start(
            pid, "render", lambda progress: render_project(svc.store, svc.templates, svc.settings, pid, progress),
            "Não conseguimos gerar o PDF. Suas alterações estão salvas; tente de novo em instantes.",
        )
    except JobBusy as exc:
        raise api_error(409, "job_running", "Já existe um processamento em andamento para esta prova.") from exc


@router.get("/{pid}/render", response_model=RenderStatus)
def render_status(pid: str, svc: Services = Depends(services)) -> RenderStatus:
    project = load_project(svc, pid)
    key = _current_key(svc, pid)
    result = load_render(svc.store, pid, project.last_render_id) if project.last_render_id else None
    return RenderStatus(current_key=key, render=result, up_to_date=bool(result and result.render_id == key))


@router.get("/{pid}/renders/{rid}/pages/{number}.jpg")
def render_page(pid: str, rid: str, number: int, svc: Services = Depends(services)) -> FileResponse:
    result = load_render(svc.store, pid, rid)
    if result is None or not 1 <= number <= result.pages:
        raise api_error(404, "not_found", "Página não encontrada.")
    path = svc.store.renders_dir(pid) / rid / f"page-{number}.jpg"
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400, immutable"})


@router.get("/{pid}/renders/{rid}/download/{kind}")
def download(pid: str, rid: str, kind: Literal["pdf", "docx"], inline: bool = False,
             svc: Services = Depends(services)) -> FileResponse:
    result = load_render(svc.store, pid, rid)
    if result is None:
        raise api_error(404, "not_found", "Arquivo não encontrado. Gere a prévia novamente.")
    path = svc.store.renders_dir(pid) / rid / f"prova.{kind}"
    name = result.pdf_name if kind == "pdf" else result.docx_name
    media = "application/pdf" if kind == "pdf" else (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    disposition = "inline" if inline and kind == "pdf" else "attachment"
    return FileResponse(path, media_type=media, headers={
        "Content-Disposition": f"{disposition}; filename=\"prova.{kind}\"; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "private, no-store",
    })


@router.get("/{pid}/renders/{rid}/answer-key/{kind}")
def download_answer_key(pid: str, rid: str, kind: Literal["pdf", "docx"], inline: bool = False,
                        svc: Services = Depends(services)) -> FileResponse:
    result = load_render(svc.store, pid, rid)
    name = (result.answer_key_pdf_name if kind == "pdf" else result.answer_key_docx_name) if result else ""
    path = svc.store.renders_dir(pid) / rid / f"gabarito.{kind}"
    if result is None or not name or not path.exists():
        raise api_error(404, "not_found", "Gabarito não encontrado. Gere a prévia novamente.")
    media = "application/pdf" if kind == "pdf" else (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    disposition = "inline" if inline and kind == "pdf" else "attachment"
    return FileResponse(path, media_type=media, headers={
        "Content-Disposition": f"{disposition}; filename=\"gabarito.{kind}\"; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "private, no-store",
    })


@router.post("/{pid}/send")
def send_to_print(pid: str, body: SendRequest, request: Request, svc: Services = Depends(services)) -> dict:
    project = load_project(svc, pid)
    client = request.client.host if request.client else "?"
    limiter = svc.limiter
    if not limiter.allow(f"send:{client}", svc.settings.max_sends_per_hour, 3600) or not limiter.allow(
            f"send-project:{pid}", 6, 3600):
        raise api_error(429, "too_many_sends",
                        "Muitos envios em pouco tempo. Aguarde um pouco ou fale com a secretaria.")
    result = load_render(svc.store, pid, body.render_id)
    if result is None:
        raise api_error(404, "render_not_found", "Gere a prévia antes de enviar.")
    if result.render_id != _current_key(svc, pid):
        raise api_error(409, "render_outdated",
                        "A prova mudou depois da última prévia. Atualize a prévia e confira antes de enviar.")
    folder = svc.store.renders_dir(pid) / result.render_id
    try:
        message = build_message(project.exam, body.request, folder / "prova.pdf", folder / "prova.docx",
                                result.pdf_name, result.docx_name, svc.settings)
        outcome = send_message(message, svc.settings)
    except MailError as exc:
        raise api_error(502, "mail_failed", str(exc)) from exc
    record = SendRecord(mode=outcome.mode, to=outcome.to, render_id=result.render_id)  # type: ignore[arg-type]
    svc.store.mutate(pid, lambda p: p.sent.append(record), bump=False)
    return {"mode": outcome.mode, "to": outcome.to, "stored_as": outcome.stored_as, "at": record.at.isoformat()}
