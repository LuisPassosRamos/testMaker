"""FastAPI application: API routers, background cleanup and the built web app."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import ai, auth, projects, renders, system, templates
from app.api.deps import Services
from app.auth import COOKIE, PUBLIC_API, token_valid
from app.config import Settings, get_settings
from app.importing.cleanup import cleanup_expired
from app.importing.storage import ProjectStore
from app.jobs import JobRunner
from app.persistence import Database
from app.templates_store import TemplateStore

log = logging.getLogger("prova_pronta")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = None
    if settings.database_url and settings.database_url.get_secret_value().strip():
        # Fail fast with a clear message: running without the database would silently lose work.
        db = Database(settings.database_url.get_secret_value().strip())
        log.info("persistence: %s database enabled", db.kind)
    store = ProjectStore(settings, db)
    svc = Services(settings=settings, store=store, templates=TemplateStore(settings, db), jobs=JobRunner(store, settings.job_workers),
                   db=db)
    if not settings.access_required:
        log.warning("ACCESS_CODE is not set: anyone who can reach this server can use it")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(cleanup_expired, settings, None, db)

        async def periodic_cleanup() -> None:
            while True:
                await asyncio.sleep(3600)
                await asyncio.to_thread(cleanup_expired, settings, None, db)

        task = asyncio.create_task(periodic_cleanup())
        try:
            yield
        finally:
            task.cancel()
            svc.jobs.shutdown()

    app = FastAPI(title="Prova Pronta", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.services = svc

    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_and_logging(request: Request, call_next):
        started = time.perf_counter()
        path = request.url.path
        if (settings.access_required and path.startswith("/api/") and path not in PUBLIC_API
                and not path.startswith(("/api/docs", "/api/openapi"))
                and not token_valid(settings, request.cookies.get(COOKIE))):
            return JSONResponse(status_code=401, content={"detail": {
                "code": "auth_required", "message": "Digite o código da escola para continuar."}},
                headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if request.url.path.startswith("/api/") and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        if request.url.path.startswith("/api/"):
            # Paths carry only random ids; no query strings or bodies are logged.
            log.info("%s %s %s %.0fms", request.method, request.url.path, response.status_code,
                     (time.perf_counter() - started) * 1000)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": {
            "code": "invalid_data",
            "message": "Alguns dados estão incompletos ou inválidos. Confira os campos destacados.",
            "fields": [".".join(str(p) for p in err.get("loc", [])[1:]) for err in exc.errors()],
        }})

    for module in (system, auth, projects, renders, templates, ai):
        app.include_router(module.router)

    _mount_frontend(app, settings.frontend_dist)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built React app (single process for local use and hosting)."""
    index = dist / "index.html"
    if not index.exists():
        @app.get("/", include_in_schema=False)
        def frontend_missing() -> JSONResponse:
            return JSONResponse({"message": "Frontend não compilado. Rode scripts/setup.ps1 ou use o modo dev."})

        return
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": {"code": "not_found", "message": "Não encontrado."}})
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
