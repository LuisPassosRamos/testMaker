"""Optional AI review endpoints. Each call requires explicit consent from the user."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.ai.gemini import AiError, AiUnavailable, GeminiModel, Suggestion, review_ocr, suggest_structure
from app.api.deps import Services, api_error, load_project, services

router = APIRouter(prefix="/api/projects", tags=["ai"])


class AiRequest(BaseModel):
    consent: bool = False
    block_ids: list[str] | None = None


def _model(svc: Services, body: AiRequest) -> GeminiModel:
    if not body.consent:
        raise api_error(400, "consent_required",
                        "Para usar a IA é preciso confirmar que o conteúdo da prova será enviado ao Google.")
    try:
        return GeminiModel(svc.settings)
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
