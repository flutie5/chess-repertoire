"""Web UI for Opening Explorer.

Run:
    python webapp/app.py
then open http://127.0.0.1:5000

GET /api/report?username=NAME&time_classes=rapid,blitz&months=12
returns opening stats per color plus a replayable main line (SAN + FENs)
for every variation.

GET /api/eval?fen=... returns a Stockfish evaluation of the position.

GET /api/opening?play=e2e4,e7e5,... returns the opening name/eco for a UCI
move sequence (proxied from the Lichess opening explorer, cached).

POST /api/scan-blunders scans opening moves in a batch of games for
inaccuracies/mistakes/blunders and returns per-game flags plus repeated
patterns within the same variation.

POST /api/annotate-game runs a full-game Stockfish review and returns
chess.com-style move annotations (blunder/mistake/great/best/brilliant).

Billing (Stripe): POST /api/billing/checkout, /api/billing/portal,
/api/billing/webhook. Pro (+ 3-day trial) gates My Repertoire sync and
practice-move. Game review (annotate-*) stays free.

Traffic analytics are Google Analytics (GA4) — see GA_MEASUREMENT_ID in
.env.example. Account admin (ANALYTICS_ADMIN_EMAIL only): GET
/api/admin/accounts (list registered accounts), POST
/api/admin/users/<id>/password (set a temporary password).
"""

from __future__ import annotations

import atexit
import hashlib
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import chess
import chess.engine
from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repertoire import analyze, classify, fetch, lichess, moves, openings, parse  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEBAPP_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

IS_PRODUCTION = os.environ.get("FLASK_ENV") == "production"

_data_dir = os.environ.get("DATA_DIR", "").strip()
if _data_dir:
    _data_path = Path(_data_dir)
    try:
        _data_path.mkdir(parents=True, exist_ok=True)
        # Verify we can actually write here (wrong path or missing disk fails early).
        probe = _data_path / ".write_probe"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        CACHE_DIR = _data_path / ".chesscom-cache"
        DB_PATH = _data_path / "users.db"
    except OSError as e:
        print(
            f"WARNING: DATA_DIR={_data_dir!r} is not writable ({e}). "
            f"Falling back to local webapp paths. "
            f"On Render, set DATA_DIR=/data and attach a disk mounted at /data.",
            flush=True,
        )
        CACHE_DIR = ROOT / ".chesscom-cache"
        DB_PATH = WEBAPP_DIR / "users.db"
else:
    CACHE_DIR = ROOT / ".chesscom-cache"
    DB_PATH = WEBAPP_DIR / "users.db"

CACHE_DIR.mkdir(parents=True, exist_ok=True)

VALID_TIME_CLASSES = {"rapid", "blitz", "bullet", "daily"}
VALID_SOURCES = {"chesscom", "lichess", "both"}
EVAL_DEPTH = 16

app = Flask(__name__, static_folder="static", static_url_path="")

# None is required for credentialed cross-origin calls (Netlify/custom domain → Render).
# Same-origin Netlify /api proxy still works with None + Secure.
if IS_PRODUCTION:
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="None",
    )

PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "").strip().rstrip("/")

_cors_origins = os.environ.get("CORS_ORIGINS", "").strip()
_cors_origin_list = [o.strip() for o in _cors_origins.split(",") if o.strip()]
if PUBLIC_APP_URL and PUBLIC_APP_URL not in _cors_origin_list:
    _cors_origin_list.append(PUBLIC_APP_URL)
if _cors_origin_list:
    from flask_cors import CORS

    CORS(
        app,
        resources={r"/api/*": {"origins": _cors_origin_list}},
        supports_credentials=True,
    )


def _origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    if origin in _cors_origin_list:
        return True
    if (
        origin.endswith(".netlify.app")
        or origin.endswith(".netlify.com")
        or origin.startswith("http://127.0.0.1:")
        or origin.startswith("http://localhost:")
    ):
        return True
    # Production custom domains (e.g. opening-explorer.com)
    if PUBLIC_APP_URL and origin == PUBLIC_APP_URL:
        return True
    if origin in ("https://opening-explorer.com", "https://www.opening-explorer.com"):
        return True
    return False


@app.after_request
def _cors_netlify_fallback(resp):
    """Allow frontends to call the Render API if the /api proxy is broken."""
    if "Access-Control-Allow-Origin" in resp.headers:
        return resp
    origin = request.headers.get("Origin") or ""
    if _origin_allowed(origin):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        resp.headers["Vary"] = "Origin"
    return resp


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def _cors_preflight(_any: str):
    return ("", 204)


@app.errorhandler(500)
def _api_500(exc):
    """Always JSON on /api/* — never Flask's HTML 500 page."""
    if not request.path.startswith("/api/"):
        return exc
    original = getattr(exc, "original_exception", None)
    detail = str(original) if original else (getattr(exc, "description", None) or "internal server error")
    print(f"WARNING: API 500 on {request.path}: {detail}", flush=True)
    return jsonify({"error": detail}), 500


@app.errorhandler(HTTPException)
def _api_http_error(exc: HTTPException):
    """Return JSON for API HTTP errors (avoids HTML error pages)."""
    if not request.path.startswith("/api/"):
        return exc
    if exc.code == 500:
        return _api_500(exc)
    return jsonify({"error": exc.description or exc.name}), exc.code or 500


@app.errorhandler(Exception)
def _api_unhandled_error(exc: Exception):
    """Catch-all so /api/* never returns non-JSON on unexpected failures."""
    if isinstance(exc, HTTPException):
        return _api_http_error(exc)
    if not request.path.startswith("/api/"):
        return ("Internal Server Error", 500)
    print(f"WARNING: unhandled API error on {request.path}: {exc}", flush=True)
    return jsonify({"error": f"server error: {exc}"}), 500


# ---- Accounts (SQLite + signed session cookie) ----

SECRET_KEY_FILE = WEBAPP_DIR / ".secret_key"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[\w.-]{1,40}$")  # chess.com / lichess handles
MIN_PASSWORD_LEN = 8


def _load_secret_key() -> str:
    env_key = os.environ.get("SECRET_KEY", "").strip()
    if env_key:
        return env_key
    if IS_PRODUCTION:
        raise RuntimeError("SECRET_KEY environment variable is required in production")
    if SECRET_KEY_FILE.exists():
        key = SECRET_KEY_FILE.read_text().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(key)
    return key


app.secret_key = _load_secret_key()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=90)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT,
                google_sub TEXT UNIQUE,
                display_name TEXT NOT NULL DEFAULT '',
                chesscom_username TEXT NOT NULL DEFAULT '',
                lichess_username TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            )
            """
        )
        conn.execute(
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
        _migrate_users(conn)


def _migrate_users(conn: sqlite3.Connection) -> None:
    """Additive migrations for existing SQLite installs."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "google_sub" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN google_sub TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub "
            "ON users(google_sub) WHERE google_sub IS NOT NULL"
        )
    if "display_name" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
        )
    billing_cols = {
        "stripe_customer_id": "TEXT",
        "stripe_subscription_id": "TEXT",
        "plan": "TEXT NOT NULL DEFAULT 'free'",
        "plan_interval": "TEXT",
        "plan_status": "TEXT",
        "plan_expires_at": "INTEGER",
    }
    for name, decl in billing_cols.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")


