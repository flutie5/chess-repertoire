"""Engine pool tests (no Stockfish binary required)."""

from __future__ import annotations

import pytest

from webapp.engine_pool import EnginePool, resolve_engine_path


def test_resolve_engine_path_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("STOCKFISH_PATH", raising=False)
    assert resolve_engine_path(tmp_path) is None


def test_pool_missing_engine_raises():
    pool = EnginePool(None, size=1)
    status = pool.status(start=True)
    assert status["ok"] is False
    assert status["path"] is None
    with pytest.raises(RuntimeError, match="not found"):
        with pool.acquire(timeout=0.1):
            pass
    pool.shutdown()


def test_pool_status_shape():
    pool = EnginePool(None, size=2, threads=1, hash_mb=16)
    st = pool.status()
    assert "available" in st
    assert st["pool_size"] == 0
    assert st["threads"] == 1
    assert st["hash_mb"] == 16
    assert st.get("started") is False
    pool.shutdown()
