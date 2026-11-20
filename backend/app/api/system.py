"""Health/capabilities: tells the UI which features really work on this machine."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app import __version__
from app.ai.factory import ai_description
from app.api.deps import Services, services
from app.extraction.ocr import ocr_status
from app.rendering.converter import converter_status
from app.rendering.fonts import available_fonts

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health(svc: Services = Depends(services)) -> dict:
    s = svc.settings
    converter = converter_status(s)
    ocr = ocr_status(s)
    return {
        "ok": True,
        "version": __version__,
        "converter": {"available": converter.available, "engine": converter.engine, "message": converter.message},
        "ocr": {"available": ocr.available, "version": ocr.version, "message": ocr.message},
        "ai": ai_description(s),
        "database": {"configured": svc.db is not None, "ok": svc.db.ping() if svc.db else None},
        "access": {"required": s.access_required},
        "mail": {"mode": s.mail_mode, "to": s.mail_to},
        "limits": {"max_upload_mb": s.max_upload_mb, "max_files": s.max_files_per_project,
                   "max_pdf_pages": s.max_pdf_pages, "retention_hours": s.retention_hours},
    }


@router.get("/fonts")
def fonts() -> list[dict[str, str]]:
    return available_fonts()
