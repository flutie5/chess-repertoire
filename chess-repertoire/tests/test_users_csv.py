"""Admin CSV export of signup emails."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_TMP = tempfile.mkdtemp(prefix="oe-users-csv-")
os.environ["DATA_DIR"] = _TMP
os.environ["SECRET_KEY"] = "test-secret-key-users-csv"
os.environ.pop("FLASK_ENV", None)
os.environ.pop("ANALYTICS_ADMIN_EMAIL", None)

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
    yield
    with _db() as conn:
        conn.execute("DELETE FROM users")


def _register(client, email="admin@example.com", password="password123"):
    resp = client.post("/api/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def test_users_csv_requires_login(client):
    resp = client.get("/api/analytics/users.csv")
    assert resp.status_code == 401


def test_users_csv_forbidden_without_admin(client):
    _register(client, email="player@example.com")
    with patch("app.ANALYTICS_ADMIN_EMAIL", "admin@example.com"):
        resp = client.get("/api/analytics/users.csv")
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "analytics_forbidden"


def test_users_csv_download_for_admin(client):
    _register(client, email="admin@example.com")
    # Insert a second account without switching the session.
    from werkzeug.security import generate_password_hash

    with _db() as conn:
        conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            ("other@example.com", generate_password_hash("password123")),
        )

    with patch("app.ANALYTICS_ADMIN_EMAIL", "admin@example.com"):
        resp = client.get("/api/analytics/users.csv")

    assert resp.status_code == 200
    assert "text/csv" in resp.content_type
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    body = resp.data.decode("utf-8-sig")
    assert "email" in body.splitlines()[0]
    assert "admin@example.com" in body
    assert "other@example.com" in body
    assert "password_hash" not in body