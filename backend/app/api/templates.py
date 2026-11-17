"""Header templates CRUD and logo upload."""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse, Response
from PIL import Image

from app.api.deps import Services, api_error, services
from app.importing.validation import UploadRejected, validate_upload
from app.templates_store import HeaderTemplate

router = APIRouter(prefix="/api/templates", tags=["templates"])


@router.get("", response_model=list[HeaderTemplate])
def list_templates(svc: Services = Depends(services)) -> list[HeaderTemplate]:
    return svc.templates.list()


@router.post("", response_model=HeaderTemplate)
def create_template(template: HeaderTemplate, svc: Services = Depends(services)) -> HeaderTemplate:
    template.id = ""
    return svc.templates.save(template)


@router.put("/{template_id}", response_model=HeaderTemplate)
def update_template(template_id: str, template: HeaderTemplate, svc: Services = Depends(services)) -> HeaderTemplate:
    template.id = template_id
    return svc.templates.save(template)


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: str, svc: Services = Depends(services)) -> Response:
    if template_id == "padrao-madre":
        raise api_error(400, "builtin", "O modelo padrão da escola não pode ser apagado (mas pode ser restaurado).")
    svc.templates.delete(template_id)
    return Response(status_code=204)


@router.post("/padrao-madre/restore", response_model=HeaderTemplate)
def restore_default(svc: Services = Depends(services)) -> HeaderTemplate:
    return svc.templates.restore_builtin()


@router.post("/{template_id}/logo", response_model=HeaderTemplate)
async def upload_logo(template_id: str, file: UploadFile = File(...), svc: Services = Depends(services)) -> HeaderTemplate:
    template = svc.templates.get(template_id)
    if template.id != template_id:
        raise api_error(404, "not_found", "Modelo não encontrado.")
    data = await file.read(svc.settings.max_upload_bytes + 1)
    try:
        valid = validate_upload(file.filename, data, svc.settings)
    except UploadRejected as exc:
        raise api_error(400, exc.code, exc.message) from exc
    if valid.kind != "image":
        raise api_error(400, "not_image", "O logotipo precisa ser uma imagem (PNG ou JPG).")
    with Image.open(io.BytesIO(valid.data)) as img:
        img = img.convert("RGBA")
        img.thumbnail((800, 800))
        out = io.BytesIO()
        img.save(out, format="PNG")
    template.logo = svc.templates.save_logo(template_id, out.getvalue())
    template.show_logo = True
    return svc.templates.save(template)


@router.get("/{template_id}/logo")
def get_logo(template_id: str, svc: Services = Depends(services)) -> FileResponse:
    template = svc.templates.get(template_id)
    path = svc.templates.logo_path(template.model_copy(update={"show_logo": True}))
    if path is None:
        raise api_error(404, "not_found", "Sem logotipo.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})
