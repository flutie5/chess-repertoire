"""Fetch games from the chess.com public API.

Chess.com's public API (https://api.chess.com/pub) is free, read-only, and
requires no authentication. It asks that clients send a descriptive
User-Agent and make requests serially, which we honor here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

BASE = "https://api.chess.com/pub"
DEFAULT_UA = "repertoire-builder (personal opening analysis script)"
REQUEST_DELAY_SECONDS = 0.5  # be polite; chess.com asks for serial access


class ChessComError(RuntimeError):
    pass


def _get(url: str, user_agent: str) -> dict:
    resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
    if resp.status_code == 404:
        raise ChessComError(
            f"404 from chess.com for {url} — check the username spelling."
        )
    if resp.status_code == 429:
        # Rate limited: back off once and retry.
        time.sleep(5)
        resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def list_archives(username: str, user_agent: str = DEFAULT_UA) -> list[str]:
    """Return the list of monthly archive URLs for a user, oldest first."""
    data = _get(f"{BASE}/player/{username.lower()}/games/archives", user_agent)
    return data.get("archives", [])


def fetch_games(
    username: str,
    months: int = 12,
    user_agent: str = DEFAULT_UA,
    cache_dir: Path | None = None,
    verbose: bool = True,
) -> list[dict]:
    """Fetch games from the most recent `months` monthly archives.

    Each returned dict is a chess.com game object (contains "pgn",
    "time_class", "white", "black", "rules", etc.).

    Completed months are cached to disk so repeat runs only re-fetch the
    current (still-changing) month.
    """
    archives = list_archives(username, user_agent)
    if not archives:
        raise ChessComError(f"No archives found for user '{username}'.")

    selected = archives[-months:] if months > 0 else archives
    current_month_url = archives[-1]
    games: list[dict] = []

    for url in selected:
        cached = None
        cache_file = None
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            # .../games/2026/07 -> 2026-07.json
            year, month = url.rstrip("/").split("/")[-2:]
            cache_file = cache_dir / f"{username.lower()}-{year}-{month}.json"
            # Never trust cache for the in-progress month.
            if cache_file.exists() and url != current_month_url:
                cached = json.loads(cache_file.read_text())

        if cached is not None:
            month_games = cached
        else:
            data = _get(url, user_agent)
            month_games = data.get("games", [])
            if cache_file is not None:
                cache_file.write_text(json.dumps(month_games))
            time.sleep(REQUEST_DELAY_SECONDS)

        if verbose:
            print(f"  {url.split('/games/')[-1]}: {len(month_games)} games")
        games.extend(month_games)

    return games
