//@ts-nocheck
/** Legacy SPA logic extracted from the monolith index.html. */
import { Chess } from "@vendor/chess.js";
import { state, START_FEN } from "./state";
import { evalFenLocal } from "./localEval";
import {
  initProductAnalytics,
  identifyProductUser,
  resetProductAnalytics,
  trackProductEvent,
} from "./analytics/posthog";

/* ---------- game model (chess.js) ---------- */

let game = new Chess();     // full played game (may extend past state.ply)
let gameRootFen = START_FEN; // FEN before any moves in `game`
let freeFen = null;          // raw FEN when Chess.js rejects (e.g. missing king)

function tryLoadChess(fen) {
  try { return new Chess(fen); } catch (e) { return null; }
}

function fenHasBothKings(fen) {
  const p = (fen || "").split(" ")[0] || "";
  return p.includes("K") && p.includes("k");
}

function rebuildDerived() {
  const verbose = game.history({ verbose: true });
  state.moves = verbose;
  state.sans = verbose.map(m => m.san);
  state.fens = [gameRootFen, ...verbose.map(m => m.after)];
}

function truncateToPly() {
  // drop moves after the current ply (standard analysis-board behavior)
  const keep = state.moves.slice(0, state.ply);
  game = new Chess(gameRootFen);
  for (const m of keep) game.move({ from: m.from, to: m.to, promotion: m.promotion });
  rebuildDerived();
}

function makeUserMove(from, to, promotion, opts = {}) {
  if (state.editMode || freeFen) return false;
  if (state.ply < state.sans.length) {
    truncateToPly();
    clearExploreAnnsFrom(state.ply + 1);
  }
  const fenBefore = currentFen();
  let played;
  try {
    played = game.move({ from, to, promotion });
  } catch (e) {
    return false;
  }
  if (!played) return false;
  rebuildDerived();
  state.ply = state.sans.length;
  clearExploreAnnsFrom(state.ply);
  clearUserShapes();
  const animate = opts.animate !== false && !opts.viaDrag;
  const finish = () => {
    playMoveSound(played, currentFen());
    if (!isMainLinePly(state.ply)) {
      scheduleExploreAnnotate(state.ply, fenBefore, state.sans[state.ply - 1]);
    }
    if (state.practiceMode) {
      updatePracticeGameStatus();
      schedulePracticeFeedback(fenBefore, played.san);
      schedulePracticeReply();
    }
  };
  if (animate) {
    animateThenRefresh(played, finish);
  } else {
    refreshBoard();
    finish();
  }
  return true;
}

/* ---------- undo (edit actions / take back one move) ---------- */

let editUndoStack = [];

function clearEditUndo() {
  editUndoStack = [];
}

function pushEditUndo() {
  if (!state.editMode) return;
  editUndoStack.push(currentFen());
  if (editUndoStack.length > 120) editUndoStack.shift();
}

function isCustomOrEditPosition() {
  return !!(state.editMode || freeFen || gameRootFen !== START_FEN);
}

/** One step back: undo editor action, or take back the move that led here. */
function undoLastAction() {
  cancelLineAnim(false);
  if (state.editMode || (freeFen && !state.sans.length)) {
    if (!editUndoStack.length) return false;
    const fen = editUndoStack.pop();
    setCustomFen(fen, { keepEdit: true });
    return true;
  }
  if (state.ply <= 0) return false;
  state.ply -= 1;
  truncateToPly();
  clearExploreAnnsFrom(state.ply + 1);
  refreshBoard();
  return true;
}

/** Normalize to a playable / evaluable FEN (clear castling/ep for editor roots). */
function normalizeEditFen(placement, turn) {
  const t = turn === "b" ? "b" : "w";
  return `${placement} ${t} - - 0 1`;
}

function setCustomFen(fen, { keepEdit = true } = {}) {
  const parts = fen.trim().split(/\s+/);
  const placement = parts[0];
  const turn = parts[1] === "b" ? "b" : "w";
  const normalized = normalizeEditFen(placement, turn);
  const c = tryLoadChess(normalized);
  if (c) {
    freeFen = null;
    gameRootFen = normalized;
    game = c;
    rebuildDerived();
  } else {
    freeFen = normalized;
    gameRootFen = normalized;
    game = new Chess(); // unused until position becomes legal
    state.moves = [];
    state.sans = [];
    state.fens = [normalized];
  }
  state.ply = 0;
  state.moveFlags = [];
  state.lastOpeningName = null;
  if (!keepEdit && state.editMode) setEditMode(false);
  refreshBoard();
}

function boardMapFromFen(fen) {
  const map = {};
  const rows = fen.split(" ")[0].split("/");
  rows.forEach((row, r) => {
    let f = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) f += +ch;
      else {
        map["abcdefgh"[f] + (8 - r)] = ch;
        f++;
      }
    }
  });
  return map;
}

function fenFromBoardMap(map, turn) {
  const ranks = [];
  for (let r = 8; r >= 1; r--) {
    let row = "";
    let empty = 0;
    for (let f = 0; f < 8; f++) {
      const sq = "abcdefgh"[f] + r;
      const pc = map[sq];
      if (!pc) empty++;
      else {
        if (empty) { row += empty; empty = 0; }
        row += pc;
      }
    }
    if (empty) row += empty;
    ranks.push(row);
  }
  const cur = currentFen().split(/\s+/);
  return normalizeEditFen(ranks.join("/"), turn || (cur[1] === "b" ? "b" : "w"));
}

function placePieceOn(sq, piece) {
  pushEditUndo();
  const map = boardMapFromFen(currentFen());
  // Only one king per side
  if (piece === "K" || piece === "k") {
    for (const [s, p] of Object.entries(map)) {
      if (p === piece) delete map[s];
    }
  }
  map[sq] = piece;
  setCustomFen(fenFromBoardMap(map));
}

function removePieceAt(sq) {
  const map = boardMapFromFen(currentFen());
  if (!map[sq]) return;
  pushEditUndo();
  delete map[sq];
  setCustomFen(fenFromBoardMap(map));
}

function relocatePiece(from, to) {
  if (from === to) return;
  const map = boardMapFromFen(currentFen());
  const pc = map[from];
  if (!pc) return;
  pushEditUndo();
  delete map[from];
  map[to] = pc;
  // After a board move in the editor, flip side-to-move (White ↔ Black).
  const curTurn = (currentFen().split(/\s+/)[1] === "b") ? "b" : "w";
  const nextTurn = curTurn === "w" ? "b" : "w";
  setCustomFen(fenFromBoardMap(map, nextTurn));
  updateEditTurnBtns();
}

function setSideToMove(turn) {
  const parts = currentFen().split(/\s+/);
  const cur = parts[1] === "b" ? "b" : "w";
  if (cur === turn) return;
  pushEditUndo();
  setCustomFen(normalizeEditFen(parts[0], turn));
  updateEditTurnBtns();
}

function updateEditTurnBtns() {
  const turn = currentFen().split(/\s+/)[1] || "w";
  document.getElementById("edit-turn-w").classList.toggle("active", turn === "w");
  document.getElementById("edit-turn-b").classList.toggle("active", turn === "b");
}

/* ---------- board rendering ---------- */

const boardEl = document.getElementById("board");

const ANN_GLYPH = {
  blunder: "??",
  mistake: "?",
  inaccuracy: "?!",
  great: "!",
  best: "★",
  brilliant: "★",
};

function clearExploreAnnsFrom(ply) {
  if (!state.exploreAnns) state.exploreAnns = {};
  for (const k of Object.keys(state.exploreAnns)) {
    if (+k >= ply) delete state.exploreAnns[k];
  }
}

/** True if sans[0..ply-1] still match the original reviewed game. */
function isMainLinePly(ply) {
  if (!ply || !state.mainLineSans?.length) return !state.mainLineSans?.length;
  if (ply > state.mainLineSans.length) return false;
  for (let i = 0; i < ply; i++) {
    if (state.sans[i] !== state.mainLineSans[i]) return false;
  }
  return true;
}

function annotationAtPly(ply) {
  if (!ply) return null;
  // Off-main-line: only use live explore classifications (never original game flags).
  if (!isMainLinePly(ply)) {
    const ex = state.exploreAnns?.[ply];
    return ex && ANN_GLYPH[ex.severity] ? ex : null;
  }
  for (const f of (state.moveFlags || [])) {
    if (f.ply === ply && ANN_GLYPH[f.severity]) return f;
  }
  return null;
}

let exploreAnnotateToken = 0;

async function scheduleExploreAnnotate(ply, fenBefore, san) {
  if (!ply || !fenBefore || !san) return;
  const token = ++exploreAnnotateToken;
  try {
    const data = await api("/api/annotate-ply", "POST", {
      fen: fenBefore,
      san,
      ply,
    });
    if (token !== exploreAnnotateToken) return;
    if (state.ply !== ply && !isMainLinePly(ply)) {
      // Still accept if this ply remains on the current line.
      if (state.sans[ply - 1] !== san) return;
    }
    if (data.annotation && ANN_GLYPH[data.annotation.severity]) {
      const sev = data.annotation.severity;
      // At most one best/great ("engine top choice") badge per game view.
      if (sev === "best" || sev === "great") {
        const hasTop = (state.moveFlags || []).some(
          f => f.severity === "best" || f.severity === "great"
        ) || Object.entries(state.exploreAnns || {}).some(
          ([k, f]) => f && (f.severity === "best" || f.severity === "great") && +k !== ply
        );
        if (hasTop) {
          delete state.exploreAnns[ply];
        } else {
          state.exploreAnns[ply] = data.annotation;
        }
      } else {
        state.exploreAnns[ply] = data.annotation;
      }
    } else {
      delete state.exploreAnns[ply];
    }
    if (state.ply === ply) refreshBoard();
    else if (state.gameView) renderGameMoveList();
  } catch (e) {
    if (!handleProGateError(e)) {
      /* explore annotate is best-effort */
    }
  }
}

function renderBoard(fen, orientation, hlSquares, hlSeverity) {
  const rows = fen.split(" ")[0].split("/");
  // grid[rank 8..1][file a..h]
  const grid = rows.map(row => {
    const cells = [];
    for (const ch of row) {
      if (/\d/.test(ch)) for (let i = 0; i < +ch; i++) cells.push(null);
      else cells.push(ch);
    }
    return cells;
  });

  boardEl.innerHTML = "";
  const idx = [0,1,2,3,4,5,6,7];
  const ranks = orientation === "black" ? [...idx].reverse() : idx;
  const files = orientation === "black" ? [...idx].reverse() : idx;
  for (const r of ranks) {          // r: 0 = rank 8
    for (const f of files) {        // f: 0 = file a
      const sq = document.createElement("div");
      const light = (r + f) % 2 === 0;
      sq.className = "sq " + (light ? "light" : "dark");
      const sqName = "abcdefgh"[f] + (8 - r);
      sq.dataset.square = sqName;
      if (hlSquares && hlSquares.has(sqName)) {
        sq.classList.add("hl");
        if (hlSeverity && ANN_GLYPH[hlSeverity]) sq.classList.add("hl-" + hlSeverity);
      }
      const pc = grid[r][f];
      if (pc) {
        const img = document.createElement("img");
        const isWhite = pc === pc.toUpperCase();
        img.className = "piece";
        img.src = "/pieces/" + (isWhite ? "w" : "b") + pc.toLowerCase() + ".png";
        img.alt = pc;
        img.dataset.piece = pc;
        img.draggable = false;
        sq.appendChild(img);
      }
      if (r === ranks[ranks.length - 1]) {
        const c = document.createElement("span");
        c.className = "coord file"; c.textContent = "abcdefgh"[f];
        sq.appendChild(c);
      }
      if (f === files[0]) {
        const c = document.createElement("span");
        c.className = "coord rank"; c.textContent = 8 - r;
        sq.appendChild(c);
      }
      boardEl.appendChild(sq);
    }
  }
}

function diffSquares(fenA, fenB) {
  // squares whose contents changed between two FENs -> highlight last move
  const expand = fen => {
    const out = {};
    fen.split(" ")[0].split("/").forEach((row, r) => {
      let f = 0;
      for (const ch of row) {
        if (/\d/.test(ch)) { f += +ch; continue; }
        out["abcdefgh"[f] + (8 - r)] = ch;
        f++;
      }
    });
    return out;
  };
  const a = expand(fenA), b = expand(fenB), changed = new Set();
  for (const s of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (a[s] !== b[s]) changed.add(s);
  }
  return changed;
}

function currentFen() {
  if (freeFen != null) return freeFen;
  return state.fens[state.ply];
}

/* ---------- material / captured pieces (chess.com-style) ---------- */

const PIECE_START = { p: 8, n: 2, b: 2, r: 2, q: 1 };
const PIECE_VALUE = { p: 1, n: 3, b: 3, r: 5, q: 9 };
const PIECE_ORDER = ["q", "r", "b", "n", "p"];  // chess.com order: high → low value

function materialState(fen) {
  const on = {
    w: { p: 0, n: 0, b: 0, r: 0, q: 0 },
    b: { p: 0, n: 0, b: 0, r: 0, q: 0 },
  };
  for (const ch of (fen || "").split(" ")[0] || "") {
    if (ch === "/" || /\d/.test(ch)) continue;
    const color = ch === ch.toUpperCase() ? "w" : "b";
    const t = ch.toLowerCase();
    if (t === "k" || on[color][t] === undefined) continue;
    on[color][t]++;
  }

  function tookFrom(defenderColor) {
    const def = on[defenderColor];
    let extras = 0;
    for (const t of ["q", "r", "b", "n"]) {
      extras += Math.max(0, def[t] - PIECE_START[t]);
    }
    const took = {};
    for (const t of PIECE_ORDER) {
      if (t === "p") {
        took.p = Math.max(0, PIECE_START.p - def.p - extras);
      } else {
        took[t] = Math.max(0, PIECE_START[t] - def[t]);
      }
    }
    return took;
  }

  // Pieces white captured = missing black pieces (and vice versa)
  const whiteTook = tookFrom("b");
  const blackTook = tookFrom("w");

  let whiteVal = 0, blackVal = 0;
  for (const t of PIECE_ORDER) {
    whiteVal += on.w[t] * PIECE_VALUE[t];
    blackVal += on.b[t] * PIECE_VALUE[t];
  }
  return { whiteTook, blackTook, diff: whiteVal - blackVal };
}

function renderMaterialTray(el, took, pieceColorPrefix, advantageForThisSide) {
  const caps = el.querySelector(".caps");
  const adv = el.querySelector(".adv");
  caps.innerHTML = "";
  for (const t of PIECE_ORDER) {
    const n = took[t] || 0;
    for (let i = 0; i < n; i++) {
      const img = document.createElement("img");
      img.src = `/pieces/${pieceColorPrefix}${t}.png`;
      img.alt = t;
      img.draggable = false;
      caps.appendChild(img);
    }
  }
  if (advantageForThisSide > 0) {
    adv.textContent = "+" + Math.round(advantageForThisSide);
    adv.classList.remove("empty");
  } else {
    adv.textContent = "";
    adv.classList.add("empty");
  }
}

function renderMaterial() {
  const { whiteTook, blackTook, diff } = materialState(currentFen());
  const top = document.getElementById("material-top");
  const bot = document.getElementById("material-bot");
  // Top of board = opponent relative to orientation; show what THAT color has captured
  if (state.orientation === "white") {
    // Top = Black sitting, Bottom = White sitting
    renderMaterialTray(top, blackTook, "w", diff < 0 ? -diff : 0);
    renderMaterialTray(bot, whiteTook, "b", diff > 0 ? diff : 0);
  } else {
    // Top = White sitting, Bottom = Black sitting
    renderMaterialTray(top, whiteTook, "b", diff > 0 ? diff : 0);
    renderMaterialTray(bot, blackTook, "w", diff < 0 ? -diff : 0);
  }
  renderPlayerStrips();
}

function fillPlayerStrip(el, player) {
  if (!el) return;
  const nameEl = el.querySelector(".player-name");
  const ratingEl = el.querySelector(".player-rating");
  if (!player || !player.name) {
    el.classList.add("idle");
    if (nameEl) nameEl.textContent = "";
    if (ratingEl) ratingEl.textContent = "";
    return;
  }
  el.classList.remove("idle");
  nameEl.textContent = player.name;
  ratingEl.textContent = player.rating ? `(${player.rating})` : "";
}

function renderPlayerStrips() {
  const top = document.getElementById("player-top");
  const bot = document.getElementById("player-bot");
  const players = state.boardPlayers;
  if (!players) {
    fillPlayerStrip(top, null);
    fillPlayerStrip(bot, null);
    return;
  }
  // Top strip = color sitting at the top of the board
  if (state.orientation === "white") {
    fillPlayerStrip(top, players.black);
    fillPlayerStrip(bot, players.white);
  } else {
    fillPlayerStrip(top, players.white);
    fillPlayerStrip(bot, players.black);
  }
}

function setBoardPlayersFromGame(g) {
  if (!g) {
    state.boardPlayers = null;
    return;
  }
  const meName = (g.my_username || state.report?.username ||
    document.getElementById("username")?.value || "").trim() || "You";
  const me = { name: meName, rating: g.my_rating || 0 };
  const opp = { name: g.opponent || "Opponent", rating: g.opponent_rating || 0 };
  state.boardPlayers = g.color === "black"
    ? { white: opp, black: me }
    : { white: me, black: opp };
}

function clearBoardPlayers() {
  state.boardPlayers = null;
  renderPlayerStrips();
  requestAnimationFrame(syncSidebarToBoard);
}

function refreshBoard() {
  let hl = null;
  let hlSeverity = null;
  if (state.ply > 0) {
    hl = diffSquares(state.fens[state.ply - 1], currentFen());
    // Prefer exact from/to of the played move when available.
    const mv = state.moves[state.ply - 1];
    if (mv?.from && mv?.to) hl = new Set([mv.from, mv.to]);
    hlSeverity = annotationAtPly(state.ply)?.severity || null;
  }
  // Opening lesson: at the critical position (before the bad move), tint the
  // squares of the move that was actually played so the mistake is visible.
  const lesson = currentReviewIssue();
  if (lesson && state.ply === Math.max(0, lesson.flag.ply - 1)) {
    const played = parseBestMove(currentFen(), lesson.flag.san);
    if (played) {
      hl = new Set([played.from, played.to]);
      hlSeverity = lesson.flag.severity || hlSeverity;
    }
  }
  clearSelection({ keepPremove: true, keepShapes: true });
  renderBoard(currentFen(), state.orientation, hl, hlSeverity);
  applyCheckHighlight();
  applyPremoveHighlight();
  markMovablePieces();
  renderMoveList();
  renderMaterial();
  if (state.editMode) updateEditTurnBtns();
  scheduleOpeningTitle();
  scheduleEval();
  if (state.repertoireOpen) updateRepTreeActive();
  renderUserShapes();
  drawReviewBestArrow();
}

/* ---------- dynamic opening title ---------- */

let openingBook = null;          // Map playUci -> {name,eco}
let openingBookLoading = null;

function playUciToPly(ply) {
  return state.moves.slice(0, ply)
    .map(m => m.from + m.to + (m.promotion || ""))
    .join(",");
}

function setBoardTitle(text) {
  const el = document.getElementById("line-title");
  if (el) el.textContent = text;
}

function longestOpeningMatch(play) {
  if (!play || !openingBook) return null;
  const parts = play.split(",");
  for (let i = parts.length; i >= 1; i--) {
    const key = parts.slice(0, i).join(",");
    const hit = openingBook[key];
    if (hit && hit.name) return { ...hit, matched_ply: i, ply: parts.length };
  }
  return null;
}

async function ensureOpeningBook() {
  if (openingBook) return openingBook;
  if (openingBookLoading) return openingBookLoading;
  openingBookLoading = fetch("/openings.json")
    .then(r => r.json())
    .then(data => { openingBook = data || {}; return openingBook; })
    .catch(() => { openingBook = {}; return openingBook; })
    .finally(() => { openingBookLoading = null; });
  return openingBookLoading;
}

function applyOpeningResult(data, ply) {
  if (data && data.name) {
    state.lastOpeningName = data.eco ? `${data.name} (${data.eco})` : data.name;
    setBoardTitle(state.lastOpeningName);
    if (state.gameView && gameOpeningTitleEl) {
      gameOpeningTitleEl.textContent = state.lastOpeningName;
    }
    return;
  }
  if (state.lastOpeningName) {
    setBoardTitle(state.lastOpeningName);
  } else {
    setBoardTitle(ply > 0 ? `Position after ${Math.ceil(ply / 2)} moves` : "Starting position");
  }
  if (state.gameView && gameOpeningTitleEl && state.lastOpeningName) {
    gameOpeningTitleEl.textContent = state.lastOpeningName;
  }
}

function scheduleOpeningTitle() {
  const ply = state.ply;
  if (ply === 0) {
    if (state.gameView) {
      // Keep the game's variation name while scrubbing back to the start.
      const title = state.activeGame?.variation || state.lastOpeningName || "Game";
      setBoardTitle(title);
      if (gameOpeningTitleEl) gameOpeningTitleEl.textContent = title;
      return;
    }
    state.lastOpeningName = null;
    if (state.editMode) setBoardTitle("Edit position");
    else if (gameRootFen !== START_FEN || freeFen) setBoardTitle("Custom position");
    else setBoardTitle("Starting position");
    return;
  }
  const play = playUciToPly(ply);

  // Fast path: local ECO book (instant, updates every move).
  if (openingBook) {
    applyOpeningResult(longestOpeningMatch(play), ply);
    return;
  }

  // Book still loading — provisional title, then refine.
  if (state.lastOpeningName) setBoardTitle(state.lastOpeningName);
  ensureOpeningBook().then(() => {
    if (state.ply !== ply) return;
    applyOpeningResult(longestOpeningMatch(play), ply);
  });
}

// Kick off book download as soon as the page loads.
ensureOpeningBook();


/* ---------- Stockfish evaluation ---------- */

const enginePanel = document.getElementById("engine-panel");
const evalFill = document.querySelector("#evalbar .white-fill");
const scoreEl = document.getElementById("engine-score");
const labelEl = document.getElementById("engine-label");
const pvEl = document.getElementById("engine-pv");

let engineAvailable = null;   // null = unknown, then true/false
let evalTimer = null;
let evalToken = 0;
const evalCache = new Map();

function scheduleEval() {
  clearTimeout(evalTimer);
  if (typeof isAnimatingLine === "function" && isAnimatingLine()) return;   // no per-step evals while replaying a line
  const fen = currentFen();
  if (!fenHasBothKings(fen)) {
    enginePanel.classList.add("visible");
    scoreEl.textContent = "—";
    scoreEl.className = "";
    labelEl.textContent = "Stockfish — place both kings to evaluate";
    pvEl.innerHTML = "";
    clearBestArrow();
    if (evalFill) evalFill.style.height = "50%";
    return;
  }
  if (evalCache.has(fen)) { renderEval(evalCache.get(fen)); return; }
  clearBestArrow();
  enginePanel.classList.add("visible");
  labelEl.textContent = "Stockfish \u2014 thinking\u2026";
  evalTimer = setTimeout(() => fetchEval(fen), 200);
}

