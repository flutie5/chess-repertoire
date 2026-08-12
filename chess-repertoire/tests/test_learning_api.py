"""Learning / CSRF / share / train-today endpoints."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

_TMP = tempfile.mkdtemp(prefix="oe-learn-test-")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("SECRET_KEY", "test-secret-key-learning")
os.environ.setdefault("GOOGLE_CLIENT_ID", "")
os.environ.pop("FLASK_ENV", None)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "webapp"))
sys.path.insert(0, str(ROOT))

from app import _db, app  # noqa: E402

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _clean():
    with _db() as conn:
        for table in (
            "users",
            "learning_cards",
            "user_habits",
            "shared_reports",
            "auth_sessions",
            "user_repertoire",
        ):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
    yield


def _register(client, email="learner@example.com", password="password123"):
    resp = client.post("/api/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _csrf(client):
    cfg = client.get("/api/auth/config").get_json()
    return cfg.get("csrf_token") or ""


def test_train_today_requires_login(client):
    resp = client.get("/api/me/train-today")
    assert resp.status_code == 401


def test_learning_card_flow(client):
    _register(client)
    token = _csrf(client)
    with patch("app._billing_configured", return_value=False):
        add = client.post(
            "/api/me/learning/cards",
            json={
                "cards": [{
                    "fen": START_FEN,
                    "prompt": "Best first move?",
                    "answer_san": "e4",
                    "kind": "puzzle",
                }],
            },
            headers={"X-CSRF-Token": token},
        )
    assert add.status_code == 201, add.get_json()
    assert add.get_json()["inserted"] == 1

    due = client.get("/api/me/learning/due")
    assert due.status_code == 200
    cards = due.get_json()["cards"]
    assert len(cards) == 1
    card_id = cards[0]["id"]

    token = _csrf(client)
    rev = client.post(
        "/api/me/learning/review",
        json={"card_id": card_id, "quality": 4},
        headers={"X-CSRF-Token": token},
    )
    assert rev.status_code == 200, rev.get_json()
    body = rev.get_json()
    assert body["repetitions"] == 1
    assert body["habits"]["reviews_today"] >= 1

    plan = client.get("/api/me/train-today")
    assert plan.status_code == 200
    assert "steps" in plan.get_json()


def test_csrf_rejects_bad_token(client):
    _register(client)
    with patch("app._billing_configured", return_value=False):
        resp = client.post(
            "/api/me/learning/cards",
            json={"cards": [{"fen": START_FEN}]},
            headers={"X-CSRF-Token": "not-a-real-token"},
        )
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "csrf_failed"


def test_share_report_roundtrip(client):
    payload = {"username": "tester", "white": {"openings": []}, "black": {"openings": []}}
    create = client.post("/api/report/share", json={"report": payload, "title": "t"})
    assert create.status_code == 201, create.get_json()
    share_id = create.get_json()["share_id"]
    got = client.get(f"/api/report/share/{share_id}")
    assert got.status_code == 200
    assert got.get_json()["report"]["username"] == "tester"


def test_opening_popularity_empty_fen(client):
    resp = client.get("/api/opening/popularity")
    assert resp.status_code == 400


def test_opening_popularity_mocked(client):
    with patch(
        "repertoire.human_moves.popularity_at_rating",
        return_value=[{"san": "e4", "white": 10, "draws": 2, "black": 5}],
    ):
        resp = client.get(f"/api/opening/popularity?fen={START_FEN}&elo=1800")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["moves"][0]["san"] == "e4"


def test_auth_config_includes_csrf(client):
    data = client.get("/api/auth/config").get_json()
    assert data.get("csrf_token")
