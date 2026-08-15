"""Eval API always returns White-perspective scores (eval-bar contract)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import chess
import pytest
from chess.engine import Cp, Mate, PovScore

_TMP = tempfile.mkdtemp(prefix="oe-eval-pov-test-")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("SECRET_KEY", "test-secret-key-eval-pov")
os.environ.pop("FLASK_ENV", None)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "webapp"))
sys.path.insert(0, str(ROOT))

from app import _eval_cache, _engine_pool, app  # noqa: E402

# Reported custom position: Black to move, Black is winning on material.
CUSTOM_BLACK_TO_MOVE = "8/p6p/6p1/1p1p4/3q4/4k2P/6P1/4Q2K b - - 0 1"


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@contextmanager
def _fake_acquire(timeout=None):
    yield MagicMock()


def _eval(client, fen: str, info: dict) -> dict:
    _eval_cache.clear()
    with (
        patch.object(_engine_pool, "acquire", _fake_acquire),
        patch.object(_engine_pool, "configure_full_strength"),
        patch("app._engine_analyse", return_value=info),
    ):
        resp = client.get(f"/api/eval?fen={fen}")
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def test_eval_black_to_move_winning_is_negative_for_white(client):
    """Engine STM +420 with Black to move must not paint a White crush."""
    body = _eval(
        client,
        CUSTOM_BLACK_TO_MOVE,
        {"score": PovScore(Cp(420), chess.BLACK), "depth": 12, "pv": []},
    )
    assert body["cp"] == -420
    assert body["mate"] is None
    assert body["turn"] == "black"
    assert body["pov"] == "white"


def test_eval_black_to_move_mate_for_black_is_negative_mate(client):
    body = _eval(
        client,
        CUSTOM_BLACK_TO_MOVE,
        {"score": PovScore(Mate(3), chess.BLACK), "depth": 12, "pv": []},
    )
    assert body["mate"] == -3
    assert body["cp"] is None


def test_eval_white_to_move_keeps_sign(client):
    start = chess.STARTING_FEN
    body = _eval(
        client,
        start,
        {"score": PovScore(Cp(35), chess.WHITE), "depth": 16, "pv": []},
    )
    assert body["cp"] == 35
    assert body["turn"] == "white"


def test_frontend_eval_bar_pov_unit_tests():
    frontend = ROOT / "webapp" / "frontend"
    proc = subprocess.run(
        ["node", "--test", "test/evalBar.test.ts"],
        cwd=frontend,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