_init_db()

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
ANALYTICS_ADMIN_EMAIL = os.environ.get("ANALYTICS_ADMIN_EMAIL", "").strip().lower()
# Google Analytics (GA4) measurement ID, e.g. "G-XXXXXXXXXX". Not a secret —
# it's meant to be public (visible in every visitor's page source) — so the
# production ID is baked in as a default here rather than requiring a Render
# dashboard step. Only used when FLASK_ENV=production, so local dev never
# reports to the real GA4 property; set GA_MEASUREMENT_ID explicitly to
# override (e.g. to test against a separate/staging GA4 property).
_DEFAULT_PRODUCTION_GA_MEASUREMENT_ID = "G-147LHEVMW7"
GA_MEASUREMENT_ID = os.environ.get("GA_MEASUREMENT_ID", "").strip()
if not GA_MEASUREMENT_ID and IS_PRODUCTION:
    GA_MEASUREMENT_ID = _DEFAULT_PRODUCTION_GA_MEASUREMENT_ID

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_PRICE_MONTHLY = os.environ.get("STRIPE_PRICE_MONTHLY", "").strip()
STRIPE_PRICE_YEARLY = os.environ.get("STRIPE_PRICE_YEARLY", "").strip()


def _stripe_secret_ok(key: str) -> bool:
    """HTTP headers must be latin-1; truncated keys often contain '…'."""
    if not key:
        return False
    if not key.isascii():
        print(
            "WARNING: STRIPE_SECRET_KEY has non-ASCII characters "
            "(often a pasted '…'). Re-copy the full key from Stripe.",
            flush=True,
        )
        return False
    if key.startswith("pk_test_") or key.startswith("pk_live_"):
        print(
            "WARNING: STRIPE_SECRET_KEY is a publishable key (pk_…). "
            "Use the Secret key (sk_test_ or sk_live_) from Stripe → API keys.",
            flush=True,
        )
        return False
    if not (key.startswith("sk_test_") or key.startswith("sk_live_")):
        print(
            "WARNING: STRIPE_SECRET_KEY should start with sk_test_ or sk_live_",
            flush=True,
        )
        return False
    # Real keys are long; a truncated paste is never this short.
    if len(key) < 20:
        print("WARNING: STRIPE_SECRET_KEY looks truncated", flush=True)
        return False
    return True


_stripe = None
if STRIPE_SECRET_KEY and _stripe_secret_ok(STRIPE_SECRET_KEY):
    try:
        import stripe as _stripe_mod
        _stripe_mod.api_key = STRIPE_SECRET_KEY
        _stripe = _stripe_mod
    except ImportError:
        print("WARNING: stripe package not installed; billing disabled", flush=True)
elif STRIPE_SECRET_KEY:
    print("WARNING: Stripe billing disabled due to invalid STRIPE_SECRET_KEY", flush=True)


def _billing_configured() -> bool:
    return bool(
        _stripe
        and STRIPE_SECRET_KEY
        and STRIPE_PRICE_MONTHLY
        and STRIPE_PRICE_YEARLY
    )


def _current_user() -> sqlite3.Row | None:
    uid = session.get("user_id")
    if uid is None:
        return None
    with _db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def _user_has_password(user: sqlite3.Row) -> bool:
    pw = user["password_hash"]
    return bool(pw)


def _user_col(user: sqlite3.Row, name: str, default=None):
    keys = user.keys()
    return user[name] if name in keys else default


TRIAL_DAYS = 3


def _can_view_analytics(user: sqlite3.Row | None) -> bool:
    """True for the one configured admin account.

    Despite the name, this no longer gates a self-hosted analytics dashboard
    (removed — Google Analytics covers traffic reporting now). It still gates
    the account-admin tools (`/api/admin/*`) and is used client-side to
    exclude this account's own visits from Google Analytics.
    """
    if user is None or not ANALYTICS_ADMIN_EMAIL:
        return False
    return (user["email"] or "").strip().lower() == ANALYTICS_ADMIN_EMAIL


def _is_pro(user: sqlite3.Row | None) -> bool:
    if user is None:
        return False
    plan = (_user_col(user, "plan") or "free").lower()
    status = (_user_col(user, "plan_status") or "").lower()
    if plan != "pro":
        return False
    return status in ("active", "trialing", "past_due")


def _require_login():
    """Return (user, None) or (None, (jsonify_response, status))."""
    user = _current_user()
    if user is None:
        return None, (jsonify({
            "error": "Sign in required",
            "code": "login_required",
        }), 401)
    return user, None


def _require_pro():
    """Return (user, None) or (None, (jsonify_response, status)).

    When Stripe is not configured (local/dev), allow access.
    """
    user, err = _require_login()
    if err:
        return None, err
    if not _billing_configured():
        return user, None
    if not _is_pro(user):
        return None, (jsonify({
            "error": "Pro subscription required",
            "code": "pro_required",
        }), 402)
    return user, None


def _user_payload(user: sqlite3.Row) -> dict:
    keys = user.keys()
    display = user["display_name"] if "display_name" in keys else ""
    google_sub = user["google_sub"] if "google_sub" in keys else None
    plan = _user_col(user, "plan") or "free"
    plan_interval = _user_col(user, "plan_interval")
    plan_status = _user_col(user, "plan_status")
    plan_expires_at = _user_col(user, "plan_expires_at")
    is_pro = _is_pro(user) or not _billing_configured()
    return {
        "email": user["email"],
        "display_name": display or "",
        "chesscom_username": user["chesscom_username"],
        "lichess_username": user["lichess_username"],
        "has_password": _user_has_password(user),
        "auth_provider": "google" if google_sub else "password",
        "plan": plan,
        "plan_interval": plan_interval,
        "plan_status": plan_status,
        "plan_expires_at": plan_expires_at,
        "is_pro": is_pro,
        "is_trialing": (plan_status or "").lower() == "trialing" and is_pro,
        "billing_enabled": _billing_configured(),
        "trial_days": TRIAL_DAYS,
        "can_view_analytics": _can_view_analytics(user),
    }

# In-memory cache of parsed games + move lists per (username, months).
_games_cache: dict = {}
_cache_lock = threading.Lock()

# ---- Stockfish ----

_engine: chess.engine.SimpleEngine | None = None
_engine_lock = threading.Lock()
_eval_cache: dict[str, dict] = {}
_scan_eval_cache: dict[str, dict] = {}  # shallower depth cache for blunder scans
_scan_cache: dict[str, dict] = {}       # game-key -> scan result
_annotate_eval_cache: dict[str, list] = {}  # fen+depth -> multipv lines
_annotate_cache: dict[str, list] = {}       # san-key -> annotations
_opening_cache: dict[str, dict] = {}    # UCI play string -> {name, eco}


