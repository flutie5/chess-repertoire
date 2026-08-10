# AGENTS.md

## Cursor Cloud specific instructions

This repo is a monorepo whose application lives under `chess-repertoire/`. It is
"Opening Explorer": a Flask backend (`chess-repertoire/webapp/app.py`) that serves
a static single-page frontend (`chess-repertoire/webapp/static/index.html`) plus a
JSON `/api/*` API, backed by the `chess-repertoire/repertoire` Python package. There
is also a CLI (`chess-repertoire/build_repertoire.py`). See
`chess-repertoire/README.md` and `chess-repertoire/DEPLOY.md` for full docs.

### Environment / running

- Python deps are installed into a virtualenv at `chess-repertoire/.venv` (the
  system Python is PEP-668 externally-managed, so a venv is required). The startup
  update script keeps it in sync with `requirements.txt`. Activate with
  `. chess-repertoire/.venv/bin/activate` (or call `chess-repertoire/.venv/bin/python`
  directly).
- Run the dev server from the `chess-repertoire/` directory: `python webapp/app.py`.
  It listens on `http://127.0.0.1:5000` (host/port are hardcoded in the
  `__main__` block; `debug=False`). It must be started from `chess-repertoire/`
  because `webapp/wsgi.py` / imports assume that as the working dir.
- Tests: from `chess-repertoire/`, run `python -m pytest tests/ -q` (19 tests, no
  network needed). There is no separate lint config; `pytest` is the check.
- The CLI: from `chess-repertoire/`, `python build_repertoire.py <chesscom-username>`.

### Non-obvious gotchas

- Stockfish is required only for the eval bar / game-review endpoints (`/api/eval`,
  `/api/annotate-game`, `/api/scan-blunders`). The binary is NOT committed; it lives
  at `chess-repertoire/engine/linux/stockfish` (gitignored). If it is missing, run
  `bash scripts/download_stockfish.sh` from `chess-repertoire/` (downloads ~76 MB
  from GitHub). The core opening-report flow works without it.
- The report flow (`/api/report?username=...`) fetches real games from the public
  chess.com API over the network — no API key needed. Completed months are cached to
  `.chesscom-cache/`.
- Optional integrations are OFF unless env vars are set (all optional for local dev):
  `GOOGLE_CLIENT_ID` (Google sign-in), `STRIPE_*` (Pro billing gates),
  `ANTHROPIC_API_KEY` (AI repertoire recommendations in the CLI). With none set, the
  app runs fully; email/password registration still works, and Pro gates are disabled.
- Local state lives in `webapp/users.db` (SQLite) and `webapp/.secret_key`, both
  gitignored and created on first run.
- Google Analytics (GA4) support exists for full traffic/audience reporting: set
  `GA_MEASUREMENT_ID` (a `G-XXXXXXXXXX` id from a GA4 property's web data stream)
  and the frontend dynamically loads `gtag.js` — see `DEPLOY.md` → "Google
  Analytics (GA4) setup". Unset by default, so no third-party script loads
  during local dev/tests. The frontend fetches this (and `google_client_id`)
  from `GET /api/auth/config` via the shared `fetchSiteConfig()` cache in
  `webapp/static/index.html`. There is also a separate, self-hosted "Site
  visits" panel gated by `ANALYTICS_ADMIN_EMAIL` (Profile page, admin-only) —
  both exclude the analytics-admin account's own visits.
