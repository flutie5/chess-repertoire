"""Learning product + security endpoints (gaps 33–48)."""

from __future__ import annotations

import json
import secrets
import time
from datetime import date
from io import StringIO
from typing import TYPE_CHECKING

import chess
import chess.engine
from flask import Response, jsonify, request, session

from repertoire import human_moves, recommend, srs
from repertoire.analyze import ColorReport, OpeningStats, Priority

if TYPE_CHECKING:
    from flask import Flask


def register_learning_routes(app: "Flask") -> None:
    from webapp import app as app_module

    _db = app_module._db
    _require_login = app_module._require_login
    _require_pro = app_module._require_pro
    _json_body = app_module._json_body
    _current_user = app_module._current_user
    limiter = app_module.limiter

    def _today() -> str:
        return date.today().isoformat()

    def _touch_habit(user_id: int, reviews: int = 1) -> dict:
        today = _today()
        with _db() as conn:
            row = conn.execute(
                "SELECT * FROM user_habits WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO user_habits "
                    "(user_id, streak_days, best_streak, last_study_day, "
                    "reviews_today, study_day) VALUES (?, 1, 1, ?, ?, ?)",
                    (user_id, today, reviews, today),
                )
            else:
                last = row["last_study_day"]
                study_day = row["study_day"]
                streak = int(row["streak_days"] or 0)
                best = int(row["best_streak"] or 0)
                reviews_today = int(row["reviews_today"] or 0)
                if study_day != today:
                    reviews_today = 0
                    study_day = today
                    if last:
                        try:
                            prev = date.fromisoformat(last)
                            delta = (date.today() - prev).days
                        except ValueError:
                            delta = 999
                        streak = streak + 1 if delta == 1 else 1
                    else:
                        streak = 1
                    best = max(best, streak)
                reviews_today += reviews
                conn.execute(
                    "UPDATE user_habits SET streak_days=?, best_streak=?, "
                    "last_study_day=?, reviews_today=?, study_day=? "
                    "WHERE user_id=?",
                    (streak, best, today, reviews_today, study_day, user_id),
                )
            row = conn.execute(
                "SELECT * FROM user_habits WHERE user_id = ?", (user_id,)
            ).fetchone()
        return {
            "streak_days": row["streak_days"],
            "best_streak": row["best_streak"],
            "reviews_today": row["reviews_today"],
            "last_study_day": row["last_study_day"],
        }

    def _ensure_csrf() -> str:
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return token

    def _check_csrf() -> tuple[bool, object | None]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True, None
        expected = (session.get("csrf_token") or "").strip()
        got = (
            request.headers.get("X-CSRF-Token")
            or (request.get_json(silent=True) or {}).get("csrf_token")
            or request.form.get("csrf_token")
            or ""
        ).strip()
        if not expected or not got or not secrets.compare_digest(expected, got):
            return False, (jsonify({
                "error": "CSRF token missing or invalid",
                "code": "csrf_failed",
            }), 403)
        return True, None

    @app.after_request
    def _ensure_csrf_header(resp):
        if request.path.startswith("/api/") and session.get("user_id"):
            resp.headers["X-CSRF-Token"] = _ensure_csrf()
        return resp

    @app.get("/api/opening/popularity")
    @limiter.limit("report")
    def opening_popularity():
        fen = (request.args.get("fen") or "").strip()
        if not fen:
            return jsonify({"error": "fen is required"}), 400
        try:
            elo = int(request.args.get("elo") or 1800)
        except ValueError:
            elo = 1800
        elo = max(400, min(2800, elo))
        try:
            chess.Board(fen)
        except ValueError:
            return jsonify({"error": "invalid FEN"}), 400
        moves = human_moves.popularity_at_rating(fen, elo=elo)
        return jsonify({"fen": fen, "elo": elo, "moves": moves})

    @app.post("/api/practice-feedback")
    @limiter.limit("engine")
    def practice_feedback():
        """Instant move quality vs engine best (gap 36)."""
        user, err = _require_login()
        if err:
            return err
        ok, cerr = _check_csrf()
        if not ok:
            return cerr
        data = _json_body()
        fen = (data.get("fen") or "").strip()
        san = (data.get("san") or "").strip()
        if not fen or not san:
            return jsonify({"error": "fen and san required"}), 400
        try:
            board = chess.Board(fen)
            move = board.parse_san(san)
        except ValueError:
            return jsonify({"error": "invalid fen/san"}), 400

        try:
            with app_module._engine_pool.acquire(timeout=10) as engine:
                app_module._engine_pool.configure_full_strength(engine)
                info = engine.analyse(
                    board, chess.engine.Limit(depth=12), multipv=2
                )
        except TimeoutError:
            return app_module._engine_busy_response()
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        if not isinstance(info, list):
            info = [info]
        best = info[0]
        best_move = (best.get("pv") or [None])[0]
        best_san = board.san(best_move) if best_move else None
        before = best["score"].white().score(mate_score=100000)
        mover_white = board.turn == chess.WHITE
        board.push(move)
        try:
            with app_module._engine_pool.acquire(timeout=10) as engine:
                after_info = engine.analyse(board, chess.engine.Limit(depth=12))
        except Exception:
            after_info = None
        after = None
        if after_info and "score" in after_info:
            after = after_info["score"].white().score(mate_score=100000)
        if before is None or after is None:
            loss = 0
        elif mover_white:
            loss = max(0, int(before - after))
        else:
            loss = max(0, int(after - before))

        if best_move and move == best_move:
            severity = "best"
        elif loss >= 200:
            severity = "blunder"
        elif loss >= 100:
            severity = "mistake"
        elif loss >= 50:
            severity = "inaccuracy"
        else:
            severity = "good"

        return jsonify({
            "severity": severity,
            "loss_cp": int(loss or 0),
            "best_san": best_san,
            "played_san": san,
        })

    @app.get("/api/me/learning/due")
    def learning_due():
        user, err = _require_login()
        if err:
            return err
        now = time.time()
        limit = min(50, max(1, int(request.args.get("limit") or 20)))
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM learning_cards WHERE user_id = ? AND due_at <= ? "
                "ORDER BY due_at ASC LIMIT ?",
                (user["id"], now, limit),
            ).fetchall()
        return jsonify({
            "cards": [dict(r) for r in rows],
            "count": len(rows),
            "server_time": now,
        })

    @app.post("/api/me/learning/cards")
    def learning_add_cards():
        user, err = _require_pro()
        if err:
            return err
        ok, cerr = _check_csrf()
        if not ok:
            return cerr
        data = _json_body()
        items = data.get("cards") or []
        if not isinstance(items, list) or not items:
            return jsonify({"error": "cards array required"}), 400
        now = time.time()
        inserted = 0
        with _db() as conn:
            for item in items[:40]:
                if not isinstance(item, dict):
                    continue
                fen = (item.get("fen") or "").strip()
                if not fen:
                    continue
                try:
                    chess.Board(fen)
                except ValueError:
                    continue
                conn.execute(
                    "INSERT INTO learning_cards "
                    "(user_id, kind, fen, san_line, prompt, answer_san, opening, "
                    "ease, interval_days, repetitions, due_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 2.5, 0, 0, ?, ?, ?)",
                    (
                        user["id"],
                        (item.get("kind") or "puzzle")[:32],
                        fen[:120],
                        (item.get("san_line") or "")[:400],
                        (item.get("prompt") or "Find the best move")[:200],
                        (item.get("answer_san") or "")[:32],
                        (item.get("opening") or "")[:120],
                        now,
                        now,
                        now,
                    ),
                )
                inserted += 1
        return jsonify({"inserted": inserted}), 201

    @app.post("/api/me/learning/review")
    def learning_review():
        user, err = _require_login()
        if err:
            return err
        ok, cerr = _check_csrf()
        if not ok:
            return cerr
        data = _json_body()
        try:
            card_id = int(data.get("card_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "card_id required"}), 400
        try:
            quality = int(data.get("quality", 3))
        except (TypeError, ValueError):
            quality = 3
        now = time.time()
        with _db() as conn:
            row = conn.execute(
                "SELECT * FROM learning_cards WHERE id = ? AND user_id = ?",
                (card_id, user["id"]),
            ).fetchone()
            if row is None:
                return jsonify({"error": "card not found"}), 404
            updated = srs.review(
                srs.SrsCard(
                    ease=float(row["ease"]),
                    interval_days=float(row["interval_days"]),
                    repetitions=int(row["repetitions"]),
                    due_at=float(row["due_at"]),
                ),
                quality,
                now,
            )
            conn.execute(
                "UPDATE learning_cards SET ease=?, interval_days=?, repetitions=?, "
                "due_at=?, updated_at=? WHERE id=?",
                (
                    updated.ease,
                    updated.interval_days,
                    updated.repetitions,
                    updated.due_at,
                    now,
                    card_id,
                ),
            )
        habit = _touch_habit(user["id"], reviews=1)
        return jsonify({
            "card_id": card_id,
            "ease": updated.ease,
            "interval_days": updated.interval_days,
            "repetitions": updated.repetitions,
            "due_at": updated.due_at,
            "habits": habit,
        })

    @app.get("/api/me/habits")
    def get_habits():
        user, err = _require_login()
        if err:
            return err
        with _db() as conn:
            row = conn.execute(
                "SELECT * FROM user_habits WHERE user_id = ?", (user["id"],)
            ).fetchone()
        if row is None:
            return jsonify({
                "streak_days": 0,
                "best_streak": 0,
                "reviews_today": 0,
                "last_study_day": None,
            })
        return jsonify({
            "streak_days": row["streak_days"],
            "best_streak": row["best_streak"],
            "reviews_today": row["reviews_today"],
            "last_study_day": row["last_study_day"],
        })

    @app.get("/api/me/train-today")
    def train_today():
        user, err = _require_login()
        if err:
            return err
        now = time.time()
        with _db() as conn:
            due = conn.execute(
                "SELECT id, kind, fen, prompt, answer_san, opening, san_line "
                "FROM learning_cards WHERE user_id=? AND due_at<=? "
                "ORDER BY due_at ASC LIMIT 10",
                (user["id"], now),
            ).fetchall()
            reps = conn.execute(
                "SELECT color, play, name, eco FROM user_repertoire "
                "WHERE user_id=? ORDER BY name COLLATE NOCASE LIMIT 6",
                (user["id"],),
            ).fetchall()
        return jsonify({
            "headline": "Today's 20-minute plan",
            "steps": [
                {
                    "type": "review",
                    "title": "Spaced review",
                    "count": len(due),
                    "cards": [dict(r) for r in due],
                },
                {
                    "type": "repertoire",
                    "title": "Drill repertoire lines",
                    "lines": [dict(r) for r in reps],
                },
                {
                    "type": "habit",
                    "title": "Keep your streak",
                    "hint": "Complete at least 5 card reviews today.",
                },
            ],
        })

    @app.get("/api/me/repertoire.pgn")
    def export_repertoire_pgn():
        user, err = _require_pro()
        if err:
            return err
        with _db() as conn:
            rows = conn.execute(
                "SELECT color, play, name, eco FROM user_repertoire WHERE user_id=?",
                (user["id"],),
            ).fetchall()
        buf = StringIO()
        for r in rows:
            play = (r["play"] or "").strip()
            if not play:
                continue
            board = chess.Board()
            sans = []
            ok = True
            for uci in play.split(","):
                uci = uci.strip()
                if not uci:
                    continue
                try:
                    move = chess.Move.from_uci(uci)
                    sans.append(board.san(move))
                    board.push(move)
                except ValueError:
                    ok = False
                    break
            if not ok or not sans:
                continue
            eco = r["eco"] or ""
            name = r["name"] or "Line"
            color = r["color"]
            buf.write('[Event "Opening Explorer repertoire"]\n')
            buf.write('[Site "Opening Explorer"]\n')
            buf.write(f'[Color "{color}"]\n')
            if eco:
                buf.write(f'[ECO "{eco}"]\n')
            buf.write(f'[Opening "{name}"]\n\n')
            parts = []
            for i, san in enumerate(sans):
                if i % 2 == 0:
                    parts.append(f"{i // 2 + 1}. {san}")
                else:
                    parts.append(san)
            buf.write(" ".join(parts) + " *\n\n")
        return Response(
            buf.getvalue(),
            mimetype="application/x-chess-pgn",
            headers={
                "Content-Disposition": "attachment; filename=repertoire.pgn",
            },
        )

    @app.post("/api/me/repertoire/import-pgn")
    def import_repertoire_pgn():
        user, err = _require_pro()
        if err:
            return err
        ok, cerr = _check_csrf()
        if not ok:
            return cerr
        data = _json_body()
        pgn_text = data.get("pgn") or ""
        color = (data.get("color") or "white").lower()
        if color not in ("white", "black"):
            color = "white"
        if not isinstance(pgn_text, str) or not pgn_text.strip():
            return jsonify({"error": "pgn required"}), 400
        import chess.pgn

        handle = StringIO(pgn_text)
        added = 0
        with _db() as conn:
            while True:
                game = chess.pgn.read_game(handle)
                if game is None:
                    break
                ucis = []
                node = game
                while node.variations:
                    node = node.variation(0)
                    if node.move is None:
                        break
                    ucis.append(node.move.uci())
                    if len(ucis) >= 40:
                        break
                if not ucis:
                    continue
                play = ",".join(ucis)
                name = (
                    game.headers.get("Opening")
                    or game.headers.get("Event")
                    or "Imported line"
                )[:120]
                eco = (game.headers.get("ECO") or "")[:16]
                conn.execute(
                    "INSERT OR REPLACE INTO user_repertoire "
                    "(user_id, color, play, name, eco) VALUES (?, ?, ?, ?, ?)",
                    (user["id"], color, play, name, eco),
                )
                added += 1
                if added >= 100:
                    break
        return jsonify({"imported": added})

    @app.post("/api/me/coach")
    @limiter.limit("report")
    def ai_coach():
        user, err = _require_pro()
        if err:
            return err
        ok, cerr = _check_csrf()
        if not ok:
            return cerr
        import os

        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return jsonify({
                "error": "AI coach is not configured (ANTHROPIC_API_KEY).",
                "code": "coach_unavailable",
            }), 503
        data = _json_body()
        white = data.get("white") or {}
        black = data.get("black") or {}
        priorities = data.get("priorities") or []

        def _fake_color(blob: dict, color: str) -> ColorReport:
            openings = []
            for o in (blob.get("openings") or [])[:12]:
                if not isinstance(o, dict):
                    continue
                openings.append(
                    OpeningStats(
                        name=str(o.get("name") or o.get("family") or "Unknown"),
                        games=int(o.get("games") or 0),
                        wins=int(o.get("wins") or 0),
                        losses=int(o.get("losses") or 0),
                        draws=int(o.get("draws") or 0),
                    )
                )
            total = int(blob.get("total_games") or sum(o.games for o in openings))
            score = float(blob.get("score") or blob.get("overall_score_pct") or 50)
            return ColorReport(
                color=color,
                total_games=total,
                score=score,
                openings=openings,
                first_moves={},
            )

        try:
            w = _fake_color(white, "white")
            b = _fake_color(black, "black")
            prios = []
            for p in priorities[:8]:
                if not isinstance(p, dict):
                    continue
                oname = str(p.get("opening") or "Unknown")
                ost = next(
                    (o for o in (w.openings + b.openings) if o.name == oname),
                    None,
                )
                if ost is None:
                    ost = OpeningStats(name=oname, games=8, wins=2, losses=5, draws=1)
                prios.append(
                    Priority(
                        color=str(p.get("color") or "white"),
                        opening=ost,
                        frequency_pct=float(p.get("frequency_pct") or 5),
                        score_gap=float(
                            p.get("score_gap")
                            or p.get("points_below_baseline")
                            or 10
                        ),
                    )
                )
            md = recommend.get_recommendations(w, b, prios)
        except Exception as exc:
            return jsonify({"error": f"coach failed: {exc}"}), 502
        return jsonify({"markdown": md})

    @app.post("/api/report/share")
    @limiter.limit("report")
    def share_report():
        user = _current_user()
        data = _json_body()
        report = data.get("report")
        if not isinstance(report, dict):
            return jsonify({"error": "report object required"}), 400
        payload = json.dumps(report)[:500_000]
        share_id = secrets.token_urlsafe(12)
        now = time.time()
        with _db() as conn:
            conn.execute(
                "INSERT INTO shared_reports "
                "(share_id, user_id, title, payload_json, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    share_id,
                    user["id"] if user else None,
                    str(data.get("title") or report.get("username") or "Report")[:120],
                    payload,
                    now,
                    now + 30 * 86400,
                ),
            )
        return jsonify({"share_id": share_id, "url": f"/?share={share_id}"}), 201

    @app.get("/api/report/share/<share_id>")
    def get_shared_report(share_id: str):
        with _db() as conn:
            row = conn.execute(
                "SELECT * FROM shared_reports WHERE share_id = ?", (share_id,)
            ).fetchone()
        if row is None:
            return jsonify({"error": "share not found"}), 404
        if row["expires_at"] and time.time() > float(row["expires_at"]):
            return jsonify({"error": "share expired"}), 410
        try:
            report = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            return jsonify({"error": "corrupt share"}), 500
        return jsonify({
            "title": row["title"],
            "report": report,
            "created_at": row["created_at"],
        })

    @app.get("/api/me/sessions")
    def list_sessions():
        user, err = _require_login()
        if err:
            return err
        with _db() as conn:
            rows = conn.execute(
                "SELECT id, created_at, last_seen, user_agent, ip, revoked "
                "FROM auth_sessions WHERE user_id=? AND revoked=0 "
                "ORDER BY last_seen DESC LIMIT 20",
                (user["id"],),
            ).fetchall()
        current = session.get("sid")
        return jsonify({
            "sessions": [
                {**dict(r), "current": r["id"] == current}
                for r in rows
            ],
        })

    @app.post("/api/me/sessions/revoke")
    def revoke_session():
        user, err = _require_login()
        if err:
            return err
        ok, cerr = _check_csrf()
        if not ok:
            return cerr
        data = _json_body()
        sid = (data.get("session_id") or "").strip()
        revoke_all = bool(data.get("all"))
        with _db() as conn:
            if revoke_all:
                current = session.get("sid")
                if current:
                    conn.execute(
                        "UPDATE auth_sessions SET revoked=1 "
                        "WHERE user_id=? AND id!=?",
                        (user["id"], current),
                    )
                else:
                    conn.execute(
                        "UPDATE auth_sessions SET revoked=1 WHERE user_id=?",
                        (user["id"],),
                    )
            elif sid:
                conn.execute(
                    "UPDATE auth_sessions SET revoked=1 "
                    "WHERE id=? AND user_id=?",
                    (sid, user["id"]),
                )
            else:
                return jsonify({"error": "session_id or all required"}), 400
        return jsonify({"ok": True})

    app_module._check_csrf = _check_csrf
    app_module._ensure_csrf = _ensure_csrf
    app_module._touch_habit = _touch_habit
