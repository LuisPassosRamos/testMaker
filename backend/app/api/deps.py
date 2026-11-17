"""Shared dependencies and error helpers for the API routers."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from app.auth import RateLimiter
from app.config import Settings
from app.domain.models import Project
from app.importing.storage import ProjectNotFound, ProjectStore
from app.jobs import JobRunner
from app.persistence import Database
from app.templates_store import TemplateStore


@dataclass
class Services:
    settings: Settings
    store: ProjectStore
    templates: TemplateStore
    jobs: JobRunner
    db: Database | None = None
    limiter: RateLimiter = field(default_factory=RateLimiter)


def services(request: Request) -> Services:
    return request.app.state.services


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def load_project(svc: Services, pid: str) -> Project:
    try:
        return svc.store.load(pid)
    except ProjectNotFound as exc:
        raise api_error(404, "project_not_found",
                        "Não encontramos esta prova. Ela pode ter sido apagada pela limpeza automática.") from exc