def _engine_path() -> Path | None:
    override = os.environ.get("STOCKFISH_PATH", "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None
    engine_dir = ROOT / "engine"
    exes = sorted(engine_dir.rglob("stockfish*.exe"))
    if exes:
        return exes[0]
    for path in sorted(engine_dir.rglob("stockfish")):
        if path.is_file() and path.suffix.lower() != ".exe":
            return path
    return None


def _configure_engine(engine: chess.engine.SimpleEngine) -> None:
    """Free-tier-friendly defaults: one thread, small hash, full strength."""
    try:
        engine.configure({
            "Threads": 1,
            "Hash": 16,
            "UCI_LimitStrength": False,
            "Skill Level": 20,
        })
    except Exception as exc:
        print(f"WARNING: Stockfish configure failed: {exc}", flush=True)


def _get_engine() -> chess.engine.SimpleEngine | None:
    global _engine
    if _engine is None:
        path = _engine_path()
        if path is None:
            print("WARNING: Stockfish binary not found under engine/", flush=True)
            return None
        try:
            print(f"Starting Stockfish at {path}", flush=True)
            _engine = chess.engine.SimpleEngine.popen_uci(str(path))
            _configure_engine(_engine)
        except Exception as exc:
            print(f"WARNING: failed to start Stockfish at {path}: {exc}", flush=True)
            _engine = None
            return None
    return _engine


def _reset_engine_limits(engine: chess.engine.SimpleEngine) -> None:
    """Clear practice-mode strength limits before a full-strength analyse."""
    _configure_engine(engine)


def _reset_engine():
    """Drop a dead engine handle so the next call restarts Stockfish."""
    global _engine
    if _engine is not None:
        try:
            _engine.quit()
        except Exception:
            pass
        _engine = None


def _shutdown_engine():
    _reset_engine()


atexit.register(_shutdown_engine)


def _load_games(username: str, months: int):
    key = ("chesscom", username.lower(), months)
    with _cache_lock:
        if key in _games_cache:
            return _games_cache[key]
    raw = fetch.fetch_games(username, months=months, cache_dir=CACHE_DIR,
                            verbose=False)
    games = []
    for r in raw:
        g = parse.parse_game(r, username)
        if g is not None:
            games.append((g, moves.san_moves(
                r.get("pgn", ""), max_plies=moves.MAX_GAME_PLIES)))
    with _cache_lock:
        _games_cache[key] = games
    return games


def _load_lichess_games(username: str, months: int):
    key = ("lichess", username.lower(), months)
    with _cache_lock:
        if key in _games_cache:
            return _games_cache[key]
    games = lichess.games_with_moves(username, months=months,
                                     cache_dir=CACHE_DIR)
    with _cache_lock:
        _games_cache[key] = games
    return games


def _variation_payload(name: str, count: int, move_lists: list[list[str]],
                       results: list[str]):
    line = moves.main_line(move_lists)
    fens = moves.fens_for(line)
    line = line[: len(fens)]  # drop anything that failed legality check
    score = None
    if results:
        pts = results.count("win") + 0.5 * results.count("draw")
        score = round(100 * pts / len(results), 1)
    return {"name": name, "games": count, "score": score,
            "san": line, "fens": fens}


MAX_GAMES_PER_OPENING = 200


def _game_entry(g, san: list[str], player_username: str = "") -> dict:
    date = ""
    if g.end_time:
        date = datetime.fromtimestamp(g.end_time, tz=timezone.utc).strftime("%Y-%m-%d")
    return {
        "opponent": g.opponent,
        "opponent_rating": g.opponent_rating,
        "my_username": player_username or "",
        "my_rating": g.my_rating,
        "result": g.result,
        "date": date,
        "time_class": g.time_class,
        "url": g.url,
        "variation": g.opening_full,
        "san": san[:moves.MAX_GAME_PLIES],
        "color": g.color,
    }


def _color_payload(report, games_with_moves, player_username: str = ""):
    """Serialize a ColorReport, attaching main lines to each variation."""
    # variation full name -> list of move lists (for this color's games)
    by_variation: dict[str, list[list[str]]] = {}
    # variation full name -> list of results ("win"/"loss"/"draw")
    variation_results: dict[str, list[str]] = {}
    # opening family -> (game, san_moves) pairs played in it (for this color)
    by_family: dict[str, list] = {}
    for g, mv in games_with_moves:
        if g.color == report.color:
            by_variation.setdefault(g.opening_full, []).append(mv)
            variation_results.setdefault(g.opening_full, []).append(g.result)
            by_family.setdefault(g.opening_family, []).append((g, mv))

    openings = []
    for op in report.openings:
        variations = [
            _variation_payload(vname, vcount, by_variation.get(vname, []),
                               variation_results.get(vname, []))
            for vname, vcount in sorted(op.variations.items(),
                                        key=lambda kv: -kv[1])
        ]
        family_games = sorted(by_family.get(op.name, []),
                          key=lambda pair: -pair[0].end_time)
        # Extra guard: only this color's games appear under this tab.
        family_games = [(g, mv) for g, mv in family_games if g.color == report.color]
        openings.append({
            "name": op.name,
            "games": op.games,
            "wins": op.wins,
            "losses": op.losses,
            "draws": op.draws,
            "score": round(op.score, 1),
            "ecos": sorted(op.ecos),
            "variations": variations,
            "game_list": [_game_entry(g, mv, player_username)
                          for g, mv in family_games[:MAX_GAMES_PER_OPENING]],
        })
    return {
        "color": report.color,
        "total_games": report.total_games,
        "score": round(report.score, 1),
        "first_moves": report.first_moves,
        "openings": openings,
    }


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/engine-status")
def engine_status():
    """Deploy health check: can we find/start Stockfish?"""
    path = _engine_path()
    if path is None:
        return jsonify({"ok": False, "error": "Stockfish binary not found", "path": None}), 503
    try:
        with _engine_lock:
            engine = _get_engine()
            if engine is None:
                return jsonify({
                    "ok": False,
                    "error": "Stockfish failed to start",
                    "path": str(path),
                }), 503
        return jsonify({"ok": True, "path": str(path)})
    except Exception as exc:
        _reset_engine()
        return jsonify({"ok": False, "error": str(exc), "path": str(path)}), 500


@app.get("/api/eval")
def evaluate():
    fen = (request.args.get("fen") or "").strip()
    if not fen:
        return jsonify({"error": "fen is required"}), 400
    try:
        board = chess.Board(fen)
    except ValueError:
        return jsonify({"error": "invalid FEN"}), 400

    if board.king(chess.WHITE) is None or board.king(chess.BLACK) is None:
        return jsonify({"error": "both kings are required"}), 400

    key = board.fen()
    if key in _eval_cache:
        return jsonify(_eval_cache[key])

    try:
        with _engine_lock:
            engine = _get_engine()
            if engine is None:
                return jsonify({"error": "Stockfish engine not found"}), 503
            _reset_engine_limits(engine)
            try:
                info = engine.analyse(board, chess.engine.Limit(depth=EVAL_DEPTH))
            except (chess.engine.EngineTerminatedError, chess.engine.EngineError):
                _reset_engine()
                engine = _get_engine()
                if engine is None:
                    return jsonify({"error": "Stockfish engine not found"}), 503
                _reset_engine_limits(engine)
                info = engine.analyse(board, chess.engine.Limit(depth=EVAL_DEPTH))

        if "score" not in info:
            return jsonify({"error": "engine returned no score"}), 500

        score = info["score"].white()
        pv = list(info.get("pv") or [])
        best_san = None
        pv_san = ""
        if pv:
            try:
                best_san = board.san(pv[0])
            except ValueError:
                best_san = pv[0].uci()
            try:
                pv_san = board.variation_san(pv[:8])
            except ValueError:
                pv_san = " ".join(m.uci() for m in pv[:8])

        result = {
            "cp": score.score(),                      # None if forced mate
            "mate": score.mate(),                     # None unless forced mate
            "depth": info.get("depth", EVAL_DEPTH),
            "best_san": best_san,
            "pv_san": pv_san,
            "turn": "white" if board.turn == chess.WHITE else "black",
        }
        _eval_cache[key] = result
        return jsonify(result)
    except Exception as exc:
        _reset_engine()
        print(f"WARNING: /api/eval failed for {fen!r}: {exc}", flush=True)
        return jsonify({"error": f"engine failed: {exc}"}), 500


@app.post("/api/practice-move")
def practice_move():
    """Return one Stockfish move at a reduced Elo for repertoire practice."""
    _user, err = _require_pro()
    if err:
        return err
    data = _json_body()
    fen = (data.get("fen") or "").strip()
    if not fen:
        return jsonify({"error": "fen is required"}), 400
    try:
        board = chess.Board(fen)
    except ValueError:
        return jsonify({"error": "invalid FEN"}), 400
    if board.is_game_over():
        return jsonify({"error": "game over"}), 400

    # Prefer Elo (200-point steps). Legacy `level` 1..10 maps to ~1400..3200.
    elo = data.get("elo")
    if elo is None and data.get("level") is not None:
        try:
            level = max(1, min(10, int(data.get("level"))))
        except (TypeError, ValueError):
            level = 5
        elo = 1200 + level * 200
    try:
        elo = int(elo if elo is not None else 1800)
    except (TypeError, ValueError):
        elo = 1800
    # Stockfish UCI_Elo is typically ~1320–3190
    elo = max(1320, min(3190, elo))
    # Stronger opponents get a bit more search time/depth
    depth = 6 + (elo - 1320) // 200  # ~6..15
    think_time = 0.25 + (elo - 1320) / 2000.0  # ~0.25..1.2s

    with _engine_lock:
        engine = _get_engine()
        if engine is None:
            return jsonify({"error": "Stockfish engine not found"}), 503
        try:
            engine.configure({
                "UCI_LimitStrength": True,
                "UCI_Elo": elo,
            })
            result = engine.play(
                board, chess.engine.Limit(depth=depth, time=think_time)
            )
        except (chess.engine.EngineTerminatedError, chess.engine.EngineError) as exc:
            _reset_engine()
            return jsonify({"error": f"engine failed: {exc}"}), 500
        finally:
            try:
                engine.configure({
                    "UCI_LimitStrength": False,
                    "Skill Level": 20,
                })
            except Exception:
                pass

    move = result.move
    if move is None:
        return jsonify({"error": "no move"}), 500
    san = board.san(move)
    promo = None
    if move.promotion:
        promo = chess.piece_symbol(move.promotion).lower()
    board.push(move)
    return jsonify({
        "from": chess.square_name(move.from_square),
        "to": chess.square_name(move.to_square),
        "promotion": promo,
        "san": san,
        "fen": board.fen(),
        "level": level,
        "skill": skill,
    })


@app.get("/api/opening")
def opening_lookup():
    """Resolve an opening name for a UCI move sequence (comma-separated).

    Uses the local ECO book (longest prefix match). No external API required.
    """
    play = (request.args.get("play") or "").strip()
    result = openings.lookup(play)
    # Also cache under _opening_cache for identical requests.
    if play:
        _opening_cache[play] = result
    return jsonify(result)


@app.post("/api/scan-blunders")
def scan_blunders():
    """Scan opening moves of games for inaccuracies / mistakes / blunders.

    Body: {
      games: [{ san, color, variation?, opponent?, date?, url?, key? }],
      opening_plies?: int (default 20),
      player_only?: bool (default true — only flag the player whose color is set)
    }
    Caps the batch to keep Render-friendly response times.
    Free on Analyze; My Repertoire UI is Pro-gated separately.
    """
    data = _json_body()
    games_in = data.get("games")
    if not isinstance(games_in, list) or not games_in:
        return jsonify({"error": "games array is required"}), 400

    opening_plies = data.get("opening_plies") or classify.OPENING_PLIES
    try:
        opening_plies = max(4, min(30, int(opening_plies)))
    except (TypeError, ValueError):
        opening_plies = classify.OPENING_PLIES

    # Cap batch size — each game can take a few second-depth analyses.
    MAX_BATCH = 40
    games_in = games_in[:MAX_BATCH]

    with _engine_lock:
        engine = _get_engine()
        if engine is None:
            return jsonify({"error": "Stockfish engine not found"}), 503

        out_games = []
        for i, g in enumerate(games_in):
            if not isinstance(g, dict):
                continue
            san = g.get("san") or []
            if not isinstance(san, list) or not san:
                continue
            color = (g.get("color") or "white").lower()
            if color not in ("white", "black"):
                color = "white"
            variation = g.get("variation") or "Unknown"
            cache_key = "|".join([
                color,
                str(opening_plies),
                variation,
                " ".join(san[:opening_plies]),
            ])
            if cache_key in _scan_cache:
                cached = dict(_scan_cache[cache_key])
                cached["index"] = i
                out_games.append(cached)
                continue

            flags = classify.classify_game(
                engine,
                [str(m) for m in san],
                color,
                opening_plies=opening_plies,
                eval_cache=_scan_eval_cache,
            )
            summary = classify.summarize_flags(flags)
            entry = {
                "index": i,
                "variation": variation,
                "opponent": g.get("opponent"),
                "date": g.get("date"),
                "url": g.get("url"),
                "color": color,
                "flags": [asdict(f) for f in flags],
                **summary,
            }
            _scan_cache[cache_key] = {
                k: v for k, v in entry.items() if k != "index"
            }
            out_games.append(entry)

    patterns = classify.find_patterns(out_games, min_count=2)
    return jsonify({
        "opening_plies": opening_plies,
        "scanned": len(out_games),
        "games": out_games,
        "patterns": patterns,
    })


@app.post("/api/annotate-game")
def annotate_game():
    """Full-game Stockfish review with chess.com-style move badges.

    Body: { san: ["e4", "c5", ...], depth?: int }
    Free for everyone — Pro gates My Repertoire instead.
    """
    data = _json_body()
    san = data.get("san") or []
    if not isinstance(san, list) or not san:
        return jsonify({"error": "san array is required"}), 400
    san = [str(m) for m in san[: classify.ANNOTATE_MAX_PLIES]]

    depth = data.get("depth") or classify.ANNOTATE_DEPTH
    try:
        depth = max(8, min(16, int(depth)))
    except (TypeError, ValueError):
        depth = classify.ANNOTATE_DEPTH

    cache_key = f"{depth}|{' '.join(san)}"
    if cache_key in _annotate_cache:
        return jsonify({
            "depth": depth,
            "plies": len(san),
            "annotations": _annotate_cache[cache_key],
        })

    with _engine_lock:
        engine = _get_engine()
        if engine is None:
            return jsonify({"error": "Stockfish engine not found"}), 503
        try:
            flags = classify.annotate_game(
                engine,
                san,
                depth=depth,
                max_plies=classify.ANNOTATE_MAX_PLIES,
                eval_cache=_annotate_eval_cache,
            )
        except (chess.engine.EngineTerminatedError, chess.engine.EngineError) as exc:
            _reset_engine()
            return jsonify({"error": f"engine failed: {exc}"}), 500

    annotations = [asdict(f) for f in flags]
    _annotate_cache[cache_key] = annotations
    return jsonify({
        "depth": depth,
        "plies": len(san),
        "annotations": annotations,
    })


@app.post("/api/annotate-ply")
def annotate_ply():
    """Classify a single exploratory move (position before + SAN).

    Body: { fen: "...", san: "h4", ply?: int, depth?: int }
    Free for everyone — Pro gates My Repertoire instead.
    """
    data = _json_body()
    fen = (data.get("fen") or "").strip()
    san = (data.get("san") or "").strip()
    if not fen or not san:
        return jsonify({"error": "fen and san are required"}), 400
    try:
        board = chess.Board(fen)
    except ValueError:
        return jsonify({"error": "invalid FEN"}), 400

    ply = data.get("ply") or (len(board.move_stack) + 1)
    try:
        ply = max(1, int(ply))
    except (TypeError, ValueError):
        ply = 1

    depth = data.get("depth") or classify.ANNOTATE_DEPTH
    try:
        depth = max(8, min(16, int(depth)))
    except (TypeError, ValueError):
        depth = classify.ANNOTATE_DEPTH

    with _engine_lock:
        engine = _get_engine()
        if engine is None:
            return jsonify({"error": "Stockfish engine not found"}), 503
        try:
            ann = classify.annotate_ply(
                engine,
                board,
                san,
                ply=ply,
                depth=depth,
                eval_cache=_annotate_eval_cache,
                mark_quiet_best=True,
            )
        except (chess.engine.EngineTerminatedError, chess.engine.EngineError) as exc:
            _reset_engine()
            return jsonify({"error": f"engine failed: {exc}"}), 500

    return jsonify({
        "depth": depth,
        "annotation": asdict(ann) if ann else None,
    })


# ---- Stripe billing ----

def _app_base_url() -> str:
    if PUBLIC_APP_URL:
        return PUBLIC_APP_URL
    # Prefer proxy / Host header when behind Netlify
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host") or request.host
    return f"{proto}://{host}".rstrip("/")


def _price_id_for_interval(interval: str) -> str | None:
    if interval == "year":
        return STRIPE_PRICE_YEARLY or None
    if interval == "month":
        return STRIPE_PRICE_MONTHLY or None
    return None


def _interval_for_price(price_id: str | None) -> str | None:
    if not price_id:
        return None
    if price_id == STRIPE_PRICE_YEARLY:
        return "year"
    if price_id == STRIPE_PRICE_MONTHLY:
        return "month"
    return None


def _subscription_as_dict(subscription) -> dict:
    """Normalize Stripe Subscription (object or webhook dict) to a plain dict."""
    if subscription is None:
        return {}
    if isinstance(subscription, dict):
        items = (subscription.get("items") or {}).get("data") or []
        price_id = None
        if items:
            price_id = (items[0].get("price") or {}).get("id")
        return {
            "id": subscription.get("id"),
            "status": subscription.get("status") or "",
            "customer": subscription.get("customer"),
            "current_period_end": subscription.get("current_period_end"),
            "trial_end": subscription.get("trial_end"),
            "price_id": price_id,
            "metadata": subscription.get("metadata") or {},
        }
    customer = getattr(subscription, "customer", None)
    if hasattr(customer, "id"):
        customer = customer.id
    price_id = None
    items = getattr(subscription, "items", None)
    if items and getattr(items, "data", None):
        price = items.data[0].price
        price_id = getattr(price, "id", None)
    meta = getattr(subscription, "metadata", None) or {}
    if hasattr(meta, "to_dict"):
        meta = meta.to_dict()
    elif not isinstance(meta, dict):
        try:
            meta = dict(meta)
        except Exception:
            meta = {}
    return {
        "id": getattr(subscription, "id", None),
        "status": getattr(subscription, "status", None) or "",
        "customer": customer,
        "current_period_end": getattr(subscription, "current_period_end", None),
        "trial_end": getattr(subscription, "trial_end", None),
        "price_id": price_id,
        "metadata": meta,
    }


def _apply_subscription_to_user(
    conn: sqlite3.Connection,
    *,
    user_id: int | None = None,
    stripe_customer_id: str | None = None,
    subscription,
) -> None:
    """Sync plan fields from a Stripe Subscription object or dict."""
    sub = _subscription_as_dict(subscription)
    if not sub:
        return
    status = (sub.get("status") or "").lower()
    sub_id = sub.get("id")
    customer = sub.get("customer") or stripe_customer_id
    interval = _interval_for_price(sub.get("price_id"))
    period_end = sub.get("current_period_end")
    trial_end = sub.get("trial_end")
    expires = trial_end if status == "trialing" and trial_end else period_end
    plan = "pro" if status in ("active", "trialing", "past_due") else "free"

    where = ""
    args: list = []
    if user_id is not None:
        where = "id = ?"
        args = [user_id]
    elif customer:
        where = "stripe_customer_id = ?"
        args = [customer]
    else:
        return

    conn.execute(
        f"""
        UPDATE users SET
            stripe_customer_id = COALESCE(?, stripe_customer_id),
            stripe_subscription_id = ?,
            plan = ?,
            plan_interval = ?,
            plan_status = ?,
            plan_expires_at = ?
        WHERE {where}
        """,
        (
            customer,
            sub_id,
            plan,
            interval,
            status,
            int(expires) if expires else None,
            *args,
        ),
    )


@app.post("/api/billing/checkout")
def billing_checkout():
    """Create a Stripe Checkout Session for Pro (month or year).

    New subscribers get a TRIAL_DAYS free trial (card collected, charged after).
    """
    if not _billing_configured():
        return jsonify({"error": "Billing is not configured"}), 503
    user, err = _require_login()
    if err:
        return err

    data = _json_body()
    interval = (data.get("interval") or "month").lower()
    if interval not in ("month", "year"):
        return jsonify({"error": "interval must be month or year"}), 400
    price_id = _price_id_for_interval(interval)
    if not price_id:
        return jsonify({"error": "Price not configured"}), 503

    base = _app_base_url()
    customer_id = _user_col(user, "stripe_customer_id")
    # First-time subscribers get a 3-day trial; returning customers do not.
    offer_trial = not _user_col(user, "stripe_subscription_id")
    try:
        sub_data = {"metadata": {"user_id": str(user["id"])}}
        if offer_trial:
            sub_data["trial_period_days"] = TRIAL_DAYS
        params = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": f"{base}/?billing=success",
            "cancel_url": f"{base}/?billing=cancel",
            "client_reference_id": str(user["id"]),
            "metadata": {"user_id": str(user["id"])},
            "subscription_data": sub_data,
            "allow_promotion_codes": True,
        }
        email = (user["email"] or "").strip()
        if customer_id:
            params["customer"] = customer_id
        elif email:
            params["customer_email"] = email

        session_obj = _stripe.checkout.Session.create(**params)
    except Exception as exc:
        print(f"WARNING: Stripe checkout failed: {exc}", flush=True)
        msg = str(exc)
        if "UnicodeEncodeError" in msg or "latin-1" in msg or "\\u2026" in msg or "…" in msg:
            return jsonify({
                "error": (
                    "Checkout failed: STRIPE_SECRET_KEY on the server looks "
                    "truncated or contains invalid characters. In Render, "
                    "re-paste the full Secret key from Stripe → Developers → "
                    "API keys (must start with sk_test_ or sk_live_, no '…')."
                ),
            }), 500
        return jsonify({"error": f"Checkout failed: {exc}"}), 500

    return jsonify({"url": session_obj.url, "session_id": session_obj.id})


