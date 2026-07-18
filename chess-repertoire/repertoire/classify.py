"""Classify opening moves with Stockfish: inaccuracy / mistake / blunder.

A move's "loss" is how much the side to move's evaluation dropped after
playing it (centipawns). Thresholds roughly match chess.com's banded
move quality (opening-only scans use a shallower depth for speed).

Also provides full-game annotate_game() for chess.com-style move badges
(blunder / mistake / great / best / brilliant).
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

# Full-game review (chess.com-style annotations)
ANNOTATE_DEPTH = 12
ANNOTATE_MAX_PLIES = 120
BEST_TOLERANCE_CP = 5
GREAT_SECOND_GAP_CP = 50
BEST_SECOND_GAP_CP = 25
TENSION_CP = 80
BRILLIANT_MAX_LOSS_CP = 30
SAVE_POSITION_CP = -150

_PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}


@dataclass
class MoveFlag:
    ply: int              # 1-based ply index of the played move
    san: str
    severity: str         # inaccuracy | mistake | blunder
    loss_cp: int
    best_san: str | None
    color: str            # white | black (who played the bad move)


@dataclass
class MoveAnnotation:
    ply: int
    san: str
    severity: str         # blunder | mistake | great | best | brilliant
    loss_cp: int
    best_san: str | None
    color: str


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


def _mover_cp(white_cp: int, mover: str) -> int:
    return white_cp if mover == "white" else -white_cp


def material_balance(board: chess.Board, color: chess.Color) -> int:
    """Net material in pawn units for `color` (own − opponent)."""
    mine = 0
    theirs = 0
    for pt, val in _PIECE_VALUES.items():
        mine += len(board.pieces(pt, color)) * val
        theirs += len(board.pieces(pt, not color)) * val
    return mine - theirs


def is_material_sacrifice(board_before: chess.Board, move: chess.Move) -> bool:
    """True if the move offers material (losing exchange / hanging piece).

    Uses a simple static check: the piece that landed is attacked, and the
    material gained on this capture is less than the piece risked by ≥1 pawn.
    """
    piece = board_before.piece_at(move.from_square)
    if piece is None or piece.piece_type == chess.KING:
        return False
    captured = board_before.piece_at(move.to_square)
    if move.promotion:
        risked = _PIECE_VALUES.get(move.promotion, 9)
    else:
        risked = _PIECE_VALUES.get(piece.piece_type, 0)
    gained = _PIECE_VALUES.get(captured.piece_type, 0) if captured else 0

    board = board_before.copy(stack=False)
    board.push(move)
    if board.piece_at(move.to_square) is None:
        return False
    # Opponent to move — can they take the piece we just moved?
    if not board.is_attacked_by(board.turn, move.to_square):
        return False
    return risked - gained >= 1


def classify_annotation(
    *,
    loss_cp: int,
    played_is_best: bool,
    is_sacrifice: bool,
    before_cp_mover: int,
    second_best_gap_cp: int,
) -> str | None:
    """Pick a chess.com-style badge, or None for quiet/unnotable moves.

    Pure heuristic — no engine. Inaccuracies/good/book are intentionally omitted.
    """
    if loss_cp >= BLUNDER_CP:
        return "blunder"
    if loss_cp >= MISTAKE_CP:
        return "mistake"

    if not played_is_best:
        return None

    if is_sacrifice and loss_cp <= BRILLIANT_MAX_LOSS_CP:
        return "brilliant"

    if second_best_gap_cp >= GREAT_SECOND_GAP_CP:
        return "great"

    if before_cp_mover <= SAVE_POSITION_CP and loss_cp <= BEST_TOLERANCE_CP:
        return "great"

    if second_best_gap_cp >= BEST_SECOND_GAP_CP or abs(before_cp_mover) >= TENSION_CP:
        return "best"

    return None


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


def _analyse_multipv(
    engine: chess.engine.SimpleEngine,
    board: chess.Board,
    limit: chess.engine.Limit,
    *,
    multipv: int = 2,
) -> list[dict]:
    """Return up to `multipv` analysis lines as plain dicts."""
    raw = engine.analyse(board, limit, multipv=multipv)
    if isinstance(raw, dict):
        infos = [raw]
    else:
        infos = list(raw)
    out = []
    for info in infos:
        best_san = None
        pv = info.get("pv") or []
        if pv:
            try:
                best_san = board.san(pv[0])
            except ValueError:
                best_san = pv[0].uci()
        out.append({
            "cp": _score_white_cp(info),
            "best_san": best_san,
        })
    return out


def annotate_ply(
    engine: chess.engine.SimpleEngine,
    board: chess.Board,
    san: str,
    *,
    ply: int,
    depth: int = ANNOTATE_DEPTH,
    eval_cache: dict | None = None,
    mark_quiet_best: bool = False,
) -> MoveAnnotation | None:
    """Classify one move on `board` (position before the move). Pushes the move.

    When mark_quiet_best is True (exploratory analysis), engine-best moves that
    would otherwise get no badge are marked severity "best".
    """
    if eval_cache is None:
        eval_cache = {}
    limit = chess.engine.Limit(depth=depth)
    san = str(san)
    mover = "white" if board.turn == chess.WHITE else "black"
    before_fen = board.fen()

    cache_key = f"mpv2|{depth}|{before_fen}"
    if cache_key in eval_cache:
        lines = eval_cache[cache_key]
    else:
        lines = _analyse_multipv(engine, board, limit, multipv=2)
        eval_cache[cache_key] = lines

    if not lines or lines[0]["cp"] is None:
        try:
            board.push_san(san)
        except ValueError:
            return None
        return None

    before_white = lines[0]["cp"]
    best_san = lines[0].get("best_san")
    before_mover = _mover_cp(before_white, mover)

    second_best_gap = 0
    if len(lines) > 1 and lines[1]["cp"] is not None:
        second_mover = _mover_cp(lines[1]["cp"], mover)
        second_best_gap = before_mover - second_mover

    try:
        move = board.parse_san(san)
    except ValueError:
        return None

    sacrifice = is_material_sacrifice(board, move)
    try:
        board.push(move)
    except ValueError:
        return None

    if board.is_checkmate():
        return MoveAnnotation(
            ply=ply,
            san=san,
            severity="brilliant" if sacrifice else "great",
            loss_cp=0,
            best_san=best_san,
            color=mover,
        )

    after_fen = board.fen()
    after_key = f"mpv1|{depth}|{after_fen}"
    if after_key in eval_cache:
        after_lines = eval_cache[after_key]
    else:
        after_lines = _analyse_multipv(engine, board, limit, multipv=1)
        eval_cache[after_key] = after_lines

    if not after_lines or after_lines[0]["cp"] is None:
        return None

    after_white = after_lines[0]["cp"]
    after_mover = _mover_cp(after_white, mover)
    loss_cp = int(before_mover - after_mover)
    played_is_best = (
        (best_san is not None and san == best_san)
        or loss_cp <= BEST_TOLERANCE_CP
    )

    severity = classify_annotation(
        loss_cp=loss_cp,
        played_is_best=played_is_best,
        is_sacrifice=sacrifice,
        before_cp_mover=before_mover,
        second_best_gap_cp=int(second_best_gap),
    )
    if not severity and mark_quiet_best and played_is_best:
        severity = "best"
    if not severity:
        return None
    return MoveAnnotation(
        ply=ply,
        san=san,
        severity=severity,
        loss_cp=max(0, loss_cp),
        best_san=best_san,
        color=mover,
    )


def annotate_game(
    engine: chess.engine.SimpleEngine,
    san_moves: list[str],
    *,
    depth: int = ANNOTATE_DEPTH,
    max_plies: int = ANNOTATE_MAX_PLIES,
    eval_cache: dict | None = None,
) -> list[MoveAnnotation]:
    """Full-game review: annotate notable moves for both colors."""
    if eval_cache is None:
        eval_cache = {}
    board = chess.Board()
    annotations: list[MoveAnnotation] = []
    max_ply = min(len(san_moves), max_plies)

    for ply_idx in range(max_ply):
        san = str(san_moves[ply_idx])
        before_len = len(board.move_stack)
        ann = annotate_ply(
            engine,
            board,
            san,
            ply=ply_idx + 1,
            depth=depth,
            eval_cache=eval_cache,
        )
        if ann:
            annotations.append(ann)
            if board.is_checkmate():
                break
        elif len(board.move_stack) == before_len:
            # Illegal / failed push — stop.
            break

    return annotations
