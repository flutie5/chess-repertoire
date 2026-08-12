"""Simple SM-2 spaced repetition helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SrsCard:
    ease: float = 2.5
    interval_days: float = 0.0
    repetitions: int = 0
    due_at: float = 0.0  # unix seconds


def review(card: SrsCard, quality: int, now: float) -> SrsCard:
    """Update card after a review. quality: 0=again .. 5=easy."""
    q = max(0, min(5, int(quality)))
    ease = max(1.3, card.ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)))
    if q < 3:
        reps = 0
        interval = 0.0
    else:
        reps = card.repetitions + 1
        if reps == 1:
            interval = 1.0
        elif reps == 2:
            interval = 3.0
        else:
            interval = max(1.0, card.interval_days * ease)
    due = now + interval * 86400.0
    return SrsCard(ease=ease, interval_days=interval, repetitions=reps, due_at=due)
