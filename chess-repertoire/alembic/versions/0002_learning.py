"""Initial learning / habits / shares schema.

Revision ID: 0002_learning
Revises: 0001_initial
Create Date: 2026-08-11
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002_learning"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS learning_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'repertoire',
            fen TEXT NOT NULL,
            san_line TEXT NOT NULL DEFAULT '',
            prompt TEXT NOT NULL DEFAULT '',
            answer_san TEXT NOT NULL DEFAULT '',
            opening TEXT NOT NULL DEFAULT '',
            ease REAL NOT NULL DEFAULT 2.5,
            interval_days REAL NOT NULL DEFAULT 0,
            repetitions INTEGER NOT NULL DEFAULT 0,
            due_at REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_learning_cards_due "
        "ON learning_cards(user_id, due_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_habits (
            user_id INTEGER PRIMARY KEY,
            streak_days INTEGER NOT NULL DEFAULT 0,
            best_streak INTEGER NOT NULL DEFAULT 0,
            last_study_day TEXT,
            reviews_today INTEGER NOT NULL DEFAULT 0,
            study_day TEXT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS shared_reports (
            share_id TEXT PRIMARY KEY,
            user_id INTEGER,
            title TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            last_seen REAL NOT NULL,
            user_agent TEXT NOT NULL DEFAULT '',
            ip TEXT NOT NULL DEFAULT '',
            revoked INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user "
        "ON auth_sessions(user_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth_sessions")
    op.execute("DROP TABLE IF EXISTS shared_reports")
    op.execute("DROP TABLE IF EXISTS user_habits")
    op.execute("DROP TABLE IF EXISTS learning_cards")
