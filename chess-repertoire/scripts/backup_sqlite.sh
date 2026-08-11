#!/usr/bin/env bash
# Backup Opening Explorer SQLite DB (users + jobs). Copy off-box after running.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${DATA_DIR:-$ROOT/webapp}"
DB="${DATA_DIR}/users.db"
OUT_DIR="${1:-$ROOT/backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$OUT_DIR/users-$STAMP.db"
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB" ".backup '$DEST'"
else
  cp -f "$DB" "$DEST"
  # Include WAL/SHM if present (crash-safe copy prefers sqlite3 .backup)
  [[ -f "${DB}-wal" ]] && cp -f "${DB}-wal" "${DEST}-wal" || true
  [[ -f "${DB}-shm" ]] && cp -f "${DB}-shm" "${DEST}-shm" || true
fi
echo "Wrote $DEST"
ls -la "$DEST"*
