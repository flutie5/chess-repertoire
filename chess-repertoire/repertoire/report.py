"""Render the analysis as a Markdown report."""

from __future__ import annotations

from datetime import datetime

from .analyze import ColorReport, Priority


def _bar(score: float, width: int = 20) -> str:
    filled = round(width * score / 100)
    return "█" * filled + "░" * (width - filled)


def _color_section(r: ColorReport, top_n: int = 15) -> str:
    lines = [f"## As {r.color.title()} — {r.total_games} games, {r.score:.1f}% score\n"]

    if r.first_moves:
        label = "You opened with" if r.color == "white" else "Opponents opened with"
        fm = ", ".join(f"**{m}** ({n})" for m, n in list(r.first_moves.items())[:5])
        lines.append(f"{label}: {fm}\n")

    lines.append("| Opening family | Games | Score | Record | ECO |")
    lines.append("|---|---:|---|---|---|")
    for o in r.openings[:top_n]:
        ecos = ", ".join(sorted(o.ecos)[:4])
        lines.append(
            f"| {o.name} | {o.games} | {o.score:.0f}% {_bar(o.score, 10)} "
            f"| +{o.wins} -{o.losses} ={o.draws} | {ecos} |"
        )
    lines.append("")

    # Variation detail for the three most-played families
    for o in r.openings[:3]:
        tv = o.top_variations()
        if len(tv) > 1:
            lines.append(f"**{o.name}** breaks down as: " + "; ".join(
                f"{v} ({n})" for v, n in tv
            ) + "\n")
    return "\n".join(lines)


def _priority_section(priorities: list[Priority]) -> str:
    if not priorities:
        return (
            "## Repertoire priorities\n\nNo frequent opening is dragging you "
            "meaningfully below your baseline. Your losses are distributed — "
            "the gains are probably in the middlegame, not the opening.\n"
        )
    lines = ["## Repertoire priorities\n",
             "Frequent openings where you score below your baseline, "
             "ranked by expected rating impact:\n"]
    for i, p in enumerate(priorities[:8], 1):
        o = p.opening
        lines.append(
            f"{i}. **{o.name}** (as {p.color}) — you face it in "
            f"{p.frequency_pct:.0f}% of games and score {o.score:.0f}%, "
            f"{p.score_gap:.0f} points below your baseline. "
            f"Most common: {', '.join(v for v, _ in o.top_variations(2))}."
        )
    lines.append("")
    return "\n".join(lines)


def render(
    username: str,
    white: ColorReport,
    black: ColorReport,
    priorities: list[Priority],
    recommendations: str | None = None,
    filters_desc: str = "",
) -> str:
    parts = [
        f"# Opening Report — {username}",
        f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}{filters_desc}_\n",
        _priority_section(priorities),
        _color_section(white),
        _color_section(black),
    ]
    if recommendations:
        parts.append("---\n\n# Recommended Repertoire\n")
        parts.append(recommendations)
    return "\n".join(parts)