async function fetchEval(fen) {
  const token = ++evalToken;
  const path = `/api/eval?fen=${encodeURIComponent(fen)}`;
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const abortTimer = ctrl ? setTimeout(() => ctrl.abort(), 10000) : null;
  try {
    await ensureApiBase();
    let resp = await fetchApi(path, ctrl ? { signal: ctrl.signal } : {});
    let data;
    try {
      data = await readJson(resp);
    } catch (e) {
      // Netlify proxy miss / HTML error page — retry once against configured API base.
      if (e.isProxyMiss && !apiBase && RENDER_API) {
        apiBase = RENDER_API;
        resp = await fetchApi(path);
        data = await readJson(resp);
      } else if (e.isProxyMiss || e.httpStatus === 504 || e.httpStatus === 502) {
        labelEl.textContent = "Stockfish — proxy timed out, using browser engine…";
        await fetchEvalLocal(fen, token);
        return;
      } else {
        throw e;
      }
    }
    if (resp.status === 401 || data?.code === "login_required") {
      engineAvailable = null;
      enginePanel.classList.add("visible");
      labelEl.textContent = "Stockfish — sign in to see evaluations";
      clearBestArrow();
      // Soft prompt once — never re-open on every eval refresh.
      openAuthModal({ soft: true });
      return;
    }
    if (resp.status === 429) {
      engineAvailable = null;
      enginePanel.classList.add("visible");
      labelEl.textContent = "Stockfish — busy, retrying shortly…";
      return;
    }
    if (resp.status === 503 || resp.status === 504 || resp.status === 502) {
      // Server engine down / gateway timeout — fall back to in-browser Stockfish WASM.
      labelEl.textContent = "Stockfish — server unavailable, using browser engine…";
      await fetchEvalLocal(fen, token);
      return;
    }
    if (!resp.ok) {
      engineAvailable = null;
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    engineAvailable = true;
    evalCache.set(fen, data);
    if (token === evalToken && fen === currentFen()) renderEval(data);
  } catch (e) {
    // Network / abort / proxy failure — try WASM before giving up.
    try {
      labelEl.textContent = "Stockfish — trying browser engine…";
      await fetchEvalLocal(fen, token);
      return;
    } catch (_) { /* fall through */ }
    if (token === evalToken) {
      enginePanel.classList.add("visible");
      const msg = (e && e.name === "AbortError")
        ? "server too slow — browser engine also unavailable"
        : (e.message || "engine unavailable");
      labelEl.textContent = "Stockfish \u2014 " + msg;
    }
  } finally {
    if (abortTimer) clearTimeout(abortTimer);
  }
}

function uciToSan(fen, uci) {
  try {
    const c = new Chess(fen);
    const from = uci.slice(0, 2);
    const to = uci.slice(2, 4);
    const promotion = uci.length > 4 ? uci[4] : undefined;
    const m = c.move({ from, to, promotion });
    return m ? m.san : null;
  } catch (_) {
    return null;
  }
}

async function fetchEvalLocal(fen, token) {
  const raw = await evalFenLocal(fen);
  if (token !== evalToken || fen !== currentFen()) return;
  const bestSan = raw.best_san ? (uciToSan(fen, raw.best_san) || raw.best_san) : null;
  const data = {
    cp: raw.cp,
    mate: raw.mate,
    best_san: bestSan,
    pv_san: bestSan || "",
    depth: raw.depth,
    source: "wasm",
  };
  engineAvailable = true;
  evalCache.set(fen, data);
  renderEval(data);
  labelEl.textContent = `Stockfish (browser) \u2014 depth ${data.depth}`;
}

function renderEval(data) {
  enginePanel.classList.add("visible");
  let text, whiteShare;
  if (data.mate != null) {
    text = (data.mate > 0 ? "M" : "-M") + Math.abs(data.mate);
    whiteShare = data.mate > 0 ? 1 : 0;
  } else if (typeof data.cp === "number") {
    const p = data.cp / 100;
    text = (p > 0 ? "+" : "") + p.toFixed(1);
    whiteShare = 1 / (1 + Math.exp(-p / 1.5));   // squash to 0..1
  } else {
    text = "—";
    whiteShare = 0.5;
  }
  scoreEl.textContent = text;
  scoreEl.className = (data.mate != null ? data.mate > 0 : (data.cp ?? 0) >= 0)
    ? "white-adv" : "black-adv";
  labelEl.textContent = `Stockfish \u2014 depth ${data.depth}`;
  if (evalFill) {
    evalFill.style.bottom = "0";
    evalFill.style.top = "auto";
    evalFill.style.height = (whiteShare * 100).toFixed(1) + "%";
  }
  pvEl.innerHTML = data.best_san
    ? `Best: <b>${data.best_san}</b> &nbsp; ${data.pv_san}`
    : "";
  if (showBestMove && data.best_san) {
    const mv = parseBestMove(currentFen(), data.best_san);
    if (mv) drawBestArrow(mv.from, mv.to);
    else clearBestArrow();
  } else if (drawReviewBestArrow()) {
    /* review lesson arrow already drawn */
  } else if (!showBestMove) {
    clearBestArrow();
  }
}

/* ---------- load FEN / PGN ---------- */

const positionInput = document.getElementById("position-input");
const positionLoadBtn = document.getElementById("position-load-btn");
const positionLoadMsg = document.getElementById("position-load-msg");

function clearPositionInput() {
  if (positionInput) positionInput.value = "";
  setPositionLoadMsg("");
}

function setPositionLoadMsg(text, isError) {
  if (!positionLoadMsg) return;
  positionLoadMsg.textContent = text || "";
  positionLoadMsg.classList.toggle("err", !!isError);
}

function stripPositionWrapper(text) {
  let t = (text || "").trim().replace(/^["']+|["']+$/g, "");
  t = t.replace(/^(fen|pgn)\s*[:=]\s*/i, "");
  return t.trim();
}

function fenFromPastedUrl(text) {
  try {
    const u = new URL(text.trim());
    const fen = u.searchParams.get("fen") || u.searchParams.get("FEN");
    if (fen) return fen;
    const pathFen = u.pathname.match(/\/analysis\/(?:(?:standard|chess960)\/)?([^/?#]+)/i);
    if (pathFen?.[1]?.includes("/")) {
      return decodeURIComponent(pathFen[1]).replace(/_/g, " ");
    }
  } catch (_) { /* not a URL */ }
  return null;
}

function looksLikeFen(text) {
  const first = (text || "").trim().split(/\s+/)[0] || "";
  return /^[rnbqkpRNBQKP1-8]+(\/[rnbqkpRNBQKP1-8]+){7}$/.test(first);
}

function applyLoadedChess(c, { plyAtEnd = false } = {}) {
  if (state.editMode) setEditMode(false);
  freeFen = null;
  const verbose = c.history({ verbose: true });
  gameRootFen = verbose.length ? verbose[0].before : c.fen();
  game = c;
  rebuildDerived();
  state.ply = plyAtEnd ? state.sans.length : 0;
  state.moveFlags = [];
  state.mainLineSans = [];
  state.exploreAnns = {};
  exploreAnnotateToken += 1;
  state.lastOpeningName = null;
  clearPremove();
  clearUserShapes();
  clearBoardPlayers();
  setGameView(false);
  clearGamesFilters();
  refreshBoard();
}

function loadPositionFromText(raw, { quietFail = false } = {}) {
  enginePanel.classList.add("visible");
  const text = stripPositionWrapper(raw);
  if (!text) {
    if (!quietFail) setPositionLoadMsg("Paste a FEN or PGN first.", true);
    return false;
  }

  const fromUrl = fenFromPastedUrl(text);
  const fenCandidate = fromUrl || (looksLikeFen(text) ? text : null);
  if (fenCandidate) {
    const c = tryLoadChess(fenCandidate);
    if (c) {
      applyLoadedChess(c);
      clearPositionInput();
      setPositionLoadMsg("Position loaded.");
      return true;
    }
    const parts = fenCandidate.trim().split(/\s+/);
    if (parts[0]?.split("/").length === 8) {
      setCustomFen(fenCandidate, { keepEdit: false });
      clearPositionInput();
      setPositionLoadMsg("Position loaded.");
      return true;
    }
    if (!quietFail) setPositionLoadMsg("That FEN isn’t a legal position.", true);
    return false;
  }

  try {
    const c = new Chess();
    c.loadPgn(text, { strict: false });
    const moves = c.history();
    const isStart = c.fen() === START_FEN;
    if (!moves.length && isStart && !/\[FEN\b/i.test(text)) {
      if (!quietFail) setPositionLoadMsg("Couldn’t read that as FEN or PGN.", true);
      return false;
    }
    applyLoadedChess(c, { plyAtEnd: moves.length > 0 });
    clearPositionInput();
    setPositionLoadMsg(moves.length ? `Loaded ${moves.length} moves.` : "Position loaded.");
    return true;
  } catch (_) {
    if (!quietFail) setPositionLoadMsg("Couldn’t read that as FEN or PGN.", true);
    return false;
  }
}

function onPositionLoad() {
  loadPositionFromText(positionInput?.value);
}

positionLoadBtn?.addEventListener("click", onPositionLoad);
positionInput?.addEventListener("keydown", e => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey || !e.shiftKey)) {
    e.preventDefault();
    onPositionLoad();
  }
});
positionInput?.addEventListener("paste", () => {
  requestAnimationFrame(() => {
    const text = positionInput?.value || "";
    if (looksLikeFen(text) || fenFromPastedUrl(text) || /\[Event\b/i.test(text) || /\d+\./.test(text)) {
      loadPositionFromText(text, { quietFail: true });
    }
  });
});

/* ---------- best-move arrow ---------- */

const BEST_MOVE_LS = "showBestMove";
let showBestMove = localStorage.getItem(BEST_MOVE_LS) === "true";
const bestMoveBtn = document.getElementById("best-move-btn");

function updateBestMoveBtn() {
  bestMoveBtn.classList.toggle("active", showBestMove);
}

function setShowBestMove(on) {
  showBestMove = on;
  localStorage.setItem(BEST_MOVE_LS, on ? "true" : "false");
  updateBestMoveBtn();
  if (on) scheduleEval();
  else clearBestArrow();
}

bestMoveBtn.classList.toggle("active", showBestMove);
bestMoveBtn.onclick = () => setShowBestMove(!showBestMove);

/* ---------- edit position mode ---------- */

let palettePiece = null;   // FEN piece char selected for click-to-place
const editPosBtn = document.getElementById("edit-pos-btn");

function fillPieceBank(el) {
  el.innerHTML = "";
  const mkRow = (chars) => {
    const row = document.createElement("div");
    row.className = "bank-row";
    for (const pc of chars) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "bank-piece";
      btn.dataset.piece = pc;
      btn.title = (pc === pc.toUpperCase() ? "White " : "Black ") +
        ({ k: "king", q: "queen", r: "rook", b: "bishop", n: "knight", p: "pawn" }[pc.toLowerCase()]);
      const img = document.createElement("img");
      const isWhite = pc === pc.toUpperCase();
      img.src = `/pieces/${isWhite ? "w" : "b"}${pc.toLowerCase()}.png`;
      img.alt = pc;
      img.draggable = false;
      btn.appendChild(img);
      row.appendChild(btn);
    }
    return row;
  };
  el.appendChild(mkRow(["K", "Q", "R", "B", "N", "P"]));
  el.appendChild(mkRow(["k", "q", "r", "b", "n", "p"]));
}

fillPieceBank(document.getElementById("bank-top"));
fillPieceBank(document.getElementById("bank-bot"));

function syncPaletteSelection() {
  document.querySelectorAll(".bank-piece").forEach(btn => {
    btn.classList.toggle("selected", btn.dataset.piece === palettePiece);
  });
}

function setPalettePiece(pc) {
  palettePiece = pc || null;
  syncPaletteSelection();
}

function setEditMode(on) {
  state.editMode = !!on;
  document.body.classList.toggle("edit-mode", state.editMode);
  editPosBtn.classList.toggle("active", state.editMode);
  palettePiece = null;
  syncPaletteSelection();
  clearSelection();
  clearEditUndo();
  // Collapse move history into a single custom root so free placement is the new start.
  setCustomFen(currentFen());
  if (state.editMode) updateEditTurnBtns();
  requestAnimationFrame(syncSidebarToBoard);
}

editPosBtn.onclick = () => setEditMode(!state.editMode);
document.getElementById("edit-turn-w").onclick = () => setSideToMove("w");
document.getElementById("edit-turn-b").onclick = () => setSideToMove("b");
document.getElementById("edit-clear").onclick = () => {
  pushEditUndo();
  setCustomFen(normalizeEditFen("8/8/8/8/8/8/8/8", currentFen().split(/\s+/)[1] || "w"));
};

document.querySelectorAll(".piece-bank").forEach(bank => {
  bank.addEventListener("click", e => {
    if (!state.editMode) return;
    const btn = e.target.closest(".bank-piece");
    if (!btn) return;
    setPalettePiece(btn.dataset.piece);
  });
});

function sqToCenter(sq, orientation) {
  const file = sq.charCodeAt(0) - 97;
  const rank = +sq[1];
  if (orientation === "black") return { x: 7 - file + 0.5, y: rank - 0.5 };
  return { x: file + 0.5, y: 8 - rank + 0.5 };
}

function parseBestMove(fen, bestSan) {
  if (!bestSan) return null;
  try {
    const c = new Chess(fen);
    const raw = String(bestSan).trim();
    try {
      const m = c.move(raw);
      if (m) return { from: m.from, to: m.to };
    } catch (_) { /* try UCI below */ }
    const uci = raw.replace(/[^a-h1-8qrbn]/gi, "");
    if (/^[a-h][1-8][a-h][1-8][qrbn]?$/i.test(uci)) {
      const m = c.move({
        from: uci.slice(0, 2).toLowerCase(),
        to: uci.slice(2, 4).toLowerCase(),
        promotion: uci.length > 4 ? uci[4].toLowerCase() : undefined,
      });
      if (m) return { from: m.from, to: m.to };
    }
    return null;
  } catch (e) {
    return null;
  }
}

let bestArrowEl = null;

function ensureBestArrowLayer() {
  const frame = document.querySelector(".board-frame") || boardEl;
  if (bestArrowEl && frame.contains(bestArrowEl)) return bestArrowEl;
  bestArrowEl = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  bestArrowEl.id = "best-arrow";
  bestArrowEl.setAttribute("viewBox", "0 0 8 8");
  bestArrowEl.setAttribute("preserveAspectRatio", "none");
  frame.appendChild(bestArrowEl);
  return bestArrowEl;
}

function clearBestArrow() {
  if (bestArrowEl) bestArrowEl.replaceChildren();
}

function drawBestArrow(from, to) {
  const svg = ensureBestArrowLayer();
  svg.replaceChildren();
  const a = sqToCenter(from, state.orientation);
  const b = sqToCenter(to, state.orientation);
  const dx = b.x - a.x, dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const margin = 0.22;
  const headLen = 0.38;
  const headW = 0.28;
  const x1 = a.x + ux * margin, y1 = a.y + uy * margin;
  const tipX = b.x - ux * margin, tipY = b.y - uy * margin;
  const baseX = tipX - ux * headLen, baseY = tipY - uy * headLen;
  const px = -uy, py = ux;
  const accent = (getComputedStyle(document.documentElement)
    .getPropertyValue("--green").trim() || "#81b64c");
  const green = accent.startsWith("#") && accent.length === 7
    ? `rgba(${parseInt(accent.slice(1, 3), 16)}, ${parseInt(accent.slice(3, 5), 16)}, ${parseInt(accent.slice(5, 7), 16)}, 0.85)`
    : accent;

  const shaft = document.createElementNS("http://www.w3.org/2000/svg", "line");
  shaft.setAttribute("x1", x1); shaft.setAttribute("y1", y1);
  shaft.setAttribute("x2", baseX); shaft.setAttribute("y2", baseY);
  shaft.setAttribute("stroke", green);
  shaft.setAttribute("stroke-width", "0.28");
  shaft.setAttribute("stroke-linecap", "round");

  const head = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
  head.setAttribute("points",
    `${tipX},${tipY} ${baseX + px * headW},${baseY + py * headW} ${baseX - px * headW},${baseY - py * headW}`);
  head.setAttribute("fill", green);

  svg.append(shaft, head);
}

/* ---------- move list / navigation ---------- */

const movesEl = document.getElementById("line-moves");
const titleEl = document.getElementById("line-title");
const gameMovesEl = document.getElementById("game-moves");
const gameOpeningTitleEl = document.getElementById("game-opening-title");
const gameAnalyzeStatusEl = document.getElementById("game-analyze-status");
let gameAnnotateToken = 0;

function setGameAnalyzeStatus(text) {
  if (!gameAnalyzeStatusEl) return;
  if (text) {
    gameAnalyzeStatusEl.textContent = text;
    gameAnalyzeStatusEl.classList.add("visible");
  } else {
    gameAnalyzeStatusEl.textContent = "";
    gameAnalyzeStatusEl.classList.remove("visible");
  }
}

const REVIEW_SEV_RANK = { blunder: 3, mistake: 2, inaccuracy: 1 };
const gameReviewNoteEl = document.getElementById("game-review-note");
const reviewSevBadgeEl = document.getElementById("review-sev-badge");
const reviewHeadlineEl = document.getElementById("review-headline");
const reviewBodyEl = document.getElementById("review-body");
const reviewProgressEl = document.getElementById("review-progress");
const reviewSeePlayedBtn = document.getElementById("review-see-played");
const reviewPrevIssueBtn = document.getElementById("review-prev-issue");
const reviewNextIssueBtn = document.getElementById("review-next-issue");

function currentReviewIssue() {
  const lesson = state.reviewLesson;
  if (!lesson?.issues?.length) return null;
  return lesson.issues[lesson.index] || null;
}

function reviewGameKey(g) {
  if (!g) return "";
  if (g.url) return String(g.url);
  return [
    g.opponent || "",
    g.date || "",
    g.variation || "",
    (g.san || []).slice(0, 10).join(" "),
  ].join("|");
}

function clearReviewLesson() {
  state.reviewLesson = null;
  if (gameReviewNoteEl) {
    gameReviewNoteEl.classList.remove("visible", "sev-blunder", "sev-mistake", "sev-inaccuracy");
  }
}

function formatReviewCopy(flag) {
  const moveNum = Math.ceil(flag.ply / 2);
  const dots = flag.ply % 2 === 0 ? "..." : ".";
  const sev = flag.severity || "inaccuracy";
  const label = sev.charAt(0).toUpperCase() + sev.slice(1);
  const loss = ((flag.loss_cp || 0) / 100).toFixed(1);
  let why;
  if (sev === "blunder") {
    why = `Your move gave away about <b>${loss}</b> pawns of evaluation — a serious opening error.`;
  } else if (sev === "mistake") {
    why = `Your move lost about <b>${loss}</b> pawns of evaluation and handed your opponent a clear edge.`;
  } else {
    why = `Your move was inaccurate, costing about <b>${loss}</b> pawns of evaluation.`;
  }
  const better = flag.best_san
    ? ` The engine’s preferred move was <b>${flag.best_san}</b> (green arrow), which keeps the position healthier.`
    : ` Look for a stronger alternative that doesn’t give ground here.`;
  return {
    severity: sev,
    glyph: ANN_GLYPH[sev] || "?!",
    headline: `${label} · ${moveNum}${dots} ${flag.san}`,
    bodyHtml: why + better,
  };
}

/** Draw the lesson’s best-move arrow when sitting on the critical pre-move ply. */
function drawReviewBestArrow() {
  const issue = currentReviewIssue();
  if (!issue?.flag?.best_san) return false;
  const criticalPly = Math.max(0, issue.flag.ply - 1);
  if (state.ply !== criticalPly) return false;
  const mv = parseBestMove(currentFen(), issue.flag.best_san);
  if (!mv) return false;
  drawBestArrow(mv.from, mv.to);
  return true;
}

function renderReviewNote() {
  if (!gameReviewNoteEl) return;
  const lesson = state.reviewLesson;
  const issue = currentReviewIssue();
  if (!lesson || !issue) {
    gameReviewNoteEl.classList.remove("visible", "sev-blunder", "sev-mistake", "sev-inaccuracy");
    return;
  }
  const copy = formatReviewCopy(issue.flag);
  gameReviewNoteEl.classList.add("visible");
  gameReviewNoteEl.classList.remove("sev-blunder", "sev-mistake", "sev-inaccuracy");
  gameReviewNoteEl.classList.add(`sev-${copy.severity}`);
  if (reviewSevBadgeEl) {
    reviewSevBadgeEl.className = "qbadge " + copy.severity;
    reviewSevBadgeEl.textContent = copy.glyph;
  }
  if (reviewHeadlineEl) {
    const vs = issue.game?.opponent ? ` vs ${issue.game.opponent}` : "";
    reviewHeadlineEl.textContent = copy.headline + vs;
  }
  if (reviewBodyEl) reviewBodyEl.innerHTML = copy.bodyHtml;

  const total = lesson.issues.length;
  const multi = total > 1;
  const nextIdx = lesson.index + 1;
  const hasNext = nextIdx < total;
  const nextIsNewGame = hasNext &&
    reviewGameKey(lesson.issues[nextIdx].game) !== reviewGameKey(issue.game);
  const gameCount = new Set(lesson.issues.map(x => reviewGameKey(x.game))).size;

  if (reviewProgressEl) {
    reviewProgressEl.hidden = !multi;
    reviewProgressEl.textContent = multi
      ? `Mistake ${lesson.index + 1} of ${total}` +
        (gameCount > 1 ? ` · ${gameCount} games in this scan` : "")
      : "";
  }
  if (reviewPrevIssueBtn) {
    reviewPrevIssueBtn.hidden = !multi;
    reviewPrevIssueBtn.disabled = lesson.index <= 0;
  }
  if (reviewNextIssueBtn) {
    reviewNextIssueBtn.hidden = !multi;
    reviewNextIssueBtn.disabled = !hasNext;
    if (!hasNext) {
      reviewNextIssueBtn.textContent = "Done";
    } else if (nextIsNewGame) {
      // No more mistakes in this game — advance to the next game's first issue.
      reviewNextIssueBtn.textContent = "Next game →";
      reviewNextIssueBtn.title = "No more mistakes in this game — open the next game";
    } else {
      reviewNextIssueBtn.textContent = "Next mistake →";
      reviewNextIssueBtn.title = "Next opening mistake in this game";
    }
  }
  if (reviewSeePlayedBtn) {
    const atCritical = state.ply === Math.max(0, issue.flag.ply - 1);
    reviewSeePlayedBtn.textContent = atCritical ? "See your move" : "Back to critical position";
  }
}

function collectScanIssues(batch, scanGames) {
  const issues = [];
  for (const sg of scanGames || []) {
    const g = batch[sg.index];
    if (!g) continue;
    for (const f of (sg.flags || [])) {
      if (!f || !REVIEW_SEV_RANK[f.severity]) continue;
      issues.push({ game: g, flag: f });
    }
  }
  issues.sort((a, b) => {
    const d = (REVIEW_SEV_RANK[b.flag.severity] || 0) - (REVIEW_SEV_RANK[a.flag.severity] || 0);
    if (d) return d;
    return (b.flag.loss_cp || 0) - (a.flag.loss_cp || 0);
  });
  return issues;
}

function openOpeningLesson(issues, startIndex = 0) {
  if (!issues?.length) return;
  const idx = Math.max(0, Math.min(startIndex, issues.length - 1));
  state.reviewLesson = { issues, index: idx };
  const { game: g, flag } = issues[idx];
  const seekPly = Math.max(0, (flag.ply || 1) - 1);
  loadGameReplay(g, { seekPly, skipAnim: true, keepReview: true });
  renderReviewNote();
  drawReviewBestArrow();
}

function stepOpeningLesson(delta) {
  const lesson = state.reviewLesson;
  if (!lesson?.issues?.length) return;
  const next = lesson.index + delta;
  if (next < 0 || next >= lesson.issues.length) return;
  openOpeningLesson(lesson.issues, next);
}

reviewSeePlayedBtn?.addEventListener("click", () => {
  const issue = currentReviewIssue();
  if (!issue) return;
  const critical = Math.max(0, issue.flag.ply - 1);
  if (state.ply === critical) setPly(issue.flag.ply);
  else setPly(critical);
  renderReviewNote();
});
reviewPrevIssueBtn?.addEventListener("click", () => stepOpeningLesson(-1));
reviewNextIssueBtn?.addEventListener("click", () => stepOpeningLesson(1));

document.addEventListener("keydown", e => {
  if (!state.reviewLesson || !state.gameView) return;
  const tag = (e.target && e.target.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if (e.key === "n" || e.key === "N") {
    e.preventDefault();
    stepOpeningLesson(1);
  } else if (e.key === "p" || e.key === "P") {
    e.preventDefault();
    stepOpeningLesson(-1);
  }
});

async function requestGameAnnotations(sanList, token) {
  if (!sanList?.length) return;
  setGameAnalyzeStatus("Analyzing moves…");
  try {
    const data = await api("/api/annotate-game", "POST", { san: sanList });
    if (token !== gameAnnotateToken) return; // stale — another game opened
    const anns = Array.isArray(data.annotations) ? data.annotations : [];
    state.moveFlags = anns;
    if (state.activeGame) {
      state.activeGame._flags = anns;
      state.activeGame._annotated = true;
    }
    setGameAnalyzeStatus("");
    if (state.gameView) {
      renderGameMoveList();
      refreshBoard();
    }
  } catch (e) {
    if (token !== gameAnnotateToken) return;
    if (handleProGateError(e)) {
      setGameAnalyzeStatus("Pro required for full game review.");
      return;
    }
    setGameAnalyzeStatus(e.message || "Analysis failed");
  }
}
const closeGameBtn = document.getElementById("close-game-btn");

const FIGURINE_PIECES = { K: "k", Q: "q", R: "r", B: "b", N: "n" };

/** Parse SAN into a piece figurine + remaining text (chess.com style). */
function parseSanFigurine(san, isWhite) {
  const s = String(san || "");
  if (!s) return { piece: null, rest: "", color: isWhite ? "w" : "b" };
  if (s.startsWith("O-O") || s.startsWith("0-0")) {
    return { piece: null, rest: s.replace(/0/g, "O"), color: isWhite ? "w" : "b" };
  }
  if (FIGURINE_PIECES[s[0]]) {
    return {
      piece: FIGURINE_PIECES[s[0]],
      rest: s.slice(1),
      color: isWhite ? "w" : "b",
    };
  }
  return { piece: null, rest: s, color: isWhite ? "w" : "b" };
}

function annGlyph(severity) {
  return ANN_GLYPH[severity] || "";
}

function gameResultLabel(g) {
  if (!g?.result) return null;
  if (g.result === "draw") return "1/2-1/2";
  const whiteWon = (g.color === "white" && g.result === "win")
    || (g.color === "black" && g.result === "loss");
  return whiteWon ? "1-0" : "0-1";
}

function makeFigurineMoveEl(san, ply, flag) {
  const isWhite = ply % 2 === 1;
  const { piece, rest, color } = parseSanFigurine(san, isWhite);
  const el = document.createElement("button");
  el.type = "button";
  const isCur = state.ply === ply;
  const sev = flag?.severity && ANN_GLYPH[flag.severity] ? flag.severity : null;
  el.className = "gm-mv" +
    (isCur ? " cur" : "") +
    (sev ? " ann-" + sev : "");

  // Fixed-width slot: badge immediately left of piece (chess.com spacing).
  const slot = document.createElement("span");
  slot.className = "gm-ann-slot";
  slot.setAttribute("aria-hidden", "true");
  if (sev) {
    const badge = document.createElement("span");
    badge.className = "gm-ann ann-" + sev;
    badge.textContent = annGlyph(sev);
    slot.appendChild(badge);
  }
  el.appendChild(slot);

  // Chess.com: figurines for N/B/R/Q/K only — pawns stay plain SAN (e4, exd5).
  if (piece && piece !== "p") {
    const img = document.createElement("img");
    img.src = `/pieces/${color}${piece}.png`;
    img.alt = "";
    img.draggable = false;
    el.appendChild(img);
  }
  const txt = document.createElement("span");
  txt.className = "san-rest";
  txt.textContent = piece && FIGURINE_PIECES[san[0]] ? rest : san;
  el.appendChild(txt);
  if (flag && sev) {
    const label = sev.charAt(0).toUpperCase() + sev.slice(1);
    if (sev === "blunder" || sev === "mistake") {
      el.title = `${label}: lost ${(flag.loss_cp / 100).toFixed(1)}` +
        (flag.best_san ? ` (better: ${flag.best_san})` : "");
    } else {
      el.title = label;
    }
  }
  el.onclick = () => { state.ply = ply; refreshBoard(); };
  return el;
}

function setGameView(on, game) {
  state.gameView = !!on;
  state.activeGame = on ? (game || state.activeGame) : null;
  if (state.gameView) document.body.classList.add("game-view");
  else document.body.classList.remove("game-view");
  if (state.gameView) {
    const g = state.activeGame;
    const title = g?.variation || state.lastOpeningName || "Game";
    if (gameOpeningTitleEl) gameOpeningTitleEl.textContent = title;
    if (titleEl) titleEl.textContent = title;
    // Game review needs the board on mobile.
    if (isMobileLayout()) setShowBoard(true);
  }
  syncBoardToggleBtn();
  renderMoveList();
  requestAnimationFrame(syncSidebarToBoard);
}

function closeGameView() {
  gameAnnotateToken += 1; // cancel in-flight annotate
  setGameAnalyzeStatus("");
  clearReviewLesson();
  setGameView(false);
  clearBoardPlayers();
  // Return mobile users to insights — board stays optional.
  if (isMobileLayout()) setShowBoard(false);
  // Keep current board position; user can keep analyzing or pick another opening.
  renderMoveList();
  requestAnimationFrame(syncSidebarToBoard);
}

function renderGameMoveList() {
  if (!gameMovesEl) return;
  gameMovesEl.innerHTML = "";
  if (!state.sans.length) {
    gameMovesEl.innerHTML = '<div class="rep-empty" style="padding:12px">No moves in this game.</div>';
    return;
  }
  const badByPly = new Map();
  for (const f of (state.moveFlags || [])) badByPly.set(f.ply, f);

  let curEl = null;
  for (let i = 0; i < state.sans.length; i += 2) {
    const row = document.createElement("div");
    row.className = "gm-row";
    const num = document.createElement("div");
    num.className = "gm-num";
    num.textContent = (i / 2 + 1) + ".";
    row.appendChild(num);

    const wPly = i + 1;
    const wEl = makeFigurineMoveEl(state.sans[i], wPly, badByPly.get(wPly));
    row.appendChild(wEl);
    if (state.ply === wPly) curEl = wEl;

    if (i + 1 < state.sans.length) {
      const bPly = i + 2;
      const bEl = makeFigurineMoveEl(state.sans[i + 1], bPly, badByPly.get(bPly));
      row.appendChild(bEl);
      if (state.ply === bPly) curEl = bEl;
    } else {
      const empty = document.createElement("div");
      empty.className = "gm-mv";
      empty.style.visibility = "hidden";
      empty.setAttribute("aria-hidden", "true");
      row.appendChild(empty);
    }
    gameMovesEl.appendChild(row);
  }

  const result = gameResultLabel(state.activeGame);
  if (result) {
    const res = document.createElement("div");
    res.className = "gm-result";
    res.textContent = result;
    gameMovesEl.appendChild(res);
  }

  if (curEl) {
    try { curEl.scrollIntoView({ block: "nearest" }); } catch (e) { /* ignore */ }
  }
}

function renderMoveList() {
  if (state.gameView) {
    renderGameMoveList();
    if (gameOpeningTitleEl) {
      gameOpeningTitleEl.textContent =
        state.activeGame?.variation || state.lastOpeningName || "Game";
    }
    return;
  }
  movesEl.innerHTML = "";
  if (state.editMode) {
    movesEl.innerHTML = '<span class="placeholder">Edit mode — drag pieces from the trays onto the board. Drag a piece off the board to remove it.</span>';
    return;
  }
  if (!state.sans.length) {
    movesEl.innerHTML = '<span class="placeholder">Move a piece to start analyzing, or pick an opening line on the right.</span>';
    return;
  }
  const badByPly = new Map();
  for (const f of (state.moveFlags || [])) {
    badByPly.set(f.ply, f);
  }
  state.sans.forEach((mv, i) => {
    if (i % 2 === 0) {
      const num = document.createElement("span");
      num.className = "mvnum";
      num.textContent = (i / 2 + 1) + ".";
      movesEl.appendChild(num);
    }
    const ply = i + 1;
    const flag = badByPly.get(ply);
    const el = document.createElement("span");
    el.className = "mv" + (state.ply === ply ? " cur" : "") +
      (flag ? " bad-" + flag.severity : "");
    el.textContent = mv;
    if (flag) {
      el.title = `${flag.severity}: lost ${(flag.loss_cp / 100).toFixed(1)}` +
        (flag.best_san ? ` (better: ${flag.best_san})` : "");
    }
    el.onclick = () => { state.ply = ply; refreshBoard(); };
    movesEl.appendChild(el);
  });
}

function setPly(p) {
  state.ply = Math.max(0, Math.min(state.sans.length, p));
  refreshBoard();
  if (state.reviewLesson) renderReviewNote();
}
function clearGamesFilters() {
  document.querySelectorAll(".line.active").forEach(el => {
    el.classList.remove("active");
    const cont = el.closest(".lines");
    if (cont && cont._renderGames) cont._renderGames(null);
  });
}

function resetGame(title) {
  if (state.editMode) {
    setCustomFen(START_FEN);
    return;
  }
  freeFen = null;
  gameRootFen = START_FEN;
  game = new Chess();
  rebuildDerived();
  state.ply = 0;
  state.moveFlags = [];
  state.mainLineSans = [];
  state.exploreAnns = {};
  exploreAnnotateToken += 1;
  state.lastOpeningName = null;
  clearPremove();
  clearUserShapes();
  clearBoardPlayers();
  setGameView(false);
  // Title is set by scheduleOpeningTitle() inside refreshBoard().
  clearGamesFilters();
  refreshBoard();
}
document.getElementById("nav-start").onclick = () => setPly(0);
document.getElementById("nav-prev").onclick = () => setPly(state.ply - 1);
document.getElementById("nav-next").onclick = () => setPly(state.ply + 1);
document.getElementById("nav-end").onclick = () => setPly(Infinity);
document.getElementById("nav-reset").onclick = () => resetGame();
document.getElementById("game-nav-start").onclick = () => setPly(0);
document.getElementById("game-nav-prev").onclick = () => setPly(state.ply - 1);
document.getElementById("game-nav-next").onclick = () => setPly(state.ply + 1);
document.getElementById("game-nav-end").onclick = () => setPly(Infinity);
if (closeGameBtn) closeGameBtn.onclick = () => closeGameView();

document.getElementById("flip-board-btn").onclick = () => {
  state.orientation = state.orientation === "white" ? "black" : "white";
  refreshBoard();
};

const BOARD_PX_LS = "boardPx";
const BOARD_PX_MIN = 320;
const BOARD_PX_MAX = 960;
const boardPanel = document.querySelector(".board-panel");
const boardResize = document.getElementById("board-resize");

function mobileBoardPx() {
  // Leave room for page padding + eval bar so the square fits an iPhone width.
  const avail = Math.min(window.innerWidth, window.visualViewport?.width || window.innerWidth) - 56;
  return Math.round(Math.max(220, Math.min(avail, 360)));
}

function applyBoardPx(px) {
  if (!boardPanel) return px;
  const mobile = window.matchMedia("(max-width: 720px)").matches;
  if (mobile) {
    // Ignore desktop-saved board size — always fit the phone viewport.
    px = mobileBoardPx();
    boardPanel.style.setProperty("--board-px", px + "px");
    requestAnimationFrame(syncSidebarToBoard);
    return px;
  }
  const minPx = BOARD_PX_MIN;
  const max = Math.min(BOARD_PX_MAX, Math.max(minPx, window.innerWidth - 48));
  px = Math.round(Math.min(max, Math.max(minPx, px)));
  boardPanel.style.setProperty("--board-px", px + "px");
  requestAnimationFrame(syncSidebarToBoard);
  return px;
}

function syncHeaderToEvalbar() {
  // Flush "Opening Explorer" with the left edge of the eval bar (desktop only).
  const evalbar = document.getElementById("evalbar");
  const header = document.querySelector("header");
  if (!evalbar || !header) return;
  if (window.matchMedia("(max-width: 720px)").matches) {
    header.style.paddingLeft = "";
    return;
  }
  const left = Math.round(evalbar.getBoundingClientRect().left);
  header.style.paddingLeft = (left > 8 ? left : 22) + "px";
}

function syncSidebarToBoard() {
  syncHeaderToEvalbar();
  const sidebar = document.getElementById("analyze-sidebar");
  const boardRow = document.querySelector(".board-row");
  if (!sidebar || !boardPanel || !boardRow) return;

  if (document.body.classList.contains("game-view")) {
    sidebar.style.removeProperty("--sidebar-align-pad");
    // Desktop: cap moves panel to board column height. Mobile stacks, so let the page scroll.
    if (window.matchMedia("(max-width: 720px)").matches) {
      sidebar.style.removeProperty("height");
      sidebar.style.removeProperty("max-height");
      return;
    }
    const h = Math.round(boardPanel.getBoundingClientRect().height);
    if (h > 0) {
      sidebar.style.height = h + "px";
      sidebar.style.maxHeight = h + "px";
    }
    return;
  }

  sidebar.style.removeProperty("height");
  sidebar.style.removeProperty("max-height");

  if (
    document.body.classList.contains("repertoire-open") ||
    window.matchMedia("(max-width: 1000px)").matches
  ) {
    sidebar.style.removeProperty("--sidebar-align-pad");
    return;
  }
  const pad = Math.max(
    0,
    Math.round(boardRow.getBoundingClientRect().top - boardPanel.getBoundingClientRect().top)
  );
  sidebar.style.setProperty("--sidebar-align-pad", pad + "px");
}

function initBoardResize() {
  const saved = Number(localStorage.getItem(BOARD_PX_LS));
  applyBoardPx(Number.isFinite(saved) && saved > 0 ? saved : 560);

  let startX = 0, startY = 0, startPx = 560;
  boardResize.addEventListener("pointerdown", e => {
    if (window.matchMedia("(max-width: 720px)").matches) return;
    e.preventDefault();
    boardResize.setPointerCapture(e.pointerId);
    startX = e.clientX;
    startY = e.clientY;
    startPx = parseFloat(getComputedStyle(boardPanel).getPropertyValue("--board-px")) || 560;
  });
  boardResize.addEventListener("pointermove", e => {
    if (!boardResize.hasPointerCapture(e.pointerId)) return;
    // Drag diagonally: average delta so the square stays roughly under the cursor.
    const delta = ((e.clientX - startX) + (e.clientY - startY)) / 2;
    applyBoardPx(startPx + delta);
  });
  boardResize.addEventListener("pointerup", e => {
    if (!boardResize.hasPointerCapture(e.pointerId)) return;
    boardResize.releasePointerCapture(e.pointerId);
    if (window.matchMedia("(max-width: 720px)").matches) return;
    const px = parseFloat(getComputedStyle(boardPanel).getPropertyValue("--board-px")) || 560;
    localStorage.setItem(BOARD_PX_LS, String(Math.round(px)));
  });
}
initBoardResize();

function onViewportChange() {
  if (window.matchMedia("(max-width: 720px)").matches) {
    applyBoardPx(mobileBoardPx());
  }
  requestAnimationFrame(syncSidebarToBoard);
}
window.addEventListener("resize", onViewportChange);
window.visualViewport?.addEventListener("resize", onViewportChange);
window.matchMedia("(max-width: 720px)").addEventListener("change", ev => {
  if (ev.matches) {
    applyBoardPx(mobileBoardPx());
  } else {
    const saved = Number(localStorage.getItem(BOARD_PX_LS));
    applyBoardPx(Number.isFinite(saved) && saved > 0 ? saved : 560);
    document.body.classList.remove("show-board");
  }
  syncBoardToggleBtn();
});
if (typeof ResizeObserver !== "undefined" && boardPanel) {
  const boardAlignRo = new ResizeObserver(() => requestAnimationFrame(syncSidebarToBoard));
  boardAlignRo.observe(boardPanel);
  const titleElAlign = document.getElementById("line-title");
  const playerTopAlign = document.getElementById("player-top");
  if (titleElAlign) boardAlignRo.observe(titleElAlign);
  if (playerTopAlign) boardAlignRo.observe(playerTopAlign);
}

const SIDEBAR_PX_LS = "sidebarPx";
const SIDEBAR_PX_MIN = 280;
const SIDEBAR_PX_MAX = 720;
const mainEl = document.querySelector("main");
const sidebarResize = document.getElementById("sidebar-resize");

function applySidebarPx(px) {
  if (!mainEl) return px;
  const max = Math.min(SIDEBAR_PX_MAX, Math.max(SIDEBAR_PX_MIN, window.innerWidth - 360));
  px = Math.round(Math.min(max, Math.max(SIDEBAR_PX_MIN, px)));
  mainEl.style.setProperty("--sidebar-px", px + "px");
  return px;
}

function initSidebarResize() {
  if (!mainEl || !sidebarResize) return;
  const saved = Number(localStorage.getItem(SIDEBAR_PX_LS));
  applySidebarPx(Number.isFinite(saved) && saved > 0 ? saved : 420);

  let startX = 0, startPx = 420;
  sidebarResize.addEventListener("pointerdown", e => {
    e.preventDefault();
    sidebarResize.setPointerCapture(e.pointerId);
    document.body.classList.add("sidebar-resizing");
    startX = e.clientX;
    startPx = parseFloat(getComputedStyle(mainEl).getPropertyValue("--sidebar-px")) || 420;
  });
  sidebarResize.addEventListener("pointermove", e => {
    if (!sidebarResize.hasPointerCapture(e.pointerId)) return;
    applySidebarPx(startPx + (e.clientX - startX));
  });
  const endResize = e => {
    if (!sidebarResize.hasPointerCapture(e.pointerId)) return;
    sidebarResize.releasePointerCapture(e.pointerId);
    document.body.classList.remove("sidebar-resizing");
    const px = parseFloat(getComputedStyle(mainEl).getPropertyValue("--sidebar-px")) || 420;
    localStorage.setItem(SIDEBAR_PX_LS, String(Math.round(px)));
  };
  sidebarResize.addEventListener("pointerup", endResize);
  sidebarResize.addEventListener("pointercancel", endResize);
}
initSidebarResize();

document.addEventListener("keydown", e => {
  const tag = (e.target.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || e.target.isContentEditable) return;

  if (e.key === "Escape") {
    hidePromoPicker();
    clearSelection({ keepPremove: true, keepShapes: true });
    if (premove) clearPremove();
    else if (userShapes.length) clearUserShapes();
    return;
  }

  // Delete / Backspace always take back one action.
  // ArrowLeft also undoes in the editor or a custom-root analysis.
  if (e.key === "Backspace" || e.key === "Delete") {
    e.preventDefault();
    undoLastAction();
    return;
  }
  if (e.key === "ArrowLeft") {
    e.preventDefault();
    if (isCustomOrEditPosition()) undoLastAction();
    else setPly(state.ply - 1);
    return;
  }
  if (e.key === "ArrowRight") {
    e.preventDefault();
    setPly(state.ply + 1);
  }
});

/* ---------- line replay animation ---------- */

const LINE_ANIM_MS = 350;
const GAME_PREVIEW_PLIES = 10;  // animate first 5 full moves on game click
let lineAnim = null;   // interval id while auto-playing a line, else null

function isAnimatingLine() {
  return lineAnim !== null;
}

function cancelLineAnim(rescheduleEval = true) {
  if (lineAnim === null) return;
  clearInterval(lineAnim);
  lineAnim = null;
  // evals were suppressed during the replay; catch up for wherever we stopped
  if (rescheduleEval) scheduleEval();
}

function animateLine(maxPly) {
  cancelLineAnim(false);
  const target = maxPly === undefined
    ? state.sans.length
    : Math.min(maxPly, state.sans.length);
  if (state.ply >= target) { refreshBoard(); return; }
  lineAnim = setInterval(() => {
    state.ply++;
    // stop animating BEFORE the final refresh so its scheduleEval() runs
    if (state.ply >= target) cancelLineAnim(false);
    refreshBoard();
  }, LINE_ANIM_MS);
  refreshBoard();   // paint the start position; evals stay suppressed until the end
}

// Any user interaction (nav buttons, squares, move list, another line, arrows)
// cancels the replay first (capture phase), then the click/key is honored normally.
document.addEventListener("pointerdown", () => cancelLineAnim(), true);
document.addEventListener("keydown", e => {
  if (e.key === "ArrowLeft" || e.key === "ArrowRight") cancelLineAnim();
}, true);

function loadLine(line, openingName) {
  // Seed the analyzer game with the line's moves; user can take over anywhere.
  if (state.editMode) setEditMode(false);
  freeFen = null;
  gameRootFen = START_FEN;
  const g = new Chess();
  for (const san of line.san) {
    try { g.move(san); }
    catch (e) { break; }
  }
  game = g;
  rebuildDerived();
  state.ply = 0;
  state.moveFlags = [];
  state.mainLineSans = [];
  state.exploreAnns = {};
  exploreAnnotateToken += 1;
  state.lastOpeningName = line.name || null;
  clearBoardPlayers();
  setGameView(false);
  // On mobile, reveal the board when the user picks a line to study.
  if (isMobileLayout()) setShowBoard(true);
  // Provisional title; scheduleOpeningTitle() refines it move-by-move.
  titleEl.textContent = line.name || "Starting position";
  clearGamesFilters();
  const el = document.querySelector(`.line[data-key="${CSS.escape(openingName + "||" + line.name)}"]`);
  if (el) {
    el.classList.add("active");
    const cont = el.closest(".lines");
    if (cont && cont._renderGames) cont._renderGames(line.name);
  }
  animateLine();   // paints the start position, then replays the whole variation
  if (isMobileLayout()) {
    document.querySelector(".board-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function loadGameReplay(g, opts = {}) {
  // Load the game into the analyzer and switch sidebar to chess.com-style moves.
  if (state.editMode) setEditMode(false);
  freeFen = null;
  gameRootFen = START_FEN;
  const gm = new Chess();
  const sans = [];
  for (const san of (g.san || [])) {
    try {
      gm.move(san);
      sans.push(san);
    } catch (e) { break; }
  }
  game = gm;
  rebuildDerived();
  state.ply = 0;
  state.mainLineSans = sans.slice();
  state.exploreAnns = {};
  exploreAnnotateToken += 1;
  state.moveFlags = g._flags || [];
  state.lastOpeningName = g.variation || null;
  if (!opts.keepReview) clearReviewLesson();
  titleEl.textContent = g.variation || "Starting position";
  setBoardPlayersFromGame(g);
  if (isMobileLayout()) setShowBoard(true);
  setGameView(true, g);
  // Keep any variation filter/highlight: the user is browsing that list.
  cancelLineAnim(false);
  if (opts.skipAnim) {
    const seek = opts.seekPly == null ? 0 : Math.max(0, Math.min(opts.seekPly, sans.length));
    state.ply = seek;
    refreshBoard();
  } else {
    animateLine(GAME_PREVIEW_PLIES);
  }
  if (isMobileLayout()) {
    document.querySelector(".board-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // Full-game chess.com-style review (ignore stale if user opens another game).
  const token = ++gameAnnotateToken;
  if (g._annotated && Array.isArray(g._flags)) {
    state.moveFlags = g._flags;
    setGameAnalyzeStatus("");
    renderGameMoveList();
  } else if (sans.length) {
    requestGameAnnotations(sans, token);
  } else {
    setGameAnalyzeStatus("");
  }
  if (opts.keepReview) renderReviewNote();
}

/* ---------- sidebar ---------- */

const openingsEl = document.getElementById("openings");
const summaryEl = document.getElementById("summary");
const mobileInsightsEl = document.getElementById("mobile-insights");
const boardToggleBtn = document.getElementById("board-toggle-btn");
const MOBILE_MQ = window.matchMedia("(max-width: 720px)");

function isMobileLayout() {
  return MOBILE_MQ.matches;
}

function setShowBoard(on) {
  document.body.classList.toggle("show-board", !!on);
  syncBoardToggleBtn();
  if (on) requestAnimationFrame(() => {
    // Board may have been display:none — force a paint/layout pass.
    refreshBoard();
    syncSidebarToBoard();
  });
}

function syncBoardToggleBtn() {
  if (!boardToggleBtn) return;
  const shown = document.body.classList.contains("show-board") ||
    document.body.classList.contains("game-view");
  boardToggleBtn.setAttribute("aria-pressed", shown ? "true" : "false");
  boardToggleBtn.textContent = shown ? "Hide analysis board" : "Show analysis board";
}

function focusOpeningFromPriority(color, openingName) {
  if (color && color !== state.color) setColor(color);
  else renderSidebar();
  requestAnimationFrame(() => {
    const box = openingsEl.querySelector(`.opening[data-opening="${CSS.escape(openingName)}"]`);
    if (!box) return;
    box.classList.add("open");
    box.scrollIntoView({ behavior: "smooth", block: "start" });
    // Nudge user toward blunder scan on mobile.
    const scanBtn = box.querySelector(".scan-row button");
    if (scanBtn && isMobileLayout()) {
      scanBtn.classList.add("pulse-hint");
      setTimeout(() => scanBtn.classList.remove("pulse-hint"), 2400);
    }
  });
}

function renderMobileInsights() {
  if (!mobileInsightsEl) return;
  mobileInsightsEl.innerHTML = "";
  mobileInsightsEl.classList.remove("has-data");
  if (!state.report) return;

  const white = state.report.white;
  const black = state.report.black;
  const games = state.report.analyzed_games || 0;
  const wScore = white?.score;
  const bScore = black?.score;

  const stats = document.createElement("div");
  stats.className = "insights-stats";
  stats.innerHTML =
    `<div class="insight-stat"><span class="val">${games}</span><span class="lbl">Games</span></div>` +
    `<div class="insight-stat"><span class="val">${wScore != null ? wScore + "%" : "—"}</span><span class="lbl">As White</span></div>` +
    `<div class="insight-stat"><span class="val">${bScore != null ? bScore + "%" : "—"}</span><span class="lbl">As Black</span></div>`;
  mobileInsightsEl.appendChild(stats);

  const priorities = (state.report.priorities || []).slice(0, 5);
  if (priorities.length) {
    const lab = document.createElement("div");
    lab.className = "insights-section-label";
    lab.textContent = "Focus areas — underperforming openings";
    mobileInsightsEl.appendChild(lab);

    const list = document.createElement("div");
    list.className = "insights-priorities";
    for (const p of priorities) {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "priority-card";
      card.title = `Open ${p.opening} (${p.color})`;
      card.innerHTML =
        `<span class="pc-color ${p.color === "black" ? "black" : "white"}" aria-hidden="true"></span>` +
        `<span class="pc-body">` +
          `<span class="pc-name"></span>` +
          `<span class="pc-meta"></span>` +
        `</span>` +
        `<span class="pc-gap"></span>`;
      card.querySelector(".pc-name").textContent = p.opening;
      card.querySelector(".pc-meta").textContent =
        `${p.color === "black" ? "As Black" : "As White"} · ${p.frequency_pct}% of games · ${p.score}% score`;
      card.querySelector(".pc-gap").textContent =
        p.score_gap > 0 ? `−${p.score_gap} pts` : `${p.score_gap} pts`;
      card.onclick = () => focusOpeningFromPriority(p.color, p.opening);
      list.appendChild(card);
    }
    mobileInsightsEl.appendChild(list);

    const hint = document.createElement("p");
    hint.className = "insights-hint";
    hint.textContent =
      "Tap a focus area, then Scan opening for blunders to see repeated early mistakes in your recent games.";
    mobileInsightsEl.appendChild(hint);
  } else {
    const hint = document.createElement("p");
    hint.className = "insights-hint";
    hint.textContent =
      "Browse openings below for history and scores. Open one and scan for blunders to surface repeated early mistakes.";
    mobileInsightsEl.appendChild(hint);
  }

  mobileInsightsEl.classList.add("has-data");
}

if (boardToggleBtn) {
  boardToggleBtn.addEventListener("click", () => {
    const next = !document.body.classList.contains("show-board");
    setShowBoard(next);
    if (next && isMobileLayout()) {
      applyBoardPx(mobileBoardPx());
      document.querySelector(".board-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
}
syncBoardToggleBtn();

/** Black-defense family names (ECO naming) — still White games when you face them. */
function isDefenseFamily(name) {
  return /\bDefen[cs]e\b/i.test(name || "");
}

/** White-system family names typically faced when you're Black. */
function isWhiteSystemFamily(name) {
  if (isDefenseFamily(name)) return false;
  return /\b(Opening|Attack|Gambit|Game|System)\b/i.test(name || "");
}

function partitionOpeningsForColor(color, openings) {
  const yours = [];
  const faced = [];
  for (const op of openings) {
    if (color === "white") {
      (isDefenseFamily(op.name) ? faced : yours).push(op);
    } else {
      (isWhiteSystemFamily(op.name) ? faced : yours).push(op);
    }
  }
  return { yours, faced };
}

function displayOpeningName(color, name) {
  if (color === "white" && isDefenseFamily(name) && !/^vs\s/i.test(name)) {
    return "vs " + name;
  }
  if (color === "black" && isWhiteSystemFamily(name) && !/^vs\s/i.test(name)) {
    return "vs " + name;
  }
  return name;
}

function openingLastPlayed(op) {
  let best = "";
  for (const g of op.game_list || []) {
    if (g.date && g.date > best) best = g.date;
  }
  return best;
}

function variationLastPlayed(op, variationName) {
  let best = "";
  for (const g of op.game_list || []) {
    if (g.variation !== variationName) continue;
    if (g.date && g.date > best) best = g.date;
  }
  return best;
}

function sortOpeningsList(list) {
  const arr = list.slice();
  if (state.openingSort === "recent") {
    arr.sort((a, b) => {
      const da = openingLastPlayed(a);
      const db = openingLastPlayed(b);
      if (da !== db) return db.localeCompare(da);
      return (b.games || 0) - (a.games || 0) || a.name.localeCompare(b.name);
    });
  } else {
    arr.sort((a, b) =>
      (b.games || 0) - (a.games || 0) || a.name.localeCompare(b.name));
  }
  return arr;
}

function sortedVariations(op) {
  const vars = (op.variations || []).slice();
  if (state.openingSort === "recent") {
    vars.sort((a, b) => {
      const da = variationLastPlayed(op, a.name);
      const db = variationLastPlayed(op, b.name);
      if (da !== db) return db.localeCompare(da);
      return (b.games || 0) - (a.games || 0);
    });
  } else {
    vars.sort((a, b) => (b.games || 0) - (a.games || 0));
  }
  return vars;
}

function appendOpeningBox(op, displayName) {
  const box = document.createElement("div");
  box.className = "opening";
  box.dataset.opening = op.name || "";

  const last = openingLastPlayed(op);
  const metaExtra = state.openingSort === "recent" && last
    ? ` \u00B7 ${last}`
    : "";

  const head = document.createElement("div");
  head.className = "opening-head";
  head.innerHTML =
    `<span class="chev">\u25B8</span>` +
    `<span class="name"></span>` +
    `<span class="meta">${op.games} games \u00B7 ${op.score}%${metaExtra}</span>` +
    `<span class="scorebar"><i style="width:${op.score}%"></i></span>`;
  head.querySelector(".name").textContent = displayName;
  head.onclick = () => box.classList.toggle("open");
  box.appendChild(head);

  const lines = document.createElement("div");
  lines.className = "lines";
  for (const v of sortedVariations(op)) {
    const row = document.createElement("div");
    row.className = "line";
    row.dataset.key = op.name + "||" + v.name;
    const showBar = v.games > 3 && v.score != null;
    row.innerHTML =
      `<span class="lname"></span>` +
      `<span class="lgames">${v.games} game${v.games === 1 ? "" : "s"}</span>` +
      (showBar ? `<span class="scorebar" title="${v.score}% score"><i style="width:${v.score}%"></i></span>` : "");
    row.querySelector(".lname").textContent = v.name;
    if (!v.san.length) {
      row.style.opacity = ".5";
      row.title = "No replayable moves found for this line";
    }
    row.onclick = () => {
      if (row.classList.contains("active")) {
        row.classList.remove("active");
        if (lines._renderGames) lines._renderGames(null);
        return;
      }
      if (v.san.length) loadLine(v, op.name);
    };
    lines.appendChild(row);
  }
  appendGamesSection(lines, op);
  box.appendChild(lines);
  openingsEl.appendChild(box);
}

const OPENING_SORT_LS = "openingSort";
const openingSortEl = document.getElementById("opening-sort");

function syncOpeningSortButtons() {
  if (!openingSortEl) return;
  for (const btn of openingSortEl.querySelectorAll("button[data-sort]")) {
    btn.classList.toggle("active", btn.dataset.sort === state.openingSort);
  }
}

function setOpeningSort(mode) {
  state.openingSort = mode === "recent" ? "recent" : "frequent";
  try { localStorage.setItem(OPENING_SORT_LS, state.openingSort); } catch (e) { /* ignore */ }
  syncOpeningSortButtons();
  renderSidebar();
}

(function initOpeningSort() {
  try {
    const saved = localStorage.getItem(OPENING_SORT_LS);
    if (saved === "recent" || saved === "frequent") state.openingSort = saved;
  } catch (e) { /* ignore */ }
  syncOpeningSortButtons();
  if (openingSortEl) {
    openingSortEl.addEventListener("click", e => {
      const btn = e.target.closest("button[data-sort]");
      if (!btn) return;
      setOpeningSort(btn.dataset.sort);
    });
  }
})();

function renderSidebar() {
  openingsEl.innerHTML = "";
  renderMobileInsights();
  if (!state.report) {
    const readyHint = currentUser
      ? "Link your chess.com or lichess username in Profile, then click <kbd>Analyze my games</kbd>."
      : "Enter a chess.com username above and click <kbd>Analyze</kbd> to see the openings you actually play.";
    openingsEl.innerHTML =
      '<div class="empty-note"><strong>Ready to analyze</strong>' +
      '<p>' + readyHint + '</p></div>';
    summaryEl.textContent = "";
    if (openingSortEl) openingSortEl.hidden = true;
    return;
  }
  const rep = state.report[state.color];
  if (!rep) {
    openingsEl.innerHTML =
      '<div class="empty-note"><strong>No games for this color</strong>' +
      '<p>Try switching As White / As Black, or widen the date range.</p></div>';
    summaryEl.textContent = "";
    if (openingSortEl) openingSortEl.hidden = true;
    return;
  }
  summaryEl.textContent =
    `${rep.total_games} games as ${state.color} \u2014 ${rep.score}% score`;

  // Only openings from games where the player sat this color.
  const openings = (rep.openings || []).filter(op => {
    const games = op.game_list || [];
    if (!games.length) return true;
    return games.every(g => !g.color || g.color === state.color);
  });

  if (!openings.length) {
    openingsEl.innerHTML =
      '<div class="empty-note"><strong>No matching games</strong>' +
      '<p>Turn on Rapid, Blitz, or Bullet — or pick a wider date range.</p></div>';
    if (openingSortEl) openingSortEl.hidden = true;
    return;
  }

  if (openingSortEl) {
    openingSortEl.hidden = false;
    syncOpeningSortButtons();
  }

  const { yours, faced } = partitionOpeningsForColor(state.color, openings);

  if (yours.length) {
    const lab = document.createElement("div");
    lab.className = "openings-section-label";
    lab.textContent = state.color === "white"
      ? "Your openings (as White)"
      : "Your defenses (as Black)";
    openingsEl.appendChild(lab);
    for (const op of sortOpeningsList(yours)) {
      appendOpeningBox(op, displayOpeningName(state.color, op.name));
    }
  }

  if (faced.length) {
    const lab = document.createElement("div");
    lab.className = "openings-section-label";
    lab.textContent = state.color === "white"
      ? "Defenses you faced (still as White)"
      : "Lines you faced (still as Black)";
    openingsEl.appendChild(lab);
    for (const op of sortOpeningsList(faced)) {
      appendOpeningBox(op, displayOpeningName(state.color, op.name));
    }
  }
}

const GAMES_SHOWN_INITIALLY = 25;

function makeGameRow(g) {
  const row = document.createElement("div");
  row.className = "game-row";

  const badge = document.createElement("span");
  badge.className = "rbadge " + g.result;
  badge.textContent = g.result === "win" ? "W" : g.result === "loss" ? "L" : "D";

  // Opponent zone: click (or whole row) opens chess.com-style game view.
  const opp = document.createElement("span");
  opp.className = "gopp";
  opp.textContent = g.opponent;
  if (g.opponent_rating) {
    const r = document.createElement("span");
    r.className = "grating";
    r.textContent = ` (${g.opponent_rating})`;
    opp.appendChild(r);
  }
  if (g.san && g.san.length) {
    opp.classList.add("replayable");
    row.classList.add("replayable");
    row.title = (g.variation ? g.variation + " \u2014 " : "") + "Open game moves";
    opp.title = row.title;
    row.onclick = e => {
      if (e.target.closest("a.gdate")) return;
      loadGameReplay(g);
    };
  } else if (g.variation) {
    opp.title = g.variation;
  }

  // Date zone: link to the game on chess.com / lichess.
  let date;
  if (g.url) {
    date = document.createElement("a");
    date.href = g.url;
    date.target = "_blank";
    date.rel = "noopener";
    date.title = "Open game on " + (g.url.includes("lichess.org") ? "lichess" : "chess.com");
  } else {
    date = document.createElement("span");
  }
  date.className = "gdate";
  date.textContent = g.date || "";
  date.addEventListener("click", e => e.stopPropagation());

  row.append(badge, opp);

  // Opening quality flag from blunder scan (if any).
  if (g._worst) {
    const q = document.createElement("span");
    q.className = "qbadge " + g._worst;
    q.textContent = g._worst === "blunder" ? "??" : g._worst === "mistake" ? "?" : "?!";
    const parts = [];
    if (g._counts) {
      if (g._counts.blunder) parts.push(g._counts.blunder + " blunder" + (g._counts.blunder > 1 ? "s" : ""));
      if (g._counts.mistake) parts.push(g._counts.mistake + " mistake" + (g._counts.mistake > 1 ? "s" : ""));
      if (g._counts.inaccuracy) parts.push(g._counts.inaccuracy + " inaccuracy" + (g._counts.inaccuracy > 1 ? "s" : ""));
    }
    if (g._flags && g._flags.length) {
      parts.push(g._flags.map(f => `${f.san} (${f.severity}, -${(f.loss_cp / 100).toFixed(1)})`).join("; "));
    }
    q.title = "Opening issues: " + (parts.join(" · ") || g._worst);
    row.appendChild(q);
  }

  row.appendChild(date);
  return row;
}

function appendGamesSection(lines, op) {
  // Never list the other color's games under this tab.
  const gl = (op.game_list || []).filter(g => !g.color || g.color === state.color);
  if (!gl.length) return;

  const scanRow = document.createElement("div");
  scanRow.className = "scan-row";
  const scanBtn = document.createElement("button");
  scanBtn.type = "button";
  scanBtn.textContent = "Scan opening for blunders";
  const scanStatus = document.createElement("span");
  scanStatus.className = "scan-status";
  scanRow.append(scanBtn, scanStatus);
  lines.appendChild(scanRow);

  const patternsEl = document.createElement("div");
  patternsEl.className = "patterns";
  lines.appendChild(patternsEl);

  const head = document.createElement("div");
  head.className = "games-head";
  lines.appendChild(head);

  const rows = document.createElement("div");
  rows.className = "game-rows";
  lines.appendChild(rows);

  let activeFilter = null;

  function renderPatterns(patterns, scanBatch) {
    patternsEl.innerHTML = "";
    if (!patterns || !patterns.length) {
      patternsEl.classList.remove("visible");
      return;
    }
    patternsEl.classList.add("visible");
    const h = document.createElement("h4");
    h.textContent = "Repeated opening issues";
    patternsEl.appendChild(h);
    for (const p of patterns.slice(0, 8)) {
      const card = document.createElement("div");
      card.className = "pattern";
      card.style.cursor = "pointer";
      card.title = "Open an example game at this mistake";
      const moveNum = Math.ceil(p.ply / 2);
      const dots = p.ply % 2 === 0 ? "..." : "";
      const sevLabel = p.severity.charAt(0).toUpperCase() + p.severity.slice(1);
      card.innerHTML =
        `<div class="ptop"><span class="qbadge ${p.severity}">${
          p.severity === "blunder" ? "??" : p.severity === "mistake" ? "?" : "?!"
        }</span>${sevLabel}: ${moveNum}.${dots} ${p.move} \u00d7 ${p.count}</div>` +
        `<div class="hint">${p.variation} \u00b7 avg loss ${(p.avg_loss_cp / 100).toFixed(1)}` +
        (p.best_san ? ` \u00b7 better: ${p.best_san}` : "") + `</div>`;
      card.onclick = () => {
        const example = (p.games || [])[0];
        let g = null;
        if (example && scanBatch && example.index != null) {
          g = scanBatch[example.index] || null;
        }
        if (!g && example) {
          g = gl.find(x =>
            (example.url && x.url === example.url) ||
            (example.opponent && x.opponent === example.opponent && example.date && x.date === example.date)
          ) || null;
        }
        if (!g) {
          g = gl.find(x => x.variation === p.variation && (x._flags || []).some(f =>
            f.ply === p.ply && f.san === p.move && f.severity === p.severity
          )) || null;
        }
        if (!g) return;
        const flag = (g._flags || []).find(f =>
          f.ply === p.ply && f.san === p.move
        ) || {
          ply: p.ply,
          san: p.move,
          severity: p.severity,
          loss_cp: p.avg_loss_cp,
          best_san: p.best_san,
        };
        openOpeningLesson([{ game: g, flag }], 0);
      };
      patternsEl.appendChild(card);
    }
  }

  // Renders the games list, optionally filtered to one variation
  // (matched on the exact variation name from the payload).
  function render(variationName) {
    activeFilter = variationName;
    const filtered = variationName == null
      ? gl : gl.filter(g => g.variation === variationName);
    const count = variationName == null ? op.games : filtered.length;
    const flagged = filtered.filter(g => g._worst).length;

    head.innerHTML = "";
    head.appendChild(document.createTextNode(`Games (${count})`));
    if (flagged) {
      const fl = document.createElement("span");
      fl.className = "gfilter";
      fl.textContent = `\u2014 ${flagged} with opening issues`;
      head.appendChild(fl);
    }
    if (variationName != null) {
      const label = document.createElement("span");
      label.className = "gfilter";
      label.textContent = `\u2014 ${variationName}`;
      label.title = variationName;
      const clear = document.createElement("span");
      clear.className = "gclear";
      clear.textContent = "All games";
      clear.title = "Clear the variation filter";
      clear.onclick = () => {
        const active = lines.querySelector(".line.active");
        if (active) active.classList.remove("active");
        render(null);
      };
      head.append(label, clear);
    }

    rows.innerHTML = "";
    for (const g of filtered.slice(0, GAMES_SHOWN_INITIALLY)) rows.appendChild(makeGameRow(g));
    if (filtered.length > GAMES_SHOWN_INITIALLY) {
      const more = document.createElement("div");
      more.className = "game-more";
      more.textContent = `Show ${filtered.length - GAMES_SHOWN_INITIALLY} more`;
      more.onclick = () => {
        more.remove();
        for (const g of filtered.slice(GAMES_SHOWN_INITIALLY)) rows.appendChild(makeGameRow(g));
      };
      rows.appendChild(more);
    }
  }

  scanBtn.onclick = async () => {
    // When a variation line is selected, scan that line only (faster + relevant).
    const source = activeFilter == null
      ? gl
      : gl.filter(g => g.variation === activeFilter);
    const batch = source.filter(g => g.san && g.san.length).slice(0, 40);
    if (!batch.length) {
      scanStatus.textContent = "No move data to scan.";
      return;
    }
    scanBtn.disabled = true;
    scanStatus.textContent = `Scanning ${batch.length} games (opening moves)\u2026`;
    try {
      const data = await apiScanBlunders({
        games: batch.map(g => ({
          san: g.san,
          color: state.color,
          variation: g.variation,
          opponent: g.opponent,
          date: g.date,
          url: g.url,
        })),
        opening_plies: 20,
      }, msg => { scanStatus.textContent = msg; });
      // Attach flags back onto the source game objects.
      for (const sg of data.games || []) {
        const g = batch[sg.index];
        if (!g) continue;
        g._flags = sg.flags || [];
        g._worst = sg.worst;
        g._counts = sg.counts;
      }
      const withIssues = (data.games || []).filter(g => g.worst).length;
      const pats = data.patterns || [];
      scanStatus.textContent = withIssues
        ? `${withIssues} game${withIssues === 1 ? "" : "s"} with opening issues` +
          (pats.length ? ` \u00b7 ${pats.length} repeated pattern${pats.length === 1 ? "" : "s"}` : "")
        : "No opening inaccuracies found in this sample.";
      renderPatterns(pats, batch);
      render(activeFilter);
      const issues = collectScanIssues(batch, data.games);
      if (issues.length) openOpeningLesson(issues, 0);
      saveMistakesAsPuzzles({
        games: (data.games || []).map(sg => ({
          ...sg,
          san: batch[sg.index]?.san || [],
        })),
      });
    } catch (e) {
      if (handleProGateError(e)) {
        scanStatus.textContent = "Pro required to scan for opening mistakes.";
      } else {
        scanStatus.textContent = e.message || "Scan failed";
      }
    } finally {
      scanBtn.disabled = false;
    }
  };

  lines._renderGames = render;
  render(null);
}

function setColor(color) {
  state.color = color;
  state.orientation = color;  // match board perspective to the side you're studying
  const tabW = document.getElementById("tab-white");
  const tabB = document.getElementById("tab-black");
  tabW.classList.toggle("active", color === "white");
  tabB.classList.toggle("active", color === "black");
  tabW.setAttribute("aria-selected", color === "white" ? "true" : "false");
  tabB.setAttribute("aria-selected", color === "black" ? "true" : "false");
  if (state.gameView) closeGameView();
  renderSidebar();
  resetGame();
}
document.getElementById("tab-white").onclick = () => setColor("white");
document.getElementById("tab-black").onclick = () => setColor("black");

/* ---------- fetching ---------- */

const statusEl = document.getElementById("status");
const btn = document.getElementById("analyze-btn");
const meBtn = document.getElementById("analyze-me-btn");

let lastAnalyze = analyzeUser;   // rerun on time-control change (user vs "me" report)

/** Optional direct API base (set via window.OPENING_EXPLORER_API or meta tag).
 * Prefer same-origin /api proxy; keep a Render default on custom domains so a
 * Netlify proxy timeout can fall back instead of surfacing "Failed to fetch". */
const DEFAULT_RENDER_API = "https://chess-repertoire-bteo.onrender.com";
function configuredApiBase() {
  if (typeof window.OPENING_EXPLORER_API === "string" && window.OPENING_EXPLORER_API.trim()) {
    return window.OPENING_EXPLORER_API.trim().replace(/\/$/, "");
  }
  const meta = document.querySelector('meta[name="opening-explorer-api"]');
  const content = meta?.getAttribute("content")?.trim();
  if (content) return content.replace(/\/$/, "");
  const host = typeof location !== "undefined" ? location.hostname : "";
  if (!host || host === "localhost" || host === "127.0.0.1" || host.includes("onrender.com")) {
    return "";
  }
  return DEFAULT_RENDER_API;
}
const RENDER_API = configuredApiBase();
let apiBase = "";  // "" = same origin

function apiUrl(path) {
  if (path.startsWith("http")) return path;
  return apiBase + path;
}

function looksLikeProxyMiss(status, text) {
  // Include 504 — Netlify's proxy ~26s limit returns empty/HTML gateway timeouts
  // for long Stockfish scans; treat like a broken proxy and fall back to Render.
  if (status === 404 || status === 502 || status === 503 || status === 504) {
    const t = (text || "").trim().slice(0, 80).toLowerCase();
    if (!t || t.startsWith("<!doctype") || t.startsWith("<html") || t.startsWith("not found")) {
      return true;
    }
  }
  return false;
}

async function readJson(resp) {
  const text = await resp.text();
  if (!text) {
    const proxyMiss = looksLikeProxyMiss(resp.status, text);
    if (resp.status === 504) {
      throw Object.assign(
        new Error(
          proxyMiss && !apiBase
            ? "Gateway timed out (Netlify proxy). Retrying via the Render backend…"
            : "Gateway timed out before the server responded. Wait a moment and try again."
        ),
        { httpStatus: resp.status, rawBody: text, isProxyMiss: proxyMiss }
      );
    }
    throw Object.assign(
      new Error(
        resp.status
          ? `Empty response (HTTP ${resp.status}). The API may still be waking up — try again.`
          : "Empty response from server. Try again."
      ),
      { httpStatus: resp.status, rawBody: text, isProxyMiss: proxyMiss }
    );
  }
  try {
    return JSON.parse(text);
  } catch (e) {
    let message;
    if (resp.status === 405) {
      message = "Server rejected the request (HTTP 405). Hard-refresh the page (Ctrl+F5) and try again.";
    } else if (looksLikeProxyMiss(resp.status, text)) {
      message = `API proxy unavailable (HTTP ${resp.status}).`;
    } else if (resp.status === 500) {
      message = "Engine server error (HTTP 500). If this persists after a Render redeploy, check /api/engine-status.";
    } else {
      message = `Server returned non-JSON (HTTP ${resp.status}). Try again in a moment.`;
    }
    const err = new Error(message);
    err.httpStatus = resp.status;
    err.rawBody = text;
    err.isProxyMiss = looksLikeProxyMiss(resp.status, text);
    throw err;
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function fetchApi(path, opts = {}) {
  // Always include cookies — needed for auth via Netlify proxy and for
  // direct Render fallback (CORS must allow credentials on the API).
  const resp = await fetch(apiUrl(path), {
    ...opts,
    credentials: "include",
  });
  return resp;
}

function isSessionApiPath(path) {
  // These need the Netlify same-origin session cookie (nonce / CSRF / login).
  // Never fall back to cross-origin Render for them.
  return /^\/api\/(auth|logout|me|billing)\b/.test(path || "");
}

async function fetchWithTimeout(path, opts = {}, timeoutMs = 20000) {
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  try {
    return await fetchApi(path, ctrl ? { ...opts, signal: ctrl.signal } : opts);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function ensureApiBase() {
  if (apiBase || location.hostname.includes("onrender.com") ||
      location.hostname === "127.0.0.1" || location.hostname === "localhost") {
    return;
  }
  // Probe same-origin proxy once; if it's a dead Netlify site, switch to configured API.
  try {
    const resp = await fetch("/api/me", { credentials: "same-origin" });
    const text = await resp.text();
    if (looksLikeProxyMiss(resp.status, text) &&
        (resp.status === 404 || resp.status === 502 || resp.status === 504) &&
        (text.trim().startsWith("Not Found") || !text.trim().startsWith("{"))) {
      // Confirm it's not Flask JSON 401
      try { JSON.parse(text); return; } catch (_) {}
      if (RENDER_API) apiBase = RENDER_API;
    }
  } catch (_) {
    if (RENDER_API) apiBase = RENDER_API;
  }
}

function applyReport(data) {
  state.report = data;
  if (data.username) rememberUsername(data.username);
  let msg = `${data.analyzed_games} rated games analyzed (${
    data.since || data.until
      ? rangeLabel.textContent
      : `last ${data.months} months`})`;
  if (data.warnings && data.warnings.length) msg += ` \u2014 ${data.warnings.join("; ")}`;
  statusEl.textContent = msg;
  if (data.warnings && data.warnings.length) statusEl.className = "error";
  setColor(state.color);
  repSlipupsScanKey = ""; // force rescan with new games
  if (state.repertoireOpen) scheduleRepSlipupsRefresh();
  trackProductEvent("report_analyzed", {
    username: data.username || undefined,
    analyzed_games: data.analyzed_games ?? undefined,
    months: data.months ?? undefined,
    kind: lastAnalyze === analyzeMe ? "me" : "search",
  });
}

async function runReportSync(syncPath, fetchingMsg) {
  statusEl.textContent = fetchingMsg.replace(/\u2026.*$/, "") + "\u2026 (direct)";
  const resp = await fetchApi(syncPath);
  const data = await readJson(resp);
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  applyReport(data);
}

async function runReport(startPath, syncPath, fetchingMsg) {
  const tcs = [...document.querySelectorAll("#tc-group input:checked")].map(c => c.value);
  if (!tcs.length) { statusEl.textContent = "Pick at least one time control."; statusEl.className = "error"; return; }

  btn.disabled = true;
  meBtn.disabled = true;
  statusEl.className = "";
  statusEl.textContent = fetchingMsg;
  const qs = `&time_classes=${tcs.join(",")}${rangeQuery()}`;

  try {
    await ensureApiBase();

    let startResp;
    let start;
    try {
      startResp = await fetchApi(`${startPath}${qs}`);
      start = await readJson(startResp);
    } catch (e) {
      if (e.isProxyMiss && !apiBase && RENDER_API) {
        apiBase = RENDER_API;
        statusEl.textContent = "Netlify API proxy missing — using Render directly\u2026";
        startResp = await fetchApi(`${startPath}${qs}`);
        start = await readJson(startResp);
      } else if (e.isProxyMiss || e.httpStatus === 404 || e.httpStatus === 405) {
        // Jobs endpoint unavailable — sync fallback
        await runReportSync(`${syncPath}${qs}`, fetchingMsg);
        return;
      } else {
        throw e;
      }
    }

    if (!startResp.ok) {
      if (startResp.status === 404 || startResp.status === 405) {
        await runReportSync(`${syncPath}${qs}`, fetchingMsg);
        return;
      }
      throw new Error(start.error || `HTTP ${startResp.status}`);
    }
    if (!start.job_id) {
      await runReportSync(`${syncPath}${qs}`, fetchingMsg);
      return;
    }

    const jobId = start.job_id;
    const deadline = Date.now() + 5 * 60 * 1000;
    let dots = 0;
    let consecutiveFails = 0;
    while (Date.now() < deadline) {
      await sleep(1500);
      let pollResp;
      let data;
      try {
        pollResp = await fetchApi(`/api/report/jobs/${encodeURIComponent(jobId)}`);
        data = await readJson(pollResp);
        consecutiveFails = 0;
      } catch (e) {
        consecutiveFails++;
        if (e.isProxyMiss && !apiBase && RENDER_API) {
          apiBase = RENDER_API;
          continue;
        }
        // Fail fast — do not poll for 5 minutes on a broken proxy
        if (consecutiveFails >= 2) {
          throw new Error(
            e.isProxyMiss
              ? "Cannot reach the analysis API (404). Open https://chess-repertoire-bteo.onrender.com/ or fix the Netlify /api proxy."
              : e.message
          );
        }
        continue;
      }

      if (!pollResp.ok) {
        if (pollResp.status === 404) {
          throw new Error(data.error || "Analysis job expired. Click Analyze again.");
        }
        throw new Error(data.error || `HTTP ${pollResp.status}`);
      }

      if (data.status === "pending" || data.status === "running") {
        dots = (dots + 1) % 4;
        statusEl.textContent = fetchingMsg.replace(/\u2026.*$/, "") +
          "\u2026 fetching" + ".".repeat(dots);
        continue;
      }
      if (data.status === "error") throw new Error(data.error || "Analysis failed");
      if (data.status !== "done") throw new Error(`Unexpected job status: ${data.status}`);

      applyReport(data.report);
      return;
    }
    throw new Error("Analysis timed out after 5 minutes. Try a shorter date range.");
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "error";
  } finally {
    btn.disabled = false;
    meBtn.disabled = false;
  }
}

async function analyzeUser() {
  const username = document.getElementById("username").value.trim();
  if (!username) { statusEl.textContent = "Enter a username first."; statusEl.className = "error"; return; }
  lastAnalyze = analyzeUser;
  await runReport(
    `/api/report/jobs?username=${encodeURIComponent(username)}`,
    `/api/report?username=${encodeURIComponent(username)}`,
    "Fetching games from chess.com\u2026 (first run can take up to a minute)"
  );
}

async function analyzeMe() {
  lastAnalyze = analyzeMe;
  await runReport(
    "/api/report/me/jobs?1=1",
    "/api/report/me?1=1",
    "Fetching games from your linked accounts\u2026 (first run can take up to a minute)"
  );
}

btn.onclick = analyzeUser;
meBtn.onclick = analyzeMe;
document.getElementById("username").addEventListener("keydown", e => {
  if (e.key === "Enter") analyzeUser();
});
document.getElementById("tc-group").addEventListener("change", () => {
  if (state.report) lastAnalyze();   // live re-filter once a report is loaded
});

/* ---------- time-frame filter ---------- */

const rangeWrap = document.getElementById("range-wrap");
const rangeBtn = document.getElementById("range-btn");
const rangeLabel = document.getElementById("range-label");
const rangeFromEl = document.getElementById("range-from");
const rangeToEl = document.getElementById("range-to");
const rangeMsgEl = document.getElementById("range-msg");

function isoLocal(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const RANGE_PRESETS = {
  "7d":  { label: "Last 7 days",    since: () => { const d = new Date(); d.setDate(d.getDate() - 7); return isoLocal(d); } },
  "30d": { label: "Last 30 days",   since: () => { const d = new Date(); d.setDate(d.getDate() - 30); return isoLocal(d); } },
  "6m":  { label: "Last 6 months",  since: () => { const d = new Date(); d.setMonth(d.getMonth() - 6); return isoLocal(d); } },
  "12m": { label: "Last 12 months", since: () => { const d = new Date(); d.setMonth(d.getMonth() - 12); return isoLocal(d); } },
  "all": { label: "All time",       since: () => "2005-01-01" },  // predates chess.com archives
};

// Default calendar selection: last 30 days (must set `since` so Analyze uses it).
state.range = { preset: "30d", since: RANGE_PRESETS["30d"].since(), until: null };
rangeLabel.textContent = RANGE_PRESETS["30d"].label;

function rangeQuery() {
  let q = "";
  if (state.range.since) q += `&since=${state.range.since}`;
  if (state.range.until) q += `&until=${state.range.until}`;
  return q;
}

function prettyDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d)
    .toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function setRangePanelOpen(open) {
  rangeWrap.classList.toggle("open", open);
  if (rangeBtn) rangeBtn.setAttribute("aria-expanded", open ? "true" : "false");
}

function markSelectedPreset(preset) {
  document.querySelectorAll("#range-panel .preset")
    .forEach(b => b.classList.toggle("selected", b.dataset.preset === preset));
}

function applyRange(range, label) {
  state.range = range;
  rangeLabel.textContent = label;
  markSelectedPreset(range.preset);
  setRangePanelOpen(false);
  if (state.report) lastAnalyze();   // live re-filter, same as the pills
}

rangeBtn.onclick = e => {
  e.stopPropagation();
  setRangePanelOpen(!rangeWrap.classList.contains("open"));
};
document.addEventListener("click", e => {
  if (!rangeWrap.contains(e.target)) setRangePanelOpen(false);
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") setRangePanelOpen(false);
});

for (const b of document.querySelectorAll("#range-panel .preset")) {
  b.onclick = () => {
    const p = RANGE_PRESETS[b.dataset.preset];
    rangeMsgEl.textContent = "";
    applyRange({ preset: b.dataset.preset, since: p.since(), until: null }, p.label);
  };
}

document.getElementById("range-apply").onclick = () => {
  const from = rangeFromEl.value;
  const to = rangeToEl.value;
  if (!from && !to) { rangeMsgEl.textContent = "Pick at least one date."; return; }
  if (from && to && from > to) { rangeMsgEl.textContent = "From must be before To."; return; }
  rangeMsgEl.textContent = "";
  const label = from && to ? `${prettyDate(from)} \u2013 ${prettyDate(to)}`
    : from ? `Since ${prettyDate(from)}` : `Until ${prettyDate(to)}`;
  applyRange({ preset: "custom", since: from || null, until: to || null }, label);
};

/* ---------- recent chess.com usernames ---------- */

const RECENT_LS = "recentUsernames";
const RECENT_MAX = 8;

function getRecentUsernames() {
  try {
    const list = JSON.parse(localStorage.getItem(RECENT_LS) || "[]");
    if (!Array.isArray(list)) return [];
    return list
      .filter(u => typeof u === "string" && u.trim())
      .map(u => u.trim())
      .slice(0, RECENT_MAX);
  } catch (e) {
    return [];
  }
}

function rememberUsername(username) {
  const u = String(username || "").trim();
  if (!u) return;
  const lower = u.toLowerCase();
  const next = [u, ...getRecentUsernames().filter(x => x.toLowerCase() !== lower)]
    .slice(0, RECENT_MAX);
  localStorage.setItem(RECENT_LS, JSON.stringify(next));
  renderRecentUsernames();
}

function renderRecentUsernames() {
  const dl = document.getElementById("username-recent");
  if (!dl) return;
  dl.innerHTML = getRecentUsernames()
    .map(u => `<option value="${String(u).replace(/"/g, "&quot;")}"></option>`)
    .join("");
}

function initRecentUsernames() {
  renderRecentUsernames();
  const input = document.getElementById("username");
  const recent = getRecentUsernames();
  if (input && !input.value.trim() && recent[0]) {
    input.value = recent[0];
  }
}

/* ---------- accounts / profile ---------- */

let currentUser = null;   // user payload from /api/me
let googleClientId = null;
let googleNonce = "";
let googleIdentityReady = false;
let googleSignInBusy = false;
let googleWarmupPromise = null;
let siteConfigPromise = null;   // shared cache for GET /api/auth/config (GA)
let csrfToken = "";

function applyAuthConfig(cfg) {
  if (cfg?.csrf_token) csrfToken = cfg.csrf_token;
  if (cfg?.google_client_id) googleClientId = cfg.google_client_id;
  if (cfg?.nonce) googleNonce = cfg.nonce;
}

function fetchSiteConfig() {
  if (!siteConfigPromise) {
    siteConfigPromise = api("/api/auth/config", "GET")
      .then((cfg) => {
        applyAuthConfig(cfg);
        return cfg || {};
      })
      .catch((e) => {
        // Don't cache a cold-start 504 forever — Google sign-in must retry.
        siteConfigPromise = null;
        throw e;
      });
  }
  return siteConfigPromise;
}

const authModal = document.getElementById("auth-modal");
const authMsg = document.getElementById("auth-msg");
const authGoogleMsg = document.getElementById("auth-google-msg");
const authOpenBtn = document.getElementById("auth-open-btn");
const userMenuBtn = document.getElementById("user-menu-btn");
const userMenu = document.getElementById("user-menu");
const profileMsg = document.getElementById("profile-msg");
const profilePwMsg = document.getElementById("profile-pw-msg");

function isProUser() {
  // When billing isn't configured, server reports is_pro=true for everyone logged in
  // and guests still hit 402 only if billing is on — treat missing user as free.
  if (currentUser) return !!currentUser.is_pro;
  return false;
}

function syncProChips() {
  // Light Pro chip for Free users when Stripe billing is live.
  const show = !!(currentUser?.billing_enabled && !currentUser.is_pro);
  const practiceChip = document.getElementById("practice-pro-chip");
  const slipChip = document.getElementById("slipups-pro-chip");
  if (practiceChip) practiceChip.hidden = !show;
  if (slipChip) slipChip.hidden = !show;
}

function setUser(user) {
  currentUser = user;
  const loggedIn = !!user;
  document.body.classList.toggle("logged-in", loggedIn);
  authOpenBtn.style.display = loggedIn ? "none" : "";
  userMenuBtn.style.display = loggedIn ? "" : "none";
  meBtn.style.display = loggedIn ? "" : "none";
  if (loggedIn) {
    userMenuBtn.textContent = user.display_name || user.email;
    fillProfileForm(user);
    loadRepertoireFromServer();
    identifyProductUser(user);
  } else {
    userMenu.classList.remove("open");
    closeProfile();
    loadRepertoireFromLocal();
    resetProductAnalytics();
  }
  syncProChips();
  if (!state.report) renderSidebar();
}

function fillProfileForm(user) {
  document.getElementById("profile-email").value = user?.email || "";
  document.getElementById("profile-chesscom").value = user?.chesscom_username || "";
  document.getElementById("profile-lichess").value = user?.lichess_username || "";
  const status = document.getElementById("profile-link-status");
  const cc = (user?.chesscom_username || "").trim();
  const li = (user?.lichess_username || "").trim();
  status.innerHTML =
    `<span class="profile-pill ${cc ? "ok" : "miss"}">chess.com: ${cc || "not linked"}</span>` +
    `<span class="profile-pill ${li ? "ok" : "miss"}">lichess: ${li || "not linked"}</span>`;
  const pwCard = document.getElementById("profile-password-card");
  if (pwCard) pwCard.style.display = user?.has_password ? "" : "none";

  const badge = document.getElementById("profile-plan-badge");
  const upBtn = document.getElementById("profile-upgrade-btn");
  const billBtn = document.getElementById("profile-billing-btn");
  const pro = !!user?.is_pro;
  const trial = !!user?.is_trialing;
  const billingOn = !!user?.billing_enabled;
  if (badge) {
    badge.textContent = trial ? "Trial" : (pro ? "Pro" : "Free");
    badge.classList.toggle("pro", pro || trial);
  }
  if (upBtn) upBtn.style.display = billingOn && !pro ? "" : "none";
  if (billBtn) billBtn.style.display = billingOn && pro ? "" : "none";

  const analyticsCard = document.getElementById("profile-analytics-card");
  if (analyticsCard) {
    const showAdmin = !!user?.can_view_analytics;
    analyticsCard.hidden = !showAdmin;
    // Belt-and-suspenders: never leave the admin panel in the layout for others.
    analyticsCard.style.display = showAdmin ? "" : "none";
    if (!showAdmin) {
      const usersList = document.getElementById("analytics-users-list");
      const searchList = document.getElementById("analytics-search-list");
      if (usersList) usersList.innerHTML = "";
      if (searchList) searchList.innerHTML = "";
    }
  }
}

function openProfile() {
  if (!currentUser) {
    openAuthModal();
    return;
  }
  closeRepertoire();
  fillProfileForm(currentUser);
  setMsg(profileMsg, "");
  setMsg(profilePwMsg, "");
  if (currentUser.can_view_analytics) loadAnalyticsSummary();
  loadAuthSessions();
  document.body.classList.add("profile-open");
  userMenu.classList.remove("open");
}

function closeProfile() {
  document.body.classList.remove("profile-open");
}

async function api(path, method, body) {
  // Auth/session calls must stay same-origin so the Netlify cookie (nonce/CSRF) is sent.
  if (!isSessionApiPath(path)) {
    await ensureApiBase();
  } else if (apiBase) {
    // A prior engine fallback switched us to Render — auth cannot use that.
    apiBase = "";
  }
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  const m = (method || "GET").toUpperCase();
  if (m !== "GET" && m !== "HEAD" && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  const opts = {
    method,
    headers: Object.keys(headers).length ? headers : undefined,
    body: body ? JSON.stringify(body) : undefined,
  };
  const timeoutMs = isSessionApiPath(path) ? 25000 : 20000;
  let resp;
  try {
    resp = await fetchWithTimeout(path, opts, timeoutMs);
  } catch (e) {
    if (e && e.name === "AbortError") {
      throw new Error("Request timed out. The server may be waking up — wait a moment and try again.");
    }
    throw new Error(e?.message || "Failed to fetch");
  }
  const hdrToken = resp.headers.get("X-CSRF-Token");
  if (hdrToken) csrfToken = hdrToken;
  let data;
  try {
    data = await readJson(resp);
  } catch (e) {
    // Netlify proxy miss / HTML error page — retry once against Render
    // (never for session/auth paths — cookies won't follow cross-origin).
    if (e.isProxyMiss && !apiBase && RENDER_API && !isSessionApiPath(path)) {
      apiBase = RENDER_API;
      resp = await fetchWithTimeout(path, opts, timeoutMs);
      const hdr2 = resp.headers.get("X-CSRF-Token");
      if (hdr2) csrfToken = hdr2;
      data = await readJson(resp);
    } else if (e.isProxyMiss && isSessionApiPath(path)) {
      throw new Error(
        "Sign-in API timed out through the proxy. Wait a few seconds for the server to wake, then try again."
      );
    } else {
      throw e;
    }
  }
  if (data?.csrf_token) csrfToken = data.csrf_token;
  if (!resp.ok) {
    const err = new Error(data.error || `HTTP ${resp.status}`);
    err.status = resp.status;
    err.code = data.code || null;
    throw err;
  }
  return data;
}

/** Opening blunder scan — prefer async jobs (Netlify proxy times out ~26s). */
async function apiScanBlunders(body, onStatus) {
  await ensureApiBase();

  async function startJob() {
    const headers = { "Content-Type": "application/json" };
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const opts = {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    };
    let resp = await fetchApi("/api/scan-blunders/jobs", opts);
    let data;
    try {
      data = await readJson(resp);
    } catch (e) {
      if (e.isProxyMiss && !apiBase && RENDER_API) {
        apiBase = RENDER_API;
        if (onStatus) onStatus("Netlify proxy timed out — using Render directly\u2026");
        resp = await fetchApi("/api/scan-blunders/jobs", opts);
        data = await readJson(resp);
      } else {
        throw e;
      }
    }
    return { resp, data };
  }

  let started = null;
  try {
    started = await startJob();
  } catch (e) {
    if (e.isProxyMiss || e.httpStatus === 404 || e.httpStatus === 405) {
      started = null; // sync fallback below
    } else {
      throw e;
    }
  }

  if (started && (started.resp.status === 404 || started.resp.status === 405)) {
    started = null;
  } else if (started && !started.resp.ok) {
    const err = new Error(started.data.error || `HTTP ${started.resp.status}`);
    err.status = started.resp.status;
    err.code = started.data.code || null;
    throw err;
  }

  if (started && started.data.job_id) {
    const jobId = started.data.job_id;
    const deadline = Date.now() + 5 * 60 * 1000;
    let dots = 0;
    while (Date.now() < deadline) {
      await sleep(1500);
      dots = (dots + 1) % 4;
      if (onStatus) onStatus("Scanning opening moves" + ".".repeat(dots));
      let pollResp;
      let poll;
      try {
        pollResp = await fetchApi(`/api/scan-blunders/jobs/${encodeURIComponent(jobId)}`);
        poll = await readJson(pollResp);
      } catch (e) {
        if (e.isProxyMiss && !apiBase && RENDER_API) {
          apiBase = RENDER_API;
          continue;
        }
        throw e;
      }
      if (!pollResp.ok) {
        throw new Error(poll.error || `HTTP ${pollResp.status}`);
      }
      if (poll.status === "pending" || poll.status === "running") continue;
      if (poll.status === "error") throw new Error(poll.error || "Scan failed");
      if (poll.status === "done") return poll.result;
      throw new Error(`Unexpected scan job status: ${poll.status}`);
    }
    throw new Error("Scan timed out after 5 minutes. Try fewer games.");
  }

  // Sync fallback — hit Render directly so Netlify's ~26s proxy cannot cut us off.
  if (!apiBase && RENDER_API && location.hostname !== "127.0.0.1" && location.hostname !== "localhost"
      && !location.hostname.includes("onrender.com")) {
    apiBase = RENDER_API;
    if (onStatus) onStatus("Scanning via Render (long analysis)\u2026");
  }
  return api("/api/scan-blunders", "POST", body);
}

/* ---------- Intro welcome ---------- */

const INTRO_SEEN_KEY = "introSeen";
const introModal = document.getElementById("intro-modal");

function markIntroSeen() {
  try { localStorage.setItem(INTRO_SEEN_KEY, "1"); } catch (e) { /* ignore */ }
}

function closeIntroModal() {
  introModal?.classList.remove("open");
  markIntroSeen();
}

function openIntroModal() {
  introModal?.classList.add("open");
}

function initIntroModal() {
  document.getElementById("intro-start-btn")?.addEventListener("click", closeIntroModal);
  introModal?.addEventListener("click", e => {
    if (e.target === introModal) closeIntroModal();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && introModal?.classList.contains("open")) {
      closeIntroModal();
    }
  });

  let seen = false;
  try { seen = localStorage.getItem(INTRO_SEEN_KEY) === "1"; } catch (e) { /* ignore */ }
  if (seen) return;

  // Skip when returning from Stripe Checkout
  try {
    if (new URLSearchParams(window.location.search).has("billing")) return;
  } catch (e) { /* ignore */ }

  // Don't fight auth/upgrade if somehow already open
  if (document.getElementById("auth-modal")?.classList.contains("open")) return;
  if (document.getElementById("upgrade-modal")?.classList.contains("open")) return;

  openIntroModal();
}

/* ---------- Pro / Stripe upgrade ---------- */

const upgradeModal = document.getElementById("upgrade-modal");
const upgradeMsg = document.getElementById("upgrade-msg");

function openUpgradeModal() {
  if (!currentUser) {
    openAuthModal();
    return;
  }
  setMsg(upgradeMsg, "");
  upgradeModal?.classList.add("open");
}

function closeUpgradeModal() {
  upgradeModal?.classList.remove("open");
  setMsg(upgradeMsg, "");
}

/** If API error needs login/Pro, open the right modal. Returns true if handled. */
function handleProGateError(e) {
  if (e?.code === "login_required" || e?.status === 401) {
    openAuthModal();
    return true;
  }
  if (e?.code === "pro_required" || e?.status === 402) {
    openUpgradeModal();
    return true;
  }
  return false;
}

async function startCheckout(interval) {
  if (!currentUser) {
    openAuthModal();
    return;
  }
  setMsg(upgradeMsg, "Redirecting to checkout\u2026");
  try {
    trackProductEvent("checkout_started", { interval });
    const data = await api("/api/billing/checkout", "POST", { interval });
    if (data.url) {
      window.location.href = data.url;
      return;
    }
    setMsg(upgradeMsg, "No checkout URL returned.", "error");
  } catch (e) {
    if (e?.code === "login_required" || e?.status === 401) {
      closeUpgradeModal();
      setUser(null);
      setMsg(authMsg, "Session expired — sign in again to upgrade.", "error");
      openAuthModal({ keepMessages: true });
      return;
    }
    setMsg(upgradeMsg, e.message || "Checkout failed", "error");
  }
}

async function openBillingPortal() {
  setMsg(profileMsg, "Opening billing portal\u2026");
  try {
    const data = await api("/api/billing/portal", "POST", {});
    if (data.url) {
      window.location.href = data.url;
      return;
    }
    setMsg(profileMsg, "No portal URL returned.", "error");
  } catch (e) {
    setMsg(profileMsg, e.message || "Could not open portal", "error");
  }
}

function initBillingUi() {
  document.getElementById("upgrade-cancel-btn")?.addEventListener("click", closeUpgradeModal);
  document.getElementById("upgrade-month-btn")?.addEventListener("click", () => startCheckout("month"));
  document.getElementById("upgrade-year-btn")?.addEventListener("click", () => startCheckout("year"));
  document.getElementById("profile-upgrade-btn")?.addEventListener("click", openUpgradeModal);
  document.getElementById("profile-billing-btn")?.addEventListener("click", openBillingPortal);
  upgradeModal?.addEventListener("click", e => {
    if (e.target === upgradeModal) closeUpgradeModal();
  });

  // Return from Stripe Checkout
  try {
    const params = new URLSearchParams(window.location.search);
    const billing = params.get("billing");
    if (billing === "success") {
      params.delete("billing");
      const qs = params.toString();
      window.history.replaceState({}, "", window.location.pathname + (qs ? "?" + qs : ""));
      refreshMeAfterBilling();
    } else if (billing === "cancel") {
      params.delete("billing");
      const qs = params.toString();
      window.history.replaceState({}, "", window.location.pathname + (qs ? "?" + qs : ""));
    }
  } catch (e) { /* ignore */ }
}

async function refreshMeAfterBilling() {
  try {
    const user = await api("/api/me", "GET");
    setUser(user);
    closeUpgradeModal();
  } catch (e) { /* ignore */ }
}

function setMsg(el, text, cls) {
  if (!el) return;
  el.textContent = text;
  el.className = "form-msg" + (cls ? " " + cls : "");
}

async function submitAuth(path) {
  const email = document.getElementById("auth-email").value.trim();
  const password = document.getElementById("auth-password").value;
  if (!email || !password) { setMsg(authMsg, "Enter your email and password.", "error"); return; }
  setMsg(authMsg, "\u2026");
  try {
    const user = await api(path, "POST", { email, password });
    finishLogin(user, path === "/api/register");
  } catch (e) {
    setMsg(authMsg, e.message, "error");
  }
}

function finishLogin(user, isNewAccount) {
  setUser(user);
  trackProductEvent(isNewAccount ? "user_signed_up" : "user_logged_in", {
    auth_provider: user?.auth_provider || "password",
  });
  closeAuthModal();
  document.getElementById("auth-password").value = "";
  setMsg(authMsg, "");
  setMsg(authGoogleMsg, "");
  const needsLinks = !(user.chesscom_username || user.lichess_username);
  if (isNewAccount || needsLinks) openProfile();
}

async function logout() {
  userMenu.classList.remove("open");
  try { await api("/api/logout", "POST"); } catch (e) { /* ignore */ }
  setUser(null);
  googleIdentityReady = false;
  googleNonce = "";
  googleWarmupPromise = null;
  siteConfigPromise = null;
  warmGoogleSignIn();
}

function setGoogleSigningIn(busy) {
  googleSignInBusy = !!busy;
  const wrap = document.getElementById("auth-google-wrap");
  if (wrap) wrap.classList.toggle("signing-in", googleSignInBusy);
}

async function handleGoogleCredential(response) {
  if (googleSignInBusy) return;
  const credential = response?.credential;
  if (!credential) {
    setMsg(authGoogleMsg, "Google sign-in failed. Please try again.", "error");
    return;
  }
  setGoogleSigningIn(true);
  setMsg(authGoogleMsg, "Verifying Google account\u2026");
  // Watchdog so the modal can never sit on "Signing in…" forever.
  const watchdog = setTimeout(() => {
    if (!googleSignInBusy) return;
    setGoogleSigningIn(false);
    setMsg(
      authGoogleMsg,
      "Google sign-in timed out. Wait a few seconds (server may be waking up), then try again.",
      "error"
    );
  }, 28000);
  try {
    const user = await api("/api/auth/google", "POST", { credential });
    // Refresh nonce for any later sign-in in this tab.
    googleNonce = "";
    googleIdentityReady = false;
    finishLogin(user, !!user.is_new_account);
  } catch (e) {
    const msg = (e && e.name === "AbortError")
      ? "Sign-in timed out. Wait a moment and try again."
      : (e.message || "Google sign-in failed.");
    setMsg(authGoogleMsg, msg, "error");
    // Mint a fresh nonce so the next Google click is not stuck on a used one.
    try { await refreshGoogleAuthConfigResilient(); } catch (_) { /* ignore */ }
    googleIdentityReady = false;
    try { await prepareGoogleSignIn(); } catch (_) { /* ignore */ }
  } finally {
    clearTimeout(watchdog);
    setGoogleSigningIn(false);
  }
}

function isTransientAuthFailure(e) {
  const status = e?.httpStatus || e?.status;
  const msg = String(e?.message || "").toLowerCase();
  return status === 502 || status === 503 || status === 504
    || e?.isProxyMiss
    || e?.name === "AbortError"
    || msg.includes("timed out")
    || msg.includes("waking up");
}

async function pingRenderWakeup() {
  if (!RENDER_API) return;
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), 45000) : null;
  try {
    await fetch(`${RENDER_API}/api/health`, {
      mode: "cors",
      credentials: "omit",
      signal: ctrl ? ctrl.signal : undefined,
    });
  } catch (_) {
    /* wake-up ping; ignore failures */
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function refreshGoogleAuthConfig() {
  const cfg = await api("/api/auth/config", "GET");
  applyAuthConfig(cfg);
  googleClientId = cfg.google_client_id || "";
  googleNonce = cfg.nonce || "";
  return googleClientId;
}

async function refreshGoogleAuthConfigResilient() {
  try {
    return await refreshGoogleAuthConfig();
  } catch (e) {
    if (!isTransientAuthFailure(e)) throw e;
    await pingRenderWakeup();
    await sleep(1500);
    return await refreshGoogleAuthConfig();
  }
}

async function ensureGoogleClientId() {
  if (googleClientId && googleNonce) return googleClientId;
  try {
    await fetchSiteConfig();
  } catch (e) {
    googleClientId = googleClientId || "";
    googleNonce = googleNonce || "";
  }
  return googleClientId;
}

function waitForGoogleSdk(timeoutMs = 8000) {
  if (window.google?.accounts?.id) return Promise.resolve(true);
  return new Promise(resolve => {
    const started = Date.now();
    const wait = setInterval(() => {
      if (window.google?.accounts?.id) {
        clearInterval(wait);
        resolve(true);
      } else if (Date.now() - started > timeoutMs) {
        clearInterval(wait);
        resolve(false);
      }
    }, 50);
  });
}

function initGoogleIdentity() {
  if (!googleClientId || !googleNonce || !window.google?.accounts?.id) return false;
  window.google.accounts.id.initialize({
    client_id: googleClientId,
    callback: handleGoogleCredential,
    nonce: googleNonce,
    auto_select: false,
    cancel_on_tap_outside: true,
    context: "signin",
    // Keep the GIS button on the iframe/popup path so the nonce claim is
    // included. use_fedcm_for_prompt is deprecated and ignored by GIS.
    use_fedcm_for_button: false,
    itp_support: true,
  });
  googleIdentityReady = true;
  return true;
}

function renderGoogleButton() {
  const host = document.getElementById("google-signin-btn");
  const wrap = document.getElementById("auth-google-wrap");
  if (!host || !googleClientId || !window.google?.accounts?.id) return false;
  if (!googleIdentityReady || !googleNonce) {
    if (!initGoogleIdentity()) return false;
  }
  // GIS measures the host while rendering. It must be visible — the default
  // CSS hides #google-signin-btn until .has-gis, which produced a dead button.
  if (wrap) wrap.classList.add("has-gis");
  host.innerHTML = "";
  const paint = () => {
    if (!host.isConnected || !window.google?.accounts?.id) return;
    const width = Math.max(
      280,
      Math.min(400, Math.floor((wrap?.clientWidth || host.parentElement?.clientWidth || 320))),
    );
    window.google.accounts.id.renderButton(host, {
      theme: "outline",
      size: "large",
      text: "continue_with",
      shape: "rectangular",
      width,
      logo_alignment: "left",
    });
  };
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(paint);
  } else {
    paint();
  }
  return true;
}

async function prepareGoogleSignIn() {
  const btn = document.getElementById("auth-google-btn");
  const wrap = document.getElementById("auth-google-wrap");
  if (wrap) wrap.classList.remove("has-gis");
  // Always refresh nonce before initialize — GIS binds it into the ID token.
  let clientId = "";
  try {
    clientId = await refreshGoogleAuthConfigResilient();
  } catch (e) {
    clientId = await ensureGoogleClientId();
  }
  if (!clientId) {
    setMsg(authGoogleMsg, "Google sign-in is not configured yet.", "error");
    if (btn) btn.disabled = true;
    return false;
  }
  if (!googleNonce) {
    setMsg(authGoogleMsg, "Could not start a secure Google sign-in session. Refresh and try again.", "error");
    return false;
  }
  if (btn) btn.disabled = false;
  const ready = await waitForGoogleSdk();
  if (!ready) {
    setMsg(authGoogleMsg, "Could not load Google sign-in. Check your connection and try again.", "error");
    return false;
  }
  setMsg(authGoogleMsg, "");
  googleIdentityReady = false;
  return renderGoogleButton();
}

/** Prefetch GIS + client ID so the modal button appears immediately. */
function warmGoogleSignIn() {
  if (currentUser || googleWarmupPromise) return googleWarmupPromise;
  googleWarmupPromise = (async () => {
    try {
      const clientId = await ensureGoogleClientId();
      if (!clientId) return false;
      await waitForGoogleSdk();
      return true;
    } catch (e) {
      return false;
    }
  })();
  return googleWarmupPromise;
}

async function startGoogleSignIn() {
  // Fallback path when the official GIS button did not render.
  setMsg(authGoogleMsg, "Loading Google sign-in\u2026");
  const ok = await prepareGoogleSignIn();
  if (!ok) return;
  const wrap = document.getElementById("auth-google-wrap");
  if (wrap?.classList.contains("has-gis")) {
    setMsg(authGoogleMsg, "Click Continue with Google above to choose an account.");
    return;
  }
  setMsg(authGoogleMsg, "Choose your Google account (stay on this tab).");
  try {
    window.google.accounts.id.prompt(notification => {
      if (notification.isNotDisplayed() || notification.isSkippedMoment()) {
        setMsg(
          authGoogleMsg,
          "Google prompt was blocked. Allow sign-in for this site, then try again — or use email/password.",
          "error"
        );
      }
    });
  } catch (e) {
    setMsg(authGoogleMsg, "Google sign-in is unavailable right now. Use email/password, or refresh the page.", "error");
  }
}

const AUTH_SOFT_PROMPT_KEY = "authSoftPromptSeen";
let authModalSoft = false;

function authSoftPromptSeen() {
  try { return localStorage.getItem(AUTH_SOFT_PROMPT_KEY) === "1"; } catch (e) { return false; }
}

function markAuthSoftPromptSeen() {
  try { localStorage.setItem(AUTH_SOFT_PROMPT_KEY, "1"); } catch (e) { /* ignore */ }
}

function closeAuthModal({ fromSoftDismiss = false } = {}) {
  if (fromSoftDismiss || authModalSoft) markAuthSoftPromptSeen();
  authModalSoft = false;
  authModal.classList.remove("open");
  setGoogleSigningIn(false);
}

async function openAuthModal(opts = {}) {
  // Soft prompts (e.g. eval login gate) show at most once until the user
  // dismisses them; explicit Sign in / gated actions always open.
  if (opts.soft) {
    if (authSoftPromptSeen()) return;
    if (authModal.classList.contains("open")) return;
    authModalSoft = true;
    // Persist immediately so eval refreshes cannot reopen this modal.
    markAuthSoftPromptSeen();
  } else {
    authModalSoft = false;
  }
  if (!opts.keepMessages) {
    setMsg(authMsg, "");
    setMsg(authGoogleMsg, "");
  }
  setGoogleSigningIn(false);
  authModal.classList.add("open");
  const btn = document.getElementById("auth-google-btn");
  if (btn) btn.disabled = false;
  // Warm in parallel so SDK is usually ready when we render.
  warmGoogleSignIn();
  await prepareGoogleSignIn();
}

authOpenBtn.onclick = () => openAuthModal();
document.getElementById("auth-google-btn").onclick = () => startGoogleSignIn();
document.getElementById("auth-login-btn").onclick = () => submitAuth("/api/login");
document.getElementById("auth-register-btn").onclick = () => submitAuth("/api/register");
document.getElementById("auth-cancel-btn").onclick = () => closeAuthModal({ fromSoftDismiss: true });
document.getElementById("auth-password").addEventListener("keydown", e => {
  if (e.key === "Enter") submitAuth("/api/login");
});

userMenuBtn.onclick = e => {
  e.stopPropagation();
  userMenu.classList.toggle("open");
};
document.addEventListener("click", e => {
  if (!userMenu.contains(e.target) && e.target !== userMenuBtn) {
    userMenu.classList.remove("open");
  }
});

document.getElementById("menu-profile").onclick = openProfile;
document.getElementById("menu-logout").onclick = logout;
document.getElementById("profile-back").onclick = closeProfile;
document.getElementById("profile-logout-btn").onclick = logout;

document.getElementById("profile-save-btn").onclick = async () => {
  setMsg(profileMsg, "Saving\u2026");
  try {
    const user = await api("/api/me", "PUT", {
      chesscom_username: document.getElementById("profile-chesscom").value.trim(),
      lichess_username: document.getElementById("profile-lichess").value.trim(),
    });
    setUser(user);
    setMsg(profileMsg, "Profile saved.", "ok");
  } catch (e) {
    setMsg(profileMsg, e.message, "error");
  }
};

document.getElementById("profile-analyze-btn").onclick = () => {
  closeProfile();
  analyzeMe();
};

document.getElementById("profile-pw-btn").onclick = async () => {
  const current_password = document.getElementById("profile-pw-current").value;
  const new_password = document.getElementById("profile-pw-new").value;
  setMsg(profilePwMsg, "Updating\u2026");
  try {
    await api("/api/me/password", "POST", { current_password, new_password });
    document.getElementById("profile-pw-current").value = "";
    document.getElementById("profile-pw-new").value = "";
    setMsg(profilePwMsg, "Password updated.", "ok");
  } catch (e) {
    setMsg(profilePwMsg, e.message, "error");
  }
};

authModal.addEventListener("click", e => {
  if (e.target === authModal) closeAuthModal({ fromSoftDismiss: true });
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && authModal?.classList.contains("open")) {
    closeAuthModal({ fromSoftDismiss: true });
  }
});

(async function restoreSession() {
  try {
    setUser(await api("/api/me", "GET"));
  } catch (e) {
    setUser(null);
  }
  // Prefetch Google Identity so Continue with Google is ready when the modal opens.
  if (!currentUser) warmGoogleSignIn();
  // After session restore so admin logins are not counted as visits.
  trackVisit();
  initGoogleAnalytics();
  try {
    const cfg = await fetchSiteConfig();
    initProductAnalytics(cfg, () => currentUser);
  } catch (e) { /* best-effort */ }
})();

/* ---------- Google Analytics (GA4, optional) ----------
 * Off unless the server has GA_MEASUREMENT_ID configured (see .env.example).
 * Loaded dynamically (rather than a static <head> snippet) so the ID can be
 * set per-deploy via an env var instead of being hardcoded in committed HTML,
 * consistent with how GOOGLE_CLIENT_ID/STRIPE_* are handled in this app. */

async function initGoogleAnalytics() {
  try {
    if (currentUser?.can_view_analytics) return; // exclude the site admin, like self-hosted stats
    const cfg = await fetchSiteConfig();
    const id = (cfg.ga_measurement_id || "").trim();
    if (!id || window.gtag) return;
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag("js", new Date());
    window.gtag("config", id);
    const script = document.createElement("script");
    script.async = true;
    script.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(id);
    document.head.appendChild(script);
  } catch (e) { /* best-effort; never block the app on analytics */ }
}

/* ---------- Site visit analytics (self-hosted) ---------- */

function analyticsSessionId() {
  try {
    let sid = sessionStorage.getItem("oe_sid");
    if (!sid) {
      sid = (crypto.randomUUID && crypto.randomUUID()) ||
        ("s" + Math.random().toString(36).slice(2) + Date.now().toString(36));
      sessionStorage.setItem("oe_sid", sid);
    }
    return sid;
  } catch (e) {
    return "anon" + Date.now().toString(36);
  }
}

async function trackVisit() {
  try {
    // Don't count the admin account — only other visitors matter.
    if (currentUser?.can_view_analytics) return;
    await ensureApiBase();
    const path = location.pathname || "/";
    const flag = "oe_hit_" + path;
    if (sessionStorage.getItem(flag)) return;
    sessionStorage.setItem(flag, "1");
    fetch(apiUrl("/api/analytics/hit"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: analyticsSessionId(),
        path,
      }),
      credentials: "include",
      keepalive: true,
    }).catch(() => {});
  } catch (e) { /* ignore */ }
}

async function loadAnalyticsSummary() {
  const msg = document.getElementById("analytics-msg");
  const list = document.getElementById("analytics-search-list");
  const usersList = document.getElementById("analytics-users-list");
  setMsg(msg, "");
  try {
    const data = await api("/api/analytics/summary", "GET");
    const set = (id, n) => {
      const el = document.getElementById(id);
      if (el) el.textContent = String(n ?? 0);
    };
    set("analytics-account-total", data.accounts?.total);
    set("analytics-today-sessions", data.today?.sessions);
    set("analytics-today-views", data.today?.pageviews);
    set("analytics-all-sessions", data.all_time?.sessions);
    set("analytics-all-views", data.all_time?.pageviews);

    if (usersList) {
      const users = data.accounts?.users || [];
      if (!users.length) {
        usersList.innerHTML = `<div class="analytics-user-row"><span class="meta">No accounts yet.</span></div>`;
      } else {
        usersList.innerHTML = users.map(u => {
          const when = u.created_at
            ? new Date(u.created_at * 1000).toLocaleDateString()
            : "";
          const name = (u.display_name || "").trim();
          const meta = [
            `#${u.id}`,
            u.auth_provider || "password",
            u.plan || "free",
            when,
            name,
          ].filter(Boolean).join(" · ");
          return `<div class="analytics-user-row" data-user-id="${u.id}">` +
            `<div>` +
            `<div class="email">${escapeHtml(u.email || "")}</div>` +
            `<div class="meta">${escapeHtml(meta)}</div>` +
            `</div>` +
            `<button type="button" class="ghost admin-set-pw-btn" data-user-id="${u.id}" data-email="${escapeHtml(u.email || "")}">Set password</button>` +
            `</div>`;
        }).join("");
        usersList.querySelectorAll(".admin-set-pw-btn").forEach(btn => {
          btn.addEventListener("click", () => adminSetUserPassword(btn));
        });
      }
    }

    if (list) {
      const rows = data.searches?.recent || [];
      const total = data.searches?.total ?? rows.length;
      if (!rows.length) {
        list.innerHTML = `<div class="analytics-search-row"><span class="meta">No searches logged yet (${total} total).</span></div>`;
      } else {
        list.innerHTML = rows.map(r => {
          const when = r.created_at
            ? new Date(r.created_at * 1000).toLocaleString()
            : "";
          const kind = r.kind === "me" ? "my games" : (r.source || "search");
          return `<div class="analytics-search-row">` +
            `<span class="q">${escapeHtml(r.query || "")}</span>` +
            `<span class="meta">${escapeHtml(kind)} · ${escapeHtml(when)}</span>` +
            `</div>`;
        }).join("") +
          (total > rows.length
            ? `<div class="analytics-search-row"><span class="meta">Showing ${rows.length} of ${total}</span></div>`
            : "");
      }
    }
  } catch (e) {
    setMsg(msg, e.message || "Could not load stats", "error");
  }
}

async function adminSetUserPassword(btn) {
  const msg = document.getElementById("analytics-msg");
  const userId = btn.getAttribute("data-user-id");
  const email = btn.getAttribute("data-email") || "user";
  const temp = window.prompt(
    `Temporary password for ${email} (min 8 chars). Share it privately; they can change it in Profile.`,
    ""
  );
  if (temp == null) return;
  const password = String(temp).trim();
  if (password.length < 8) {
    setMsg(msg, "Password must be at least 8 characters.", "error");
    return;
  }
  setMsg(msg, "Setting password…");
  try {
    await api(`/api/admin/users/${userId}/password`, "POST", { new_password: password });
    setMsg(msg, `Temporary password set for ${email}. Tell them out of band.`, "ok");
  } catch (e) {
    setMsg(msg, e.message || "Could not set password", "error");
  }
}

async function exportUsersCsv() {
  const msg = document.getElementById("analytics-msg");
  setMsg(msg, "Downloading users…");
  try {
    await ensureApiBase();
    const headers = {};
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const resp = await fetchApi("/api/analytics/users.csv", { headers });
    if (!resp.ok) {
      const data = await readJson(resp).catch(() => ({}));
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const cd = resp.headers.get("Content-Disposition") || "";
    const match = /filename="?([^"]+)"?/i.exec(cd);
    const filename = match?.[1] || "users.csv";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    setMsg(msg, `Downloaded ${filename}.`, "ok");
  } catch (e) {
    setMsg(msg, e.message || "Could not download users CSV", "error");
  }
}

document.getElementById("analytics-export-users-btn")?.addEventListener(
  "click",
  () => exportUsersCsv()
);

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ---------- Chess.com-style board mechanics ---------- */

const MOVE_ANIM_MS = 150;
const DRAW_COLOR = "#15781B";
const DRAW_COLOR_ALT = "#FFC107";

let selected = null;
let legalDests = new Map();
let selectingPremove = false;
let premove = null;           // { from, to, promotion? }
let userShapes = [];          // { kind:'arrow'|'circle', from, to?, color }
let drawStroke = null;        // { from, color, shift }
let promoEl = null;
let promoShade = null;
let pendingPromo = null;      // { from, to, isPremove }
let justSelected = false;
let suppressBoardClick = false;
let drag = null;
let drawLayerEl = null;
let audioCtx = null;

function positionAtPly() {
  return tryLoadChess(currentFen());
}

function userColorChar() {
  return state.repColor === "black" ? "b" : "w";
}

function isUsersTurn(pos) {
  if (!pos) return false;
  if (!state.practiceMode) return true;
  return pos.turn() === userColorChar();
}

function ensureAudio() {
  if (!audioCtx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    audioCtx = new AC();
  }
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function tone(freq, dur, type, gain, when) {
  const ctx = ensureAudio();
  if (!ctx) return;
  const t0 = when ?? ctx.currentTime;
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = type || "sine";
  osc.frequency.value = freq;
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(gain, t0 + 0.01);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  osc.connect(g);
  g.connect(ctx.destination);
  osc.start(t0);
  osc.stop(t0 + dur + 0.02);
}

function noiseBurst(dur, gain) {
  const ctx = ensureAudio();
  if (!ctx) return;
  const n = Math.floor(ctx.sampleRate * dur);
  const buf = ctx.createBuffer(1, n, ctx.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < n; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / n);
  const src = ctx.createBufferSource();
  src.buffer = buf;
  const g = ctx.createGain();
  const f = ctx.createBiquadFilter();
  f.type = "lowpass";
  f.frequency.value = 1800;
  g.gain.value = gain;
  src.connect(f);
  f.connect(g);
  g.connect(ctx.destination);
  src.start();
}

function playSfx(kind) {
  try {
    const ctx = ensureAudio();
    if (!ctx) return;
    const t = ctx.currentTime;
    if (kind === "move") {
      tone(420, 0.06, "triangle", 0.045, t);
      noiseBurst(0.03, 0.03);
    } else if (kind === "capture") {
      noiseBurst(0.07, 0.07);
      tone(280, 0.08, "square", 0.035, t);
    } else if (kind === "check") {
      tone(520, 0.07, "square", 0.04, t);
      tone(780, 0.1, "square", 0.03, t + 0.07);
    } else if (kind === "castle") {
      tone(360, 0.05, "triangle", 0.04, t);
      tone(440, 0.06, "triangle", 0.035, t + 0.06);
    } else if (kind === "promote") {
      tone(500, 0.05, "sine", 0.04, t);
      tone(650, 0.06, "sine", 0.035, t + 0.05);
      tone(820, 0.08, "sine", 0.03, t + 0.1);
    } else if (kind === "game-end") {
      tone(300, 0.12, "sine", 0.05, t);
      tone(220, 0.18, "sine", 0.045, t + 0.12);
      tone(160, 0.22, "sine", 0.04, t + 0.28);
    }
  } catch (_) { /* autoplay / missing AudioContext */ }
}

function soundKindForMove(played, fenAfter) {
  const after = tryLoadChess(fenAfter);
  if (after?.isCheckmate() || after?.isDraw()) return "game-end";
  if (after?.isCheck()) return "check";
  const flags = played?.flags || "";
  if (flags.includes("p")) return "promote";
  if (flags.includes("k") || flags.includes("q")) return "castle";
  if (played?.captured) return "capture";
  return "move";
}

function playMoveSound(played, fenAfter) {
  playSfx(soundKindForMove(played, fenAfter));
}

function kingSquareInCheck(fen) {
  const pos = tryLoadChess(fen);
  if (!pos || !pos.isCheck()) return null;
  const turn = pos.turn();
  const map = boardMapFromFen(fen);
  const needle = turn === "w" ? "K" : "k";
  for (const [sq, pc] of Object.entries(map)) {
    if (pc === needle) return sq;
  }
  return null;
}

function applyCheckHighlight() {
  boardEl.querySelectorAll(".sq.check").forEach(el => el.classList.remove("check"));
  const sq = kingSquareInCheck(currentFen());
  if (!sq) return;
  const el = boardEl.querySelector(`.sq[data-square="${sq}"]`);
  if (el) el.classList.add("check");
}

function applyPremoveHighlight() {
  boardEl.querySelectorAll(".premove-from, .premove-to")
    .forEach(el => el.classList.remove("premove-from", "premove-to"));
  if (!premove) return;
  const a = boardEl.querySelector(`.sq[data-square="${premove.from}"]`);
  const b = boardEl.querySelector(`.sq[data-square="${premove.to}"]`);
  if (a) a.classList.add("premove-from");
  if (b) b.classList.add("premove-to");
}

function setPremove(from, to, promotion) {
  premove = { from, to, promotion: promotion || undefined };
  applyPremoveHighlight();
}

function clearPremove() {
  premove = null;
  applyPremoveHighlight();
}

function sqCenterPx(sqName) {
  const el = boardEl.querySelector(`.sq[data-square="${sqName}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, size: r.width };
}

function pieceSrcForMove(played) {
  const color = played.color === "w" ? "w" : "b";
  return `/pieces/${color}${played.piece}.png`;
}

function animatePieceFlight(from, to, src, ms = MOVE_ANIM_MS) {
  return new Promise(resolve => {
    const fromEl = boardEl.querySelector(`.sq[data-square="${from}"]`);
    const toEl = boardEl.querySelector(`.sq[data-square="${to}"]`);
    if (!fromEl || !toEl) { resolve(); return; }
    const fr = fromEl.getBoundingClientRect();
    const tr = toEl.getBoundingClientRect();
    document.getElementById("piece-fly")?.remove();
    const fly = document.createElement("img");
    fly.id = "piece-fly";
    fly.src = src;
    const size = Math.min(fr.width, fr.height);
    Object.assign(fly.style, {
      width: size + "px",
      height: size + "px",
      left: fr.left + "px",
      top: fr.top + "px",
      transition: `left ${ms}ms cubic-bezier(.2,.7,.3,1), top ${ms}ms cubic-bezier(.2,.7,.3,1)`,
    });
    document.body.appendChild(fly);
    const destImg = toEl.querySelector("img.piece");
    if (destImg) destImg.classList.add("anim-hide");
    requestAnimationFrame(() => {
      fly.style.left = tr.left + "px";
      fly.style.top = tr.top + "px";
    });
    setTimeout(() => {
      fly.remove();
      if (destImg) destImg.classList.remove("anim-hide");
      resolve();
    }, ms + 16);
  });
}

function animateThenRefresh(played, after) {
  const fromEl = boardEl.querySelector(`.sq[data-square="${played.from}"] img.piece`);
  const src = fromEl?.src || pieceSrcForMove(played);
  // Capture from/to rects before re-render
  const fromRect = boardEl.querySelector(`.sq[data-square="${played.from}"]`)?.getBoundingClientRect();
  const toRect = boardEl.querySelector(`.sq[data-square="${played.to}"]`)?.getBoundingClientRect();

  // Chess.com also slides the rook on castling
  let rookFlight = null;
  const flags = played.flags || "";
  if (flags.includes("k") || flags.includes("q")) {
    const rank = played.from[1];
    const rookFrom = flags.includes("k") ? ("h" + rank) : ("a" + rank);
    const rookTo = flags.includes("k") ? ("f" + rank) : ("d" + rank);
    const rf = boardEl.querySelector(`.sq[data-square="${rookFrom}"]`)?.getBoundingClientRect();
    const rt = boardEl.querySelector(`.sq[data-square="${rookTo}"]`)?.getBoundingClientRect();
    const rookImg = boardEl.querySelector(`.sq[data-square="${rookFrom}"] img.piece`);
    if (rf && rt && rookImg) {
      rookFlight = { fromRect: rf, toRect: rt, src: rookImg.src, to: rookTo };
    }
  }

  refreshBoard();
  if (!fromRect || !toRect) {
    after?.();
    return;
  }

  const flights = [];
  const spawn = (fr, tr, imgSrc, hideSq) => {
    const destImg = boardEl.querySelector(`.sq[data-square="${hideSq}"] img.piece`);
    if (destImg) destImg.classList.add("anim-hide");
    const fly = document.createElement("img");
    fly.className = "piece-fly-anim";
    fly.src = imgSrc;
    const size = Math.min(fr.width, fr.height);
    Object.assign(fly.style, {
      position: "fixed",
      zIndex: "90",
      pointerEvents: "none",
      width: size + "px",
      height: size + "px",
      left: fr.left + "px",
      top: fr.top + "px",
      transition: `left ${MOVE_ANIM_MS}ms cubic-bezier(.2,.7,.3,1), top ${MOVE_ANIM_MS}ms cubic-bezier(.2,.7,.3,1)`,
    });
    document.body.appendChild(fly);
    requestAnimationFrame(() => {
      fly.style.left = tr.left + "px";
      fly.style.top = tr.top + "px";
    });
    flights.push({ fly, destImg });
  };

  document.querySelectorAll(".piece-fly-anim, #piece-fly").forEach(el => el.remove());
  spawn(fromRect, toRect, src, played.to);
  if (rookFlight) spawn(rookFlight.fromRect, rookFlight.toRect, rookFlight.src, rookFlight.to);

  setTimeout(() => {
    for (const { fly, destImg } of flights) {
      fly.remove();
      if (destImg) destImg.classList.remove("anim-hide");
    }
    after?.();
  }, MOVE_ANIM_MS + 16);
}

function ensureDrawLayer() {
  if (drawLayerEl && boardEl.contains(drawLayerEl)) return drawLayerEl;
  drawLayerEl = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  drawLayerEl.id = "draw-layer";
  drawLayerEl.setAttribute("viewBox", "0 0 8 8");
  drawLayerEl.setAttribute("preserveAspectRatio", "none");
  boardEl.appendChild(drawLayerEl);
  return drawLayerEl;
}

function clearUserShapes() {
  userShapes = [];
  renderUserShapes();
}

function renderUserShapes() {
  const svg = ensureDrawLayer();
  svg.replaceChildren();
  const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
  const colors = [...new Set(userShapes.map(s => s.color || DRAW_COLOR))];
  for (const color of colors) {
    const id = "draw-ah-" + color.replace(/[^a-zA-Z0-9]/g, "");
    const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
    marker.setAttribute("id", id);
    marker.setAttribute("markerWidth", "2.8");
    marker.setAttribute("markerHeight", "2.8");
    marker.setAttribute("refX", "1.4");
    marker.setAttribute("refY", "1.4");
    marker.setAttribute("orient", "auto");
    marker.setAttribute("markerUnits", "strokeWidth");
    const head = document.createElementNS("http://www.w3.org/2000/svg", "path");
    head.setAttribute("d", "M0,0 L2.8,1.4 L0,2.8 Z");
    head.setAttribute("fill", color);
    marker.appendChild(head);
    defs.appendChild(marker);
  }
  svg.appendChild(defs);

  for (const shape of userShapes) {
    const color = shape.color || DRAW_COLOR;
    if (shape.kind === "circle") {
      const c = sqToCenter(shape.from, state.orientation);
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", c.x);
      circle.setAttribute("cy", c.y);
      circle.setAttribute("r", "0.42");
      circle.setAttribute("fill", "none");
      circle.setAttribute("stroke", color);
      circle.setAttribute("stroke-width", "0.12");
      circle.setAttribute("opacity", "0.85");
      svg.appendChild(circle);
    } else if (shape.kind === "arrow") {
      const a = sqToCenter(shape.from, state.orientation);
      const b = sqToCenter(shape.to, state.orientation);
      const dx = b.x - a.x, dy = b.y - a.y;
      const len = Math.hypot(dx, dy) || 1;
      const shrink = 0.28;
      const x1 = a.x + (dx / len) * shrink;
      const y1 = a.y + (dy / len) * shrink;
      const x2 = b.x - (dx / len) * shrink;
      const y2 = b.y - (dy / len) * shrink;
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", x1);
      line.setAttribute("y1", y1);
      line.setAttribute("x2", x2);
      line.setAttribute("y2", y2);
      line.setAttribute("stroke", color);
      line.setAttribute("stroke-width", "0.18");
      line.setAttribute("stroke-linecap", "round");
      line.setAttribute("opacity", "0.85");
      line.setAttribute("marker-end", `url(#draw-ah-${color.replace(/[^a-zA-Z0-9]/g, "")})`);
      svg.appendChild(line);
    }
  }
}

function toggleUserShape(shape) {
  const key = shape.kind === "circle"
    ? `c:${shape.from}:${shape.color}`
    : `a:${shape.from}:${shape.to}:${shape.color}`;
  const idx = userShapes.findIndex(s => {
    const k = s.kind === "circle"
      ? `c:${s.from}:${s.color}`
      : `a:${s.from}:${s.to}:${s.color}`;
    return k === key;
  });
  if (idx >= 0) userShapes.splice(idx, 1);
  else userShapes.push(shape);
  renderUserShapes();
}

function clearSelection(opts = {}) {
  selected = null;
  legalDests = new Map();
  selectingPremove = false;
  hidePromoPicker();
  boardEl.querySelectorAll(".sel, .dest, .capture, .premove-dest, .drag-over")
    .forEach(el => el.classList.remove("sel", "dest", "capture", "premove-dest", "drag-over"));
  if (!opts.keepPremove) { /* selection clear doesn't clear premove by default on refresh */ }
  if (!opts.keepShapes) { /* shapes cleared explicitly on left-click / move */ }
}

function premoveCandidates(sqName) {
  const parts = currentFen().split(/\s+/);
  parts[1] = userColorChar();
  const pos = tryLoadChess(parts.join(" "));
  if (!pos) return [];
  return pos.moves({ square: sqName, verbose: true });
}

function markMovablePieces() {
  boardEl.querySelectorAll(".sq.movable").forEach(el => el.classList.remove("movable"));
  if (state.editMode) {
    for (const sq of boardEl.querySelectorAll(".sq")) {
      if (sq.querySelector("img.piece")) sq.classList.add("movable");
    }
    return;
  }
  const pos = positionAtPly();
  if (!pos || pos.isGameOver()) return;

  if (state.practiceMode) {
    // Own pieces always movable (live move or premove), matching Chess.com play.
    const uc = userColorChar();
    for (const sq of boardEl.querySelectorAll(".sq")) {
      const pc = pos.get(sq.dataset.square);
      if (pc && pc.color === uc) sq.classList.add("movable");
    }
    return;
  }

  const turn = pos.turn();
  for (const sq of boardEl.querySelectorAll(".sq")) {
    const pc = pos.get(sq.dataset.square);
    if (pc && pc.color === turn) sq.classList.add("movable");
  }
}

function paintDests(moves, asPremove) {
  for (const m of moves) {
    legalDests.set(m.to, m);
    const el = boardEl.querySelector(`.sq[data-square="${m.to}"]`);
    if (!el) continue;
    el.classList.add("dest");
    if (asPremove) el.classList.add("premove-dest");
    if (m.captured || (m.flags && m.flags.includes("e"))) el.classList.add("capture");
  }
}

function selectSquare(sqName) {
  clearSelection({ keepPremove: true, keepShapes: true });
  if (state.editMode) {
    selected = sqName;
    const selEl = boardEl.querySelector(`.sq[data-square="${sqName}"]`);
    if (selEl) selEl.classList.add("sel");
    return true;
  }
  const pos = positionAtPly();
  if (!pos) return false;

  if (state.practiceMode) {
    const pc = pos.get(sqName);
    if (!pc || pc.color !== userColorChar()) return false;
    selected = sqName;
    selectingPremove = !isUsersTurn(pos);
    const moves = selectingPremove
      ? premoveCandidates(sqName)
      : pos.moves({ square: sqName, verbose: true });
    if (!moves.length && !selectingPremove) return false;
    const selEl = boardEl.querySelector(`.sq[data-square="${sqName}"]`);
    if (selEl) selEl.classList.add("sel");
    paintDests(moves, selectingPremove);
    return true;
  }

  const moves = pos.moves({ square: sqName, verbose: true });
  if (!moves.length) return false;
  selected = sqName;
  selectingPremove = false;
  const selEl = boardEl.querySelector(`.sq[data-square="${sqName}"]`);
  if (selEl) selEl.classList.add("sel");
  paintDests(moves, false);
  return true;
}

function tryMove(from, to, opts = {}) {
  if (state.editMode) {
    relocatePiece(from, to);
    clearSelection();
    return true;
  }
  const m = legalDests.get(to);
  if (!m && !opts.force) return false;

  if (selectingPremove || (state.practiceMode && positionAtPly() && !isUsersTurn(positionAtPly()))) {
    // Queue premove (Chess.com: one pending move)
    if (m?.promotion || opts.promotionNeeded) {
      showPromoPicker(from, to, { isPremove: true });
      return true;
    }
    clearSelection({ keepPremove: true, keepShapes: true });
    setPremove(from, to, opts.promotion);
    return true;
  }

  const move = m || legalDests.get(to);
  if (!move && !opts.promotion) return false;
  if ((move && move.promotion) || opts.promotionNeeded) {
    showPromoPicker(from, to, { isPremove: false });
    return true;
  }
  clearSelection({ keepPremove: true, keepShapes: true });
  makeUserMove(from, to, opts.promotion, { viaDrag: !!opts.viaDrag, animate: opts.animate });
  return true;
}

function hidePromoPicker() {
  if (promoEl) { promoEl.remove(); promoEl = null; }
  if (promoShade) { promoShade.remove(); promoShade = null; }
  pendingPromo = null;
}

function showPromoPicker(from, to, { isPremove = false } = {}) {
  hidePromoPicker();
  const pos = positionAtPly();
  // For premoves, side is the user; otherwise side to move
  const color = isPremove
    ? userColorChar()
    : (pos ? pos.turn() : "w");
  const destEl = boardEl.querySelector(`.sq[data-square="${to}"]`);
  if (!destEl) {
    if (isPremove) setPremove(from, to, "q");
    else makeUserMove(from, to, "q", { viaDrag: true });
    return;
  }

  pendingPromo = { from, to, isPremove };

  promoShade = document.createElement("div");
  promoShade.id = "promo-shade";
  promoShade.onclick = () => {
    hidePromoPicker();
    clearSelection({ keepPremove: true, keepShapes: true });
  };
  boardEl.appendChild(promoShade);

  // Chess.com: Q/N/R/B cover the promotion file squares (orientation-safe)
  const file = to[0];
  const promoRank = +to[1];
  const promotingWhite = color === "w";
  const ranks = promotingWhite
    ? [promoRank, promoRank - 1, promoRank - 2, promoRank - 3]
    : [promoRank, promoRank + 1, promoRank + 2, promoRank + 3];
  const pieces = ["q", "n", "r", "b"];

  promoEl = document.createElement("div");
  promoEl.id = "promo-picker";
  promoEl.style.background = "transparent";
  promoEl.style.boxShadow = "none";
  promoEl.style.width = "100%";
  promoEl.style.height = "100%";
  promoEl.style.left = "0";
  promoEl.style.top = "0";
  promoEl.style.display = "block";
  promoEl.style.pointerEvents = "none";

  const bRect = boardEl.getBoundingClientRect();
  pieces.forEach((p, i) => {
    const sqName = file + ranks[i];
    const cell = boardEl.querySelector(`.sq[data-square="${sqName}"]`) || destEl;
    const r = cell.getBoundingClientRect();
    const b = document.createElement("button");
    b.type = "button";
    b.style.pointerEvents = "auto";
    b.style.position = "absolute";
    b.style.left = (r.left - bRect.left) + "px";
    b.style.top = (r.top - bRect.top) + "px";
    b.style.width = r.width + "px";
    b.style.height = r.height + "px";
    const img = document.createElement("img");
    img.src = `/pieces/${color}${p}.png`;
    img.alt = p;
    b.appendChild(img);
    b.onclick = ev => {
      ev.stopPropagation();
      const { from: f, to: t, isPremove: pre } = pendingPromo || {};
      hidePromoPicker();
      if (pre) {
        setPremove(f, t, p);
        clearSelection({ keepPremove: true, keepShapes: true });
      } else {
        clearSelection({ keepPremove: true, keepShapes: true });
        makeUserMove(f, t, p, { viaDrag: true });
      }
    };
    promoEl.appendChild(b);
  });
  boardEl.appendChild(promoEl);
}

function tryExecutePremove() {
  if (!premove || !state.practiceMode) return false;
  const pos = positionAtPly();
  if (!pos || !isUsersTurn(pos) || pos.isGameOver()) {
    if (pos?.isGameOver()) clearPremove();
    return false;
  }
  const { from, to, promotion } = premove;
  const legal = pos.moves({ square: from, verbose: true });
  const hit = legal.find(m =>
    m.to === to && (!m.promotion || m.promotion === (promotion || "q"))
  );
  clearPremove();
  if (!hit) return false;
  makeUserMove(from, to, hit.promotion ? (promotion || "q") : undefined, { animate: true });
  return true;
}

function updatePracticeGameStatus() {
  const st = document.getElementById("rep-practice-status");
  if (!st || !state.practiceMode) return;
  const pos = positionAtPly();
  if (!pos) return;
  if (pos.isCheckmate()) {
    const winner = pos.turn() === "w" ? "Black" : "White";
    st.textContent = `Checkmate — ${winner} wins.`;
    return true;
  }
  if (pos.isStalemate()) { st.textContent = "Draw by stalemate."; return true; }
  if (pos.isThreefoldRepetition()) { st.textContent = "Draw by repetition."; return true; }
  if (pos.isInsufficientMaterial()) { st.textContent = "Draw — insufficient material."; return true; }
  if (pos.isDraw()) { st.textContent = "Draw."; return true; }
  return false;
}

/* click-to-move */
boardEl.addEventListener("click", e => {
  if (suppressBoardClick) { suppressBoardClick = false; return; }
  const sq = e.target.closest(".sq");
  if (!sq || !boardEl.contains(sq)) return;
  if (promoEl) { hidePromoPicker(); clearSelection({ keepPremove: true, keepShapes: true }); return; }

  // Left-click clears drawn arrows/circles (Chess.com)
  if (userShapes.length) clearUserShapes();

  const name = sq.dataset.square;

  if (state.editMode) {
    if (palettePiece) {
      placePieceOn(name, palettePiece);
      return;
    }
    if (selected) {
      if (name === selected) {
        if (justSelected) { justSelected = false; return; }
        clearSelection();
        return;
      }
      justSelected = false;
      const map = boardMapFromFen(currentFen());
      if (map[selected]) relocatePiece(selected, name);
      clearSelection();
      return;
    }
    if (boardMapFromFen(currentFen())[name]) selectSquare(name);
    return;
  }

  if (selected) {
    if (name === selected) {
      if (justSelected) { justSelected = false; return; }
      clearSelection({ keepPremove: true, keepShapes: true });
      return;
    }
    justSelected = false;
    if (tryMove(selected, name, { animate: true })) return;
    // clicking another of my pieces re-selects; otherwise deselect
    if (!selectSquare(name)) clearSelection({ keepPremove: true, keepShapes: true });
  } else {
    selectSquare(name);
  }
});

/* Right-click arrows & circles */
boardEl.addEventListener("contextmenu", e => {
  e.preventDefault();
});

boardEl.addEventListener("pointerdown", e => {
  if (e.button === 2) {
    const sq = e.target.closest(".sq");
    if (!sq || !boardEl.contains(sq)) return;
    drawStroke = {
      from: sq.dataset.square,
      color: e.shiftKey ? DRAW_COLOR_ALT : DRAW_COLOR,
    };
    e.preventDefault();
    return;
  }
  if (e.button !== 0 || promoEl) return;

  if (state.editMode) {
    const sq = e.target.closest(".sq");
    if (!sq || !boardEl.contains(sq)) return;
    const img = sq.querySelector("img.piece");
    if (!img) return;
    const wasSelected = selected === sq.dataset.square;
    selectSquare(sq.dataset.square);
    justSelected = !wasSelected;
    const ghost = makeGhostFromImg(img, e.clientX, e.clientY);
    drag = {
      from: sq.dataset.square,
      ghost,
      img,
      moved: false,
      piece: img.alt,
      fromPalette: false,
    };
    try { boardEl.setPointerCapture(e.pointerId); } catch (_) {}
    e.preventDefault();
    return;
  }

  const sq = e.target.closest(".sq");
  if (!sq || !sq.classList.contains("movable")) return;
  const img = sq.querySelector("img.piece");
  if (!img) return;
  const wasSelected = selected === sq.dataset.square;
  if (!selectSquare(sq.dataset.square)) return;
  justSelected = !wasSelected;

  const ghost = makeGhostFromImg(img, e.clientX, e.clientY);
  drag = { from: sq.dataset.square, ghost, img, moved: false, fromPalette: false };
  try { boardEl.setPointerCapture(e.pointerId); } catch (_) {}
  e.preventDefault();
});

boardEl.addEventListener("pointerup", e => {
  if (e.button !== 2 || !drawStroke) return;
  e.stopPropagation();
  const sq = e.target.closest(".sq");
  const to = sq && boardEl.contains(sq) ? sq.dataset.square : drawStroke.from;
  if (to === drawStroke.from) {
    toggleUserShape({ kind: "circle", from: to, color: drawStroke.color });
  } else {
    toggleUserShape({ kind: "arrow", from: drawStroke.from, to, color: drawStroke.color });
  }
  drawStroke = null;
});

document.addEventListener("pointerup", e => {
  if (e.button !== 2 || !drawStroke) return;
  // Released outside the board — treat as circle on start square
  toggleUserShape({ kind: "circle", from: drawStroke.from, color: drawStroke.color });
  drawStroke = null;
});

function makeGhostFromImg(img, clientX, clientY) {
  const rect = img.getBoundingClientRect();
  const ghost = img.cloneNode(true);
  ghost.id = "drag-ghost";
  // Chess.com: dragged piece is slightly larger than a square
  const size = Math.max(rect.width, 36) * 1.08;
  ghost.style.width = size + "px";
  ghost.style.height = size + "px";
  document.body.appendChild(ghost);
  ghost.style.left = clientX - ghost.offsetWidth / 2 + "px";
  ghost.style.top = clientY - ghost.offsetHeight / 2 + "px";
  return ghost;
}

document.querySelectorAll(".piece-bank").forEach(bank => {
  bank.addEventListener("pointerdown", e => {
    if (!state.editMode || e.button !== 0) return;
    const btn = e.target.closest(".bank-piece");
    if (!btn) return;
    const img = btn.querySelector("img");
    if (!img) return;
    setPalettePiece(btn.dataset.piece);
    const ghost = makeGhostFromImg(img, e.clientX, e.clientY);
    drag = {
      from: null,
      ghost,
      img: null,
      moved: false,
      piece: btn.dataset.piece,
      fromPalette: true,
    };
    e.preventDefault();
  });
});

function moveGhost(e) {
  drag.ghost.style.left = e.clientX - drag.ghost.offsetWidth / 2 + "px";
  drag.ghost.style.top = e.clientY - drag.ghost.offsetHeight / 2 + "px";
}

function updateDragOver(e) {
  boardEl.querySelectorAll(".sq.drag-over").forEach(el => el.classList.remove("drag-over"));
  const target = document.elementFromPoint(e.clientX, e.clientY);
  const sq = target && target.closest(".sq");
  if (sq && boardEl.contains(sq) && (sq.classList.contains("dest") || sq.dataset.square === drag?.from)) {
    sq.classList.add("drag-over");
  }
}

document.addEventListener("pointermove", e => {
  if (drag) {
    if (!drag.moved) {
      drag.moved = true;
      if (drag.img) drag.img.classList.add("dragging");
    }
    moveGhost(e);
    updateDragOver(e);
  }
});

document.addEventListener("pointerup", e => {
  if (e.button === 2) return;
  if (!drag) return;
  const { from, ghost, img, moved, piece, fromPalette } = drag;
  drag = null;
  ghost.remove();
  if (img) img.classList.remove("dragging");
  boardEl.querySelectorAll(".sq.drag-over").forEach(el => el.classList.remove("drag-over"));

  if (state.editMode) {
    if (!moved && !fromPalette) return;
    if (moved) suppressBoardClick = true;
    const target = document.elementFromPoint(e.clientX, e.clientY);
    const sq = target && target.closest(".sq");
    const onBoard = sq && boardEl.contains(sq);
    if (fromPalette) {
      if (onBoard) placePieceOn(sq.dataset.square, piece);
      return;
    }
    if (onBoard) {
      if (sq.dataset.square !== from) relocatePiece(from, sq.dataset.square);
      clearSelection();
    } else {
      removePieceAt(from);
      clearSelection();
    }
    return;
  }

  if (!moved) return;
  suppressBoardClick = true;
  const target = document.elementFromPoint(e.clientX, e.clientY);
  const sq = target && target.closest(".sq");
  if (sq && boardEl.contains(sq) && sq.dataset.square !== from) {
    if (!tryMove(from, sq.dataset.square, { viaDrag: true })) {
      clearSelection({ keepPremove: true, keepShapes: true });
    }
  } else {
    // Illegal / off-board drop: snap-back (selection kept if still selected)
    clearSelection({ keepPremove: true, keepShapes: true });
  }
});

document.addEventListener("pointercancel", () => {
  if (!drag) return;
  drag.ghost.remove();
  if (drag.img) drag.img.classList.remove("dragging");
  drag = null;
  boardEl.querySelectorAll(".sq.drag-over").forEach(el => el.classList.remove("drag-over"));
});

/* Unlock audio on first gesture */
["pointerdown", "keydown"].forEach(ev => {
  document.addEventListener(ev, () => ensureAudio(), { once: true, passive: true });
});

/* ---------- my repertoire ---------- */

const REP_LS = "myRepertoire";
const UCI_FIRST_LABEL = {
  e2e4: "1.e4", d2d4: "1.d4", c2c4: "1.c4", g1f3: "1.Nf3",
  b1c3: "1.Nc3", f2f4: "1.f4", g2g3: "1.g3", b2b3: "1.b3",
  b2b4: "1.b4", e2e3: "1.e3", a2a3: "1.a3", a2a4: "1.a4",
  g2g4: "1.g4", f2f3: "1.f3", h2h4: "1.h4", g1h3: "1.Nh3",
};
let repTreeBuilt = false;
let practiceBusy = false;
let practiceTimer = null;

function normalizeRepData(data) {
  const out = { white: [], black: [] };
  for (const color of ["white", "black"]) {
    const list = Array.isArray(data?.[color]) ? data[color] : [];
    const seen = new Set();
    for (const e of list) {
      if (!e || !e.play || !e.name) continue;
      if (seen.has(e.play)) continue;
      seen.add(e.play);
      out[color].push({
        play: String(e.play),
        name: String(e.name),
        eco: String(e.eco || ""),
      });
    }
  }
  return out;
}

function loadRepertoireFromLocal() {
  try {
    state.myRepertoire = normalizeRepData(JSON.parse(localStorage.getItem(REP_LS) || "{}"));
  } catch (e) {
    state.myRepertoire = { white: [], black: [] };
  }
  renderPracticeList();
  if (repTreeBuilt) updateRepTreeButtons();
}

function guestRepertoireSnapshot() {
  try {
    return normalizeRepData(JSON.parse(localStorage.getItem(REP_LS) || "{}"));
  } catch (e) {
    return { white: [], black: [] };
  }
}

function mergeRepertoire(guest, server) {
  // Union by color+play; server wins on name/eco conflicts.
  const out = { white: [], black: [] };
  for (const color of ["white", "black"]) {
    const byPlay = new Map();
    for (const e of guest[color] || []) byPlay.set(e.play, e);
    for (const e of server[color] || []) byPlay.set(e.play, e);
    out[color] = [...byPlay.values()];
  }
  return normalizeRepData(out);
}

function repertoireCount(data) {
  return (data.white?.length || 0) + (data.black?.length || 0);
}

async function loadRepertoireFromServer() {
  const guest = guestRepertoireSnapshot();
  try {
    const data = await api("/api/me/repertoire", "GET");
    const server = normalizeRepData(data);
    if (repertoireCount(guest) > 0) {
      const merged = mergeRepertoire(guest, server);
      state.myRepertoire = merged;
      localStorage.setItem(REP_LS, JSON.stringify(merged));
      if (repertoireCount(merged) !== repertoireCount(server) ||
          JSON.stringify(merged) !== JSON.stringify(server)) {
        try {
          await api("/api/me/repertoire", "PUT", merged);
        } catch (e) {
          console.warn("repertoire merge sync failed", e);
        }
      }
    } else {
      state.myRepertoire = server;
      localStorage.setItem(REP_LS, JSON.stringify(server));
    }
  } catch (e) {
    loadRepertoireFromLocal();
    return;
  }
  renderPracticeList();
  if (repTreeBuilt) updateRepTreeButtons();
  if (state.repertoireOpen) scheduleRepSlipupsRefresh();
}

async function persistRepertoire() {
  localStorage.setItem(REP_LS, JSON.stringify(state.myRepertoire));
  if (state.repertoireOpen) {
    repSlipupsScanKey = "";
    scheduleRepSlipupsRefresh();
  }
  if (!currentUser) return;
  try {
    await api("/api/me/repertoire", "PUT", state.myRepertoire);
  } catch (e) {
    console.warn("repertoire sync failed", e);
  }
}

function familyName(name) {
  return (name || "").split(":")[0].trim() || name || "Opening";
}

function playCompatible(currentParts, openingPlay) {
  const op = String(openingPlay || "").split(",").filter(Boolean);
  const n = Math.min(currentParts.length, op.length);
  for (let i = 0; i < n; i++) {
    if (currentParts[i] !== op[i]) return false;
  }
  return true;
}

function currentPlayParts() {
  return state.moves.slice(0, state.ply).map(m => m.from + m.to + (m.promotion || ""));
}

function isInMyRep(play) {
  return state.myRepertoire[state.repColor].some(e => e.play === play);
}

function toggleRepOpening(entry) {
  const list = state.myRepertoire[state.repColor];
  const i = list.findIndex(e => e.play === entry.play);
  if (i >= 0) list.splice(i, 1);
  else list.push({ play: entry.play, name: entry.name, eco: entry.eco || "" });
  persistRepertoire();
  updateRepTreeButtons();
  renderPracticeList();
}

function uciToSanLine(play) {
  try {
    const c = new Chess();
    const sans = [];
    for (const u of String(play).split(",").filter(Boolean)) {
      const from = u.slice(0, 2), to = u.slice(2, 4), promotion = u.slice(4) || undefined;
      const m = c.move({ from, to, promotion });
      if (!m) break;
      sans.push(m.san);
    }
    return sans;
  } catch (e) {
    return [];
  }
}

async function ensureRepTree() {
  await ensureOpeningBook();
  if (repTreeBuilt) {
    updateRepTreeActive();
    updateRepTreeButtons();
    return;
  }
  const treeEl = document.getElementById("rep-tree");
  if (!openingBook || !Object.keys(openingBook).length) {
    treeEl.innerHTML = '<div class="rep-empty">Opening book unavailable.</div>';
    return;
  }

  // Prefer shortest play per exact name; group by first move → family → lines
  const byName = new Map();
  for (const [play, meta] of Object.entries(openingBook)) {
    if (!meta || !meta.name) continue;
    const parts = play.split(",").filter(Boolean);
    if (!parts.length || parts.length > 8) continue;
    const prev = byName.get(meta.name);
    if (!prev || parts.length < prev.parts.length) {
      byName.set(meta.name, { play, name: meta.name, eco: meta.eco || "", parts });
    }
  }

  const roots = new Map(); // firstUci -> Map(family -> entries[])
  for (const entry of byName.values()) {
    const first = entry.parts[0];
    if (!roots.has(first)) roots.set(first, new Map());
    const fam = familyName(entry.name);
    const fm = roots.get(first);
    if (!fm.has(fam)) fm.set(fam, []);
    fm.get(fam).push(entry);
  }

  const preferred = ["e2e4", "d2d4", "c2c4", "g1f3"];
  const firstMoves = [
    ...preferred.filter(m => roots.has(m)),
    ...[...roots.keys()].filter(m => !preferred.includes(m))
      .sort((a, b) => (roots.get(b).size - roots.get(a).size) || a.localeCompare(b)),
  ];

  treeEl.innerHTML = "";
  for (const first of firstMoves) {
    const families = roots.get(first);
    const moveDet = document.createElement("details");
    moveDet.className = "rep-move-group";
    moveDet.open = first === "e2e4" || first === "d2d4";
    moveDet.dataset.first = first;
    const moveSum = document.createElement("summary");
    moveSum.textContent = UCI_FIRST_LABEL[first] || first;
    moveDet.appendChild(moveSum);

    const famNames = [...families.keys()].sort((a, b) => a.localeCompare(b));
    for (const fam of famNames) {
      const lines = families.get(fam).sort((a, b) => a.name.localeCompare(b.name));
      const famDet = document.createElement("details");
      famDet.className = "rep-family";
      famDet.dataset.plays = lines.map(l => l.play).join("|");
      const famSum = document.createElement("summary");
      famSum.textContent = `${fam} (${lines.length})`;
      famDet.appendChild(famSum);
      for (const line of lines) {
        const row = document.createElement("div");
        row.className = "rep-line";
        row.dataset.play = line.play;
        row.title = "Replay opening on the board";
        row.innerHTML =
          `<span class="eco"></span>` +
          `<span class="nm"></span>` +
          `<button type="button"></button>`;
        const ecoEl = row.querySelector(".eco");
        ecoEl.textContent = line.eco || "";
        if (line.eco) {
          ecoEl.title = "Copy opening name";
          ecoEl.onclick = ev => {
            ev.stopPropagation();
            copyOpeningName(line.name, ecoEl);
          };
        }
        row.querySelector(".nm").textContent = line.name;
        const btn = row.querySelector("button");
        btn.onclick = ev => {
          ev.stopPropagation();
          toggleRepOpening(line);
        };
        row.onclick = () => previewRepOpening(line);
        famDet.appendChild(row);
      }
      moveDet.appendChild(famDet);
    }
    treeEl.appendChild(moveDet);
  }

  repTreeBuilt = true;
  updateRepTreeButtons();
  updateRepTreeActive();
}

function updateRepTreeButtons() {
  document.querySelectorAll("#rep-tree .rep-line").forEach(row => {
    const play = row.dataset.play;
    const btn = row.querySelector("button");
    if (!btn) return;
    const on = isInMyRep(play);
    btn.textContent = on ? "In repertoire" : "Add to my repertoire";
    btn.classList.toggle("in-rep", on);
  });
}

function updateRepTreeActive() {
  if (!state.repertoireOpen || !repTreeBuilt) return;
  const cur = currentPlayParts();
  document.querySelectorAll("#rep-tree .rep-line").forEach(row => {
    const ok = playCompatible(cur, row.dataset.play);
    row.classList.toggle("inactive", !ok);
  });
  document.querySelectorAll("#rep-tree .rep-family").forEach(fam => {
    const plays = (fam.dataset.plays || "").split("|").filter(Boolean);
    const ok = plays.some(p => playCompatible(cur, p));
    fam.classList.toggle("inactive", !ok);
  });
  document.querySelectorAll("#rep-tree .rep-move-group").forEach(g => {
    const first = g.dataset.first;
    const ok = !cur.length || cur[0] === first;
    g.classList.toggle("inactive", !ok);
  });
}

function setRepColor(color) {
  state.repColor = color === "black" ? "black" : "white";
  document.getElementById("rep-tab-white").classList.toggle("active", state.repColor === "white");
  document.getElementById("rep-tab-black").classList.toggle("active", state.repColor === "black");
  updateRepTreeButtons();
  renderPracticeList();
  scheduleRepSlipupsRefresh();
}

/* ---------- repertoire slip-ups (opening issues in saved lines) ---------- */

let repSlipupsByPlay = {}; // play -> { entry, worst, counts, issueGames, games[] }
let repSlipupsScanKey = "";
let repSlipupsBusy = false;
let repSlipupsTimer = null;
let repSlipupsExpandedPlay = "";

const SLIP_SEV_RANK = { blunder: 3, mistake: 2 };
/** First 6 full moves (12 plies) — early opening only, not middlegame slips. */
const REP_SLIPUP_OPENING_PLIES = 12;

function slipupsCacheKey() {
  const plays = (state.myRepertoire[state.repColor] || [])
    .map(e => e.play).filter(Boolean).sort().join("|");
  const uname = state.report?.username || "";
  const n = state.report?.analyzed_games || 0;
  const since = state.report?.since || "";
  const until = state.report?.until || "";
  // v3 = early-opening window (first 6 moves) + mistakes/blunders only
  return `v3:${REP_SLIPUP_OPENING_PLIES}:${state.repColor}:${uname}:${n}:${since}:${until}:${plays}`;
}

/** Keep only early-opening mistake/blunder flags and summarize them. */
function slipFlagsFromScan(sg) {
  const flags = (sg?.flags || []).filter(f =>
    (f.severity === "blunder" || f.severity === "mistake")
    && (f.ply || 0) <= REP_SLIPUP_OPENING_PLIES
  );
  const counts = { blunder: 0, mistake: 0 };
  let worst = null;
  for (const f of flags) {
    counts[f.severity] = (counts[f.severity] || 0) + 1;
    worst = worst ? worseSeverity(worst, f.severity) : f.severity;
  }
  return { flags, counts, worst };
}

function sanMatchesPlayPrefix(sanList, playUci) {
  const playParts = String(playUci || "").split(",").filter(Boolean);
  if (!playParts.length || !Array.isArray(sanList) || !sanList.length) return false;
  if (sanList.length < playParts.length) return false;
  try {
    const c = new Chess();
    for (let i = 0; i < playParts.length; i++) {
      const m = c.move(sanList[i]);
      if (!m) return false;
      const u = m.from + m.to + (m.promotion || "");
      if (u !== playParts[i]) return false;
    }
    return true;
  } catch (e) {
    return false;
  }
}

function openingNameMatchesRep(opName, entryName) {
  const a = String(opName || "").toLowerCase().trim();
  const b = String(entryName || "").toLowerCase().trim();
  if (!a || !b) return false;
  if (a === b) return true;
  if (a.startsWith(b) || b.startsWith(a)) return true;
  const af = a.split(":")[0].trim();
  const bf = b.split(":")[0].trim();
  return !!af && af === bf;
}

function collectGamesForRepEntry(entry) {
  const rep = state.report?.[state.repColor];
  if (!rep) return [];
  const out = [];
  const seen = new Set();
  for (const op of (rep.openings || [])) {
    const nameHit = openingNameMatchesRep(op.name, entry.name);
    for (const g of (op.game_list || [])) {
      if (g.color && g.color !== state.repColor) continue;
      if (!g.san || !g.san.length) continue;
      const playHit = sanMatchesPlayPrefix(g.san, entry.play);
      const varHit = openingNameMatchesRep(g.variation, entry.name);
      if (!nameHit && !playHit && !varHit) continue;
      const key = g.url || `${g.date}|${g.opponent}|${(g.san || []).slice(0, 8).join(" ")}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(g);
    }
  }
  return out;
}

function formatSlipCounts(counts) {
  if (!counts) return "";
  const parts = [];
  if (counts.blunder) parts.push(`${counts.blunder} blunder${counts.blunder === 1 ? "" : "s"}`);
  if (counts.mistake) parts.push(`${counts.mistake} mistake${counts.mistake === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

function mergeCounts(a, b) {
  return {
    blunder: (a?.blunder || 0) + (b?.blunder || 0),
    mistake: (a?.mistake || 0) + (b?.mistake || 0),
  };
}

function worseSeverity(a, b) {
  return (SLIP_SEV_RANK[a] || 0) >= (SLIP_SEV_RANK[b] || 0) ? a : b;
}

function setSlipupsStatus(text) {
  const el = document.getElementById("rep-slipups-status");
  if (el) el.textContent = text || "";
}

function loadSlipupGame(g) {
  if (!g?.san?.length) return;
  stopPractice();
  closeRepertoire();
  // Match analyze tab to the side you played so the board context is right.
  if (g.color === "white" || g.color === "black") setColor(g.color);
  loadGameReplay(g);
}

function makeSlipupGameRow(g) {
  const row = document.createElement("div");
  row.className = "rep-slipup-game";
  row.title = "Open this game on the analysis board";

  const badge = document.createElement("span");
  badge.className = "qbadge " + (g._worst === "blunder" ? "blunder" : "mistake");
  badge.textContent = g._worst === "blunder" ? "??" : "?";

  const opp = document.createElement("span");
  opp.className = "opp";
  opp.textContent = g.opponent || "Opponent";

  const ratings = document.createElement("span");
  ratings.className = "ratings";
  const meR = g.my_rating || "—";
  const oppR = g.opponent_rating || "—";
  ratings.textContent = `You ${meR} · Opp ${oppR}`;

  const date = document.createElement("span");
  date.className = "date";
  date.textContent = g.date || "";

  const link = document.createElement("span");
  link.className = "open-link";
  link.textContent = "Open";

  row.append(badge, opp, ratings, date, link);
  row.onclick = () => loadSlipupGame(g);
  return row;
}

function renderRepSlipups() {
  const listEl = document.getElementById("rep-slipups-list");
  if (!listEl) return;
  listEl.innerHTML = "";
  const rows = Object.values(repSlipupsByPlay)
    .filter(x => x.issueGames > 0)
    .sort((a, b) => {
      const dr = (SLIP_SEV_RANK[b.worst] || 0) - (SLIP_SEV_RANK[a.worst] || 0);
      if (dr) return dr;
      return b.issueGames - a.issueGames;
    })
    .slice(0, 5);

  if (!rows.length) return;

  for (const item of rows) {
    const wrap = document.createElement("div");
    wrap.className = "rep-slipup-item" + (repSlipupsExpandedPlay === item.entry.play ? " open" : "");

    const row = document.createElement("div");
    row.className = "rep-slipup-row";
    row.setAttribute("role", "button");
    row.setAttribute("aria-expanded", wrap.classList.contains("open") ? "true" : "false");
    row.tabIndex = 0;

    const badge = document.createElement("span");
    badge.className = "qbadge " + item.worst;
    badge.textContent = item.worst === "blunder" ? "??" : "?";

    const chev = document.createElement("span");
    chev.className = "chev";
    chev.textContent = "▸";
    chev.setAttribute("aria-hidden", "true");

    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = item.entry.name + (item.entry.eco ? ` (${item.entry.eco})` : "");

    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = `${item.issueGames} game${item.issueGames === 1 ? "" : "s"} · ${formatSlipCounts(item.counts)}`;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "primary";
    btn.textContent = "Practice";
    btn.onclick = e => {
      e.stopPropagation();
      setRepColor(state.repColor);
      startPractice(item.entry);
    };

    row.append(badge, chev, nm, meta, btn);
    row.onclick = () => {
      repSlipupsExpandedPlay = repSlipupsExpandedPlay === item.entry.play ? "" : item.entry.play;
      renderRepSlipups();
    };
    row.onkeydown = e => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        row.click();
      }
    };

    const gamesEl = document.createElement("div");
    gamesEl.className = "rep-slipup-games";
    const games = (item.games || []).slice().sort((a, b) =>
      String(b.date || "").localeCompare(String(a.date || "")));
    if (!games.length) {
      const empty = document.createElement("div");
      empty.className = "rep-empty";
      empty.style.padding = "8px 12px";
      empty.textContent = "No game details available.";
      gamesEl.appendChild(empty);
    } else {
      for (const g of games) gamesEl.appendChild(makeSlipupGameRow(g));
    }

    wrap.append(row, gamesEl);
    listEl.appendChild(wrap);
  }
}

function scheduleRepSlipupsRefresh() {
  clearTimeout(repSlipupsTimer);
  repSlipupsTimer = setTimeout(() => { refreshRepSlipups(); }, 80);
}

async function refreshRepSlipups() {
  if (!state.repertoireOpen) return;
  const statusEl = document.getElementById("rep-slipups-status");
  const listEl = document.getElementById("rep-slipups-list");
  if (!statusEl || !listEl) return;

  const entries = state.myRepertoire[state.repColor] || [];
  if (!entries.length) {
    repSlipupsByPlay = {};
    repSlipupsScanKey = "";
    listEl.innerHTML = "";
    setSlipupsStatus("Add openings to your repertoire to see early-opening slip-ups here.");
    return;
  }

  if (!state.report) {
    repSlipupsByPlay = {};
    repSlipupsScanKey = "";
    listEl.innerHTML = "";
    setSlipupsStatus("Analyze your games first to see early-opening slip-ups (moves 1–6) here.");
    return;
  }

  const key = slipupsCacheKey();
  if (key === repSlipupsScanKey && !repSlipupsBusy) {
    if (!Object.values(repSlipupsByPlay).some(x => x.issueGames > 0)) {
      setSlipupsStatus("No early-opening mistakes (moves 1–6) in your repertoire lines.");
    } else {
      setSlipupsStatus("");
    }
    renderRepSlipups();
    renderPracticeList();
    return;
  }

  if (repSlipupsBusy) {
    scheduleRepSlipupsRefresh();
    return;
  }
  repSlipupsBusy = true;
  setSlipupsStatus("Scanning early opening (moves 1–6) for slip-ups…");
  listEl.innerHTML = "";

  try {
    // Build game batches linked to each repertoire entry (cap total scans).
    const MAX_SCAN = 40;
    const perEntryGames = [];
    let totalQueued = 0;
    for (const entry of entries) {
      const games = collectGamesForRepEntry(entry);
      perEntryGames.push({ entry, games });
      totalQueued += games.length;
    }
    if (!totalQueued) {
      repSlipupsByPlay = {};
      repSlipupsScanKey = key;
      setSlipupsStatus("No analyzed games match your repertoire lines yet.");
      renderPracticeList();
      return;
    }

    // Fair share across entries, then fill remaining slots.
    const take = new Map(); // entry.play -> games[]
    for (const { entry, games } of perEntryGames) take.set(entry.play, []);
    let remaining = MAX_SCAN;
    let progressed = true;
    while (remaining > 0 && progressed) {
      progressed = false;
      for (const { entry, games } of perEntryGames) {
        if (remaining <= 0) break;
        const bucket = take.get(entry.play);
        if (bucket.length >= games.length) continue;
        bucket.push(games[bucket.length]);
        remaining -= 1;
        progressed = true;
      }
    }

    const batch = [];
    const batchMeta = []; // { play, entry, game }
    for (const { entry } of perEntryGames) {
      for (const g of take.get(entry.play)) {
        batchMeta.push({ play: entry.play, entry, game: g });
        batch.push({
          san: g.san,
          color: state.repColor,
          variation: g.variation || entry.name,
          opponent: g.opponent,
          date: g.date,
          url: g.url,
        });
      }
    }

    const data = await apiScanBlunders({
      games: batch,
      opening_plies: REP_SLIPUP_OPENING_PLIES,
    }, msg => setSlipupsStatus(msg));

    const byPlay = {};
    for (const entry of entries) {
      byPlay[entry.play] = {
        entry,
        worst: null,
        counts: { blunder: 0, mistake: 0 },
        issueGames: 0,
        games: [],
      };
    }

    for (const sg of (data.games || [])) {
      const meta = batchMeta[sg.index];
      if (!meta) continue;
      const slot = byPlay[meta.play];
      if (!slot) continue;
      const { flags, counts, worst } = slipFlagsFromScan(sg);
      if (!worst) continue; // no early-opening mistake/blunder
      const g = meta.game;
      g._flags = flags;
      g._worst = worst;
      g._counts = counts;
      slot.issueGames += 1;
      slot.counts = mergeCounts(slot.counts, counts);
      slot.worst = slot.worst ? worseSeverity(slot.worst, worst) : worst;
      slot.games.push(g);
    }

    repSlipupsByPlay = byPlay;
    repSlipupsScanKey = key;

    const withIssues = Object.values(byPlay).filter(x => x.issueGames > 0).length;
    if (!withIssues) {
      setSlipupsStatus("No early-opening mistakes (moves 1–6) in your repertoire lines.");
      listEl.innerHTML = "";
    } else {
      setSlipupsStatus("");
      renderRepSlipups();
    }
    renderPracticeList();
    const puzzleGames = (data.games || []).map(sg => ({
      ...sg,
      san: batchMeta[sg.index]?.game?.san || [],
    }));
    saveMistakesAsPuzzles({ games: puzzleGames });
  } catch (e) {
    if (handleProGateError(e)) {
      setSlipupsStatus("Pro required to scan for early-opening slip-ups.");
    } else {
      setSlipupsStatus(e.message || "Could not scan for slip-ups.");
    }
  } finally {
    repSlipupsBusy = false;
  }
}

function renderPracticeList() {
  const el = document.getElementById("rep-practice-list");
  if (!el) return;
  const list = state.myRepertoire[state.repColor] || [];
  if (!list.length) {
    el.innerHTML = `<div class="rep-empty">No openings saved for ${state.repColor} yet. Add some from the tree.</div>`;
    return;
  }
  el.innerHTML = "";
  for (const entry of list) {
    const row = document.createElement("div");
    row.className = "rep-practice-row";
    row.dataset.play = entry.play;
    const slip = repSlipupsByPlay[entry.play];
    row.innerHTML =
      `<span class="eco">${entry.eco || ""}</span>` +
      `<span class="nm"></span>` +
      (slip?.worst
        ? `<span class="slip-badge ${slip.worst}" title="${formatSlipCounts(slip.counts)}">${
            slip.worst === "blunder" ? "??" : "?"
          }</span>`
        : "") +
      `<button type="button" class="primary" data-act="practice">Practice</button>` +
      `<button type="button" data-act="load">Load line</button>` +
      `<button type="button" data-act="remove">Remove</button>`;
    row.querySelector(".nm").textContent = entry.name;
    row.querySelector('[data-act="practice"]').onclick = () => startPractice(entry);
    row.querySelector('[data-act="load"]').onclick = () => loadRepLine(entry, false);
    row.querySelector('[data-act="remove"]').onclick = () => {
      toggleRepOpening(entry);
    };
    el.appendChild(row);
  }
}

const REP_PREVIEW_PLIES = 10; // first 5 full moves

async function copyOpeningName(name, ecoEl) {
  const text = String(name || "").trim();
  if (!text) return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    if (ecoEl) {
      ecoEl.classList.add("copied");
      const prev = ecoEl.title;
      ecoEl.title = "Copied!";
      clearTimeout(ecoEl._copyTimer);
      ecoEl._copyTimer = setTimeout(() => {
        ecoEl.classList.remove("copied");
        ecoEl.title = prev || "Copy opening name";
      }, 1200);
    }
  } catch (e) {
    console.warn("Clipboard copy failed", e);
  }
}

function previewRepOpening(entry) {
  // Click a repertoire-tree opening → animate its first moves onto the board.
  stopPractice();
  if (state.editMode) setEditMode(false);
  const sans = uciToSanLine(entry.play);
  if (!sans.length) return;
  const g = new Chess();
  for (const san of sans) {
    try { g.move(san); } catch (e) { break; }
  }
  freeFen = null;
  gameRootFen = START_FEN;
  game = g;
  rebuildDerived();
  state.ply = 0;
  state.moveFlags = [];
  state.lastOpeningName = entry.name;
  clearBoardPlayers();
  setGameView(false);
  state.orientation = state.repColor;
  setBoardTitle(entry.name + (entry.eco ? ` (${entry.eco})` : ""));
  document.querySelectorAll("#rep-tree .rep-line").forEach(el => {
    el.classList.toggle("active-preview", el.dataset.play === entry.play);
  });
  animateLine(Math.min(REP_PREVIEW_PLIES, state.sans.length));
}

function loadRepLine(entry, enterPractice) {
  stopPractice();
  const sans = uciToSanLine(entry.play);
  const g = new Chess();
  for (const san of sans) {
    try { g.move(san); } catch (e) { break; }
  }
  freeFen = null;
  gameRootFen = START_FEN;
  game = g;
  rebuildDerived();
  state.ply = state.sans.length;
  state.moveFlags = [];
  state.mainLineSans = [];
  state.exploreAnns = {};
  exploreAnnotateToken += 1;
  state.lastOpeningName = entry.name;
  state.orientation = state.repColor;
  if (enterPractice) {
    state.practiceMode = true;
    document.body.classList.add("practice-mode");
  }
  setBoardTitle(entry.name + (entry.eco ? ` (${entry.eco})` : ""));
  refreshBoard();
}

function stopPractice() {
  state.practiceMode = false;
  document.body.classList.remove("practice-mode");
  practiceBusy = false;
  clearTimeout(practiceTimer);
  clearPremove();
  const st = document.getElementById("rep-practice-status");
  if (st) st.textContent = "";
}

function startPractice(entry) {
  if (currentUser?.billing_enabled && !currentUser.is_pro) {
    openUpgradeModal();
    return;
  }
  state.practiceElo = +document.getElementById("rep-difficulty").value || 1800;
  loadRepLine(entry, true);
  const st = document.getElementById("rep-practice-status");
  if (state.repColor === "black") {
    if (st) st.textContent = "Stockfish (White) is thinking…";
    schedulePracticeReply();
  } else if (st) {
    st.textContent = "Your move as White. Stockfish will reply.";
  }
}

function repertoirePool(colorChoice) {
  const white = (state.myRepertoire.white || []).map(e => ({ color: "white", entry: e }));
  const black = (state.myRepertoire.black || []).map(e => ({ color: "black", entry: e }));
  if (colorChoice === "white") return white;
  if (colorChoice === "black") return black;
  return white.concat(black);
}

function pickRandomRepertoireLine(colorChoice) {
  const pool = repertoirePool(colorChoice);
  if (!pool.length) return null;
  return pool[Math.floor(Math.random() * pool.length)];
}

function openPracticeColorModal() {
  const modal = document.getElementById("practice-color-modal");
  const msg = document.getElementById("practice-color-msg");
  if (msg) {
    msg.textContent = "";
    msg.classList.remove("error", "ok");
  }
  const total = repertoirePool("random").length;
  if (!total) {
    const st = document.getElementById("rep-practice-status");
    if (st) st.textContent = "Add openings to your repertoire first.";
    return;
  }
  modal.classList.add("open");
}

function closePracticeColorModal() {
  document.getElementById("practice-color-modal").classList.remove("open");
}

function startRandomPractice(colorChoice) {
  const msg = document.getElementById("practice-color-msg");
  const picked = pickRandomRepertoireLine(colorChoice);
  if (!picked) {
    if (msg) {
      msg.textContent = colorChoice === "random"
        ? "No openings in your repertoire yet."
        : `No openings saved for ${colorChoice}.`;
      msg.classList.add("error");
      msg.classList.remove("ok");
    }
    return;
  }
  closePracticeColorModal();
  setRepColor(picked.color);
  state.orientation = picked.color;
  startPractice(picked.entry);
  const st = document.getElementById("rep-practice-status");
  if (st) {
    const label = `${picked.entry.name}${picked.entry.eco ? ` (${picked.entry.eco})` : ""}`;
    st.textContent = picked.color === "black"
      ? `Practice All · ${label}. Stockfish (White) is thinking…`
      : `Practice All · ${label}. Your move as White.`;
  }
}

function schedulePracticeReply() {
  clearTimeout(practiceTimer);
  practiceTimer = setTimeout(runPracticeReply, 250);
}

async function runPracticeReply() {
  if (!state.practiceMode || practiceBusy || state.editMode) return;
  const fen = currentFen();
  let turn;
  try {
    const c = new Chess(fen);
    if (c.isGameOver()) {
      updatePracticeGameStatus();
      return;
    }
    turn = c.turn();
  } catch (e) {
    return;
  }
  // User plays repColor; engine plays the other side
  const userIsWhite = state.repColor === "white";
  const engineToMove = userIsWhite ? turn === "b" : turn === "w";
  if (!engineToMove) {
    document.getElementById("rep-practice-status").textContent =
      `Your move as ${state.repColor === "white" ? "White" : "Black"}.`;
    tryExecutePremove();
    return;
  }

  practiceBusy = true;
  document.getElementById("rep-practice-status").textContent = "Opponent is thinking…";
  try {
    const data = await api("/api/practice-move", "POST", {
      fen,
      elo: state.practiceElo || +document.getElementById("rep-difficulty").value || 1800,
      style: "human",
    });
    if (!state.practiceMode) return;
    if (state.ply < state.sans.length) truncateToPly();
    let played;
    try {
      played = game.move({
        from: data.from,
        to: data.to,
        promotion: data.promotion || undefined,
      });
    } catch (e) {
      document.getElementById("rep-practice-status").textContent = "Engine move failed.";
      return;
    }
    rebuildDerived();
    state.ply = state.sans.length;
    clearUserShapes();

    await new Promise(resolve => {
      animateThenRefresh(played, () => {
        playMoveSound(played, currentFen());
        resolve();
      });
    });

    updatePracticeGameStatus();
    const pos = positionAtPly();
    if (pos && !pos.isGameOver()) {
      document.getElementById("rep-practice-status").textContent =
        `Stockfish played ${data.san}. Your move.`;
      // Chess.com: fire premove immediately after opponent lands
      tryExecutePremove();
    }
  } catch (e) {
    if (handleProGateError(e)) {
      stopPractice();
      document.getElementById("rep-practice-status").textContent =
        "Pro required for Practice vs Stockfish.";
    } else {
      document.getElementById("rep-practice-status").textContent = e.message;
    }
  } finally {
    practiceBusy = false;
  }
}

async function openRepertoire() {
  // My Repertoire is the Pro product (with 3-day trial via Checkout).
  if (!currentUser) {
    setMsg(authMsg, "Sign in to use My Repertoire.", "error");
    openAuthModal({ keepMessages: true });
    return;
  }
  if (currentUser.billing_enabled && !currentUser.is_pro) {
    openUpgradeModal();
    return;
  }
  closeProfile();
  if (state.editMode) setEditMode(false);
  stopPractice();
  state.repertoireOpen = true;
  document.body.classList.add("repertoire-open");
  document.getElementById("my-repertoire-btn").classList.add("active");
  setRepColor(state.repColor);
  state.orientation = state.repColor;
  resetGame();
  setBoardTitle("My Repertoire");
  await ensureRepTree();
  renderPracticeList();
  scheduleRepSlipupsRefresh();
  refreshHabits();
  requestAnimationFrame(syncSidebarToBoard);
}

function closeRepertoire() {
  if (!state.repertoireOpen) return;
  stopPractice();
  state.repertoireOpen = false;
  document.body.classList.remove("repertoire-open");
  document.getElementById("my-repertoire-btn").classList.remove("active");
  requestAnimationFrame(syncSidebarToBoard);
}

document.getElementById("my-repertoire-btn").onclick = () => {
  if (state.repertoireOpen) closeRepertoire();
  else openRepertoire();
};
document.getElementById("repertoire-back").onclick = () => closeRepertoire();
document.getElementById("rep-tab-white").onclick = () => {
  setRepColor("white");
  state.orientation = "white";
  if (state.repertoireOpen) refreshBoard();
};
document.getElementById("rep-tab-black").onclick = () => {
  setRepColor("black");
  state.orientation = "black";
  if (state.repertoireOpen) refreshBoard();
};
document.getElementById("rep-difficulty").onchange = e => {
  state.practiceElo = +e.target.value || 1800;
};
document.getElementById("rep-randomize-btn").onclick = () => openPracticeColorModal();
document.getElementById("rep-train-today-btn")?.addEventListener("click", () => loadTrainToday());
document.getElementById("rep-export-pgn-btn")?.addEventListener("click", () => exportRepertoirePgn());
document.getElementById("rep-import-pgn-btn")?.addEventListener("click", () => importRepertoirePgn());
document.getElementById("rep-coach-btn")?.addEventListener("click", () => runAiCoach());
document.getElementById("profile-revoke-sessions-btn")?.addEventListener("click", () => revokeOtherSessions());

let practiceFeedbackTimer = null;
function schedulePracticeFeedback(fenBefore, san) {
  clearTimeout(practiceFeedbackTimer);
  if (!currentUser || !fenBefore || !san) return;
  practiceFeedbackTimer = setTimeout(async () => {
    if (!state.practiceMode) return;
    try {
      const fb = await api("/api/practice-feedback", "POST", { fen: fenBefore, san });
      const st = document.getElementById("rep-practice-status");
      if (!st || !state.practiceMode) return;
      const label = fb.severity === "best"
        ? "Best move!"
        : `${fb.severity}${fb.best_san ? ` · better was ${fb.best_san}` : ""}`;
      st.textContent = label;
    } catch (_) { /* non-blocking */ }
  }, 80);
}

async function refreshHabits() {
  if (!currentUser) return;
  try {
    const h = await api("/api/me/habits", "GET");
    const el = document.getElementById("rep-habits");
    if (el) {
      el.textContent = `Streak ${h.streak_days || 0} · today ${h.reviews_today || 0}`;
    }
  } catch (_) { /* ignore */ }
}

async function loadTrainToday() {
  if (!currentUser) {
    openAuthModal();
    return;
  }
  const box = document.getElementById("rep-train-today");
  if (!box) return;
  box.hidden = false;
  box.textContent = "Loading today's plan…";
  try {
    const plan = await api("/api/me/train-today", "GET");
    const due = plan.steps?.[0]?.cards || [];
    box.innerHTML = `<strong>${plan.headline || "Train today"}</strong>
      <div>${due.length} cards due · ${plan.steps?.[1]?.lines?.length || 0} repertoire lines</div>
      <button type="button" id="rep-review-due-btn">Review due cards</button>
      <button type="button" id="rep-share-report-btn">Share last report</button>`;
    document.getElementById("rep-review-due-btn")?.addEventListener("click", () => reviewDueCards(due));
    document.getElementById("rep-share-report-btn")?.addEventListener("click", () => shareLastReport());
    await refreshHabits();
  } catch (e) {
    if (!handleProGateError(e)) box.textContent = e.message || "Could not load plan";
  }
}

async function reviewDueCards(cards) {
  if (!cards?.length) {
    document.getElementById("rep-practice-status").textContent = "No cards due — nice work.";
    return;
  }
  const card = cards[0];
  try {
    const c = tryLoadChess(card.fen);
    if (c) {
      game = c;
      gameRootFen = card.fen;
      freeFen = null;
      rebuildDerived();
      state.ply = 0;
      refreshBoard();
    }
  } catch (_) { /* ignore */ }
  const guess = window.prompt(`${card.prompt || "Best move?"} (SAN)`, "");
  if (guess == null) return;
  const ok = (guess || "").trim() === (card.answer_san || "").trim();
  try {
    await api("/api/me/learning/review", "POST", {
      card_id: card.id,
      quality: ok ? 4 : 1,
    });
    document.getElementById("rep-practice-status").textContent = ok
      ? `Correct: ${card.answer_san}`
      : `Answer was ${card.answer_san || "—"}. Scheduled again.`;
    await refreshHabits();
    await loadTrainToday();
  } catch (e) {
    if (!handleProGateError(e)) {
      document.getElementById("rep-practice-status").textContent = e.message;
    }
  }
}

async function exportRepertoirePgn() {
  try {
    await ensureApiBase();
    const headers = {};
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const resp = await fetchApi("/api/me/repertoire.pgn", { headers });
    if (!resp.ok) {
      const data = await readJson(resp).catch(() => ({}));
      const err = new Error(data.error || `HTTP ${resp.status}`);
      err.status = resp.status;
      err.code = data.code;
      throw err;
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "repertoire.pgn";
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    if (!handleProGateError(e)) alert(e.message || "Export failed");
  }
}

async function importRepertoirePgn() {
  if (!currentUser) {
    openAuthModal();
    return;
  }
  const pgn = window.prompt("Paste PGN to import into your repertoire:");
  if (!pgn?.trim()) return;
  try {
    const data = await api("/api/me/repertoire/import-pgn", "POST", {
      pgn,
      color: state.repColor || "white",
    });
    state.myRepertoire = null;
    await ensureRepTree();
    renderPracticeList();
    alert(`Imported ${data.imported || 0} line(s).`);
  } catch (e) {
    if (!handleProGateError(e)) alert(e.message || "Import failed");
  }
}

async function runAiCoach() {
  if (!state.report) {
    alert("Analyze a username first for coach notes.");
    return;
  }
  const box = document.getElementById("rep-train-today");
  if (box) {
    box.hidden = false;
    box.textContent = "Asking coach…";
  }
  try {
    const data = await api("/api/me/coach", "POST", {
      white: state.report.white,
      black: state.report.black,
      priorities: state.report.priorities || [],
    });
    if (box) {
      box.innerHTML = `<strong>AI coach</strong><pre style="white-space:pre-wrap;font:inherit;margin:8px 0 0">${
        (data.markdown || "").replace(/[<>&]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]))
      }</pre>`;
    }
  } catch (e) {
    if (box) box.textContent = e.message || "Coach unavailable";
    else if (!handleProGateError(e)) alert(e.message || "Coach unavailable");
  }
}

async function shareLastReport() {
  if (!state.report) {
    alert("Analyze a username first, then share.");
    return;
  }
  try {
    const data = await api("/api/report/share", "POST", {
      report: state.report,
      title: state.report.username || "Report",
    });
    const url = `${location.origin}${data.url || ("/?share=" + data.share_id)}`;
    try {
      await navigator.clipboard.writeText(url);
      alert("Share link copied:\n" + url);
    } catch (_) {
      prompt("Share link", url);
    }
  } catch (e) {
    alert(e.message || "Share failed");
  }
}

async function saveMistakesAsPuzzles(scanResult) {
  if (!currentUser) return;
  if (currentUser.billing_enabled && !currentUser.is_pro) return;
  const cards = [];
  for (const g of scanResult?.games || []) {
    for (const f of g.flags || []) {
      if (!["blunder", "mistake"].includes(f.severity)) continue;
      if (!f.best_san || !g.san?.length) continue;
      try {
        const c = new Chess();
        for (let i = 0; i < (f.ply || 1) - 1; i++) {
          if (!g.san[i]) break;
          c.move(g.san[i]);
        }
        cards.push({
          kind: "puzzle",
          fen: c.fen(),
          san_line: (g.san || []).slice(0, f.ply).join(" "),
          prompt: `Find a better move (${f.severity})`,
          answer_san: f.best_san,
          opening: g.variation || "",
        });
      } catch (_) { /* skip */ }
      if (cards.length >= 20) break;
    }
    if (cards.length >= 20) break;
  }
  if (!cards.length) return;
  try {
    await api("/api/me/learning/cards", "POST", { cards });
  } catch (_) { /* best-effort */ }
}

async function loadAuthSessions() {
  const el = document.getElementById("profile-sessions-list");
  if (!el || !currentUser) return;
  try {
    const data = await api("/api/me/sessions", "GET");
    const sessions = data.sessions || [];
    if (!sessions.length) {
      el.textContent = "No tracked sessions yet.";
      return;
    }
    el.innerHTML = sessions.map(s => {
      const when = s.last_seen
        ? new Date(Number(s.last_seen) * 1000).toLocaleString()
        : "—";
      const ua = (s.user_agent || "Unknown device").slice(0, 64);
      return `<div>${s.current ? "<strong>This device</strong>" : ua} · ${when}</div>`;
    }).join("");
  } catch (_) {
    el.textContent = "Could not load sessions.";
  }
}

async function revokeOtherSessions() {
  try {
    await api("/api/me/sessions/revoke", "POST", { all: true });
    // Keep current browser session via Flask cookie; just refresh list
    await loadAuthSessions();
    alert("Other sessions marked revoked.");
  } catch (e) {
    alert(e.message || "Could not revoke sessions");
  }
}

async function loadSharedReportIfPresent() {
  try {
    const shareId = new URLSearchParams(location.search).get("share");
    if (!shareId) return;
    const data = await api(`/api/report/share/${encodeURIComponent(shareId)}`, "GET");
    if (data?.report) applyReport(data.report);
  } catch (e) {
    console.warn("shared report load failed", e);
  }
}

document.getElementById("practice-color-cancel").onclick = () => closePracticeColorModal();
document.getElementById("practice-color-modal").addEventListener("click", e => {
  if (e.target.id === "practice-color-modal") closePracticeColorModal();
});
document.querySelectorAll("#practice-color-modal .color-choice-row button").forEach(btn => {
  btn.onclick = () => startRandomPractice(btn.dataset.color);
});

/* ---------- init ---------- */

const BOARD_THEME_LS = "boardTheme";
const BOARD_THEMES = ["blue", "green", "pink", "grey"];

function syncThemeSwatches(theme) {
  document.querySelectorAll("#theme-swatches .theme-swatch").forEach(btn => {
    const on = btn.dataset.theme === theme;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

function applyBoardTheme(theme, { persist = true } = {}) {
  const t = BOARD_THEMES.includes(theme) ? theme : "blue";
  document.documentElement.setAttribute("data-board-theme", t);
  if (persist) {
    try { localStorage.setItem(BOARD_THEME_LS, t); } catch (e) { /* ignore */ }
  }
  syncThemeSwatches(t);
  if (typeof refreshBoard === "function" && document.getElementById("board")) {
    try { refreshBoard(); } catch (e) { /* not ready yet */ }
  }
}

function initBoardTheme() {
  let saved = "blue";
  try {
    const v = localStorage.getItem(BOARD_THEME_LS);
    if (BOARD_THEMES.includes(v)) saved = v;
  } catch (e) { /* ignore */ }
  applyBoardTheme(saved, { persist: false });
  const host = document.getElementById("theme-swatches");
  if (host) {
    host.addEventListener("click", e => {
      const btn = e.target.closest(".theme-swatch[data-theme]");
      if (!btn) return;
      applyBoardTheme(btn.dataset.theme);
    });
  }
}

initBoardTheme();
initIntroModal(); // before billing URL cleanup so ?billing= can skip intro
initBillingUi();
initRecentUsernames();
loadRepertoireFromLocal();
rebuildDerived();
refreshBoard();
updateEditTurnBtns();
syncProChips();
requestAnimationFrame(syncSidebarToBoard);

/** Local demo: ?demo=opening-lesson — opens the post-scan coaching board UI. */
function runOpeningLessonDemo() {
  markIntroSeen();
  closeIntroModal();
  const g1 = {
    san: ["e4", "e5", "Nf3", "Nc6", "Bc4", "Qh4", "O-O", "Bc5", "d4"],
    color: "black",
    variation: "Italian Game",
    opponent: "DemoPlayer",
    date: "2026-08-01",
    url: "#demo-italian",
    result: "loss",
    white: { username: "DemoPlayer", rating: 1500 },
    black: { username: "you", rating: 1480 },
    _annotated: true,
    _flags: [
      {
        ply: 6,
        san: "Qh4",
        severity: "blunder",
        loss_cp: 320,
        best_san: "Nf6",
        color: "black",
      },
      {
        ply: 8,
        san: "Bc5",
        severity: "inaccuracy",
        loss_cp: 70,
        best_san: "d6",
        color: "black",
      },
    ],
    _worst: "blunder",
  };
  const g2 = {
    san: ["e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6", "Be2", "e5"],
    color: "black",
    variation: "Sicilian Defense",
    opponent: "DemoPlayer2",
    date: "2026-08-02",
    url: "#demo-sicilian",
    result: "loss",
    white: { username: "DemoPlayer2", rating: 1520 },
    black: { username: "you", rating: 1480 },
    _annotated: true,
    _flags: [{
      ply: 12,
      san: "e5",
      severity: "mistake",
      loss_cp: 150,
      best_san: "e6",
      color: "black",
    }],
    _worst: "mistake",
  };
  const g3 = {
    san: ["d4", "Nf6", "c4", "g6", "Nc3", "d5", "cxd5", "Nxd5", "e4", "Nxc3", "bxc3", "Bg7", "Nf3", "c5"],
    color: "black",
    variation: "Gruenfeld Defense",
    opponent: "DemoPlayer3",
    date: "2026-08-03",
    url: "#demo-gruenfeld",
    result: "draw",
    white: { username: "DemoPlayer3", rating: 1550 },
    black: { username: "you", rating: 1480 },
    _annotated: true,
    _flags: [{
      ply: 14,
      san: "c5",
      severity: "mistake",
      loss_cp: 120,
      best_san: "c6",
      color: "black",
    }],
    _worst: "mistake",
  };
  state.orientation = "black";
  state.color = "black";
  // Tiny report so closing game view isn't a blank Analyze screen.
  applyReport({
    username: "demo",
    analyzed_games: 3,
    months: 1,
    white: { total_games: 0, score: 0, openings: [] },
    black: {
      total_games: 3,
      score: 0,
      openings: [{
        name: "Italian Game",
        eco: "C50",
        games: 1,
        wins: 0, losses: 1, draws: 0,
        score: 0,
        game_list: [g1, g2, g3],
        lines: [],
      }],
    },
  });
  openOpeningLesson([
    { game: g1, flag: g1._flags[0] },
    { game: g1, flag: g1._flags[1] },
    { game: g2, flag: g2._flags[0] },
    { game: g3, flag: g3._flags[0] },
  ], 0);
  if (statusEl) {
    statusEl.className = "";
    statusEl.textContent = "Demo: Next mistake stays in-game; when that runs out it becomes Next game.";
  }
}

try {
  const demo = new URLSearchParams(location.search).get("demo");
  if (demo === "opening-lesson") {
    requestAnimationFrame(() => runOpeningLessonDemo());
  }
  loadSharedReportIfPresent();
} catch (e) { /* ignore */ }

