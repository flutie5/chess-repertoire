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
| Start command | `gunicorn webapp.wsgi:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120` |
| Root directory | repo root |

**Environment variables (Render)**

| Variable | Value |
|---|---|
| `FLASK_ENV` | `production` |
| `SECRET_KEY` | Generate a random 64-char hex string (Render can auto-generate) |
| `DATA_DIR` | `/data` |

Add a **persistent disk** mounted at `/data` (1 GB) so `users.db` and `.chesscom-cache` survive redeploys.

Note the service URL, e.g. `https://chess-repertoire-api.onrender.com`.

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

- [ ] Render service is **Live** (check Logs for Stockfish install + Gunicorn start)
- [ ] `https://YOUR-SERVICE.onrender.com/api/me` returns `401` JSON (not 502)
- [ ] Netlify site loads at `https://YOUR-SITE.netlify.app`
- [ ] Register / login works (session cookie on Netlify domain)
- [ ] Analyze a username — report loads (chess.com fetch + cache)
- [ ] Board eval bar works (Stockfish via `/api/eval`)
- [ ] After redeploy, existing account still works (`users.db` on `/data` disk)

## Local development (unchanged)

```bash
pip install -r requirements.txt
python webapp/app.py
# → http://127.0.0.1:5000
```

Uses `webapp/.secret_key`, `webapp/users.db`, repo-root `.chesscom-cache`, and Windows `engine/**/stockfish*.exe` if present.

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
