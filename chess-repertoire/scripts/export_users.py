#!/usr/bin/env python3
"""Export signup accounts (email, etc.) from users.db to a CSV file.

Usage (from chess-repertoire/):
    python scripts/export_users.py
    python scripts/export_users.py -o /tmp/users.csv
    DATA_DIR=/data python scripts/export_users.py

Does not include password hashes. Safe for admin review / mailing lists.
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "webapp" / "users.db"


def resolve_db() -> Path:
    data_dir = os.environ.get("DATA_DIR", "").strip()
    if data_dir:
        return Path(data_dir) / "users.db"
    return DEFAULT_DB


def main() -> int:
    parser = argparse.ArgumentParser(description="Export signup emails to CSV")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (default: users-YYYYMMDD.csv in cwd)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to users.db (default: DATA_DIR/users.db or webapp/users.db)",
    )
    args = parser.parse_args()

    db_path = args.db or resolve_db()
    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = args.output or Path(f"users-{stamp}.csv")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, email, display_name, created_at, "
            "google_sub, password_hash, plan, plan_status "
            "FROM users ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id",
            "email",
            "display_name",
            "created_at_utc",
            "auth_provider",
            "has_password",
            "plan",
            "plan_status",
        ])
        for r in rows:
            created = int(r["created_at"] or 0)
            created_iso = (
                datetime.fromtimestamp(created, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                if created
                else ""
            )
            writer.writerow([
                int(r["id"]),
                r["email"] or "",
                (r["display_name"] or "").strip(),
                created_iso,
                "google" if (r["google_sub"] or "").strip() else "password",
                "1" if (r["password_hash"] or "").strip() else "0",
                (r["plan"] or "free"),
                r["plan_status"] or "",
            ])

    print(f"Wrote {len(rows)} user(s) to {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