@app.post("/api/billing/portal")
def billing_portal():
    """Stripe Customer Portal for managing/canceling Pro."""
    if not _billing_configured():
        return jsonify({"error": "Billing is not configured"}), 503
    user, err = _require_login()
    if err:
        return err
    customer_id = _user_col(user, "stripe_customer_id")
    if not customer_id:
        return jsonify({"error": "No billing account yet — subscribe first"}), 400
    base = _app_base_url()
    try:
        portal = _stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{base}/",
        )
    except Exception as exc:
        print(f"WARNING: Stripe portal failed: {exc}", flush=True)
        return jsonify({"error": f"Portal failed: {exc}"}), 500
    return jsonify({"url": portal.url})


@app.post("/api/billing/webhook")
def billing_webhook():
    """Stripe webhook — sync subscription state onto users."""
    if not _stripe or not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "Webhook not configured"}), 503
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = _stripe.Webhook.construct_event(
            payload, sig, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400
    except Exception as exc:
        # SignatureVerificationError and others
        print(f"WARNING: Stripe webhook verify failed: {exc}", flush=True)
        return jsonify({"error": "Invalid signature"}), 400

    etype = event["type"]
    obj = event["data"]["object"]
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()
    elif not isinstance(obj, dict):
        try:
            obj = dict(obj)
        except Exception:
            obj = {}

    def _uid_from(meta_or_ref) -> int | None:
        if meta_or_ref is None:
            return None
        try:
            return int(meta_or_ref)
        except (TypeError, ValueError):
            return None

    try:
        with _db() as conn:
            if etype == "checkout.session.completed":
                user_id = _uid_from(
                    obj.get("client_reference_id")
                    or (obj.get("metadata") or {}).get("user_id")
                )
                customer = obj.get("customer")
                sub_id = obj.get("subscription")
                if customer and user_id is not None:
                    conn.execute(
                        "UPDATE users SET stripe_customer_id = ? WHERE id = ?",
                        (customer, user_id),
                    )
                if sub_id:
                    sub = _stripe.Subscription.retrieve(sub_id)
                    _apply_subscription_to_user(
                        conn,
                        user_id=user_id,
                        stripe_customer_id=customer,
                        subscription=sub,
                    )

            elif etype == "customer.subscription.updated":
                meta = obj.get("metadata") or {}
                user_id = _uid_from(meta.get("user_id"))
                _apply_subscription_to_user(
                    conn,
                    user_id=user_id,
                    stripe_customer_id=obj.get("customer"),
                    subscription=obj,
                )

            elif etype == "customer.subscription.deleted":
                meta = obj.get("metadata") or {}
                user_id = _uid_from(meta.get("user_id"))
                customer = obj.get("customer")
                sub_id = obj.get("id")
                if user_id is not None:
                    where, args = "id = ?", [user_id]
                elif customer:
                    where, args = "stripe_customer_id = ?", [customer]
                else:
                    where, args = "", []
                if where:
                    conn.execute(
                        f"""
                        UPDATE users SET
                            plan = 'free',
                            plan_status = 'canceled',
                            stripe_subscription_id = ?,
                            plan_interval = NULL,
                            plan_expires_at = NULL
                        WHERE {where}
                        """,
                        (sub_id, *args),
                    )
    except Exception as exc:
        print(f"WARNING: Stripe webhook handler failed: {exc}", flush=True)
        return jsonify({"error": "Handler failed"}), 500

    return jsonify({"ok": True})


MAX_FETCH_MONTHS = 240  # widest window we'll fetch (20 years ~ all history)


def _parse_iso_date(name: str) -> datetime | None:
    val = (request.args.get(name) or "").strip()
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD)")


