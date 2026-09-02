"""Dropbox sync state — SQLite persistence for incremental sync."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable, Protocol

if TYPE_CHECKING:
    from dropbox_sync.scanner import ScanEntry

log = logging.getLogger("orchestrator.dropbox.state")

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS files (
    project TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    remote_rev TEXT,
    uploaded_at TEXT NOT NULL,
    PRIMARY KEY (project, rel_path)
);
CREATE TABLE IF NOT EXISTS pending_sessions (
    project TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    session_id TEXT NOT NULL,
    offset INTEGER NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    content_hash TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project, rel_path)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    trigger TEXT NOT NULL,
    project TEXT,
    files_scanned INTEGER DEFAULT 0,
    files_uploaded INTEGER DEFAULT 0,
    bytes_uploaded INTEGER DEFAULT 0,
    files_deleted INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    error_sample TEXT
);
CREATE TABLE IF NOT EXISTS project_stats (
    project TEXT PRIMARY KEY,
    files_synced INTEGER,
    bytes_synced INTEGER,
    last_synced_at TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    project TEXT,
    path TEXT,
    message TEXT NOT NULL
);
"""

_MAX_ERRORS = 200


class _ScanEntryLike(Protocol):
    """Structural type matching ScanEntry (rel_path, size, mtime_ns)."""

    @property
    def rel_path(self) -> str: ...

    @property
    def size(self) -> int: ...

    @property
    def mtime_ns(self) -> int: ...


@dataclass(frozen=True)
class FileRecord:
    project: str
    rel_path: str
    size: int
    mtime_ns: int
    content_hash: str
    remote_rev: str | None
    uploaded_at: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def diff_entries(
    known: dict[str, FileRecord],
    entries: list[_ScanEntryLike],
) -> tuple[list[_ScanEntryLike], list[str], list[_ScanEntryLike]]:
    """Compare scanned entries against known state.

    Returns (changed_or_new, deleted_rel_paths, unchanged).
    Comparison is by (size, mtime_ns) equality.
    """
    changed: list[_ScanEntryLike] = []
    unchanged: list[_ScanEntryLike] = []
    seen: set[str] = set()

    for entry in entries:
        seen.add(entry.rel_path)
        rec = known.get(entry.rel_path)
        if rec is None or rec.size != entry.size or rec.mtime_ns != entry.mtime_ns:
            changed.append(entry)
        else:
            unchanged.append(entry)

    deleted = [rp for rp in known if rp not in seen]
    return changed, deleted, unchanged


