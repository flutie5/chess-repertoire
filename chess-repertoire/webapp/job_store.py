"""SQLite-backed async job store with a bounded worker pool."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from webapp.db import connect as db_connect


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


class JobStore:
    """Durable jobs in SQLite; workers run on bounded daemon threads."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        workers: int | None = None,
        ttl_seconds: int = 3600,
    ) -> None:
        self.db_path = Path(db_path)
        self.ttl_seconds = ttl_seconds
        n = workers if workers is not None else _env_int("JOB_WORKERS", 2)
        self._slots = threading.Semaphore(n)
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        return db_connect(self.db_path)

    def _ensure_table(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS async_jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL DEFAULT 'generic',
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    user_id INTEGER
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_async_jobs_created "
                "ON async_jobs(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_async_jobs_status "
                "ON async_jobs(status)"
            )

    def prune(self, ttl: int | None = None) -> int:
        cutoff = time.time() - (ttl if ttl is not None else self.ttl_seconds)
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM async_jobs WHERE created_at < ?", (cutoff,)
            )
            return cur.rowcount or 0

    def pending_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM async_jobs "
                "WHERE status IN ('pending', 'running')"
            ).fetchone()
            return int(row["n"]) if row else 0

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT job_id, kind, status, created_at, updated_at, "
                "result_json, error, user_id FROM async_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        result = None
        if row["result_json"]:
            try:
                result = json.loads(row["result_json"])
            except json.JSONDecodeError:
                result = None
        return {
            "job_id": row["job_id"],
            "kind": row["kind"],
            "status": row["status"],
            "created": row["created_at"],
            "updated": row["updated_at"],
            "result": result,
            "error": row["error"],
            "user_id": row["user_id"],
        }

    def _set_status(
        self,
        job_id: str,
        status: str,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        now = time.time()
        result_json = json.dumps(result) if result is not None else None
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE async_jobs
                SET status = ?, updated_at = ?, result_json = ?, error = ?
                WHERE job_id = ?
                """,
                (status, now, result_json, error, job_id),
            )

    def enqueue(
        self,
        worker: Callable[[], Any],
        *,
        kind: str = "generic",
        user_id: int | None = None,
    ) -> str:
        self.prune()
        job_id = secrets.token_urlsafe(16)
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO async_jobs
                    (job_id, kind, status, created_at, updated_at, user_id)
                VALUES (?, ?, 'pending', ?, ?, ?)
                """,
                (job_id, kind, now, now, user_id),
            )

        def run() -> None:
            self._slots.acquire()
            try:
                self._set_status(job_id, "running")
                try:
                    result = worker()
                    self._set_status(job_id, "done", result=result)
                except Exception as exc:
                    self._set_status(job_id, "error", error=str(exc))
            finally:
                self._slots.release()

        threading.Thread(target=run, daemon=True, name=f"oe-job-{kind}").start()
        return job_id

    def shutdown(self, wait: bool = False) -> None:
        if wait:
            time.sleep(0.05)
