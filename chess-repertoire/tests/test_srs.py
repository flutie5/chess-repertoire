"""Unit tests for SM-2 spaced repetition helpers."""

from repertoire.srs import SrsCard, review


def test_review_fail_resets_interval():
    card = SrsCard(ease=2.5, interval_days=10, repetitions=3, due_at=1000)
    out = review(card, quality=1, now=2000)
    assert out.repetitions == 0
    assert out.interval_days == 0.0
    assert out.due_at == 2000


def test_review_first_pass_schedules_one_day():
    card = SrsCard()
    out = review(card, quality=4, now=0)
    assert out.repetitions == 1
    assert out.interval_days == 1.0
    assert out.due_at == 86400


def test_review_second_pass_schedules_three_days():
    card = SrsCard(repetitions=1, interval_days=1.0, ease=2.5)
    out = review(card, quality=4, now=0)
    assert out.repetitions == 2
    assert out.interval_days == 3.0
