"""AI endpoints: create an exam, redo a question, reorganise a reading, and the older review helpers.

Long work runs as a background job (the hosting router cuts requests after 30 s); the UI
polls /job as it does for reading and rendering. Where exam content goes is stated once on
the screen (the provider's notice from /api/health) and each project can opt out ("ler sem IA").
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.ai.base import AiError, AiUnavailable
from app.ai.creation import run_generation, run_organize, run_regenerate
from app.ai.factory import get_model
from app.ai.gemini import Suggestion, review_ocr, suggest_structure
from app.api.deps import Services, api_error, load_project, services
from app.domain.models import GenerationRequest, JobState, Project
from app.jobs import JobBusy

router = APIRouter(prefix="/api/projects", tags=["ai"])


class AiRequest(BaseModel):
    consent: bool = False
    block_ids: list[str] | None = None


class UseAiRequest(BaseModel):
    use_ai: bool


class QuestionRequest(BaseModel):
    action: Literal["trocar", "mais_facil", "mais_dificil"]


def _require_ai(svc: Services) -> None:
    if not svc.settings.ai_enabled:
        raise api_error(503, "ai_unavailable", "A inteligência artificial ainda não foi configurada neste site.")


def _start(svc: Services, pid: str, kind: str, work, failure: str) -> JobState:
    try:
        return svc.jobs.start(pid, kind, work, failure)
    except JobBusy as exc:
        raise api_error(409, "job_running", "Já existe um processamento em andamento para esta prova.") from exc


@router.put("/{pid}/use-ai", response_model=Project)
def set_use_ai(pid: str, body: UseAiRequest, svc: Services = Depends(services)) -> Project:
    load_project(svc, pid)
    return svc.store.mutate(pid, lambda p: setattr(p, "use_ai", body.use_ai))


@router.post("/{pid}/ai/generate", response_model=JobState)
def ai_generate(pid: str, body: GenerationRequest, svc: Services = Depends(services)) -> JobState:
    load_project(svc, pid)
    _require_ai(svc)
    svc.store.mutate(pid, lambda p: setattr(p, "generation", body))
    return _start(svc, pid, "generate", lambda progress: run_generation(svc.store, svc.settings, pid, progress),
                  "Não foi possível criar a prova agora. O pedido ficou salvo: tente de novo em instantes.")


@router.post("/{pid}/ai/question/{block_id}", response_model=JobState)
def ai_question(pid: str, block_id: str, body: QuestionRequest, svc: Services = Depends(services)) -> JobState:
    project = load_project(svc, pid)
    _require_ai(svc)
    if not any(b.id == block_id and b.kind == "question" for b in project.exam.blocks):
        raise api_error(404, "block_not_found", "Questão não encontrada. Recarregue a página.")
    return _start(svc, pid, "generate",
                  lambda progress: run_regenerate(svc.store, svc.settings, pid, block_id, body.action, progress),
                  "Não foi possível refazer a questão agora. Nada foi alterado.")


@router.post("/{pid}/ai/organize", response_model=JobState)
def ai_organize(pid: str, svc: Services = Depends(services)) -> JobState:
    project = load_project(svc, pid)
    _require_ai(svc)
    if not project.exam.blocks:
        raise api_error(400, "empty", "Ainda não há conteúdo para organizar.")
    return _start(svc, pid, "organize", lambda progress: run_organize(svc.store, svc.settings, pid, progress),
                  "Não foi possível reorganizar agora. Nada foi alterado.")


# ------------------------------------------------------------ older helpers


def _model(svc: Services, body: AiRequest):
    if not body.consent:
        raise api_error(400, "consent_required", "Para usar a IA é preciso confirmar o envio do conteúdo da prova.")
    try:
        return get_model(svc.settings, "read")
    except AiUnavailable as exc:
        raise api_error(503, "ai_unavailable", str(exc)) from exc


@router.post("/{pid}/ai/structure", response_model=list[Suggestion])
def ai_structure(pid: str, body: AiRequest, svc: Services = Depends(services)) -> list[Suggestion]:
    project = load_project(svc, pid)
    model = _model(svc, body)
    try:
        return suggest_structure(project.exam, model)
    except AiError as exc:
        raise api_error(502, "ai_invalid", str(exc)) from exc
    except Exception as exc:
        raise api_error(502, "ai_failed", "A IA não respondeu agora. Tente mais tarde; nada foi alterado.") from exc


@router.post("/{pid}/ai/ocr-review", response_model=list[Suggestion])
def ai_ocr_review(pid: str, body: AiRequest, svc: Services = Depends(services)) -> list[Suggestion]:
    project = load_project(svc, pid)
    model = _model(svc, body)
    pages_dir = svc.store.pages_dir(pid)
    wanted = set(body.block_ids or [])
    pages: set[tuple[str, int]] = set()
    for block in project.exam.blocks:
        if wanted and block.id not in wanted:
            continue
        for part in block.parts:
            if part.provenance.method == "ocr" and part.provenance.source_id and part.provenance.page:
                pages.add((part.provenance.source_id, part.provenance.page))
    images: list[tuple[str, bytes]] = []
    for source_id, page_no in sorted(pages)[:6]:
        path = pages_dir / f"{source_id}-p{page_no}.jpg"
        if path.exists():
            images.append(("image/jpeg", path.read_bytes()))
    try:
        return review_ocr(project.exam, images, model, wanted or None)
    except AiError as exc:
        raise api_error(502, "ai_invalid", str(exc)) from exc
    except Exception as exc:
        raise api_error(502, "ai_failed", "A IA não respondeu agora. Tente mais tarde; nada foi alterado.") from exc
