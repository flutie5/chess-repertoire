"""Auth / Pro gates on Stockfish routes."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Isolate DB before importing the Flask app (same pattern as test_auth_google).
_TMP = tempfile.mkdtemp(prefix="oe-gates-test-")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("SECRET_KEY", "test-secret-key-engine-gates")
os.environ.setdefault("GOOGLE_CLIENT_ID", "")
os.environ.pop("FLASK_ENV", None)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "webapp"))
sys.path.insert(0, str(ROOT))

from app import _db, app  # noqa: E402


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_users():
    with _db() as conn:
        conn.execute("DELETE FROM users")
        try:
            conn.execute("DELETE FROM async_jobs")
        except Exception:
            pass
    yield
    with _db() as conn:
        conn.execute("DELETE FROM users")
        try:
            conn.execute("DELETE FROM async_jobs")
        except Exception:
            pass


def _register(client, email="free@example.com", password="password123"):
    resp = client.post("/api/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def test_eval_requires_login(client):
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    resp = client.get(f"/api/eval?fen={fen}")
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["code"] == "login_required"


def test_annotate_requires_pro_when_billing_configured(client):
    _register(client)
    with patch("app._billing_configured", return_value=True):
        resp = client.post("/api/annotate-game", json={"san": ["e4", "e5"]})
    assert resp.status_code == 402
    body = resp.get_json()
    assert body["code"] == "pro_required"


def test_scan_requires_pro_when_billing_configured(client):
    _register(client)
    with patch("app._billing_configured", return_value=True):
        resp = client.post(
            "/api/scan-blunders",
            json={"games": [{"san": ["e4", "c5"], "color": "white"}]},
        )
    assert resp.status_code == 402
    assert resp.get_json()["code"] == "pro_required"


def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "ok" in body
    assert "engine_pool" in body
    assert "jobs_pending" in body


def test_pro_user_passes_annotate_auth_gate(client):
    _register(client, email="pro@example.com")
    with _db() as conn:
        conn.execute(
            "UPDATE users SET plan = 'pro', plan_status = 'active' "
            "WHERE email = ?",
            ("pro@example.com",),
        )
    with patch("app._billing_configured", return_value=True):
        resp = client.post("/api/annotate-game", json={"san": ["e4", "e5"]})
    # May 503 without Stockfish; must not be 401/402.
    assert resp.status_code not in (401, 402)
