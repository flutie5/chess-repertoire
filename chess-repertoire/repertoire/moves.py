"""Extract move sequences from chess.com PGNs and build representative
"main lines" for opening variations.

The CLI report never needed actual moves, but the web UI lets you click an
opening line and replay it on a board. For each variation we walk a
popularity trie over the games that reached it: at every ply we follow the
most common next move, giving the line as *you* most often experience it.
"""

from __future__ import annotations

import re
from collections import Counter

import chess

MAX_PLIES = 20  # 10 full moves is plenty to characterize an opening
MAX_GAME_PLIES = 200  # cap for full-game replay in the web UI

_COMMENT_RE = re.compile(r"\{[^}]*\}")
_VARIATION_RE = re.compile(r"\([^()]*\)")
_MOVE_NUM_RE = re.compile(r"\d+\.(\.\.)?")
_SAN_RE = re.compile(
    r"^(O-O(-O)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](=[QRBN])?)[+#]?$"
)
_RESULTS = {"1-0", "0-1", "1/2-1/2", "*"}


def san_moves(pgn: str, max_plies: int = MAX_PLIES) -> list[str]:
    """Return the first `max_plies` SAN moves from a PGN's movetext."""
    # Movetext is the chunk after the header block.
    movetext = pgn.split("\n\n")[-1]
    movetext = _COMMENT_RE.sub(" ", movetext)
    while _VARIATION_RE.search(movetext):
        movetext = _VARIATION_RE.sub(" ", movetext)
    movetext = _MOVE_NUM_RE.sub(" ", movetext)

    moves = []
    for tok in movetext.split():
        if tok in _RESULTS or tok.startswith("$"):
            continue
        if _SAN_RE.match(tok):
            moves.append(tok)
            if len(moves) >= max_plies:
                break
    return moves


def main_line(move_lists: list[list[str]], max_plies: int = MAX_PLIES) -> list[str]:
    """Most-popular line through a set of games: at each ply, follow the
    most common next move among games still matching the prefix."""
    line: list[str] = []
    candidates = [m for m in move_lists if m]
    while len(line) < max_plies:
        depth = len(line)
        nexts = Counter(m[depth] for m in candidates if len(m) > depth)
        if not nexts:
            break
        move, _ = nexts.most_common(1)[0]
        line.append(move)
        candidates = [m for m in candidates if len(m) > depth and m[depth] == move]
    return line


def fens_for(moves: list[str]) -> list[str]:
    """FEN after each ply. Stops early if a SAN move is illegal (bad parse)."""
    board = chess.Board()
    fens = []
    for san in moves:
        try:
            board.push_san(san)
        except ValueError:
            break
        fens.append(board.fen())
    return fens
