/**
 * Regression: browser Stockfish reports scores from the side to move.
 * Painting that raw score on the eval bar makes Black-to-move wins look like
 * White is crushing (the custom-position bug).
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  displayScore,
  evalBarFill,
  evalFavorsWhite,
  fenTurn,
  formatEvalText,
  parseUciInfoLine,
  toWhitePov,
  whiteShareFromEval,
} from "../src/evalBar.ts";

/** Custom position from the broken-eval-bar report: Black to move, Black winning. */
const CUSTOM_BLACK_WINNING =
  "8/p6p/6p1/1p1p4/3q4/4k2P/6P1/4Q2K b - - 0 1";

test("FEN turn is Black in the reported custom position", () => {
  assert.equal(fenTurn(CUSTOM_BLACK_WINNING), "b");
});

test("UCI parses +cp and mate with optional plus sign", () => {
  const cp = parseUciInfoLine("info depth 12 score cp +420 pv e3d3");
  assert.equal(cp?.cp, 420);
  assert.equal(cp?.pvUci, "e3d3");
  const mate = parseUciInfoLine("info depth 8 score mate -2 pv a2a1");
  assert.equal(mate?.mate, -2);
  const neg = parseUciInfoLine("info depth 10 score cp -150");
  assert.equal(neg?.cp, -150);
});

test("Black-to-move winning score is White-negative after POV convert", () => {
  // Engine: Black to move and better by 4.2 pawns → UCI "score cp 420"
  const stm = { cp: 420, mate: null };
  const white = toWhitePov(stm, "b");
  assert.equal(white.cp, -420);
  assert.equal(formatEvalText(white), "-4.2");
  assert.equal(evalFavorsWhite(white), false);
  const share = whiteShareFromEval(white);
  assert.ok(share < 0.2, `bar must be mostly Black, got whiteShare=${share}`);
});

test("forgetting POV convert paints the bar White — the original bug", () => {
  const stm = { cp: 420, mate: null };
  const wrongShare = whiteShareFromEval(stm);
  assert.ok(
    wrongShare > 0.85,
    "raw STM score would look like a White crush",
  );
  const rightShare = whiteShareFromEval(toWhitePov(stm, fenTurn(CUSTOM_BLACK_WINNING)));
  assert.ok(rightShare < 0.2);
});

test("White to move keeps the engine sign", () => {
  const white = toWhitePov({ cp: 80, mate: null }, "w");
  assert.equal(white.cp, 80);
  assert.equal(formatEvalText(white), "+0.8");
});

test("Black-to-move mate for Black becomes -M for White", () => {
  const white = toWhitePov({ cp: null, mate: 3 }, "b");
  assert.equal(white.mate, -3);
  assert.equal(formatEvalText(white), "-M3");
  assert.equal(whiteShareFromEval(white), 0);
});

test("Black-to-move mate against Black becomes +M for White", () => {
  const white = toWhitePov({ cp: null, mate: -2 }, "b");
  assert.equal(white.mate, 2);
  assert.equal(formatEvalText(white), "M2");
  assert.equal(whiteShareFromEval(white), 1);
});

test("eval bar does not invert White's share when the board is flipped", () => {
  const blackWin = whiteShareFromEval({ cp: -420, mate: null });
  const whiteOrient = evalBarFill(blackWin, "white");
  const blackOrient = evalBarFill(blackWin, "black");
  assert.equal(whiteOrient.heightPct, blackOrient.heightPct);
  assert.equal(whiteOrient.anchor, "bottom");
  assert.equal(blackOrient.anchor, "top");
  assert.match(blackOrient.heightPct, /^(0|1?\d)\.\d%$/);
});

test("WASM payload without pov is still converted at display time", () => {
  const shown = displayScore(
    { cp: 420, mate: null, source: "wasm" },
    CUSTOM_BLACK_WINNING,
  );
  assert.equal(shown.cp, -420);
  assert.ok(whiteShareFromEval(shown) < 0.2);
  const alreadyWhite = displayScore(
    { cp: -420, mate: null, pov: "white", source: "wasm" },
    CUSTOM_BLACK_WINNING,
  );
  assert.equal(alreadyWhite.cp, -420);
});
