"""Unit tests for opening-move quality classification helpers."""

import chess

from repertoire.classify import (
    MoveFlag,
    classify_annotation,
    classify_severity,
    find_patterns,
    is_material_sacrifice,
    material_balance,
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


def test_classify_annotation_blunder_and_mistake():
    assert classify_annotation(
        loss_cp=220,
        played_is_best=False,
        is_sacrifice=False,
        before_cp_mover=50,
        second_best_gap_cp=0,
    ) == "blunder"
    assert classify_annotation(
        loss_cp=120,
        played_is_best=False,
        is_sacrifice=False,
        before_cp_mover=50,
        second_best_gap_cp=0,
    ) == "mistake"


def test_classify_annotation_skips_inaccuracy():
    assert classify_annotation(
        loss_cp=60,
        played_is_best=False,
        is_sacrifice=False,
        before_cp_mover=20,
        second_best_gap_cp=0,
    ) is None


def test_classify_annotation_brilliant_sacrifice():
    assert classify_annotation(
        loss_cp=10,
        played_is_best=True,
        is_sacrifice=True,
        before_cp_mover=40,
        second_best_gap_cp=10,
    ) == "brilliant"


def test_classify_annotation_great_vs_best():
    assert classify_annotation(
        loss_cp=0,
        played_is_best=True,
        is_sacrifice=False,
        before_cp_mover=30,
        second_best_gap_cp=80,
    ) == "great"
    assert classify_annotation(
        loss_cp=0,
        played_is_best=True,
        is_sacrifice=False,
        before_cp_mover=100,
        second_best_gap_cp=30,
    ) == "best"
    # Quiet equal position, sole-best gap tiny → no badge
    assert classify_annotation(
        loss_cp=0,
        played_is_best=True,
        is_sacrifice=False,
        before_cp_mover=10,
        second_best_gap_cp=5,
    ) is None


def test_classify_annotation_saves_bad_position():
    assert classify_annotation(
        loss_cp=0,
        played_is_best=True,
        is_sacrifice=False,
        before_cp_mover=-180,
        second_best_gap_cp=10,
    ) == "great"


def test_material_sacrifice_queen_takes_defended_knight():
    # White queen takes Nf6, defended by Black's pieces → hanging queen.
    before = chess.Board(
        "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    )
    move = before.parse_san("Qxf6")
    assert is_material_sacrifice(before, move) is True


def test_material_equal_trade_not_sacrifice():
    before = chess.Board()
    before.push_san("e4")
    before.push_san("d5")
    move = before.parse_san("exd5")
    # Pawn takes pawn — equal trade, not a sacrifice
    assert is_material_sacrifice(before, move) is False


def test_material_balance_startpos():
    assert material_balance(chess.Board(), chess.WHITE) == 0
