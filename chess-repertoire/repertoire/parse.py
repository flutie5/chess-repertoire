"""Parse chess.com game objects into a flat, analysis-ready structure.

We rely on the PGN headers chess.com embeds in every game:
  - ECO      : the ECO code (e.g. "B90")
  - ECOUrl   : a URL whose slug is the full opening/variation name
  - Result   : "1-0", "0-1", "1/2-1/2"
plus the top-level JSON fields (time_class, rated, white/black usernames
and ratings, end_time).

No engine, no move generation — header parsing only, which keeps this
fast enough to chew through years of games in milliseconds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADER_RE = re.compile(r'^\[(\w+)\s+"([^"]*)"\]', re.MULTILINE)

# Tokens that mark the end of an opening *family* name inside the ECOUrl
# slug. "Sicilian-Defense-Open-Najdorf-Variation" -> family "Sicilian Defense".
FAMILY_TERMINATORS = {
    "Defense",
    "Defence",
    "Opening",
    "Game",
    "Gambit",
    "Attack",
    "System",
    "Variation",
    "Counter-Gambit",
    "Countergambit",
}

# First-move buckets so you can see the 1.e4 / 1.d4 split at a glance.
FIRST_MOVE_RE = re.compile(r"\n1\.\s*(?:\{[^}]*\}\s*)?([a-hRNBQKO][\w+#=-]*)")


@dataclass
class Game:
    color: str            # "white" | "black"
    result: str           # "win" | "loss" | "draw"
    eco: str              # "B90" (may be "" if absent)
    opening_full: str     # "Sicilian Defense: Najdorf Variation, English Attack"
    opening_family: str   # "Sicilian Defense"
    first_move: str       # "e4", "d4", "Nf3", "c4", ...
    time_class: str       # "rapid" | "blitz" | "bullet" | "daily"
    rated: bool
    opponent: str
    opponent_rating: int
    my_rating: int
    end_time: int         # unix timestamp
    url: str


def _headers(pgn: str) -> dict[str, str]:
    return dict(HEADER_RE.findall(pgn))


def slug_to_name(eco_url: str) -> str:
    """'https://www.chess.com/openings/Sicilian-Defense-Open-Najdorf...'
    -> 'Sicilian Defense Open Najdorf ...' (dots for move numbers kept)."""
    slug = eco_url.rstrip("/").split("/")[-1]
    # Strip trailing move sequences like '...4.Nxd4-Nf6-5.Nc3-a6'
    slug = re.sub(r"-?\d+\.[\w.+#=-]*$", "", slug)
    return slug.replace("-", " ").strip()


def family_from_name(name: str) -> str:
    """Cut the full opening name down to its family."""
    words = name.split()
    for i, w in enumerate(words):
        if w in FAMILY_TERMINATORS:
            # Keep everything up to and including the terminator, but a bare
            # leading terminator (rare malformed slug) falls through.
            if i >= 1:
                return " ".join(words[: i + 1])
    # No terminator found: first two words is the best stable guess
    # ("Kings Fianchetto", "Reti", ...).
    return " ".join(words[:2]) if len(words) >= 2 else name


def parse_game(raw: dict, username: str) -> Game | None:
    """Convert one chess.com game object to a Game. Returns None for games
    we can't analyze (no PGN, variants like bughouse, unknown result)."""
    if raw.get("rules", "chess") != "chess":
        return None
    pgn = raw.get("pgn")
    if not pgn:
        return None

    h = _headers(pgn)
    uname = username.lower()
    white = raw.get("white", {})
    black = raw.get("black", {})

    if white.get("username", "").lower() == uname:
        color, me, opp = "white", white, black
    elif black.get("username", "").lower() == uname:
        color, me, opp = "black", black, white
    else:
        return None

    result_tag = h.get("Result", "")
    if result_tag == "1/2-1/2":
        result = "draw"
    elif result_tag in ("1-0", "0-1"):
        white_won = result_tag == "1-0"
        result = "win" if (white_won == (color == "white")) else "loss"
    else:
        return None

    eco_url = h.get("ECOUrl", "")
    full = slug_to_name(eco_url) if eco_url else h.get("Opening", "Unknown")
    family = family_from_name(full) if full else "Unknown"

    fm = FIRST_MOVE_RE.search(pgn)
    first_move = fm.group(1) if fm else ""

    return Game(
        color=color,
        result=result,
        eco=h.get("ECO", ""),
        opening_full=full or "Unknown",
        opening_family=family or "Unknown",
        first_move=first_move,
        time_class=raw.get("time_class", "unknown"),
        rated=bool(raw.get("rated", False)),
        opponent=opp.get("username", "?"),
        opponent_rating=int(opp.get("rating", 0) or 0),
        my_rating=int(me.get("rating", 0) or 0),
        end_time=int(raw.get("end_time", 0) or 0),
        url=raw.get("url", ""),
    )


def parse_games(raw_games: list[dict], username: str) -> list[Game]:
    out = []
    for raw in raw_games:
        g = parse_game(raw, username)
        if g is not None:
            out.append(g)
    return out
