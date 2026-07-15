"""Local ECO opening-book lookup from repertoire/data/openings.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

BOOK_PATH = Path(__file__).resolve().parent / "data" / "openings.json"


@lru_cache(maxsize=1)
def _book() -> dict[str, dict]:
    if not BOOK_PATH.exists():
        return {}
    return json.loads(BOOK_PATH.read_text(encoding="utf-8"))


def lookup(play_uci: str) -> dict:
    """Longest-prefix match on a comma-separated UCI move string.

    Returns {name, eco, ply, matched_ply} — name/eco may be None.
    """
    parts = [p for p in (play_uci or "").split(",") if p]
    if not parts:
        return {"name": None, "eco": None, "ply": 0, "matched_ply": 0}

    book = _book()
    matched = None
    matched_ply = 0
    for i in range(len(parts), 0, -1):
        key = ",".join(parts[:i])
        hit = book.get(key)
        if hit:
            matched = hit
            matched_ply = i
            break

    if not matched:
        return {"name": None, "eco": None, "ply": len(parts), "matched_ply": 0}
    return {
        "name": matched.get("name"),
        "eco": matched.get("eco"),
        "ply": len(parts),
        "matched_ply": matched_ply,
    }