def _parse_report_args():
    """Shared query-arg parsing for report endpoints.
    Returns (months, time_classes, since_ts, until_ts) or raises ValueError
    with a message. since/until are optional ISO dates; the range is
    inclusive: [since 00:00:00, until 23:59:59] UTC."""
    months = request.args.get("months", type=int) or 12
    tc_param = (request.args.get("time_classes") or "").strip()
    time_classes = None
    if tc_param:
        time_classes = {t for t in tc_param.split(",") if t in VALID_TIME_CLASSES}
        if not time_classes:
            raise ValueError("no valid time classes selected")

    since_d = _parse_iso_date("since")
    until_d = _parse_iso_date("until")
    if since_d and until_d and since_d > until_d:
        raise ValueError("since must not be after until")
    since_ts = int(since_d.timestamp()) if since_d else None
    until_ts = int(until_d.timestamp()) + 86399 if until_d else None

    if since_d:
        # Widen the fetch window so the archives cover the requested range.
        now = datetime.now(timezone.utc)
        needed = (now.year - since_d.year) * 12 + (now.month - since_d.month) + 1
        if needed > months:
            months = min(needed, MAX_FETCH_MONTHS)
    return months, time_classes, since_ts, until_ts


def _build_report(games_with_moves, username_label: str, months: int,
                  time_classes, sources: list[str],
                  since_ts: int | None = None, until_ts: int | None = None):
    def in_range(g):
        return ((since_ts is None or g.end_time >= since_ts)
                and (until_ts is None or g.end_time <= until_ts))

    games = [g for g, _ in games_with_moves if in_range(g)]
    filtered_pairs = [
        (g, mv) for g, mv in games_with_moves
        if g.rated and (time_classes is None or g.time_class in time_classes)
        and in_range(g)
    ]

    white, black, priorities = analyze.analyze(
        games, time_classes=time_classes, rated_only=True
    )

    return {
        "username": username_label,
        "sources": sources,
        "months": months,
        "since": (datetime.fromtimestamp(since_ts, tz=timezone.utc)
                  .strftime("%Y-%m-%d") if since_ts is not None else None),
        "until": (datetime.fromtimestamp(until_ts, tz=timezone.utc)
                  .strftime("%Y-%m-%d") if until_ts is not None else None),
        "time_classes": sorted(time_classes) if time_classes else "all",
        "analyzed_games": len(filtered_pairs),
        "white": _color_payload(white, filtered_pairs, username_label),
        "black": _color_payload(black, filtered_pairs, username_label),
        "priorities": [
            {
                "color": p.color,
                "opening": p.opening.name,
                "frequency_pct": round(p.frequency_pct, 1),
                "score": round(p.opening.score, 1),
                "score_gap": round(p.score_gap, 1),
            }
            for p in priorities
        ],
    }