class SyncState:
    """Thread-safe SQLite state for Dropbox sync."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()

        # Create directory with restricted permissions
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, mode=0o700, exist_ok=True)

        is_new = not os.path.exists(db_path)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")

        # Schema upgrade: if pending_sessions exists but lacks created_at,
        # drop and recreate (only holds resumable sessions, safe to lose).
        if not is_new:
            self._upgrade_pending_sessions()

        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        if is_new:
            os.chmod(db_path, 0o600)

        # Secure WAL/SHM files if present
        for suffix in ("-wal", "-shm"):
            aux = db_path + suffix
            if os.path.exists(aux):
                os.chmod(aux, 0o600)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _upgrade_pending_sessions(self) -> None:
        """Drop and recreate pending_sessions if it lacks the created_at column."""
        cur = self._conn.execute("PRAGMA table_info(pending_sessions)")
        columns = {row[1] for row in cur.fetchall()}
        if columns and "created_at" not in columns:
            log.info("Upgrading pending_sessions table (adding created_at, making content_hash nullable)")
            self._conn.execute("DROP TABLE pending_sessions")
            self._conn.commit()

    # ── files ──

    def get_project_files(self, project: str) -> dict[str, FileRecord]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT project, rel_path, size, mtime_ns, content_hash, remote_rev, uploaded_at "
                "FROM files WHERE project = ?",
                (project,),
            )
            return {
                row[1]: FileRecord(*row) for row in cur.fetchall()
            }

    def upsert_files(self, records: Iterable[FileRecord]) -> None:
        rows = [
            (r.project, r.rel_path, r.size, r.mtime_ns, r.content_hash, r.remote_rev, r.uploaded_at)
            for r in records
        ]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO files "
                "(project, rel_path, size, mtime_ns, content_hash, remote_rev, uploaded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()

    def delete_files(self, project: str, rel_paths: Iterable[str]) -> None:
        paths = list(rel_paths)
        if not paths:
            return
        with self._lock:
            self._conn.executemany(
                "DELETE FROM files WHERE project = ? AND rel_path = ?",
                [(project, rp) for rp in paths],
            )
            self._conn.commit()

    def count_files(self, project: str) -> tuple[int, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM files WHERE project = ?",
                (project,),
            ).fetchone()
            return (row[0], row[1])

    def forget_project(self, project: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM files WHERE project = ?", (project,))
            self._conn.execute("DELETE FROM pending_sessions WHERE project = ?", (project,))
            self._conn.execute("DELETE FROM project_stats WHERE project = ?", (project,))
            self._conn.commit()

    # ── resumable upload sessions ──

    def pending_put(
        self,
        project: str,
        rel_path: str,
        session_id: str,
        offset: int,
        size: int,
        mtime_ns: int,
        content_hash: str | None,
        created_at: str | None = None,
    ) -> None:
        if created_at is None:
            created_at = _utcnow()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO pending_sessions "
                "(project, rel_path, session_id, offset, size, mtime_ns, content_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (project, rel_path, session_id, offset, size, mtime_ns, content_hash, created_at),
            )
            self._conn.commit()

    def pending_get(self, project: str, rel_path: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id, offset, size, mtime_ns, content_hash, created_at "
                "FROM pending_sessions WHERE project = ? AND rel_path = ?",
                (project, rel_path),
            ).fetchone()
            if row is None:
                return None
            return {
                "session_id": row[0],
                "offset": row[1],
                "size": row[2],
                "mtime_ns": row[3],
                "content_hash": row[4],
                "created_at": row[5],
            }

    def pending_delete(self, project: str, rel_path: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM pending_sessions WHERE project = ? AND rel_path = ?",
                (project, rel_path),
            )
            self._conn.commit()

    def pending_all(self, project: str) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT rel_path, session_id, offset, size, mtime_ns, content_hash, created_at "
                "FROM pending_sessions WHERE project = ?",
                (project,),
            )
            return [
                {
                    "rel_path": row[0],
                    "session_id": row[1],
                    "offset": row[2],
                    "size": row[3],
                    "mtime_ns": row[4],
                    "content_hash": row[5],
                    "created_at": row[6],
                }
                for row in cur.fetchall()
            ]

    # ── rename / batch ──

    def rename_project(self, old: str, new: str) -> None:
        """Rename a project across files, pending_sessions, project_stats and runs."""
        with self._lock:
            self._conn.execute(
                "UPDATE files SET project = ? WHERE project = ?", (new, old),
            )
            self._conn.execute(
                "UPDATE pending_sessions SET project = ? WHERE project = ?", (new, old),
            )
            # project_stats has a PK on project — delete old then insert new
            row = self._conn.execute(
                "SELECT files_synced, bytes_synced, last_synced_at, last_error "
                "FROM project_stats WHERE project = ?",
                (old,),
            ).fetchone()
            if row is not None:
                self._conn.execute("DELETE FROM project_stats WHERE project = ?", (old,))
                self._conn.execute(
                    "INSERT OR REPLACE INTO project_stats "
                    "(project, files_synced, bytes_synced, last_synced_at, last_error) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (new, row[0], row[1], row[2], row[3]),
                )
            self._conn.execute(
                "UPDATE runs SET project = ? WHERE project = ?", (new, old),
            )
            self._conn.commit()

    def commit_batch_results(
        self,
        upserts: Iterable[FileRecord],
        pending_deletes: Iterable[tuple[str, str]],
    ) -> None:
        """Atomically upsert files and delete pending sessions."""
        upsert_rows = [
            (r.project, r.rel_path, r.size, r.mtime_ns, r.content_hash, r.remote_rev, r.uploaded_at)
            for r in upserts
        ]
        delete_rows = list(pending_deletes)
        with self._lock:
            self._conn.execute("SAVEPOINT commit_batch")
            try:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO files "
                    "(project, rel_path, size, mtime_ns, content_hash, remote_rev, uploaded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    upsert_rows,
                )
                self._conn.executemany(
                    "DELETE FROM pending_sessions WHERE project = ? AND rel_path = ?",
                    delete_rows,
                )
                self._conn.execute("RELEASE SAVEPOINT commit_batch")
                self._conn.commit()
            except Exception:
                self._conn.execute("ROLLBACK TO SAVEPOINT commit_batch")
                self._conn.execute("RELEASE SAVEPOINT commit_batch")
                raise

    # ── runs ──

    def run_start(self, trigger: str) -> int:
        now = _utcnow()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO runs (started_at, status, trigger) VALUES (?, 'running', ?)",
                (now, trigger),
            )
            self._conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def run_update(self, run_id: int, *, project: str | None = None, **counters: int) -> None:
        parts: list[str] = []
        params: list[object] = []
        if project is not None:
            parts.append("project = ?")
            params.append(project)
        for col in ("files_scanned", "files_uploaded", "bytes_uploaded", "files_deleted", "errors"):
            if col in counters:
                parts.append(f"{col} = ?")
                params.append(counters[col])
        if not parts:
            return
        params.append(run_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE runs SET {', '.join(parts)} WHERE id = ?",
                params,
            )
            self._conn.commit()

    def run_finish(self, run_id: int, status: str, error_sample: str | None = None) -> None:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, error_sample = ? WHERE id = ?",
                (now, status, error_sample, run_id),
            )
            self._conn.commit()

    def run_current(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, started_at, finished_at, status, trigger, project, "
                "files_scanned, files_uploaded, bytes_uploaded, files_deleted, errors, error_sample "
                "FROM runs WHERE status = 'running' ORDER BY id DESC LIMIT 1",
            ).fetchone()
            return self._run_row_to_dict(row) if row else None

    def run_last(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, started_at, finished_at, status, trigger, project, "
                "files_scanned, files_uploaded, bytes_uploaded, files_deleted, errors, error_sample "
                "FROM runs WHERE status != 'running' ORDER BY id DESC LIMIT 1",
            ).fetchone()
            return self._run_row_to_dict(row) if row else None

    def runs_recent(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, started_at, finished_at, status, trigger, project, "
                "files_scanned, files_uploaded, bytes_uploaded, files_deleted, errors, error_sample "
                "FROM runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._run_row_to_dict(r) for r in rows]

    def runs_mark_interrupted(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE runs SET status = 'interrupted', finished_at = ? WHERE status = 'running'",
                (_utcnow(),),
            )
            self._conn.commit()
            return cur.rowcount

    @staticmethod
    def _run_row_to_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "started_at": row[1],
            "finished_at": row[2],
            "status": row[3],
            "trigger": row[4],
            "project": row[5],
            "files_scanned": row[6],
            "files_uploaded": row[7],
            "bytes_uploaded": row[8],
            "files_deleted": row[9],
            "errors": row[10],
            "error_sample": row[11],
        }

    # ── per-project stats ──

    def project_stats_all(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT project, files_synced, bytes_synced, last_synced_at, last_error "
                "FROM project_stats",
            ).fetchall()
            return {
                row[0]: {
                    "files_synced": row[1],
                    "bytes_synced": row[2],
                    "last_synced_at": row[3],
                    "last_error": row[4],
                }
                for row in rows
            }

    def project_stats_update(self, project: str, **fields: object) -> None:
        allowed = {"files_synced", "bytes_synced", "last_synced_at", "last_error"}
        filtered = {k: v for k, v in fields.items() if k in allowed}
        if not filtered:
            return
        cols = list(filtered.keys())
        vals = [filtered[c] for c in cols]
        with self._lock:
            # Try update first
            set_clause = ", ".join(f"{c} = ?" for c in cols)
            cur = self._conn.execute(
                f"UPDATE project_stats SET {set_clause} WHERE project = ?",
                vals + [project],
            )
            if cur.rowcount == 0:
                # Insert with defaults
                all_cols = ["project"] + cols
                placeholders = ", ".join("?" for _ in all_cols)
                self._conn.execute(
                    f"INSERT INTO project_stats ({', '.join(all_cols)}) VALUES ({placeholders})",
                    [project] + vals,
                )
            self._conn.commit()

    # ── error log ──

    def error_add(self, project: str | None, path: str | None, message: str) -> None:
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                "INSERT INTO errors (at, project, path, message) VALUES (?, ?, ?, ?)",
                (now, project, path, message),
            )
            # Prune to last _MAX_ERRORS rows
            self._conn.execute(
                "DELETE FROM errors WHERE id NOT IN "
                "(SELECT id FROM errors ORDER BY id DESC LIMIT ?)",
                (_MAX_ERRORS,),
            )
            self._conn.commit()

    def errors_recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, at, project, path, message FROM errors ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {"id": row[0], "at": row[1], "project": row[2], "path": row[3], "message": row[4]}
                for row in rows
            ]
