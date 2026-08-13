# AGENTS.md

## Cursor Cloud specific instructions

This repo is a monorepo whose application lives under `chess-repertoire/`. It is
"Opening Explorer": a Flask backend (`chess-repertoire/webapp/app.py`) that serves
a Vite-built SPA from `chess-repertoire/webapp/static/` (source under
`chess-repertoire/webapp/frontend/`) plus a JSON `/api/*` API, backed by the
`chess-repertoire/repertoire` Python package. There is also a CLI
(`chess-repertoire/build_repertoire.py`). See `chess-repertoire/README.md` and
`chess-repertoire/DEPLOY.md` for full docs.

### Environment / running

- Python deps are installed into a virtualenv at `chess-repertoire/.venv` (the
  system Python is PEP-668 externally-managed, so a venv is required). The startup
  update script keeps it in sync with `requirements.txt`. Activate with
  `. chess-repertoire/.venv/bin/activate` (or call `chess-repertoire/.venv/bin/python`
  directly).
- Frontend: `cd chess-repertoire/webapp/frontend && npm ci && npm run build` writes
  production assets into `webapp/static/`. Dev UI: `npm run dev` on :5173 (proxies
  `/api` → Flask :5000). Flask alone serves the last build from `webapp/static/`.
- Run the API from the `chess-repertoire/` directory: `python webapp/app.py`.
  It listens on `http://127.0.0.1:5000` (host/port are hardcoded in the
  `__main__` block; `debug=False`). It must be started from `chess-repertoire/`
  because `webapp/wsgi.py` / imports assume that as the working dir.
- Schema: Alembic (`chess-repertoire/alembic/`). Non-production auto-migrates on
  boot unless `AUTO_MIGRATE=0`. Production: `alembic upgrade head` as a release step.
- Tests: from `chess-repertoire/`, run `python -m pytest tests/ -q`. Frontend typecheck:
  `npm run typecheck` in `webapp/frontend/`.
- The CLI: from `chess-repertoire/`, `python build_repertoire.py <chesscom-username>`.

### Non-obvious gotchas

- Stockfish is required only for the eval bar / game-review endpoints (`/api/eval`,
  `/api/annotate-game`, `/api/scan-blunders`). The binary is NOT committed; it lives
  at `chess-repertoire/engine/linux/stockfish` (gitignored). If it is missing, run
  `bash scripts/download_stockfish.sh` from `chess-repertoire/` (downloads ~76 MB
  from GitHub). The core opening-report flow works without it. `/api/eval` is
  free (rate-limited); annotate/scan require Pro when Stripe is configured. Keep Gunicorn
  `--workers 1` while Stockfish runs in-process.
- The report flow (`/api/report?username=...`) fetches real games from the public
  chess.com API over the network — no API key needed. Completed months are cached to
  `.chesscom-cache/` (quotas: `CACHE_MAX_MB` / `CACHE_MAX_FILES`).
- Optional integrations are OFF unless env vars are set (all optional for local
  dev): `GOOGLE_CLIENT_ID` (Google sign-in), `STRIPE_*` (Pro billing gates),
  `ANTHROPIC_API_KEY` (AI repertoire recommendations in the CLI),
  `POSTHOG_PROJECT_API_KEY` (+ optional `POSTHOG_HOST`) for product analytics.
  With none set, the app runs fully; email/password registration still works,
  and Pro gates are disabled.
- Local state lives in `webapp/users.db` (SQLite + WAL) and `webapp/.secret_key`, both
  gitignored and created on first run. Backup: `scripts/backup_sqlite.sh` / `.ps1`.
- Product usage (who signed up, funnels, retention, person emails) belongs in
  **PostHog** — see `chess-repertoire/DEPLOY.md` → "Product analytics (PostHog)".
  Google Analytics (GA4) is for traffic/audience only — production Measurement ID
  is baked into `webapp/app.py` as `_DEFAULT_PRODUCTION_GA_MEASUREMENT_ID` when
  `FLASK_ENV=production`; local never loads `gtag.js` unless `GA_MEASUREMENT_ID`
  is set. Profile → Ops tools (`ANALYTICS_ADMIN_EMAIL`) is an account roster /
  password-reset escape hatch, not a product-analytics dashboard.