# ---- async report jobs (Netlify proxy times out ~26s; chess.com fetch can take longer) ----

_report_jobs: dict[str, dict] = {}
_report_jobs_lock = threading.Lock()
_REPORT_JOB_TTL = 3600


def _prune_report_jobs() -> None:
    cutoff = time.time() - _REPORT_JOB_TTL
    with _report_jobs_lock:
        for jid in [k for k, j in _report_jobs.items() if j.get("created", 0) < cutoff]:
            del _report_jobs[jid]


def _start_report_job(worker) -> str:
    _prune_report_jobs()
    job_id = secrets.token_urlsafe(16)
    with _report_jobs_lock:
        _report_jobs[job_id] = {"status": "pending", "created": time.time()}

    def run() -> None:
        try:
            with _report_jobs_lock:
                if job_id in _report_jobs:
                    _report_jobs[job_id]["status"] = "running"
            result = worker()
            with _report_jobs_lock:
                if job_id in _report_jobs:
                    _report_jobs[job_id]["status"] = "done"
                    _report_jobs[job_id]["result"] = result
        except Exception as e:
            with _report_jobs_lock:
                if job_id in _report_jobs:
                    _report_jobs[job_id]["status"] = "error"
                    _report_jobs[job_id]["error"] = str(e)

    threading.Thread(target=run, daemon=True).start()
    return job_id


def _fetch_games_for_report(username: str, source: str, months: int):
    games_with_moves = []
    if source in ("chesscom", "both"):
        games_with_moves.extend(_load_games(username, months))
    if source in ("lichess", "both"):
        games_with_moves.extend(_load_lichess_games(username, months))
    return games_with_moves


def _make_username_report(username: str, source: str, months: int,
                          time_classes, since_ts, until_ts):
    games_with_moves = _fetch_games_for_report(username, source, months)
    sources = ["chesscom", "lichess"] if source == "both" else [source]
    return _build_report(games_with_moves, username, months, time_classes,
                         sources, since_ts=since_ts, until_ts=until_ts)


def _make_me_report(user, months: int, time_classes, since_ts, until_ts):
    cc_name = user["chesscom_username"]
    li_name = user["lichess_username"]
    games_with_moves = []
    sources = []
    errors = []
    if cc_name:
        try:
            games_with_moves.extend(_load_games(cc_name, months))
            sources.append("chesscom")
        except fetch.ChessComError as e:
            errors.append(f"chess.com: {e}")
    if li_name:
        try:
            games_with_moves.extend(_load_lichess_games(li_name, months))
            sources.append("lichess")
        except lichess.LichessError as e:
            errors.append(f"lichess: {e}")

    if not games_with_moves:
        msg = "; ".join(errors) if errors else "No games found for your linked accounts."
        raise ValueError(msg)

    label_parts = [p for p in (cc_name, li_name) if p]
    label = label_parts[0] if len(set(label_parts)) == 1 else " + ".join(label_parts)
    payload = _build_report(games_with_moves, label, months, time_classes,
                          sources, since_ts=since_ts, until_ts=until_ts)
    if errors:
        payload["warnings"] = errors
    return payload


@app.route("/api/report/jobs", methods=["GET", "POST"])
def start_report_job():
    username = (request.args.get("username") or "").strip()
    if not username:
        return jsonify({"error": "username is required"}), 400

    source = (request.args.get("source") or "chesscom").strip().lower()
    if source not in VALID_SOURCES:
        return jsonify({"error": "source must be chesscom, lichess or both"}), 400

    try:
        months, time_classes, since_ts, until_ts = _parse_report_args()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    job_id = _start_report_job(
        lambda: _make_username_report(
            username, source, months, time_classes, since_ts, until_ts
        )
    )
    return jsonify({"job_id": job_id, "status": "pending"})


