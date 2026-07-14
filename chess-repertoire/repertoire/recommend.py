"""Generate a concrete repertoire from the aggregated stats via the Claude API.

Requires ANTHROPIC_API_KEY in the environment. If it's absent, the CLI
skips this step and you still get the full statistical report.
"""

from __future__ import annotations

import json
import os

import requests

from .analyze import ColorReport, Priority

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a chess opening coach building a practical repertoire
for a club-level player based on their real game data. Be concrete: name the
exact variation to adopt, give the defining move sequence in SAN (6-10 moves
deep), state the main plan in one or two sentences, and name the single most
common trap or mistake to avoid. Prefer sound, low-maintenance lines over
sharp theory-heavy ones unless the data shows the player scores well in
tactical positions. Structure the response in Markdown with sections for
White and Black, ordered by priority. End with a one-week study plan."""


def _report_to_dict(r: ColorReport, top_n: int = 12) -> dict:
    return {
        "color": r.color,
        "total_games": r.total_games,
        "overall_score_pct": round(r.score, 1),
        "first_moves_faced_or_played": r.first_moves,
        "openings": [
            {
                "family": o.name,
                "games": o.games,
                "score_pct": round(o.score, 1),
                "record": f"+{o.wins}-{o.losses}={o.draws}",
                "ecos": sorted(o.ecos),
                "top_variations": [
                    {"line": v, "games": n} for v, n in o.top_variations()
                ],
            }
            for o in r.openings[:top_n]
        ],
    }


def build_prompt(
    white: ColorReport, black: ColorReport, priorities: list[Priority]
) -> str:
    payload = {
        "as_white": _report_to_dict(white),
        "as_black": _report_to_dict(black),
        "priority_problems": [
            {
                "color": p.color,
                "opening": p.opening.name,
                "frequency_pct": round(p.frequency_pct, 1),
                "score_pct": round(p.opening.score, 1),
                "points_below_baseline": round(p.score_gap, 1),
                "most_common_variations": [
                    v for v, _ in p.opening.top_variations()
                ],
            }
            for p in priorities[:8]
        ],
    }
    return (
        "Here is my opening data from recent chess.com games. Build me a "
        "repertoire that fixes my priority problems first, then rounds out "
        "the rest.\n\n" + json.dumps(payload, indent=2)
    )


def get_recommendations(
    white: ColorReport,
    black: ColorReport,
    priorities: list[Priority],
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> str:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set — skipping AI recommendations."
        )
    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 4000,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": build_prompt(white, black, priorities)}
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return "\n".join(
        block["text"] for block in data.get("content", []) if block.get("type") == "text"
    )
