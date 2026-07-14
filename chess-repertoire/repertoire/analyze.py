"""Aggregate parsed games into opening statistics and repertoire priorities.

The core idea: your repertoire should be built against what you actually
face, weighted by how badly it's going. A line you see twice a year at 50%
doesn't matter; a line you see every third game at 38% is priority one.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .parse import Game


@dataclass
class OpeningStats:
    name: str
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    ecos: set = field(default_factory=set)
    variations: dict = field(default_factory=lambda: defaultdict(int))
    example_urls: list = field(default_factory=list)

    @property
    def score(self) -> float:
        """Score percentage (win = 1, draw = 0.5)."""
        if self.games == 0:
            return 0.0
        return 100.0 * (self.wins + 0.5 * self.draws) / self.games

    def add(self, g: Game) -> None:
        self.games += 1
        if g.result == "win":
            self.wins += 1
        elif g.result == "loss":
            self.losses += 1
        else:
            self.draws += 1
        if g.eco:
            self.ecos.add(g.eco)
        self.variations[g.opening_full] += 1
        if len(self.example_urls) < 3:
            self.example_urls.append(g.url)

    def top_variations(self, n: int = 4) -> list[tuple[str, int]]:
        return sorted(self.variations.items(), key=lambda kv: -kv[1])[:n]


@dataclass
class ColorReport:
    color: str
    total_games: int
    score: float
    openings: list[OpeningStats]          # sorted by frequency desc
    first_moves: dict                      # first move -> count (opponent's or yours)


@dataclass
class Priority:
    """A frequent opening where your score is below your overall baseline."""
    color: str
    opening: OpeningStats
    frequency_pct: float
    score_gap: float  # baseline score minus this opening's score (positive = underperforming)


def analyze(
    games: list[Game],
    time_classes: set[str] | None = None,
    rated_only: bool = True,
) -> tuple[ColorReport, ColorReport, list[Priority]]:
    """Returns (white_report, black_report, priorities)."""
    filtered = [
        g for g in games
        if (time_classes is None or g.time_class in time_classes)
        and (not rated_only or g.rated)
    ]

    reports = {}
    for color in ("white", "black"):
        subset = [g for g in filtered if g.color == color]
        buckets: dict[str, OpeningStats] = {}
        first_moves: dict[str, int] = defaultdict(int)
        for g in subset:
            buckets.setdefault(
                g.opening_family, OpeningStats(name=g.opening_family)
            ).add(g)
            if g.first_move:
                first_moves[g.first_move] += 1
        openings = sorted(buckets.values(), key=lambda o: -o.games)
        total = len(subset)
        pts = sum(1 for g in subset if g.result == "win") + 0.5 * sum(
            1 for g in subset if g.result == "draw"
        )
        reports[color] = ColorReport(
            color=color,
            total_games=total,
            score=(100.0 * pts / total) if total else 0.0,
            openings=openings,
            first_moves=dict(sorted(first_moves.items(), key=lambda kv: -kv[1])),
        )

    priorities = _find_priorities(reports["white"]) + _find_priorities(reports["black"])
    priorities.sort(key=lambda p: -(p.frequency_pct * max(p.score_gap, 0.0)))
    return reports["white"], reports["black"], priorities


def _find_priorities(
    report: ColorReport,
    min_games: int = 8,
    min_frequency_pct: float = 4.0,
) -> list[Priority]:
    out = []
    for op in report.openings:
        if op.games < min_games or report.total_games == 0:
            continue
        freq = 100.0 * op.games / report.total_games
        if freq < min_frequency_pct:
            continue
        gap = report.score - op.score
        if gap > 2.0:  # meaningfully below baseline
            out.append(
                Priority(
                    color=report.color,
                    opening=op,
                    frequency_pct=freq,
                    score_gap=gap,
                )
            )
    return out