@app.route("/api/report/me/jobs", methods=["GET", "POST"])
def start_report_me_job():
    user = _current_user()
    if user is None:
        return jsonify({"error": "not logged in"}), 401

    cc_name = user["chesscom_username"]
    li_name = user["lichess_username"]
    if not cc_name and not li_name:
        return jsonify({
            "error": "No linked accounts. Add your chess.com or lichess "
                     "username in account settings first."
        }), 400

    try:
        months, time_classes, since_ts, until_ts = _parse_report_args()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    job_id = _start_report_job(
        lambda: _make_me_report(user, months, time_classes, since_ts, until_ts)
    )
    return jsonify({"job_id": job_id, "status": "pending"})


@app.get("/api/report/jobs/<job_id>")
def poll_report_job(job_id: str):
    with _report_jobs_lock:
        job = _report_jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found or expired"}), 404
    status = job["status"]
    if status == "done":
        return jsonify({"status": "done", "report": job["result"]})
    if status == "error":
        return jsonify({"status": "error", "error": job.get("error", "unknown")})
    return jsonify({"status": status})


@app.get("/api/report")
def report():
    username = (request.args.get("username") or "").strip()
    if not username:
        return jsonify({"error": "username is required"}), 400

    source = (request.args.get("source") or "chesscom").strip().lower()
    if source not in VALID_SOURCES:
        return jsonify({"error": "source must be chesscom, lichess or both"}), 400

    try:
        months, time_classes, since_ts, until_ts = _parse_report_args()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    games_with_moves = []
    try:
        if source in ("chesscom", "both"):
            games_with_moves.extend(_load_games(username, months))
        if source in ("lichess", "both"):
            games_with_moves.extend(_load_lichess_games(username, months))
    except (fetch.ChessComError, lichess.LichessError) as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        # Never return an HTML 500 — the frontend always expects JSON.
        return jsonify({"error": f"Failed to fetch games: {e}"}), 500

    sources = ["chesscom", "lichess"] if source == "both" else [source]
    return jsonify(_build_report(games_with_moves, username, months,
                                 time_classes, sources,
                                 since_ts=since_ts, until_ts=until_ts))


@app.get("/api/report/me")
def report_me():
    user = _current_user()
    if user is None:
        return jsonify({"error": "not logged in"}), 401

    cc_name = user["chesscom_username"]
    li_name = user["lichess_username"]
    if not cc_name and not li_name:
        return jsonify({
            "error": "No linked accounts. Add your chess.com or lichess "
                     "username in account settings first."
        }), 400

    try:
        months, time_classes, since_ts, until_ts = _parse_report_args()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    games_with_moves = []
    sources = []
    errors = []
    if cc_name:
        try:
            games_with_moves.extend(_load_games(cc_name, months))
            sources.append("chesscom")
        except fetch.ChessComError as e:
            errors.append(f"chess.com: {e}")
    if li_name:
        try:
            games_with_moves.extend(_load_lichess_games(li_name, months))
            sources.append("lichess")
        except lichess.LichessError as e:
            errors.append(f"lichess: {e}")

    if not games_with_moves:
        msg = "; ".join(errors) if errors else "No games found for your linked accounts."
        return jsonify({"error": msg}), 404

    try:
        label_parts = [p for p in (cc_name, li_name) if p]
        label = label_parts[0] if len(set(label_parts)) == 1 else " + ".join(label_parts)
        payload = _build_report(games_with_moves, label, months, time_classes,
                                sources, since_ts=since_ts, until_ts=until_ts)
    except Exception as e:
        return jsonify({"error": f"Failed to build report: {e}"}), 500
    if errors:
        payload["warnings"] = errors
    return jsonify(payload)


# ---- Auth endpoints ----


def _json_body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _login_user(user: sqlite3.Row) -> dict:
    session.permanent = True
    session["user_id"] = user["id"]
    return _user_payload(user)


# Google ID tokens for interactive sign-in should be fresh (GIS issues short-lived JWTs).
_GOOGLE_TOKEN_MAX_AGE_SEC = 5 * 60


@app.get("/api/auth/config")
def auth_config():
    """Return GIS client ID, GA4 id, and a one-time nonce bound to this session."""
    payload = {
        "google_client_id": GOOGLE_CLIENT_ID or None,
        "ga_measurement_id": GA_MEASUREMENT_ID or None,
        "nonce": None,
    }
    if GOOGLE_CLIENT_ID:
        nonce = secrets.token_urlsafe(32)
        session.permanent = True
        session["google_nonce"] = nonce
        payload["nonce"] = nonce
    return jsonify(payload)


@app.post("/api/auth/google")
def auth_google():
    if not GOOGLE_CLIENT_ID:
        return jsonify({"error": "Google sign-in is not configured on this server."}), 503

    body = _json_body()
    credential = (body.get("credential") or "").strip()
    if not credential:
        return jsonify({"error": "Missing Google credential."}), 400

    expected_nonce = (session.get("google_nonce") or "").strip()
    if not expected_nonce:
        return jsonify({
            "error": "Sign-in session expired. Close and reopen the sign-in dialog, then try again.",
            "code": "nonce_missing",
        }), 401

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
        info = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=60,
        )
    except Exception as exc:
        print(f"WARNING: Google token verify failed: {exc}", flush=True)
        return jsonify({"error": "Invalid Google credential."}), 401

    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        return jsonify({"error": "Invalid Google credential."}), 401

    token_nonce = (info.get("nonce") or "").strip()
    # GIS normally echoes the nonce; some Google flows put SHA-256(hex) instead.
    expected_hash = hashlib.sha256(expected_nonce.encode("utf-8")).hexdigest()
    nonce_ok = bool(token_nonce) and (
        secrets.compare_digest(token_nonce, expected_nonce)
        or secrets.compare_digest(token_nonce, expected_hash)
    )
    if not nonce_ok:
        return jsonify({
            "error": "Google sign-in could not be verified. Please try again.",
            "code": "nonce_mismatch",
        }), 401
    # One-time nonce — prevent replay of the same credential against this session.
    session.pop("google_nonce", None)

    iat = info.get("iat")
    try:
        if iat is not None and (time.time() - int(iat)) > _GOOGLE_TOKEN_MAX_AGE_SEC:
            return jsonify({"error": "Google sign-in expired. Please try again."}), 401
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid Google credential."}), 401

    sub = (info.get("sub") or "").strip()
    email = (info.get("email") or "").strip().lower()
    if not sub or not email:
        return jsonify({"error": "Google account is missing email."}), 400
    if not info.get("email_verified", False):
        return jsonify({"error": "Google account email is not verified."}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Google account email is invalid."}), 400

    display_name = (info.get("name") or "").strip()[:120]
    is_new_account = False

    with _db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE google_sub = ?", (sub,)
        ).fetchone()
        if user is None:
            by_email = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            if by_email is not None:
                existing_sub = (by_email["google_sub"] or "").strip()
                if existing_sub and existing_sub != sub:
                    return jsonify({
                        "error": "This email is already linked to a different Google account.",
                    }), 409
                # Safe to link: Google verified ownership of this email (email_verified).
                conn.execute(
                    "UPDATE users SET google_sub = ?, "
                    "display_name = CASE WHEN COALESCE(display_name, '') = '' "
                    "THEN ? ELSE display_name END WHERE id = ?",
                    (sub, display_name, by_email["id"]),
                )
                user = conn.execute(
                    "SELECT * FROM users WHERE id = ?", (by_email["id"],)
                ).fetchone()
            else:
                # Empty string password_hash: Google-only (works with older NOT NULL schemas).
                cur = conn.execute(
                    "INSERT INTO users (email, password_hash, google_sub, display_name) "
                    "VALUES (?, '', ?, ?)",
                    (email, sub, display_name),
                )
                user = conn.execute(
                    "SELECT * FROM users WHERE id = ?", (cur.lastrowid,)
                ).fetchone()
                is_new_account = True
        else:
            # Returning Google user: keep email in sync if Google reports a new verified address.
            updates = []
            params: list = []
            current_email = (user["email"] or "").strip().lower()
            if email and email != current_email:
                clash = conn.execute(
                    "SELECT id FROM users WHERE email = ? AND id != ?",
                    (email, user["id"]),
                ).fetchone()
                if clash is None:
                    updates.append("email = ?")
                    params.append(email)
            if display_name and not (user["display_name"] or "").strip():
                updates.append("display_name = ?")
                params.append(display_name)
            if updates:
                params.append(user["id"])
                conn.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                user = conn.execute(
                    "SELECT * FROM users WHERE id = ?", (user["id"],)
                ).fetchone()

    payload = _login_user(user)
    payload["is_new_account"] = is_new_account
    return jsonify(payload)

