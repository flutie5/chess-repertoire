# Production deployment: Netlify (frontend) + Render (API)

Split hosting keeps the static UI on Netlify (custom domain, CDN) while the Python API runs on Render with Stockfish and persistent SQLite/cache storage. Netlify proxies `/api/*` to Render so the browser treats auth cookies as same-origin.

## Architecture

```
Browser  →  your-domain.com (Netlify)
              ├── /, /vendor/*, /pieces/*  →  static files (webapp/static)
              └── /api/*                   →  proxy → Render backend
```

## Prerequisites

- Git repo on GitHub or GitLab
- [Render](https://render.com) account (free tier)
- [Netlify](https://netlify.com) account (free tier)
- Custom domain (optional; Netlify DNS or external DNS)

## 1. Push the repository

Ensure `chess-repertoire/` is in a remote Git repository Render and Netlify can connect to.

## 2. Deploy the API on Render

**Option A — Blueprint (recommended)**

1. Render Dashboard → **New** → **Blueprint**
2. Connect the repo; Render reads `render.yaml`
3. Confirm the web service `chess-repertoire-api` and 1 GB persistent disk at `/data`
4. Deploy and wait for the build (installs deps + downloads Linux Stockfish)

**Option B — Manual web service**

| Setting | Value |
|---|---|
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt && bash scripts/download_stockfish.sh` |
| Start command | `gunicorn webapp.wsgi:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120` |
| Health check | `/api/health` (503 if Stockfish cannot start in production) |
| Root directory | `chess-repertoire` |

**Keep `--workers 1`.** Report/scan jobs are durable in SQLite (`async_jobs`), but the Stockfish engine pool is in-process. Multiple Gunicorn workers would multiply engine processes and fragment rate-limit state. `--threads 4` lets Sign in with Google and `/api/health` proceed while an eval is running on another thread. Raise workers only after engines move off the web process.

**Plan:** Free tier sleeps and is fine for demos. For real traffic, upgrade to a paid always-on Render plan before any growth campaign.

**Environment variables (Render)**

| Variable | Value |
|---|---|
| `FLASK_ENV` | `production` |
| `SECRET_KEY` | Generate a random 64-char hex string (Render can auto-generate) |
| `DATA_DIR` | `/data` |
| `ENGINE_POOL_SIZE` | `2` (concurrent Stockfish processes in this worker) |
| `JOB_WORKERS` | `2` (thread pool for report/scan jobs) |
| `ENGINE_THREADS` / `ENGINE_HASH_MB` | `1` / `16` (raise on paid CPU) |
| `ENGINE_WARMUP` | `1` — start + analyse smoke at boot |
| `HEALTH_REQUIRE_ENGINE` | `1` — `/api/health` 503 if Stockfish is down |
| `ENGINE_DOWNLOAD_ON_MISSING` | `1` — re-run download script if binary missing at boot |
| `RATE_LIMIT_ENGINE` / `REPORT` / `AUTH` | per-minute caps (defaults 30 / 10 / 20) |
| `CURRENT_MONTH_CACHE_SECONDS` | `900` — short TTL for chess.com current-month disk cache |
| `GOOGLE_CLIENT_ID` | OAuth 2.0 Web client ID from Google Cloud Console (for Sign in with Google) |
| `ANALYTICS_ADMIN_EMAIL` | Your login email — unlocks ops tools in Profile (account roster, password reset) |
| `POSTHOG_PROJECT_API_KEY` | PostHog **project** API key — product analytics (People, Insights, Retention, Replay). Public by design. |
| `POSTHOG_HOST` | Optional. Default `https://us.i.posthog.com`. Use `https://eu.i.posthog.com` for EU cloud. |

Add a **persistent disk** mounted at `/data` (1 GB) so `users.db`, job rows, and `.chesscom-cache` survive redeploys.

### API domain / CORS

Prefer the Netlify `/api/*` proxy (same-origin cookies). If the SPA must call Render directly, set `PUBLIC_APP_URL` + `CORS_ORIGINS`, and optionally inject a frontend API base via `window.OPENING_EXPLORER_API` or `<meta name="opening-explorer-api" content="https://…">` (no hardcoded Render host in the SPA).

### Google Sign-In setup

1. [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials
2. Create an **OAuth client ID** of type **Web application**
3. Authorized JavaScript origins:
   - `http://127.0.0.1:5000` (local)
   - `https://YOUR-SITE.netlify.app` (and custom domain if any)
   - `https://YOUR-SERVICE.onrender.com` if you open the API host directly
4. Copy the Client ID into Render as `GOOGLE_CLIENT_ID` (no client secret needed for the GIS ID-token flow)
5. Redeploy the API so `/api/auth/config` returns the client ID

The app uses Google Identity Services with a session-bound **nonce**, FedCM, and server-side ID-token verification (`email_verified`, audience, issuer, freshness). Email and `google_sub` are stored in SQLite `users`.

Confirm storage locally:

```bash
python -m pytest tests/test_auth_google.py -v
```

Note the service URL, e.g. `https://chess-repertoire-api.onrender.com`.

### Product analytics (PostHog) — who is using the app

This is the industry-standard path for **signed-in users, funnels, retention, and
person profiles (email)**. Do not rely on the Profile ops panel or a CSV export
for product reporting.

1. Create a free project at [PostHog US](https://us.posthog.com) or [EU](https://eu.posthog.com)
2. **Project settings → Project API Key** (starts with `phc_…`) — this is a
   public client key, same class as a GA4 measurement ID
3. On Render, set:
   - `POSTHOG_PROJECT_API_KEY=phc_…`
   - `POSTHOG_HOST=https://us.i.posthog.com` (or `https://eu.i.posthog.com`)
4. Redeploy the API, then rebuild/redeploy the frontend static assets so the
   SPA picks up the SDK wiring
5. Open the live site, sign up / analyze once, then in PostHog check:
   - **Activity** → live events (`$pageview`, `user_signed_up`, `report_analyzed`, …)
   - **People** → person profile with `email`, `plan`, `auth_provider`
   - **Product analytics → Insights / Retention** for dashboards

The SPA uses the official `posthog-js` SDK: `identify(user.id)` on login with
email as a person property, `reset()` on logout, and named product events for
signup, login, report analyze, and checkout start. Your `ANALYTICS_ADMIN_EMAIL`
account is opted out (same as GA).

### Google Analytics (GA4) — traffic / audience

GA4 answers “how many visitors / where from?” — not “which emails signed up.”
Use PostHog for the latter.

A production Measurement ID (`G-147LHEVMW7`) is baked into `webapp/app.py` when
`FLASK_ENV=production`. Local/dev never reports unless you set
`GA_MEASUREMENT_ID` explicitly.

Offline CSV of the SQLite roster (ops escape hatch only):

```bash
cd chess-repertoire
python scripts/export_users.py -o users.csv
# or with a persistent disk:
DATA_DIR=/data python scripts/export_users.py -o users.csv
```

- To point at a **different** GA4 property (e.g. your own, or a staging
  property), set `GA_MEASUREMENT_ID` on Render (Environment tab) to override
  the default, then redeploy.
- To verify: visit the live site once (not logged in as the
  `ANALYTICS_ADMIN_EMAIL` account — that account is excluded, same as the
  self-hosted stats) and check **Reports → Realtime** in GA.

## 3. Deploy the frontend on Netlify

1. Netlify Dashboard → **Add new site** → **Import an existing project**
2. Connect the same Git repo (`flutie5/chess-repertoire`)
3. Build settings — leave Base directory **empty** (repo root). A root
   `netlify.toml` publishes `chess-repertoire/webapp/static` automatically.
   - **Publish directory:** `chess-repertoire/webapp/static`
   - **Build command:** leave empty (static site)
4. Deploy

If you prefer a Base directory of `chess-repertoire` instead, set that in the
UI and use publish directory `webapp/static` (see nested `chess-repertoire/netlify.toml`).

## 4. Wire the API proxy

Edit `netlify.toml` (and `webapp/static/_redirects` if you rely on that backup) — replace the placeholder backend URL with your Render URL:

```toml
[[redirects]]
  from = "/api/*"
  to = "https://YOUR-SERVICE.onrender.com/api/:splat"
  status = 200
  force = true
```

Commit and push; Netlify redeploys automatically.

## 5. Custom domain (optional)

1. Netlify → **Domain management** → **Add a domain**
2. Follow Netlify DNS or add the CNAME/A records they provide at your registrar
3. Enable HTTPS (automatic on Netlify)

No Render domain changes are required — only Netlify serves the public site; API traffic is proxied.

## 6. First-deploy verification

- [ ] Render service is **Live** (check Logs for Stockfish install + warmup + Gunicorn start)
- [ ] `https://YOUR-SERVICE.onrender.com/api/health` returns JSON with `ok: true`, `engine_ok: true`
- [ ] `https://YOUR-SERVICE.onrender.com/api/engine-status` returns `ok: true` with a real `path`
- [ ] `https://YOUR-SERVICE.onrender.com/api/me` returns `401` JSON (not 502)
- [ ] Netlify site loads at `https://YOUR-SITE.netlify.app`
- [ ] Register / login works (session cookie on Netlify domain)
- [ ] Continue with Google works (origins + `GOOGLE_CLIENT_ID` configured)
- [ ] Analyze a username — report loads (chess.com fetch + cache)
- [ ] Board eval bar works without signing in (`/api/eval` is free, rate-limited)
- [ ] Deep review (annotate / scan) requires Pro when Stripe is configured
- [ ] After redeploy, existing account still works (`users.db` on `/data` disk)
- [ ] In-flight report jobs survive a brief restart (rows in `async_jobs`)

## SQLite durability (single-node)

Opening Explorer keeps **SQLite** on the Render disk (`DATA_DIR=/data`). Connections use **WAL** + `busy_timeout`. Schema changes go through **Alembic** (`alembic/`); on boot, non-production runs `alembic upgrade head` unless `AUTO_MIGRATE=0`. In production, run migrations as a release step:

```bash
cd chess-repertoire
DATA_DIR=/data alembic upgrade head
```

**Backups** (copy off-box nightly):

```bash
DATA_DIR=/data bash scripts/backup_sqlite.sh /secure/backups
# Windows: .\scripts\backup_sqlite.ps1 -OutDir D:\backups
```

Prefer `sqlite3 .backup` when available. Test a restore monthly. Also keep Stripe customer/subscription IDs reconcilable from the Dashboard if `users.db` is lost.

**Cache quotas:** `.chesscom-cache` is pruned by `CACHE_MAX_MB` / `CACHE_MAX_FILES` (defaults 400 MB / 2000 files). `/api/health` reports `disk_cache`.

**Scale limit:** SQLite + in-process LRU caches + Stockfish pool remain **single-instance**. Do not raise Gunicorn workers or add a second node until Postgres (and shared cache/queue) exist.

## Local development (unchanged)

```bash
pip install -r requirements.txt
python webapp/app.py
# → http://127.0.0.1:5000
```

Uses `webapp/.secret_key`, `webapp/users.db`, repo-root `.chesscom-cache`, and Windows `engine/**/stockfish*.exe` if present.

### Frontend (Vite + TypeScript)

```bash
cd webapp/frontend
npm ci
npm run dev          # :5173, proxies /api → Flask :5000
npm run build        # writes production assets into webapp/static
```

Netlify runs `npm ci && npm run build` with publish `webapp/static`.

## Optional: direct API access (no proxy)

Set `CORS_ORIGINS` on Render to your Netlify/custom domain and point the frontend at the Render URL via `API_BASE`. The default setup uses the Netlify proxy only — no frontend changes needed.

## Files reference

| File | Purpose |
|---|---|
| `webapp/wsgi.py` | Gunicorn entry |
| `Procfile` | Alternative start command |
| `render.yaml` | Render Blueprint |
| `scripts/download_stockfish.sh` | Linux Stockfish for Render build |
| `netlify.toml` | Static publish + API proxy |
| `.env.example` | Local/production env template |
