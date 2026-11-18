"""Projects: create, upload originals, extract, paste text, save edits, crops and images."""

from __future__ import annotations

import io
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel, Field

from app.api.deps import Services, api_error, load_project, services
from app.domain.models import Exam, ExamMeta, JobState, Project, Run
from app.extraction.pipeline import ExtractionPipeline, crop_from_source
from app.importing.storage import ProjectNotFound
from app.importing.validation import UploadRejected, validate_upload
from app.jobs import JobBusy
from app.rendering.docx_writer import header_title
from app.structuring.paste import pasted_items
from app.structuring.segmenter import segment

router = APIRouter(prefix="/api/projects", tags=["projects"])

IMAGE_MEDIA = {"jpg": "image/jpeg", "png": "image/png"}


class CreateProject(BaseModel):
    start_mode: Literal["upload", "paste", "blank"] = "upload"
    meta: ExamMeta = Field(default_factory=ExamMeta)


class ProjectSummary(BaseModel):
    id: str
    title: str
    doc_type: str
    disciplina: str
    ano: str
    turma: str
    questions: int
    updated_at: str


class SaveExam(BaseModel):
    revision: int
    exam: Exam


class PasteRequest(BaseModel):
    paragraphs: list[list[Run]] = Field(max_length=5000)
    replace: bool = False


class CropRequest(BaseModel):
    source_id: str
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float]


class OrderRequest(BaseModel):
    ids: list[str]


class ExtractRequest(BaseModel):
    replace: bool = False


@router.post("", response_model=Project)
def create_project(body: CreateProject, svc: Services = Depends(services)) -> Project:
    return svc.store.create(body.start_mode, body.meta)


@router.get("", response_model=list[ProjectSummary])
def list_projects(ids: str = Query("", max_length=2000), svc: Services = Depends(services)) -> list[ProjectSummary]:
    """Summaries only for ids the browser already knows (projects are never listed globally)."""
    out = []
    for pid in [i for i in ids.split(",") if i][:20]:
        try:
            p = svc.store.load(pid)
        except ProjectNotFound:
            continue
        out.append(ProjectSummary(
            id=p.id, title=header_title(p.exam), doc_type=p.exam.meta.doc_type, disciplina=p.exam.meta.disciplina, ano=p.exam.meta.ano,
            turma=p.exam.meta.turma, questions=sum(1 for b in p.exam.blocks if b.kind == "question"),
            updated_at=p.updated_at.isoformat(),
        ))
    return out


@router.get("/{pid}", response_model=Project)
def get_project(pid: str, svc: Services = Depends(services)) -> Project:
    return load_project(svc, pid)


@router.delete("/{pid}", status_code=204)
def delete_project(pid: str, svc: Services = Depends(services)) -> Response:
    load_project(svc, pid)
    if svc.jobs.is_running(pid):
        raise api_error(409, "job_running", "Aguarde o processamento terminar para apagar.")
    svc.store.delete(pid)
    return Response(status_code=204)


@router.put("/{pid}/meta", response_model=Project)
def update_meta(pid: str, meta: ExamMeta, svc: Services = Depends(services)) -> Project:
    load_project(svc, pid)
    return svc.store.mutate(pid, lambda p: setattr(p.exam, "meta", meta))


# ------------------------------------------------------------------- sources


@router.post("/{pid}/sources")
async def upload_sources(pid: str, files: list[UploadFile] = File(...), svc: Services = Depends(services)) -> dict:
    project = load_project(svc, pid)
    if svc.jobs.is_running(pid):
        raise api_error(409, "job_running", "Aguarde a leitura atual terminar.")
    settings = svc.settings
    rejected: list[dict[str, str]] = []
    accepted = []
    room = settings.max_files_per_project - len(project.sources)
    for upload in files:
        if room <= 0:
            rejected.append({"name": upload.filename or "arquivo",
                             "message": f"Limite de {settings.max_files_per_project} arquivos por prova."})
            continue
        data = await upload.read(settings.max_upload_bytes + 1)
        try:
            valid = validate_upload(upload.filename, data, settings)
        except UploadRejected as exc:
            rejected.append({"name": upload.filename or "arquivo", "message": exc.message})
            continue
        kinds = {s.kind for s in project.sources} | {s.kind for s in accepted}
        if kinds and (valid.kind != "image" or kinds != {"image"}):
            rejected.append({"name": valid.display_name, "message": (
                "Envie um arquivo por prova. Várias imagens são aceitas apenas quando todas são páginas da mesma prova."
            )})
            continue
        accepted.append(svc.store.add_source(pid, valid))
        room -= 1
    project = svc.store.mutate(pid, lambda p: p.sources.extend(accepted))
    svc.store.sync_files(pid)
    return {"project": project.model_dump(mode="json"), "rejected": rejected}


@router.put("/{pid}/sources/order", response_model=Project)
def order_sources(pid: str, body: OrderRequest, svc: Services = Depends(services)) -> Project:
    project = load_project(svc, pid)
    by_id = {s.id: s for s in project.sources}
    if sorted(body.ids) != sorted(by_id):
        raise api_error(400, "bad_order", "A lista de páginas não confere.")
    return svc.store.mutate(pid, lambda p: setattr(p, "sources", [by_id[i] for i in body.ids]))


@router.delete("/{pid}/sources/{sid}", response_model=Project)
def delete_source(pid: str, sid: str, svc: Services = Depends(services)) -> Project:
    project = load_project(svc, pid)
    source = next((s for s in project.sources if s.id == sid), None)
    if source is None:
        raise api_error(404, "source_not_found", "Arquivo não encontrado.")
    svc.store.source_path(pid, source).unlink(missing_ok=True)
    project = svc.store.mutate(pid, lambda p: setattr(p, "sources", [s for s in p.sources if s.id != sid]))
    svc.store.sync_files(pid)
    return project


