/** Browser Stockfish WASM fallback when the server engine is unavailable. */

export type LocalEvalResult = {
  cp?: number | null;
  mate?: number | null;
  best_san?: string | null;
  pv_san?: string;
  depth: number;
  source: "wasm";
};

type Pending = {
  fen: string;
  resolve: (v: LocalEvalResult) => void;
  reject: (e: Error) => void;
  cp: number | null;
  mate: number | null;
  bestUci: string | null;
  depth: number;
  timer: ReturnType<typeof setTimeout>;
};

let worker: Worker | null = null;
let readyPromise: Promise<Worker> | null = null;
let pending: Pending | null = null;

const WASM_DEPTH = 12;
const WORKER_URLS = [
  "/vendor/stockfish.js",
  "https://cdn.jsdelivr.net/npm/stockfish.js@10.0.2/stockfish.js",
];

function onMessage(e: MessageEvent) {
  if (!pending) return;
  const line = String(e.data || "");
  if (line.startsWith("info")) {
    const depthM = /\bdepth (\d+)/.exec(line);
    const mateM = /\bscore mate (-?\d+)/.exec(line);
    const cpM = /\bscore cp (-?\d+)/.exec(line);
    const pvM = /\bpv ([a-h][1-8][a-h][1-8][qrbn]?)/i.exec(line);
    if (depthM) pending.depth = +depthM[1];
    if (mateM) {
      pending.mate = +mateM[1];
      pending.cp = null;
    } else if (cpM) {
      pending.cp = +cpM[1];
      pending.mate = null;
    }
    if (pvM) pending.bestUci = pvM[1];
    return;
  }
  if (!line.startsWith("bestmove")) return;
  const cur = pending;
  pending = null;
  clearTimeout(cur.timer);
  const bestM = /^bestmove (\S+)/.exec(line);
  const bestUci = bestM?.[1] && bestM[1] !== "(none)" ? bestM[1] : cur.bestUci;
  cur.resolve({
    cp: cur.cp,
    mate: cur.mate,
    best_san: bestUci,
    pv_san: bestUci || "",
    depth: cur.depth || WASM_DEPTH,
    source: "wasm",
  });
}

async function ensureWorker(): Promise<Worker> {
  if (worker) return worker;
  if (readyPromise) return readyPromise;
  readyPromise = (async () => {
    let lastErr: Error | null = null;
    for (const url of WORKER_URLS) {
      try {
        let workerUrl = url;
        if (url.startsWith("http")) {
          const res = await fetch(url);
          if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
          workerUrl = URL.createObjectURL(await res.blob());
        }
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
  if (pending) {
    clearTimeout(pending.timer);
    pending.reject(new Error("superseded"));
    pending = null;
  }
  return new Promise<LocalEvalResult>((resolve, reject) => {
    const timer = setTimeout(() => {
      if (pending) {
        pending = null;
        reject(new Error("Stockfish WASM eval timed out"));
      }
    }, 20000);
    pending = {
      fen,
      resolve,
      reject,
      cp: 0,
      mate: null,
      bestUci: null,
      depth: 0,
      timer,
    };
    w.postMessage("stop");
    w.postMessage(`position fen ${fen}`);
    w.postMessage(`go depth ${WASM_DEPTH}`);
  });
}
