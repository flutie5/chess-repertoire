"""Human-like move suggestions via the Lichess opening explorer API."""

from __future__ import annotations

import os
import random
import time
from typing import Any

import chess
import requests

EXPLORER_URL = "https://explorer.lichess.ovh/lichess"
DEFAULT_UA = (
    "OpeningExplorer/1.0 "
    "(https://github.com/flutie5/chess-repertoire; human practice replies)"
)

_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600.0


def _rating_range(elo: int) -> list[int]:
    """Map player Elo to Lichess explorer rating buckets."""
    buckets = [400, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500]
    # Pick closest bucket and neighbors for a band of human play
    closest = min(buckets, key=lambda b: abs(b - elo))
    idx = buckets.index(closest)
    lo = max(0, idx - 1)
    hi = min(len(buckets), idx + 2)
    return buckets[lo:hi]


def fetch_explorer(fen: str, elo: int = 1800, speeds: str = "blitz,rapid") -> dict:
    key = f"{fen}|{elo}|{speeds}"
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    ratings = _rating_range(elo)
    try:
        resp = requests.get(
            EXPLORER_URL,
            params={
                "fen": fen,
                "variant": "standard",
                "speeds": speeds,
                "ratings": ",".join(str(r) for r in ratings),
            },
            headers={"User-Agent": DEFAULT_UA},
            timeout=12,
        )
        if resp.status_code == 429:
            time.sleep(1.5)
            resp = requests.get(
                EXPLORER_URL,
                params={
                    "fen": fen,
                    "variant": "standard",
                    "speeds": speeds,
                    "ratings": ",".join(str(r) for r in ratings),
                },
                headers={"User-Agent": DEFAULT_UA},
                timeout=12,
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        data = {"moves": []}

    # Bound cache size
    if len(_cache) > 2000:
        _cache.clear()
    _cache[key] = (now, data)
    return data


def pick_human_move(
    board: chess.Board,
    elo: int = 1800,
    *,
    top_n: int = 5,
) -> dict[str, Any] | None:
    """Weighted random among the top human moves from Lichess explorer.

    Returns {uci, san, from, to, promotion, fen, source, games} or None.
    """
    if board.is_game_over():
        return None
    data = fetch_explorer(board.fen(), elo=elo)
    moves = data.get("moves") or []
    if not moves:
        return None

    candidates = []
    for m in moves[: max(1, top_n)]:
        uci = (m.get("uci") or "").strip()
        games = int(m.get("white", 0) + m.get("draws", 0) + m.get("black", 0))
        if not uci or games <= 0:
            continue
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            continue
        if move not in board.legal_moves:
            continue
        candidates.append((move, games, m))

    if not candidates:
        return None

    weights = [c[1] for c in candidates]
    move, games, raw = random.choices(candidates, weights=weights, k=1)[0]
    san = board.san(move)
    promo = None
    if move.promotion:
        promo = chess.piece_symbol(move.promotion).lower()
    board.push(move)
    fen = board.fen()
    board.pop()
    return {
        "uci": move.uci(),
        "san": san,
        "from": chess.square_name(move.from_square),
        "to": chess.square_name(move.to_square),
        "promotion": promo,
        "fen": fen,
        "source": "lichess_explorer",
        "games": games,
        "total": int(raw.get("white", 0) + raw.get("draws", 0) + raw.get("black", 0)),
    }


def popularity_at_rating(fen: str, elo: int = 1800) -> list[dict]:
    """Top human replies with frequency for UI."""
    data = fetch_explorer(fen, elo=elo)
    out = []
    total = 0
    moves = data.get("moves") or []
    for m in moves[:8]:
        g = int(m.get("white", 0) + m.get("draws", 0) + m.get("black", 0))
        total += g
    for m in moves[:8]:
        g = int(m.get("white", 0) + m.get("draws", 0) + m.get("black", 0))
        out.append({
            "uci": m.get("uci"),
            "san": m.get("san"),
            "games": g,
            "pct": round(100.0 * g / total, 1) if total else 0.0,
        })
    return out
