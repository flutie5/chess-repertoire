"""Web UI for the chess repertoire builder.

Run:
    python webapp/app.py
then open http://127.0.0.1:5000

GET /api/report?username=NAME&time_classes=rapid,blitz&months=12
returns opening stats per color plus a replayable main line (SAN + FENs)
for every variation.

GET /api/eval?fen=... returns a Stockfish evaluation of the position.

POST /api/scan-blunders scans opening moves in a batch of games for
inaccuracies/mistakes/blunders and returns per-game flags plus repeated
patterns within the same variation.
"""

from __future__ import annotations

import atexit
import os
import re
import secrets
import sqlite3
import sys
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import chess
import chess.engine
from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repertoire import analyze, classify, fetch, lichess, moves, parse  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEBAPP_DIR = Path(__file__).resolve().parent
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

if IS_PRODUCTION:
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

_cors_origins = os.environ.get("CORS_ORIGINS", "").strip()
if _cors_origins:
    from flask_cors import CORS

    CORS(
        app,
        resources={r"/api/*": {"origins": [o.strip() for o in _cors_origins.split(",") if o.strip()]}},
        supports_credentials=True,
    )

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
                password_hash TEXT NOT NULL,
                chesscom_username TEXT NOT NULL DEFAULT '',
                lichess_username TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            )
            """
        )


_init_db()


def _current_user() -> sqlite3.Row | None:
    uid = session.get("user_id")
    if uid is None:
        return None
    with _db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def _user_payload(user: sqlite3.Row) -> dict:
    return {
        "email": user["email"],
        "chesscom_username": user["chesscom_username"],
        "lichess_username": user["lichess_username"],
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


def _get_engine() -> chess.engine.SimpleEngine | None:
    global _engine
    if _engine is None:
        path = _engine_path()
        if path is None:
            return None
        _engine = chess.engine.SimpleEngine.popen_uci(str(path))
    return _engine


def _shutdown_engine():
    if _engine is not None:
        _engine.quit()


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


def _game_entry(g, san: list[str]) -> dict:
    date = ""
    if g.end_time:
        date = datetime.fromtimestamp(g.end_time, tz=timezone.utc).strftime("%Y-%m-%d")
    return {
        "opponent": g.opponent,
        "opponent_rating": g.opponent_rating,
        "result": g.result,
        "date": date,
        "time_class": g.time_class,
        "url": g.url,
        "variation": g.opening_full,
        "san": san[:moves.MAX_GAME_PLIES],
    }


def _color_payload(report, games_with_moves):
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
        openings.append({
            "name": op.name,
            "games": op.games,
            "wins": op.wins,
            "losses": op.losses,
            "draws": op.draws,
            "score": round(op.score, 1),
            "ecos": sorted(op.ecos),
            "variations": variations,
            "game_list": [_game_entry(g, mv)
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


@app.get("/api/eval")
def evaluate():
    fen = (request.args.get("fen") or "").strip()
    if not fen:
        return jsonify({"error": "fen is required"}), 400
    try:
        board = chess.Board(fen)
    except ValueError:
        return jsonify({"error": "invalid FEN"}), 400

    key = board.fen()
    if key in _eval_cache:
        return jsonify(_eval_cache[key])

    with _engine_lock:
        engine = _get_engine()
        if engine is None:
            return jsonify({"error": "Stockfish engine not found"}), 503
        info = engine.analyse(board, chess.engine.Limit(depth=EVAL_DEPTH))

    score = info["score"].white()
    pv = info.get("pv", [])
    result = {
        "cp": score.score(),                      # None if forced mate
        "mate": score.mate(),                     # None unless forced mate
        "depth": info.get("depth", EVAL_DEPTH),
        "best_san": board.san(pv[0]) if pv else None,
        "pv_san": board.variation_san(pv[:8]) if pv else "",
        "turn": "white" if board.turn == chess.WHITE else "black",
    }
    _eval_cache[key] = result
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
        "white": _color_payload(white, filtered_pairs),
        "black": _color_payload(black, filtered_pairs),
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
        return jsonify({"error": str(e)}), 404

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

    label_parts = [p for p in (cc_name, li_name) if p]
    label = label_parts[0] if len(set(label_parts)) == 1 else " + ".join(label_parts)
    payload = _build_report(games_with_moves, label, months, time_classes,
                            sources, since_ts=since_ts, until_ts=until_ts)
    if errors:
        payload["warnings"] = errors
    return jsonify(payload)


# ---- Auth endpoints ----


def _json_body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


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
    except sqlite3.IntegrityError:
        return jsonify({"error": "An account with that email already exists."}), 409

    session.permanent = True
    session["user_id"] = user_id
    return jsonify({"email": email, "chesscom_username": "",
                    "lichess_username": ""}), 201


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
    if user is None or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Incorrect email or password."}), 401

    session.permanent = True
    session["user_id"] = user["id"]
    return jsonify(_user_payload(user))


@app.post("/api/logout")
def logout():
    session.pop("user_id", None)
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    user = _current_user()
    if user is None:
        return jsonify({"error": "not logged in"}), 401
    return jsonify(_user_payload(user))


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