@router.post("/{pid}/extract", response_model=JobState)
def start_extraction(pid: str, body: ExtractRequest | None = None, svc: Services = Depends(services)) -> JobState:
    project = load_project(svc, pid)
    if not project.sources:
        raise api_error(400, "no_sources", "Envie pelo menos um arquivo antes de continuar.")
    if project.exam.blocks and not (body and body.replace):
        raise api_error(409, "has_content", "Esta prova já tem conteúdo. Confirme para substituí-lo pela nova leitura.")
    pipeline = ExtractionPipeline(svc.store, svc.settings)
    try:
        return svc.jobs.start(pid, "extract", lambda progress: pipeline.run(pid, progress),
                              "Não conseguimos terminar a leitura. Seus arquivos continuam salvos: tente de novo "
                              "ou envie em outro formato (por exemplo, PDF).")
    except JobBusy as exc:
        raise api_error(409, "job_running", "Já existe um processamento em andamento para esta prova.") from exc


@router.get("/{pid}/job", response_model=JobState | None)
def get_job(pid: str, svc: Services = Depends(services)) -> JobState | None:
    return load_project(svc, pid).job


@router.post("/{pid}/paste", response_model=Project)
def paste_text(pid: str, body: PasteRequest, svc: Services = Depends(services)) -> Project:
    load_project(svc, pid)
    result = segment(pasted_items(body.paragraphs))

    def apply(p: Project) -> None:
        if body.replace:
            p.exam.blocks = result.blocks
            p.exam.document_issues = result.issues
        else:
            p.exam.blocks.extend(result.blocks)
            p.exam.document_issues.extend(i for i in result.issues if i.code != "no_questions" or not p.exam.blocks)
        if result.has_closing:
            p.exam.meta.mensagem_final = ""  # the teacher's own closing words are kept; do not repeat them

    return svc.store.mutate(pid, apply)


@router.put("/{pid}/exam")
def save_exam(pid: str, body: SaveExam, svc: Services = Depends(services)) -> dict:
    with svc.store.lock(pid):
        project = load_project(svc, pid)
        if body.revision != project.revision:
            raise api_error(409, "revision_conflict",
                            "Esta prova foi alterada em outra aba. Recarregue a página para ver a versão mais recente.")
        project.exam = body.exam
        svc.store.save(project)
    return {"revision": project.revision, "updated_at": project.updated_at.isoformat()}


# ------------------------------------------------------------------- images


@router.get("/{pid}/pages/{name}")
def page_image(pid: str, name: str, svc: Services = Depends(services)) -> FileResponse:
    try:
        path = svc.store.safe_child(svc.store.pages_dir(pid), name)
    except (FileNotFoundError, ProjectNotFound) as exc:
        raise api_error(404, "not_found", "Página não encontrada.") from exc
    if not path.exists() or path.suffix not in (".jpg", ".png"):
        raise api_error(404, "not_found", "Página não encontrada.")
    return FileResponse(path, media_type=IMAGE_MEDIA[path.suffix[1:]], headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{pid}/assets/{asset_id}")
def asset_image(pid: str, asset_id: str, svc: Services = Depends(services)) -> FileResponse:
    try:
        path = svc.store.asset_path(pid, asset_id)
    except (FileNotFoundError, ProjectNotFound) as exc:
        raise api_error(404, "not_found", "Imagem não encontrada.") from exc
    if not path.exists():
        raise api_error(404, "not_found", "Imagem não encontrada.")
    return FileResponse(path, media_type=IMAGE_MEDIA[path.suffix[1:]],
                        headers={"Cache-Control": "private, max-age=86400, immutable"})


def _width_cm(image: Image.Image, dpi_guess: float = 150) -> float:
    return round(min(17.0, image.width / dpi_guess * 2.54), 2)


@router.post("/{pid}/assets")
async def upload_asset(pid: str, file: UploadFile = File(...), svc: Services = Depends(services)) -> dict:
    load_project(svc, pid)
    data = await file.read(svc.settings.max_upload_bytes + 1)
    try:
        valid = validate_upload(file.filename, data, svc.settings)
    except UploadRejected as exc:
        raise api_error(400, exc.code, exc.message) from exc
    if valid.kind != "image":
        raise api_error(400, "not_image", "Envie uma imagem (JPG ou PNG).")
    asset_id = svc.store.save_asset_bytes(pid, valid.data)
    svc.store.sync_files(pid)
    with Image.open(io.BytesIO(valid.data)) as img:
        width = _width_cm(img)
    return {"asset_id": asset_id, "width_cm": width}


@router.post("/{pid}/crops")
def crop_original(pid: str, body: CropRequest, svc: Services = Depends(services)) -> dict:
    project = load_project(svc, pid)
    source = next((s for s in project.sources if s.id == body.source_id), None)
    if source is None:
        raise api_error(404, "source_not_found", "Arquivo original não encontrado.")
    try:
        crop = crop_from_source(svc.store, pid, source, body.page, body.bbox)
    except ValueError as exc:
        raise api_error(400, "crop_too_small", str(exc)) from exc
    except FileNotFoundError as exc:
        raise api_error(404, "page_not_found", "Página do original não encontrada.") from exc
    asset_id = svc.store.save_asset_image(pid, crop)
    svc.store.sync_files(pid)
    # Originals are A4 pages: a crop keeps the printed width it had on paper.
    width_cm = round(min(18.0, (body.bbox[2] - body.bbox[0]) * 21.0), 2)
    return {"asset_id": asset_id, "width_cm": width_cm}
