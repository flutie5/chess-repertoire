"""Initial schema: users, repertoire, analytics, async_jobs.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-10
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT,
            google_sub TEXT UNIQUE,
            display_name TEXT NOT NULL DEFAULT '',
            chesscom_username TEXT NOT NULL DEFAULT '',
            lichess_username TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            plan TEXT NOT NULL DEFAULT 'free',
            plan_interval TEXT,
            plan_status TEXT,
            plan_expires_at INTEGER
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub
        ON users(google_sub) WHERE google_sub IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_repertoire (
            user_id INTEGER NOT NULL,
            color TEXT NOT NULL CHECK (color IN ('white', 'black')),
            play TEXT NOT NULL,
            name TEXT NOT NULL,
            eco TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (user_id, color, play)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_daily (
            day TEXT PRIMARY KEY,
            pageviews INTEGER NOT NULL DEFAULT 0,
            sessions INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_sessions (
            day TEXT NOT NULL,
            session_id TEXT NOT NULL,
            PRIMARY KEY (day, session_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            query TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'chesscom',
            kind TEXT NOT NULL DEFAULT 'username',
            user_id INTEGER
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_analytics_searches_created "
        "ON analytics_searches(created_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS async_jobs (
            job_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL DEFAULT 'generic',
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            result_json TEXT,
            error TEXT,
            user_id INTEGER
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_async_jobs_created ON async_jobs(created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_async_jobs_status ON async_jobs(status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS async_jobs")
    op.execute("DROP TABLE IF EXISTS analytics_searches")
    op.execute("DROP TABLE IF EXISTS analytics_sessions")
    op.execute("DROP TABLE IF EXISTS analytics_daily")
    op.execute("DROP TABLE IF EXISTS user_repertoire")
    op.execute("DROP TABLE IF EXISTS users")
