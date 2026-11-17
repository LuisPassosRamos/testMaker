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
import logging
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
from app.persistence import Database

log = logging.getLogger(__name__)

_PROJECT_ID = re.compile(r"^[0-9a-f]{32}$")
_ASSET_ID = re.compile(r"^[0-9a-f]{16}\.(png|jpg)$")
_SAFE_NAME = re.compile(r"^[0-9A-Za-z_.-]{1,80}$")


class ProjectNotFound(Exception):
    pass


class RevisionConflict(Exception):
    def __init__(self, current: int):
        super().__init__(f"revision conflict (current={current})")
        self.current = current


# Folders mirrored to the database; renders are rebuilt on demand and never stored.
PERSISTED_DIRS = ("sources", "pages", "assets")


class ProjectStore:
    def __init__(self, settings: Settings, db: Database | None = None):
        self.settings = settings
        self.db = db
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
            path = self.project_dir(pid) / "project.json"
        except ProjectNotFound:
            return False
        return path.is_file() or bool(self.db and self.db.get_project(pid) is not None)

    def load(self, pid: str) -> Project:
        path = self.project_dir(pid) / "project.json"
        if not path.is_file():
            with self.lock(pid):
                if not path.is_file() and not self._restore(pid):
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
        if self.db:
            self.db.delete_project(pid)

    def _write(self, project: Project) -> None:
        path = self.project_dir(project.id) / "project.json"
        data = project.model_dump_json()
        tmp = path.with_suffix(f".{secrets.token_hex(3)}.tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)
        if self.db:
            self.db.save_project(project.id, data, project.updated_at.timestamp())

    # ------------------------------------------------------------- database
    def _restore(self, pid: str) -> bool:
        """Rebuild a project folder from the database (after a restart or on an ephemeral disk)."""
        if not self.db:
            return False
        data = self.db.get_project(pid)
        if data is None:
            return False
        base = self.project_dir(pid)
        for sub in (*PERSISTED_DIRS, "renders"):
            (base / sub).mkdir(parents=True, exist_ok=True)
        for rel, content, mtime in self.db.iter_files(pid):
            target = self._mirror_path(base, rel)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            os.utime(target, (mtime, mtime))
        (base / "project.json").write_text(data, encoding="utf-8")
        log.info("project %s restored from the database", pid)
        return True

    @staticmethod
    def _mirror_path(base: Path, rel: str) -> Path | None:
        parts = rel.split("/")
        if len(parts) != 2 or parts[0] not in PERSISTED_DIRS or not _SAFE_NAME.match(parts[1]) or ".." in parts[1]:
            return None
        return base / parts[0] / parts[1]

    def sync_files(self, pid: str) -> None:
        """Mirror originals, page images and crops to the database (no-op without one)."""
        if not self.db:
            return
        base = self.project_dir(pid)
        remote = self.db.file_manifest(pid)
        local: set[str] = set()
        for sub in PERSISTED_DIRS:
            folder = base / sub
            if not folder.is_dir():
                continue
            for file in folder.iterdir():
                if not file.is_file() or file.suffix == ".tmp":
                    continue
                rel = f"{sub}/{file.name}"
                local.add(rel)
                stat = file.stat()
                known = remote.get(rel)
                if known and known[0] == stat.st_size and abs(known[1] - stat.st_mtime) < 0.001:
                    continue
                self.db.put_file(pid, rel, file.read_bytes(), stat.st_mtime)
        for rel in set(remote) - local:
            self.db.delete_file(pid, rel)

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

    def forget_local(self, pid: str) -> None:
        """Drop the local cache of a project (it stays in the database)."""
        shutil.rmtree(self.project_dir(pid), ignore_errors=True)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
