"""Optional database persistence for hosted deployments.

Locally the app keeps everything in ``data/`` and needs no database. When
``DATABASE_URL`` is set (PostgreSQL, e.g. a free Neon database), the database becomes
the durable copy and the local folder works as a cache:

- every project save writes its JSON to the database;
- originals, page images and crops are mirrored after each change;
- after a restart (or on a host with an ephemeral disk) a project is restored from
  the database the first time it is opened;
- generated renders are *not* stored: they are rebuilt from the exam on demand.

``sqlite:///path`` is also accepted, which keeps tests and single-machine setups simple.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS pp_projects (
        id TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS pp_files (
        owner TEXT NOT NULL,
        path TEXT NOT NULL,
        content {blob} NOT NULL,
        size INTEGER NOT NULL,
        mtime DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (owner, path)
    )""",
    """CREATE TABLE IF NOT EXISTS pp_settings (
        key TEXT PRIMARY KEY,
        data TEXT NOT NULL
    )""",
]


class Database:
    def __init__(self, url: str):
        self.url = url
        self._lock = threading.RLock()
        self._conn: Any = None
        if url.startswith(("postgres://", "postgresql://")):
            self.kind = "postgres"
        elif url.startswith("sqlite:///"):
            self.kind = "sqlite"
            self._path = url[len("sqlite:///"):]
        else:
            raise ValueError("DATABASE_URL precisa começar com postgresql:// ou sqlite:///")
        self._migrate()

    # ----------------------------------------------------------- connection
    def _connect(self) -> Any:
        if self.kind == "postgres":
            import psycopg

            # prepare_threshold=None: Neon's pooled endpoint (PgBouncer, transaction mode) does not
            # keep server-side prepared statements between transactions.
            return psycopg.connect(self.url, autocommit=True, connect_timeout=20, prepare_threshold=None)
        conn = sqlite3.connect(self._path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _run(self, work: Callable[[Any], T]) -> T:
        """Run ``work(cursor)`` on the shared connection, reconnecting once when the server
        dropped it (serverless Postgres suspends idle computes and closes connections)."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None:
                        self._conn = self._connect()
                    cur = self._conn.cursor()
                    result = work(cur)
                    if self.kind == "sqlite":
                        self._conn.commit()
                    return result
                except Exception as exc:
                    if attempt == 2 or not self._is_connection_error(exc):
                        raise
                    log.info("database connection lost; reconnecting")
                    self._close()
        raise RuntimeError("unreachable")

    def _is_connection_error(self, exc: Exception) -> bool:
        if self.kind == "postgres":
            import psycopg

            return isinstance(exc, psycopg.OperationalError | psycopg.InterfaceError)
        return False

    def _close(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass
        self._conn = None

    def _sql(self, sql: str) -> str:
        return sql if self.kind == "postgres" else sql.replace("%s", "?")

    def _exec(self, sql: str, params: tuple = ()) -> None:
        self._run(lambda cur: cur.execute(self._sql(sql), params))

    def _one(self, sql: str, params: tuple = ()) -> Any:
        def work(cur: Any) -> Any:
            cur.execute(self._sql(sql), params)
            return cur.fetchone()

        return self._run(work)

    def _all(self, sql: str, params: tuple = ()) -> list:
        def work(cur: Any) -> list:
            cur.execute(self._sql(sql), params)
            return list(cur.fetchall())

        return self._run(work)

    def _migrate(self) -> None:
        blob = "BYTEA" if self.kind == "postgres" else "BLOB"
        for statement in _SCHEMA:
            self._exec(statement.format(blob=blob))

    # ------------------------------------------------------------- projects
    def save_project(self, pid: str, data: str, updated_at: float | None = None) -> None:
        self._exec(
            "INSERT INTO pp_projects (id, data, updated_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (pid, data, updated_at or time.time()),
        )

    def get_project(self, pid: str) -> str | None:
        row = self._one("SELECT data FROM pp_projects WHERE id = %s", (pid,))
        return row[0] if row else None

    def delete_project(self, pid: str) -> None:
        self._exec("DELETE FROM pp_projects WHERE id = %s", (pid,))
        self._exec("DELETE FROM pp_files WHERE owner = %s", (pid,))

    def expired_projects(self, before: float) -> list[str]:
        return [row[0] for row in self._all("SELECT id FROM pp_projects WHERE updated_at < %s", (before,))]

    # ---------------------------------------------------------------- files
    def file_manifest(self, owner: str) -> dict[str, tuple[int, float]]:
        rows = self._all("SELECT path, size, mtime FROM pp_files WHERE owner = %s", (owner,))
        return {row[0]: (int(row[1]), float(row[2])) for row in rows}

    def put_file(self, owner: str, path: str, content: bytes, mtime: float) -> None:
        self._exec(
            "INSERT INTO pp_files (owner, path, content, size, mtime) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (owner, path) DO UPDATE SET content = excluded.content, size = excluded.size, "
            "mtime = excluded.mtime",
            (owner, path, content, len(content), mtime),
        )

    def delete_file(self, owner: str, path: str) -> None:
        self._exec("DELETE FROM pp_files WHERE owner = %s AND path = %s", (owner, path))

    def iter_files(self, owner: str) -> Iterator[tuple[str, bytes, float]]:
        """Yield files one by one so large projects never sit in memory all at once."""
        for path in self.file_manifest(owner):
            row = self._one("SELECT content, mtime FROM pp_files WHERE owner = %s AND path = %s", (owner, path))
            if row:
                yield path, bytes(row[0]), float(row[1])

    # ------------------------------------------------------------- settings
    def put_setting(self, key: str, data: str) -> None:
        self._exec(
            "INSERT INTO pp_settings (key, data) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET data = excluded.data",
            (key, data),
        )

    def get_setting(self, key: str) -> str | None:
        row = self._one("SELECT data FROM pp_settings WHERE key = %s", (key,))
        return row[0] if row else None

    def ping(self) -> bool:
        try:
            return self._one("SELECT 1") is not None
        except Exception:
            return False
