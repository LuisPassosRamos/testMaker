"""Retention policy for temporary school content.

Projects (uploads, renders) and outbox e-mails are deleted once they have not been
touched for ``RETENTION_HOURS``. Header templates are configuration and are kept.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from app.config import Settings
from app.persistence import Database

log = logging.getLogger(__name__)


def _last_touch(path: Path) -> float:
    marker = path / "project.json"
    target = marker if marker.exists() else path
    return target.stat().st_mtime


def cleanup_expired(settings: Settings, now: float | None = None, db: Database | None = None) -> int:
    now = now or time.time()
    limit = settings.retention_hours * 3600
    removed = 0
    if db is not None:
        try:
            for pid in db.expired_projects(now - limit):
                db.delete_project(pid)
                shutil.rmtree(settings.projects_dir / pid, ignore_errors=True)
                removed += 1
        except Exception:
            log.warning("cleanup: database not reachable, will try again later")
    projects = settings.projects_dir
    if projects.exists():
        for child in projects.iterdir():
            try:
                if child.is_dir() and now - _last_touch(child) > limit:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
            except OSError:
                log.warning("cleanup: could not inspect %s", child.name)
    outbox = settings.outbox_dir
    if outbox.exists():
        for mail in outbox.glob("*.eml"):
            if now - mail.stat().st_mtime > limit:
                mail.unlink(missing_ok=True)
                removed += 1
    if removed:
        log.info("cleanup: removed %d expired item(s)", removed)
    return removed
