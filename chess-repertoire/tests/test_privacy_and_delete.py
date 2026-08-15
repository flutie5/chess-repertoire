"""Privacy pages, AI/third-party disclosure text, and account deletion."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="oe-privacy-test-")
os.environ["DATA_DIR"] = _TMP
os.environ["SECRET_KEY"] = "test-secret-key-privacy-delete"
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
def _clean():
    with _db() as conn:
        for table in (
            "users",
            "learning_cards",
            "user_habits",
            "shared_reports",
            "auth_sessions",
            "user_repertoire",
            "async_jobs",
            "analytics_searches",
        ):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
    yield


def _register(client, email="deleteme@example.com", password="password123"):
    resp = client.post("/api/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _csrf(client):
    cfg = client.get("/api/auth/config").get_json()
    return cfg.get("csrf_token") or ""


def test_privacy_policy_covers_ai_and_third_parties(client):
    resp = client.get("/privacy")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "Anthropic" in text
    assert "Claude" in text
    assert "Stockfish" in text
    assert "Google" in text
    assert "Stripe" in text
    assert "PostHog" in text
    assert "We collect account, usage, and chess data" in text


def test_privacy_html_alias(client):
    resp = client.get("/privacy.html")
    assert resp.status_code == 200
    assert "Privacy Policy" in resp.get_data(as_text=True)


def test_terms_page(client):
    resp = client.get("/terms")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "Terms of Use" in text
    assert "Anthropic" in text
    assert "Stockfish" in text


def test_delete_requires_login(client):
    resp = client.delete("/api/me", json={"confirm": "DELETE"})
    assert resp.status_code == 401


def test_delete_requires_csrf_and_confirm(client):
    _register(client)
    token = _csrf(client)
    missing_csrf = client.delete("/api/me", json={"confirm": "DELETE"})
    assert missing_csrf.status_code == 403
    bad_confirm = client.delete(
        "/api/me",
        json={"confirm": "please"},
        headers={"X-CSRF-Token": token},
    )
    assert bad_confirm.status_code == 400


def test_delete_account_erases_user_and_repertoire(client):
    _register(client)
    token = _csrf(client)
    with _db() as conn:
        uid = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            ("deleteme@example.com",),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO user_repertoire (user_id, color, play, name, eco) "
            "VALUES (?, 'white', 'e2e4', 'King Pawn', 'B00')",
            (uid,),
        )

    resp = client.delete(
        "/api/me",
        json={"confirm": "DELETE"},
        headers={"X-CSRF-Token": token},
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["ok"] is True

    me = client.get("/api/me")
    assert me.status_code == 401

    with _db() as conn:
        assert conn.execute(
            "SELECT id FROM users WHERE email = ?",
            ("deleteme@example.com",),
        ).fetchone() is None
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM user_repertoire WHERE user_id = ?",
            (uid,),
        ).fetchone()["n"] == 0
