"""Tests for parse and analyze using fixtures shaped like real chess.com
API game objects (PGN header format, ECOUrl slugs, JSON fields)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repertoire import analyze, parse
from repertoire.recommend import build_prompt

PGN_TEMPLATE = """[Event "Live Chess"]
[Site "Chess.com"]
[Result "{result}"]
[ECO "{eco}"]
[ECOUrl "https://www.chess.com/openings/{slug}"]
[TimeControl "600"]

1. {first} e5 2. Nf3 Nc6 3. Bb5 a6 {result}
"""


def make_game(me, color, result_tag, eco, slug, first="e4",
              time_class="rapid", rated=True, rules="chess"):
    white = {"username": me if color == "white" else "opp", "rating": 1500}
    black = {"username": me if color == "black" else "opp", "rating": 1480}
    return {
        "white": white,
        "black": black,
        "pgn": PGN_TEMPLATE.format(result=result_tag, eco=eco, slug=slug, first=first),
        "time_class": time_class,
        "rated": rated,
        "rules": rules,
        "end_time": 1750000000,
        "url": "https://www.chess.com/game/live/1",
    }


def test_parse_colors_and_results():
    g = parse.parse_game(make_game("alex", "white", "1-0", "C60",
                                   "Ruy-Lopez-Opening-Morphy-Defense"), "Alex")
    assert g.color == "white" and g.result == "win"
    assert g.opening_family == "Ruy Lopez Opening"

    g = parse.parse_game(make_game("alex", "black", "1-0", "B90",
                                   "Sicilian-Defense-Open-Najdorf-Variation"), "alex")
    assert g.color == "black" and g.result == "loss"
    assert g.opening_family == "Sicilian Defense"
    assert g.opening_full == "Sicilian Defense Open Najdorf Variation"

    g = parse.parse_game(make_game("alex", "black", "1/2-1/2", "D06",
                                   "Queens-Gambit-Declined"), "alex")
    assert g.result == "draw"
    assert g.opening_family == "Queens Gambit"


def test_slug_move_suffix_stripped():
    name = parse.slug_to_name(
        "https://www.chess.com/openings/Sicilian-Defense-Najdorf-Variation-6.Be3-e5-7.Nb3"
    )
    assert name == "Sicilian Defense Najdorf Variation"


def test_variants_and_missing_pgn_skipped():
    assert parse.parse_game(make_game("a", "white", "1-0", "C20", "Kings-Pawn",
                                      rules="bughouse"), "a") is None
    g = make_game("a", "white", "1-0", "C20", "Kings-Pawn")
    del g["pgn"]
    assert parse.parse_game(g, "a") is None


def test_first_move_extracted():
    g = parse.parse_game(make_game("a", "white", "1-0", "A00",
                                   "Kings-Fianchetto-Opening", first="g3"), "a")
    assert g.first_move == "g3"


def test_analyze_scores_and_priorities():
    raw = []
    # As black: 12 Ruy Lopez games at a terrible score (2 wins) — priority.
    for i in range(12):
        raw.append(make_game("alex", "black", "0-1" if i < 2 else "1-0",
                             "C60", "Ruy-Lopez-Opening"))
    # As black: 10 Italian games at a strong score (8 wins).
    for i in range(10):
        raw.append(make_game("alex", "black", "0-1" if i < 8 else "1-0",
                             "C50", "Italian-Game"))
    # As white: 5 Sicilians, all wins.
    for _ in range(5):
        raw.append(make_game("alex", "white", "1-0", "B20", "Sicilian-Defense"))
    # One unrated game that should be excluded by default.
    raw.append(make_game("alex", "white", "0-1", "B20", "Sicilian-Defense",
                         rated=False))

    games = parse.parse_games(raw, "alex")
    white, black, priorities = analyze.analyze(games)

    assert white.total_games == 5 and white.score == 100.0
    assert black.total_games == 22
    ruy = next(o for o in black.openings if o.name == "Ruy Lopez Opening")
    assert ruy.games == 12 and round(ruy.score, 1) == round(100 * 2 / 12, 1)

    assert priorities, "Ruy Lopez should be flagged as a priority"
    top = priorities[0]
    assert top.opening.name == "Ruy Lopez Opening" and top.color == "black"

    # Unrated game excluded unless requested
    w2, _, _ = analyze.analyze(games, rated_only=False)
    assert w2.total_games == 6


def test_time_class_filter():
    raw = [make_game("a", "white", "1-0", "B20", "Sicilian-Defense",
                     time_class="bullet") for _ in range(3)]
    raw += [make_game("a", "white", "1-0", "C50", "Italian-Game",
                      time_class="rapid") for _ in range(2)]
    games = parse.parse_games(raw, "a")
    white, _, _ = analyze.analyze(games, time_classes={"rapid"})
    assert white.total_games == 2
    assert white.openings[0].name == "Italian Game"


def test_prompt_builds_valid_json_payload():
    raw = [make_game("a", "black", "1-0", "C60", "Ruy-Lopez-Opening")
           for _ in range(10)]
    games = parse.parse_games(raw, "a")
    white, black, priorities = analyze.analyze(games)
    prompt = build_prompt(white, black, priorities)
    assert "as_black" in prompt and "Ruy Lopez Opening" in prompt
