"""Engine pool tests (no Stockfish binary required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from webapp.engine_pool import (
    EnginePool,
    ensure_engine_binaries,
    resolve_engine_candidates,
    resolve_engine_path,
)


def test_resolve_engine_path_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("STOCKFISH_PATH", raising=False)
    monkeypatch.delenv("STOCKFISH_FALLBACK_PATH", raising=False)
    assert resolve_engine_path(tmp_path) is None
    assert resolve_engine_candidates(tmp_path) == []


def test_resolve_prefers_env_override(tmp_path, monkeypatch):
    primary = tmp_path / "engine" / "linux" / "stockfish"
    primary.parent.mkdir(parents=True)
    primary.write_bytes(b"x")
    fallback = tmp_path / "engine" / "linux" / "stockfish-fallback"
    fallback.write_bytes(b"y")
    custom = tmp_path / "custom-sf"
    custom.write_bytes(b"z")
    monkeypatch.setenv("STOCKFISH_PATH", str(custom))
    monkeypatch.setenv("STOCKFISH_FALLBACK_PATH", str(fallback))
    cands = resolve_engine_candidates(tmp_path)
    assert cands[0] == custom
    assert fallback in cands


def test_resolve_linux_layout(tmp_path, monkeypatch):
    monkeypatch.delenv("STOCKFISH_PATH", raising=False)
    monkeypatch.delenv("STOCKFISH_FALLBACK_PATH", raising=False)
    primary = tmp_path / "engine" / "linux" / "stockfish"
    primary.parent.mkdir(parents=True)
    primary.write_bytes(b"x")
    fb = tmp_path / "engine" / "linux" / "stockfish-fallback"
    fb.write_bytes(b"y")
    cands = resolve_engine_candidates(tmp_path)
    assert cands[0] == primary
    assert fb in cands


def test_ensure_skips_download_when_present(tmp_path, monkeypatch):
    monkeypatch.delenv("STOCKFISH_PATH", raising=False)
    primary = tmp_path / "engine" / "linux" / "stockfish"
    primary.parent.mkdir(parents=True)
    primary.write_bytes(b"x")
    monkeypatch.setenv("ENGINE_DOWNLOAD_ON_MISSING", "1")
    cands = ensure_engine_binaries(tmp_path)
    assert cands[0] == primary


def test_pool_missing_engine_raises():
    pool = EnginePool(None, size=1)
    status = pool.status(start=True)
    assert status["ok"] is False
    assert status["path"] is None
    with pytest.raises(RuntimeError, match="not found"):
        with pool.acquire(timeout=0.1):
            pass
    pool.shutdown()


def test_pool_candidates_empty():
    pool = EnginePool(candidates=[], size=1)
    assert pool.status(start=True)["ok"] is False
    pool.shutdown()


def test_pool_status_shape():
    pool = EnginePool(None, size=2, threads=1, hash_mb=16)
    st = pool.status()
    assert "available" in st
    assert st["pool_size"] == 0
    assert st["threads"] == 1
    assert st["hash_mb"] == 16
    assert st.get("started") is False
    assert "candidates" in st
    pool.shutdown()
