"""Fetch games from the lichess.org API.

Lichess exposes a free NDJSON export of a user's games
(GET https://lichess.org/api/games/user/{username}). Each line is a JSON
game object that already includes the opening (ECO + name) and the SAN
move list, so no PGN parsing is needed.

Responses are cached to disk (lichess-{username}.json) and reused for up
to an hour, mirroring the chess.com cache in fetch.py.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from . import parse
from .moves import MAX_GAME_PLIES
from .parse import Game

API_URL = "https://lichess.org/api/games/user/{username}"
DEFAULT_UA = "repertoire-builder (personal opening analysis script)"
MAX_GAMES = 2000
CACHE_TTL_SECONDS = 3600
SECONDS_PER_MONTH = int(30.44 * 86400)

# lichess "speed" -> chess.com-style time_class
SPEED_TO_TIME_CLASS = {
    "ultraBullet": "bullet",
    "bullet": "bullet",
    "blitz": "blitz",
    "rapid": "rapid",
    "classical": "daily",
    "correspondence": "daily",
}

# Finished-game statuses that mean a draw when no winner is present.
DRAW_STATUSES = {"draw", "stalemate", "outoftime", "timeout", "unknownFinish"}


class LichessError(RuntimeError):
    pass


def fetch_games(
    username: str,
    months: int = 12,
    cache_dir: Path | None = None,
    user_agent: str = DEFAULT_UA,
) -> list[dict]:
    """Fetch up to MAX_GAMES rated games from the last `months` months.

    Returns raw lichess game dicts (NDJSON lines parsed to JSON).
    """
    cache_file = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"lichess-{username.lower()}.json"
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text())
            except (json.JSONDecodeError, OSError):
                cached = None
            if (
                isinstance(cached, dict)
                and time.time() - cached.get("fetched_at", 0) < CACHE_TTL_SECONDS
            ):
                return cached.get("games", [])

    since_ms = int((time.time() - months * SECONDS_PER_MONTH) * 1000)
    resp = requests.get(
        API_URL.format(username=username),
        headers={"Accept": "application/x-ndjson", "User-Agent": user_agent},
        params={
            "rated": "true",
            "opening": "true",
            "moves": "true",
            "perfType": "bullet,blitz,rapid,classical",
            "max": MAX_GAMES,
            "since": since_ms,
        },
        timeout=120,
    )
    if resp.status_code == 404:
        raise LichessError(
            f"Lichess user '{username}' not found — check the spelling."
        )
    if resp.status_code == 429:
        raise LichessError("Rate limited by lichess — try again in a minute.")
    resp.raise_for_status()

    games = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    if cache_file is not None:
        cache_file.write_text(
            json.dumps({"fetched_at": time.time(), "games": games})
        )
    return games


def _family(full_name: str) -> str:
    """Lichess names are 'Family: Variation, Sub-variation'; the part before
    the colon matches chess.com's family names, so merged reports bucket
    both sites' games together."""
    if ":" in full_name:
        return full_name.split(":", 1)[0].strip()
    return parse.family_from_name(full_name)


def parse_game(raw: dict, username: str) -> tuple[Game, list[str]] | None:
    """Convert one lichess game dict to (Game, san_moves). Returns None for
    games we can't analyze (variants, unknown player, unfinished)."""
    if raw.get("variant", "standard") != "standard":
        return None

    players = raw.get("players", {})
    white = players.get("white", {})
    black = players.get("black", {})
    uname = username.lower()
    white_name = (white.get("user") or {}).get("name", "")
    black_name = (black.get("user") or {}).get("name", "")

    if white_name.lower() == uname:
        color, me, opp, opp_name = "white", white, black, black_name
    elif black_name.lower() == uname:
        color, me, opp, opp_name = "black", black, white, white_name
    else:
        return None

    winner = raw.get("winner")
    if winner in ("white", "black"):
        result = "win" if winner == color else "loss"
    elif raw.get("status") in DRAW_STATUSES:
        result = "draw"
    else:
        return None

    opening = raw.get("opening") or {}
    full = opening.get("name") or "Unknown"
    family = _family(full) if full != "Unknown" else "Unknown"

    san = (raw.get("moves") or "").split()[:MAX_GAME_PLIES]

    game = Game(
        color=color,
        result=result,
        eco=opening.get("eco", ""),
        opening_full=full,
        opening_family=family,
        first_move=san[0] if san else "",
        time_class=SPEED_TO_TIME_CLASS.get(raw.get("speed", ""), "unknown"),
        rated=bool(raw.get("rated", False)),
        opponent=opp_name or "?",
        opponent_rating=int(opp.get("rating", 0) or 0),
        my_rating=int(me.get("rating", 0) or 0),
        end_time=int(raw.get("lastMoveAt") or raw.get("createdAt") or 0) // 1000,
        url=f"https://lichess.org/{raw.get('id', '')}",
    )
    return game, san


def games_with_moves(
    username: str,
    months: int = 12,
    cache_dir: Path | None = None,
) -> list[tuple[Game, list[str]]]:
    """Fetch + parse in one call: list of (Game, san_moves) pairs."""
    out = []
    for raw in fetch_games(username, months=months, cache_dir=cache_dir):
        parsed = parse_game(raw, username)
        if parsed is not None:
            out.append(parsed)
    return out
