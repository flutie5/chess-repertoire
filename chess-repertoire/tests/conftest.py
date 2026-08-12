"""Session cleanup so daemon/engine resources do not keep pytest alive."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _shutdown_platform_resources():
    yield
    try:
        from app import _engine_pool, _job_store

        _job_store.shutdown(wait=False)
        _engine_pool.shutdown()
    except Exception:
        pass
