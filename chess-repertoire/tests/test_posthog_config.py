"""PostHog config exposure + user id for identify()."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_TMP = tempfile.mkdtemp(prefix="oe-posthog-")
os.environ["DATA_DIR"] = _TMP
os.environ["SECRET_KEY"] = "test-secret-key-posthog"
os.environ.pop("FLASK_ENV", None)
os.environ.pop("POSTHOG_PROJECT_API_KEY", None)
os.environ.pop("POSTHOG_HOST", None)

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


def test_auth_config_omits_posthog_when_unset(client):
    data = client.get("/api/auth/config").get_json()
    assert data.get("posthog_project_api_key") is None
    assert data.get("posthog_host") is None


def test_auth_config_exposes_posthog_when_set(client):
    with patch("app.POSTHOG_PROJECT_API_KEY", "phc_test_key"), patch(
        "app.POSTHOG_HOST", "https://eu.i.posthog.com"
    ):
        data = client.get("/api/auth/config").get_json()
    assert data["posthog_project_api_key"] == "phc_test_key"
    assert data["posthog_host"] == "https://eu.i.posthog.com"


def test_me_includes_stable_user_id_for_identify(client):
    resp = client.post(
        "/api/register",
        json={"email": "player@example.com", "password": "password123"},
    )
    assert resp.status_code == 201
    user = resp.get_json()
    assert isinstance(user["id"], int)
    assert user["id"] > 0
    assert user["email"] == "player@example.com"
    assert "created_at" in user

    me = client.get("/api/me").get_json()
    assert me["id"] == user["id"]
