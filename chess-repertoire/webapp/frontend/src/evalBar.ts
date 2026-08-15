/**
 * Eval-bar math. Stockfish UCI scores are from the side to move; the bar and
 * score label are always White's perspective (positive = White is better).
 */

export type EvalScore = {
  cp: number | null;
  mate: number | null;
};

export type BoardOrientation = "white" | "black";

export function fenTurn(fen: string): "w" | "b" {
  const parts = fen.trim().split(/\s+/);
  return parts[1] === "b" ? "b" : "w";
}

/** Flip a side-to-move score into White's perspective. */
export function toWhitePov(score: EvalScore, turn: "w" | "b"): EvalScore {
  if (turn !== "b") {
    return { cp: score.cp, mate: score.mate };
  }
  return {
    cp: score.cp == null ? null : -score.cp,
    mate: score.mate == null ? null : -score.mate,
  };
}

export function parseUciInfoLine(line: string): {
  depth?: number;
  cp?: number;
  mate?: number;
  pvUci?: string;
} | null {
  if (!line.startsWith("info")) return null;
  const depthM = /\bdepth (\d+)/.exec(line);
  const mateM = /\bscore mate ([+-]?\d+)/.exec(line);
  const cpM = /\bscore cp ([+-]?\d+)/.exec(line);
  const pvM = /\bpv ([a-h][1-8][a-h][1-8][qrbn]?)/i.exec(line);
  const out: { depth?: number; cp?: number; mate?: number; pvUci?: string } = {};
  if (depthM) out.depth = +depthM[1];
  if (mateM) out.mate = +mateM[1];
  else if (cpM) out.cp = +cpM[1];
  if (pvM) out.pvUci = pvM[1];
  return out;
}

/** 0 = Black winning, 1 = White winning. `score` must already be White POV. */
export function whiteShareFromEval(score: EvalScore): number {
  if (score.mate != null) return score.mate > 0 ? 1 : 0;
  if (typeof score.cp === "number") {
    const pawns = score.cp / 100;
    return 1 / (1 + Math.exp(-pawns / 1.5));
  }
  return 0.5;
}

export function formatEvalText(score: EvalScore): string {
  if (score.mate != null) {
    return (score.mate > 0 ? "M" : "-M") + Math.abs(score.mate);
  }
  if (typeof score.cp === "number") {
    const p = score.cp / 100;
    return (p > 0 ? "+" : "") + p.toFixed(1);
  }
  return "—";
}

export function evalFavorsWhite(score: EvalScore): boolean {
  if (score.mate != null) return score.mate > 0;
  return (score.cp ?? 0) >= 0;
}

/** Normalize an /api/eval or WASM payload to White POV for the bar. */
export function displayScore(
  data: EvalScore & { pov?: string; source?: string },
  fen: string,
): EvalScore {
  const raw = { cp: data.cp ?? null, mate: data.mate ?? null };
  if (data.pov === "white") return raw;
  if (data.source === "wasm") return toWhitePov(raw, fenTurn(fen));
  return raw;
}

/**
 * White's fill always represents White. When the board is flipped (Black at
 * the bottom), White's pieces are at the top of the bar, so the fill grows
 * from the top — never invert the percentage.
 */
export function evalBarFill(
  whiteShare: number,
  orientation: BoardOrientation,
): { heightPct: string; anchor: "top" | "bottom" } {
  const clamped = Math.min(1, Math.max(0, whiteShare));
  return {
    heightPct: (clamped * 100).toFixed(1) + "%",
    anchor: orientation === "black" ? "top" : "bottom",
  };
}
