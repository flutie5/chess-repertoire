"""Classify opening moves with Stockfish: inaccuracy / mistake / blunder.

A move's "loss" is how much the side to move's evaluation dropped after
playing it (centipawns). Thresholds roughly match chess.com's banded
move quality (opening-only scans use a shallower depth for speed).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict

import chess
import chess.engine

INACCURACY_CP = 50
MISTAKE_CP = 100
BLUNDER_CP = 200

OPENING_PLIES = 20       # first 10 full moves
SCAN_DEPTH = 12


@dataclass
class MoveFlag:
    ply: int              # 1-based ply index of the played move
    san: str
    severity: str         # inaccuracy | mistake | blunder
    loss_cp: int
    best_san: str | None
    color: str            # white | black (who played the bad move)


def classify_severity(loss_cp: int) -> str | None:
    if loss_cp >= BLUNDER_CP:
        return "blunder"
    if loss_cp >= MISTAKE_CP:
        return "mistake"
    if loss_cp >= INACCURACY_CP:
        return "inaccuracy"
    return None


def _score_white_cp(info: dict) -> int | None:
    """White-perspective centipawns; mates mapped to a large ± value."""
    score = info["score"].white()
    mate = score.mate()
    if mate is not None:
        # Prefer mates sooner: roughly 10k − 100×moves-to-mate.
        sign = 1 if mate > 0 else -1
        return sign * (10000 - 100 * min(abs(mate), 50))
    cp = score.score()
    return int(cp) if cp is not None else None


def classify_game(
    engine: chess.engine.SimpleEngine,
    san_moves: list[str],
    player_color: str,
    *,
    opening_plies: int = OPENING_PLIES,
    depth: int = SCAN_DEPTH,
    eval_cache: dict | None = None,
) -> list[MoveFlag]:
    """Flag only the player's moves in the opening that lose significant eval."""
    if eval_cache is None:
        eval_cache = {}
    board = chess.Board()
    flags: list[MoveFlag] = []
    limit = chess.engine.Limit(depth=depth)
    max_ply = min(len(san_moves), opening_plies)

    for ply_idx in range(max_ply):
        san = san_moves[ply_idx]
        mover = "white" if board.turn == chess.WHITE else "black"
        before_key = board.fen()
        if before_key not in eval_cache:
            info = engine.analyse(board, limit)
            eval_cache[before_key] = {
                "cp": _score_white_cp(info),
                "best_san": board.san(info["pv"][0]) if info.get("pv") else None,
            }
        before = eval_cache[before_key]

        try:
            board.push_san(san)
        except ValueError:
            break

        after_key = board.fen()
        if after_key not in eval_cache:
            info = engine.analyse(board, limit)
            eval_cache[after_key] = {
                "cp": _score_white_cp(info),
                "best_san": board.san(info["pv"][0]) if info.get("pv") else None,
            }
        after = eval_cache[after_key]

        if mover != player_color:
            continue
        if before["cp"] is None or after["cp"] is None:
            continue

        # Loss from the mover's perspective (positive = worse for them).
        if mover == "white":
            loss = before["cp"] - after["cp"]
        else:
            loss = after["cp"] - before["cp"]

        severity = classify_severity(loss)
        if severity:
            flags.append(MoveFlag(
                ply=ply_idx + 1,
                san=san,
                severity=severity,
                loss_cp=int(loss),
                best_san=before.get("best_san"),
                color=mover,
            ))

    return flags


def find_patterns(
    game_results: list[dict],
    *,
    min_count: int = 2,
) -> list[dict]:
    """Find repeated opening mistakes: same variation + ply + played move.

    `game_results` items: {
        index, variation, flags: [MoveFlag|dict], opponent?, date?, url?
    }
    """
    buckets: dict[tuple, list] = defaultdict(list)
    for g in game_results:
        variation = g.get("variation") or "Unknown"
        for f in g.get("flags") or []:
            fd = asdict(f) if isinstance(f, MoveFlag) else f
            if fd.get("severity") not in ("blunder", "mistake", "inaccuracy"):
                continue
            key = (variation, fd["ply"], fd["san"], fd["severity"])
            buckets[key].append({
                "index": g.get("index"),
                "loss_cp": fd["loss_cp"],
                "best_san": fd.get("best_san"),
                "opponent": g.get("opponent"),
                "date": g.get("date"),
                "url": g.get("url"),
            })

    patterns = []
    for (variation, ply, san, severity), items in buckets.items():
        if len(items) < min_count:
            continue
        avg_loss = round(sum(i["loss_cp"] for i in items) / len(items))
        patterns.append({
            "variation": variation,
            "ply": ply,
            "move": san,
            "severity": severity,
            "count": len(items),
            "avg_loss_cp": avg_loss,
            "best_san": items[0].get("best_san"),
            "games": items,
        })

    severity_rank = {"blunder": 3, "mistake": 2, "inaccuracy": 1}
    patterns.sort(
        key=lambda p: (
            -severity_rank.get(p["severity"], 0),
            -p["count"],
            -p["avg_loss_cp"],
        )
    )
    return patterns


def summarize_flags(flags: list[MoveFlag]) -> dict:
    counts = {"blunder": 0, "mistake": 0, "inaccuracy": 0}
    worst = None
    rank = {"blunder": 3, "mistake": 2, "inaccuracy": 1}
    for f in flags:
        counts[f.severity] = counts.get(f.severity, 0) + 1
        if worst is None or rank[f.severity] > rank[worst]:
            worst = f.severity
    return {"counts": counts, "worst": worst, "total": sum(counts.values())}
