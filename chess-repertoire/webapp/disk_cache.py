"""Disk cache size quotas for .chesscom-cache (and similar)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from webapp.db import env_int


def cache_stats(cache_dir: Path) -> dict:
    if not cache_dir.is_dir():
        return {"files": 0, "bytes": 0}
    files = 0
    total = 0
    for p in cache_dir.glob("*.json"):
        try:
            total += p.stat().st_size
            files += 1
        except OSError:
            continue
    return {"files": files, "bytes": total}


def enforce_cache_quota(cache_dir: Path) -> dict:
    """Evict oldest *.json files until under CACHE_MAX_MB / CACHE_MAX_FILES."""
    max_mb = max(0, env_int("CACHE_MAX_MB", 400))
    max_files = max(0, env_int("CACHE_MAX_FILES", 2000))
    max_bytes = max_mb * 1024 * 1024 if max_mb else 0

    if not cache_dir.is_dir():
        return {"evicted": 0, **cache_stats(cache_dir)}

    entries: list[tuple[float, int, Path]] = []
    for p in cache_dir.glob("*.json"):
        try:
            st = p.stat()
            entries.append((st.st_mtime, st.st_size, p))
        except OSError:
            continue
    entries.sort()  # oldest first

    total_bytes = sum(e[1] for e in entries)
    evicted = 0

    def over_quota() -> bool:
        if max_files and len(entries) > max_files:
            return True
        if max_bytes and total_bytes > max_bytes:
            return True
        return False

    while entries and over_quota():
        _mtime, size, path = entries.pop(0)
        try:
            path.unlink(missing_ok=True)
            total_bytes -= size
            evicted += 1
        except OSError:
            break

    stats = cache_stats(cache_dir)
    stats["evicted"] = evicted
    stats["max_mb"] = max_mb
    stats["max_files"] = max_files
    return stats
