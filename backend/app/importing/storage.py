"""File-system storage for projects.

Each project lives in ``data/projects/<uuid>/``::

    project.json        structured exam + sources + job state
    sources/            uploaded files (renamed, never the user's file name)
    pages/              page images of the originals, used for review and crops
    assets/             images extracted from the originals or cropped by the user
    renders/<id>/       generated DOCX, PDF, page previews and checks

No database is needed for one-exam-at-a-time usage; writes are atomic and guarded
by a per-project lock so background jobs and API calls do not clobber each other.
"""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from app.config import Settings
from app.domain.models import ExamMeta, Project, SourceFile, utcnow
from app.importing.validation import ValidatedFile

_PROJECT_ID = re.compile(r"^[0-9a-f]{32}$")
_ASSET_ID = re.compile(r"^[0-9a-f]{16}\.(png|jpg)$")
_SAFE_NAME = re.compile(r"^[0-9A-Za-z_.-]{1,80}$")


class ProjectNotFound(Exception):
    pass


class RevisionConflict(Exception):
    def __init__(self, current: int):
        super().__init__(f"revision conflict (current={current})")
        self.current = current


class ProjectStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.projects_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    # ------------------------------------------------------------------ paths
    def project_dir(self, pid: str) -> Path:
        if not _PROJECT_ID.match(pid or ""):
            raise ProjectNotFound(pid)
        return self.root / pid

    def sources_dir(self, pid: str) -> Path:
        return self.project_dir(pid) / "sources"

    def pages_dir(self, pid: str) -> Path:
        return self.project_dir(pid) / "pages"

    def assets_dir(self, pid: str) -> Path:
        return self.project_dir(pid) / "assets"

    def renders_dir(self, pid: str) -> Path:
        return self.project_dir(pid) / "renders"

    def asset_path(self, pid: str, asset_id: str) -> Path:
        if not _ASSET_ID.match(asset_id or ""):
            raise FileNotFoundError(asset_id)
        return self.assets_dir(pid) / asset_id

    def safe_child(self, base: Path, name: str) -> Path:
        if not _SAFE_NAME.match(name or "") or ".." in name:
            raise FileNotFoundError(name)
        return base / name

    # ------------------------------------------------------------------ locking
    @contextmanager
    def lock(self, pid: str) -> Iterator[None]:
        with self._locks_guard:
            lk = self._locks.setdefault(pid, threading.RLock())
        with lk:
            yield

    # ------------------------------------------------------------------ CRUD
    def create(self, start_mode: str, meta: ExamMeta) -> Project:
        pid = uuid.uuid4().hex
        project = Project(id=pid, start_mode=start_mode)  # type: ignore[arg-type]
        project.exam.meta = meta
        for sub in ("sources", "pages", "assets", "renders"):
            (self.project_dir(pid) / sub).mkdir(parents=True, exist_ok=True)
        self._write(project)
        return project

    def exists(self, pid: str) -> bool:
        try:
            return (self.project_dir(pid) / "project.json").is_file()
        except ProjectNotFound:
            return False

    def load(self, pid: str) -> Project:
        path = self.project_dir(pid) / "project.json"
        if not path.is_file():
            raise ProjectNotFound(pid)
        return Project.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, project: Project, *, bump: bool = True) -> Project:
        with self.lock(project.id):
            if bump:
                project.revision += 1
            project.updated_at = utcnow()
            self._write(project)
        return project

    def mutate(self, pid: str, fn: Callable[[Project], None], *, bump: bool = True) -> Project:
        """Load, apply ``fn`` and save under the project lock."""
        with self.lock(pid):
            project = self.load(pid)
            fn(project)
            return self.save(project, bump=bump)

    def delete(self, pid: str) -> None:
        shutil.rmtree(self.project_dir(pid), ignore_errors=True)

    def _write(self, project: Project) -> None:
        path = self.project_dir(project.id) / "project.json"
        tmp = path.with_suffix(f".{secrets.token_hex(3)}.tmp")
        tmp.write_text(project.model_dump_json(), encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------------------ files
    def add_source(self, pid: str, file: ValidatedFile) -> SourceFile:
        source = SourceFile(name=file.display_name, kind=file.kind, stored_as="", size=len(file.data))
        source.stored_as = f"{source.id}{file.ext}"
        dest = self.sources_dir(pid) / source.stored_as
        dest.write_bytes(file.data)
        return source

    def source_path(self, pid: str, source: SourceFile) -> Path:
        return self.safe_child(self.sources_dir(pid), source.stored_as)

    def save_asset_image(self, pid: str, image: Image.Image, *, fmt: str = "png") -> str:
        asset_id = f"{secrets.token_hex(8)}.{'jpg' if fmt == 'jpg' else 'png'}"
        path = self.assets_dir(pid) / asset_id
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "jpg":
            image.convert("RGB").save(path, format="JPEG", quality=92)
        else:
            image.save(path, format="PNG")
        return asset_id

    def save_asset_bytes(self, pid: str, data: bytes) -> str:
        """Store an image exactly as received when it is PNG/JPEG, otherwise convert to PNG."""
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").upper()
            if fmt in ("PNG", "JPEG"):
                asset_id = f"{secrets.token_hex(8)}.{'jpg' if fmt == 'JPEG' else 'png'}"
                path = self.assets_dir(pid) / asset_id
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                return asset_id
            img.load()
            converted = img.convert("RGBA") if img.mode in ("P", "LA", "RGBA") else img.convert("RGB")
            return self.save_asset_image(pid, converted)

    def list_ids(self) -> list[str]:
        return [p.name for p in self.root.iterdir() if p.is_dir() and _PROJECT_ID.match(p.name)]


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
