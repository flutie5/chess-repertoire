"""Confirmation tests: Google sign-in stores email and user fields accurately."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Isolate DB / secrets before importing the Flask app (module init creates tables).
_TMP = tempfile.mkdtemp(prefix="oe-auth-test-")
os.environ["DATA_DIR"] = _TMP
os.environ["SECRET_KEY"] = "test-secret-key-auth-google"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
os.environ.pop("FLASK_ENV", None)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "webapp"))
sys.path.insert(0, str(ROOT))

from app import DB_PATH, _db, app  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_users():
    with _db() as conn:
        conn.execute("DELETE FROM users")
    yield
    with _db() as conn:
        conn.execute("DELETE FROM users")


def _token_info(**overrides):
    now = int(time.time())
    info = {
        "iss": "https://accounts.google.com",
        "aud": GOOGLE_CLIENT_ID,
        "sub": "google-sub-abc",
        "email": "Player@Example.COM",
        "email_verified": True,
        "name": "Test Player",
        "iat": now,
        "exp": now + 3600,
        "nonce": "will-be-set-from-session",
    }
    info.update(overrides)
    return info


def _google_login(client, *, token_overrides=None, patch_verify=True):
    """Fetch nonce via /api/auth/config, then POST credential with matching nonce."""
    cfg = client.get("/api/auth/config")
    assert cfg.status_code == 200
    body = cfg.get_json()
    assert body["google_client_id"] == GOOGLE_CLIENT_ID
    nonce = body["nonce"]
    assert nonce

    info = _token_info(nonce=nonce, **(token_overrides or {}))

    if not patch_verify:
        return client.post("/api/auth/google", json={"credential": "fake.jwt"})

    with patch(
        "google.oauth2.id_token.verify_oauth2_token",
        return_value=info,
    ) as mocked:
        resp = client.post("/api/auth/google", json={"credential": "fake.jwt.token"})
        mocked.assert_called_once()
        return resp


def _row_by_email(email: str):
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower(),)
        ).fetchone()


def test_db_path_uses_isolated_temp_dir():
    assert str(DB_PATH).startswith(_TMP) or DB_PATH.parent == Path(_TMP)


def test_auth_config_issues_nonce(client):
    r = client.get("/api/auth/config")
    assert r.status_code == 200
    data = r.get_json()
    assert data["google_client_id"] == GOOGLE_CLIENT_ID
    assert isinstance(data["nonce"], str) and len(data["nonce"]) >= 16


def test_google_signup_stores_email_and_profile_fields(client):
    resp = _google_login(client)
    assert resp.status_code == 200, resp.get_json()
    payload = resp.get_json()

    assert payload["email"] == "player@example.com"
    assert payload["display_name"] == "Test Player"
    assert payload["auth_provider"] == "google"
    assert payload["has_password"] is False
    assert payload["is_new_account"] is True
    assert payload["chesscom_username"] == ""
    assert payload["lichess_username"] == ""

    row = _row_by_email("player@example.com")
    assert row is not None
    assert row["email"] == "player@example.com"
    assert row["google_sub"] == "google-sub-abc"
    assert row["display_name"] == "Test Player"
    assert row["password_hash"] == ""
    assert (row["chesscom_username"] or "") == ""
    assert (row["lichess_username"] or "") == ""


def test_google_signup_session_round_trip_via_me(client):
    resp = _google_login(client)
    assert resp.status_code == 200
    me = client.get("/api/me")
    assert me.status_code == 200
    data = me.get_json()
    assert data["email"] == "player@example.com"
    assert data["auth_provider"] == "google"
    assert data["display_name"] == "Test Player"


def test_returning_google_user_not_duplicated(client):
    first = _google_login(client)
    assert first.status_code == 200
    assert first.get_json()["is_new_account"] is True

    second = _google_login(client)
    assert second.status_code == 200
    assert second.get_json()["is_new_account"] is False
    assert second.get_json()["email"] == "player@example.com"

    with _db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE email = ?",
            ("player@example.com",),
        ).fetchone()["n"]
    assert count == 1


def test_google_links_existing_password_account_by_verified_email(client):
    with _db() as conn:
        conn.execute(
            "INSERT INTO users (email, password_hash, display_name) VALUES (?, ?, ?)",
            (
                "player@example.com",
                generate_password_hash("password123"),
                "Existing Name",
            ),
        )

    resp = _google_login(client)
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["email"] == "player@example.com"
    assert payload["display_name"] == "Existing Name"  # keep existing name
    assert payload["has_password"] is True
    assert payload["auth_provider"] == "google"
    assert payload["is_new_account"] is False

    row = _row_by_email("player@example.com")
    assert row["google_sub"] == "google-sub-abc"
    assert row["display_name"] == "Existing Name"


def test_google_rejects_unverified_email(client):
    resp = _google_login(client, token_overrides={"email_verified": False})
    assert resp.status_code == 400
    assert "not verified" in resp.get_json()["error"].lower()
    assert _row_by_email("player@example.com") is None


def test_google_rejects_missing_email(client):
    resp = _google_login(client, token_overrides={"email": ""})
    assert resp.status_code == 400
    assert _row_by_email("player@example.com") is None


def test_auth_config_reuses_unused_nonce(client):
    first = client.get("/api/auth/config").get_json()["nonce"]
    second = client.get("/api/auth/config").get_json()["nonce"]
    assert first and first == second


def test_auth_config_rotates_nonce_after_login(client):
    before = client.get("/api/auth/config").get_json()["nonce"]
    resp = _google_login(client)
    assert resp.status_code == 200
    after = client.get("/api/auth/config").get_json()["nonce"]
    assert after and after != before


def test_google_accepts_sha256_hex_nonce(client):
    cfg = client.get("/api/auth/config")
    nonce = cfg.get_json()["nonce"]
    hashed = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    info = _token_info(nonce=hashed)
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=info):
        resp = client.post("/api/auth/google", json={"credential": "fake.jwt"})
    assert resp.status_code == 200, resp.get_json()


def test_google_accepts_missing_fedcm_nonce(client):
    info = _token_info(nonce="")
    cfg = client.get("/api/auth/config")
    assert cfg.get_json()["nonce"]
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=info):
        resp = client.post("/api/auth/google", json={"credential": "fake.jwt"})
    assert resp.status_code == 200, resp.get_json()


def test_google_rejects_nonce_mismatch(client):
    cfg = client.get("/api/auth/config")
    nonce = cfg.get_json()["nonce"]
    info = _token_info(nonce="totally-wrong-nonce")
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=info):
        resp = client.post("/api/auth/google", json={"credential": "fake.jwt"})
    assert resp.status_code == 401
    assert resp.get_json().get("code") == "nonce_mismatch"
    # Expected nonce from config should still be usable until success — session still has it.
    assert nonce


def test_google_rejects_stale_token_iat(client):
    old_iat = int(time.time()) - (10 * 60)
    resp = _google_login(client, token_overrides={"iat": old_iat})
    assert resp.status_code == 401
    assert "expired" in resp.get_json()["error"].lower()


def test_google_updates_empty_display_name_on_return(client):
    with _db() as conn:
        conn.execute(
            "INSERT INTO users (email, password_hash, google_sub, display_name) "
            "VALUES (?, '', ?, ?)",
            ("player@example.com", "google-sub-abc", ""),
        )
    resp = _google_login(client)
    assert resp.status_code == 200
    assert resp.get_json()["display_name"] == "Test Player"
    assert _row_by_email("player@example.com")["display_name"] == "Test Player"


def test_google_syncs_verified_email_change_for_same_sub(client):
    with _db() as conn:
        conn.execute(
            "INSERT INTO users (email, password_hash, google_sub, display_name) "
            "VALUES (?, '', ?, ?)",
            ("old@example.com", "google-sub-abc", "Test Player"),
        )
    resp = _google_login(
        client,
        token_overrides={"email": "New.Email@Example.COM"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["email"] == "new.email@example.com"
    assert _row_by_email("new.email@example.com") is not None
    assert _row_by_email("old@example.com") is None
