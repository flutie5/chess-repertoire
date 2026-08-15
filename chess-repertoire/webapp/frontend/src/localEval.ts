/** Browser Stockfish WASM fallback when the server engine is unavailable. */

import { fenTurn, parseUciInfoLine, toWhitePov } from "./evalBar";

export type LocalEvalResult = {
  cp?: number | null;
  mate?: number | null;
  best_san?: string | null;
  pv_san?: string;
  depth: number;
  source: "wasm";
  pov: "white";
};

type Pending = {
  fen: string;
  gen: number;
  resolve: (v: LocalEvalResult) => void;
  reject: (e: Error) => void;
  cp: number | null;
  mate: number | null;
  bestUci: string | null;
  depth: number;
  timer: ReturnType<typeof setTimeout>;
};

type Queued = {
  fen: string;
  resolve: (v: LocalEvalResult) => void;
  reject: (e: Error) => void;
};

let worker: Worker | null = null;
let readyPromise: Promise<Worker> | null = null;
let pending: Pending | null = null;
let queued: Queued | null = null;
let stopping = false;
let searchGen = 0;

const WASM_DEPTH = 12;
const WORKER_URLS = [
  "/vendor/stockfish.js",
  "https://cdn.jsdelivr.net/npm/stockfish.js@10.0.2/stockfish.js",
];

function rejectQueued(reason: string) {
  if (!queued) return;
  queued.reject(new Error(reason));
  queued = null;
}

function finishPending(line: string) {
  const cur = pending;
  pending = null;
  if (!cur) return;
  clearTimeout(cur.timer);
  const bestM = /^bestmove (\S+)/.exec(line);
  const bestUci = bestM?.[1] && bestM[1] !== "(none)" ? bestM[1] : cur.bestUci;
  const white = toWhitePov({ cp: cur.cp, mate: cur.mate }, fenTurn(cur.fen));
  cur.resolve({
    cp: white.cp,
    mate: white.mate,
    best_san: bestUci,
    pv_san: bestUci || "",
    depth: cur.depth || WASM_DEPTH,
    source: "wasm",
    pov: "white",
  });
}

function startGo(w: Worker, job: Queued) {
  const myGen = ++searchGen;
  const timer = setTimeout(() => {
    if (pending?.gen !== myGen) return;
    pending = null;
    stopping = true;
    try {
      w.postMessage("stop");
    } catch {
      stopping = false;
    }
    job.reject(new Error("Stockfish WASM eval timed out"));
  }, 20000);
  pending = {
    fen: job.fen,
    gen: myGen,
    resolve: job.resolve,
    reject: job.reject,
    cp: null,
    mate: null,
    bestUci: null,
    depth: 0,
    timer,
  };
  w.postMessage(`position fen ${job.fen}`);
  w.postMessage(`go depth ${WASM_DEPTH}`);
}

function onMessage(e: MessageEvent) {
  const line = String(e.data || "");

  if (line.startsWith("bestmove")) {
    if (stopping) {
      stopping = false;
      const next = queued;
      queued = null;
      if (next && worker) startGo(worker, next);
      return;
    }
    finishPending(line);
    const next = queued;
    queued = null;
    if (next && worker) startGo(worker, next);
    return;
  }

  if (!pending || stopping) return;
  const parsed = parseUciInfoLine(line);
  if (!parsed) return;
  if (parsed.depth != null) pending.depth = parsed.depth;
  if (parsed.mate != null) {
    pending.mate = parsed.mate;
    pending.cp = null;
  } else if (parsed.cp != null) {
    pending.cp = parsed.cp;
    pending.mate = null;
  }
  if (parsed.pvUci) pending.bestUci = parsed.pvUci;
}

async function workerUrlFor(url: string): Promise<string> {
  if (!url.startsWith("http") && !url.startsWith("/")) {
    return url;
  }
  // Same-origin /vendor/stockfish.js used to 200 with SPA HTML when the file
  // was missing — that silently breaks Worker init. Probe content-type/body.
  const res = await fetch(url, { cache: "force-cache" });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  const ctype = (res.headers.get("content-type") || "").toLowerCase();
  const buf = await res.arrayBuffer();
  const head = new TextDecoder().decode(buf.slice(0, 64)).trim().toLowerCase();
  if (
    ctype.includes("text/html") ||
    head.startsWith("<!doctype") ||
    head.startsWith("<html")
  ) {
    throw new Error(`Not a Stockfish worker script: ${url} (${ctype || "unknown type"})`);
  }
  const blob = new Blob([buf], {
    type: ctype.includes("javascript") || ctype.includes("ecmascript")
      ? ctype
      : "application/javascript",
  });
  return URL.createObjectURL(blob);
}

async function ensureWorker(): Promise<Worker> {
  if (worker) return worker;
  if (readyPromise) return readyPromise;
  readyPromise = (async () => {
    let lastErr: Error | null = null;
    for (const url of WORKER_URLS) {
      try {
        const workerUrl = await workerUrlFor(url);
        const w = new Worker(workerUrl);
        await new Promise<void>((resolve, reject) => {
          const t = setTimeout(() => reject(new Error("WASM init timeout")), 15000);
          const onMsg = (ev: MessageEvent) => {
            if (String(ev.data || "").toLowerCase().includes("uciok")) {
              clearTimeout(t);
              w.removeEventListener("message", onMsg);
              resolve();
            }
          };
          w.addEventListener("message", onMsg);
          w.addEventListener("error", () => {
            clearTimeout(t);
            reject(new Error(`Worker error loading ${url}`));
          }, { once: true });
          w.postMessage("uci");
        });
        w.addEventListener("message", onMessage);
        worker = w;
        return w;
      } catch (e) {
        lastErr = e instanceof Error ? e : new Error(String(e));
      }
    }
    readyPromise = null;
    throw lastErr || new Error("Stockfish WASM unavailable");
  })();
  return readyPromise;
}

export async function evalFenLocal(fen: string): Promise<LocalEvalResult> {
  const w = await ensureWorker();
  return new Promise<LocalEvalResult>((resolve, reject) => {
    const job: Queued = { fen, resolve, reject };
    if (pending || stopping) {
      rejectQueued("superseded");
      queued = job;
      if (pending && !stopping) {
        stopping = true;
        clearTimeout(pending.timer);
        pending.reject(new Error("superseded"));
        pending = null;
        w.postMessage("stop");
      }
      return;
    }
    startGo(w, job);
  });
}
