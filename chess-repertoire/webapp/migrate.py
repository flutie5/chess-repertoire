"""Run Alembic migrations (used on boot when AUTO_MIGRATE is enabled)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")


def should_auto_migrate() -> bool:
    flag = os.environ.get("AUTO_MIGRATE", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    # Default: auto-migrate outside production so local/tests stay easy.
    return os.environ.get("FLASK_ENV", "").strip() != "production"
