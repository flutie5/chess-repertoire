"""Unit tests for opening-move quality classification helpers."""

from repertoire.classify import (
    MoveFlag,
    classify_severity,
    find_patterns,
    summarize_flags,
)


def test_severity_thresholds():
    assert classify_severity(40) is None
    assert classify_severity(50) == "inaccuracy"
    assert classify_severity(100) == "mistake"
    assert classify_severity(200) == "blunder"


def test_summarize_worst_is_blunder():
    flags = [
        MoveFlag(2, "f6", "inaccuracy", 60, "e5", "black"),
        MoveFlag(6, "Qh4", "blunder", 320, "Nf6", "black"),
        MoveFlag(4, "Ke7", "mistake", 120, "Nc6", "black"),
    ]
    s = summarize_flags(flags)
    assert s["worst"] == "blunder"
    assert s["counts"]["blunder"] == 1
    assert s["total"] == 3


def test_repeated_patterns():
    games = [
        {
            "index": 0,
            "variation": "Scotch Game",
            "opponent": "a",
            "flags": [MoveFlag(6, "Bf4", "blunder", 250, "Nc3", "white")],
        },
        {
            "index": 1,
            "variation": "Scotch Game",
            "opponent": "b",
            "flags": [MoveFlag(6, "Bf4", "blunder", 280, "Nc3", "white")],
        },
        {
            "index": 2,
            "variation": "French Defense",
            "opponent": "c",
            "flags": [MoveFlag(6, "Bf4", "blunder", 300, "Nc3", "white")],
        },
    ]
    pats = find_patterns(games, min_count=2)
    assert len(pats) == 1
    assert pats[0]["move"] == "Bf4"
    assert pats[0]["count"] == 2
    assert pats[0]["variation"] == "Scotch Game"
