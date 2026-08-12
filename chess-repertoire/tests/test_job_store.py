"""Unit tests for BoundedLRU and JobStore."""

from __future__ import annotations

import time
from pathlib import Path

from webapp.cache_util import BoundedLRU
from webapp.job_store import JobStore


def test_bounded_lru_evicts_oldest():
    cache: BoundedLRU[str, int] = BoundedLRU(maxsize=2)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3
    assert "a" not in cache
    assert cache["b"] == 2
    assert cache["c"] == 3
    assert len(cache) == 2


def test_bounded_lru_get_promotes():
    cache: BoundedLRU[str, int] = BoundedLRU(maxsize=2)
    cache["a"] = 1
    cache["b"] = 2
    assert cache.get("a") == 1  # promote a
    cache["c"] = 3
    assert "b" not in cache
    assert cache["a"] == 1


def test_job_store_runs_and_persists(tmp_path: Path):
    db = tmp_path / "jobs.db"
    store = JobStore(db, workers=1, ttl_seconds=3600)
    done = {"ok": False}

    def worker():
        done["ok"] = True
        return {"hello": "world"}

    job_id = store.enqueue(worker, kind="test")
    deadline = time.time() + 5
    job = None
    while time.time() < deadline:
        job = store.get(job_id)
        if job and job["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert job is not None
    assert job["status"] == "done"
    assert job["result"] == {"hello": "world"}
    assert done["ok"]
    store.shutdown(wait=True)


def test_job_store_records_errors(tmp_path: Path):
    db = tmp_path / "jobs.db"
    store = JobStore(db, workers=1)

    def worker():
        raise ValueError("boom")

    job_id = store.enqueue(worker, kind="fail")
    deadline = time.time() + 5
    job = None
    while time.time() < deadline:
        job = store.get(job_id)
        if job and job["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert job is not None
    assert job["status"] == "error"
    assert "boom" in (job.get("error") or "")
    store.shutdown(wait=True)