@app.post("/api/register")
def register():
    data = _json_body()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not EMAIL_RE.match(email):
        return jsonify({"error": "Please enter a valid email address."}), 400
    if len(password) < MIN_PASSWORD_LEN:
        return jsonify({
            "error": f"Password must be at least {MIN_PASSWORD_LEN} characters."
        }), 400

    try:
        with _db() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email, generate_password_hash(password)),
            )
            user_id = cur.lastrowid
            user = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({"error": "An account with that email already exists."}), 409

    return jsonify(_login_user(user)), 201


@app.post("/api/login")
def login():
    data = _json_body()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    with _db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
    if user is None:
        return jsonify({"error": "Incorrect email or password."}), 401
    if not _user_has_password(user):
        return jsonify({"error": "Use Google to sign in."}), 401
    if not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Incorrect email or password."}), 401

    return jsonify(_login_user(user))


@app.post("/api/logout")
def logout():
    session.pop("user_id", None)
    return jsonify({"ok": True})


@app.get("/api/admin/accounts")
def admin_accounts():
    """Admin-only: list all registered accounts (email, plan, auth method).

    Traffic/visit stats used to live here too, but that's now covered by
    Google Analytics (see .env GA_MEASUREMENT_ID) — this endpoint is just
    account administration, which GA can't provide.
    """
    user, err = _require_login()
    if err:
        return err
    if not _can_view_analytics(user):
        return jsonify({"error": "Forbidden", "code": "analytics_forbidden"}), 403

    with _db() as conn:
        account_total = conn.execute(
            "SELECT COUNT(*) AS n FROM users"
        ).fetchone()["n"]
        accounts = conn.execute(
            "SELECT id, email, display_name, created_at, "
            "google_sub, password_hash, plan, plan_status "
            "FROM users ORDER BY id"
        ).fetchall()

    return jsonify({
        "accounts": {
            "total": int(account_total or 0),
            "users": [
                {
                    "id": int(r["id"]),
                    "email": r["email"],
                    "display_name": (r["display_name"] or "").strip(),
                    "created_at": int(r["created_at"] or 0),
                    "auth_provider": (
                        "google" if (r["google_sub"] or "").strip() else "password"
                    ),
                    "has_password": bool((r["password_hash"] or "").strip()),
                    "plan": (r["plan"] or "free"),
                    "plan_status": r["plan_status"],
                }
                for r in accounts
            ],
        },
    })


@app.post("/api/admin/users/<int:user_id>/password")
def admin_set_password(user_id: int):
    """Admin-only: set a temporary password so the user can sign in / change it."""
    admin, err = _require_login()
    if err:
        return err
    if not _can_view_analytics(admin):
        return jsonify({"error": "Forbidden", "code": "analytics_forbidden"}), 403

    data = _json_body()
    new = data.get("new_password") or ""
    if len(new) < MIN_PASSWORD_LEN:
        return jsonify({
            "error": f"Password must be at least {MIN_PASSWORD_LEN} characters."
        }), 400

    with _db() as conn:
        row = conn.execute(
            "SELECT id, email, google_sub FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return jsonify({"error": "User not found."}), 404
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new), user_id),
        )

    return jsonify({
        "ok": True,
        "id": user_id,
        "email": row["email"],
        "note": (
            "Temporary password set. Share it out of band; "
            "Google sign-in still works if linked."
        ),
    })


@app.get("/api/me")
def me():
    user = _current_user()
    if user is None:
        return jsonify({"error": "not logged in"}), 401
    return jsonify(_user_payload(user))


def _repertoire_payload(user_id: int) -> dict:
    with _db() as conn:
        rows = conn.execute(
            "SELECT color, play, name, eco FROM user_repertoire WHERE user_id = ? "
            "ORDER BY name COLLATE NOCASE",
            (user_id,),
        ).fetchall()
    out = {"white": [], "black": []}
    for r in rows:
        out[r["color"]].append({
            "play": r["play"],
            "name": r["name"],
            "eco": r["eco"] or "",
        })
    return out


@app.get("/api/me/repertoire")
def get_repertoire():
    user, err = _require_pro()
    if err:
        return err
    return jsonify(_repertoire_payload(user["id"]))


@app.put("/api/me/repertoire")
def put_repertoire():
    user, err = _require_pro()
    if err:
        return err
    data = _json_body()
    items = []
    for color in ("white", "black"):
        entries = data.get(color)
        if entries is None:
            continue
        if not isinstance(entries, list):
            return jsonify({"error": f"{color} must be a list"}), 400
        for e in entries[:200]:
            if not isinstance(e, dict):
                continue
            play = (e.get("play") or "").strip()
            name = (e.get("name") or "").strip()
            eco = (e.get("eco") or "").strip()[:16]
            if not play or not name:
                continue
            if len(play) > 400 or len(name) > 120:
                continue
            items.append((user["id"], color, play, name, eco))

    with _db() as conn:
        conn.execute("DELETE FROM user_repertoire WHERE user_id = ?", (user["id"],))
        if items:
            conn.executemany(
                "INSERT OR REPLACE INTO user_repertoire "
                "(user_id, color, play, name, eco) VALUES (?, ?, ?, ?, ?)",
                items,
            )
    return jsonify(_repertoire_payload(user["id"]))


@app.put("/api/me")
def update_me():
    user = _current_user()
    if user is None:
        return jsonify({"error": "not logged in"}), 401

    data = _json_body()
    updates = {}
    for field in ("chesscom_username", "lichess_username"):
        if field in data:
            value = (data.get(field) or "").strip()
            if value and not USERNAME_RE.match(value):
                site = "chess.com" if field.startswith("chesscom") else "lichess"
                return jsonify({"error": f"Invalid {site} username."}), 400
            updates[field] = value

    if updates:
        sets = ", ".join(f"{f} = ?" for f in updates)
        with _db() as conn:
            conn.execute(
                f"UPDATE users SET {sets} WHERE id = ?",
                (*updates.values(), user["id"]),
            )
        user = _current_user()
    return jsonify(_user_payload(user))


@app.post("/api/me/password")
def change_password():
    user = _current_user()
    if user is None:
        return jsonify({"error": "not logged in"}), 401

    data = _json_body()
    current = data.get("current_password") or ""
    new = data.get("new_password") or ""
    if not current or not new:
        return jsonify({"error": "Current and new password are required."}), 400
    if len(new) < MIN_PASSWORD_LEN:
        return jsonify({
            "error": f"New password must be at least {MIN_PASSWORD_LEN} characters."
        }), 400
    if not _user_has_password(user):
        return jsonify({"error": "This account uses Google sign-in."}), 400
    if not check_password_hash(user["password_hash"], current):
        return jsonify({"error": "Current password is incorrect."}), 401
    if current == new:
        return jsonify({"error": "New password must be different."}), 400

    with _db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new), user["id"]),
        )
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
