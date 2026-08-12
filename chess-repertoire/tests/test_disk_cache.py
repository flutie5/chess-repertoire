"""Disk cache quota helpers."""

from __future__ import annotations

from pathlib import Path

from webapp.disk_cache import cache_stats, enforce_cache_quota


def test_enforce_cache_quota_evicts_oldest(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CACHE_MAX_FILES", "2")
    monkeypatch.setenv("CACHE_MAX_MB", "100")
    for i, name in enumerate(("a.json", "b.json", "c.json")):
        p = tmp_path / name
        p.write_text("x" * 10)
        # Stagger mtimes
        import os
        os.utime(p, (1000 + i, 1000 + i))
    result = enforce_cache_quota(tmp_path)
    assert result["evicted"] == 1
    assert result["files"] == 2
    assert not (tmp_path / "a.json").exists()
    assert (tmp_path / "c.json").exists()


def test_cache_stats_empty(tmp_path: Path):
    assert cache_stats(tmp_path) == {"files": 0, "bytes": 0}
