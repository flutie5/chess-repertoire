/** Board keyboard navigation + screen-reader announcements. */

const FILES = "abcdefgh";

function squareName(file: number, rank: number): string {
  return `${FILES[file]}${rank + 1}`;
}

function parseSquare(name: string): { file: number; rank: number } | null {
  if (!/^[a-h][1-8]$/.test(name)) return null;
  return { file: name.charCodeAt(0) - 97, rank: Number(name[1]) - 1 };
}

function pieceLabel(code: string | null | undefined): string {
  if (!code) return "empty";
  const color = code === code.toUpperCase() ? "white" : "black";
  const map: Record<string, string> = {
    p: "pawn",
    n: "knight",
    b: "bishop",
    r: "rook",
    q: "queen",
    k: "king",
  };
  const kind = map[code.toLowerCase()] || "piece";
  return `${color} ${kind}`;
}

export function announce(message: string): void {
  const live = document.getElementById("board-live");
  if (!live) return;
  live.textContent = "";
  // Force a DOM change so SR re-reads.
  requestAnimationFrame(() => {
    live.textContent = message;
  });
}

export function enhanceBoardAccessibility(): void {
  const board = document.getElementById("board");
  if (!board) return;

  board.setAttribute("role", "grid");
  board.setAttribute("aria-label", "Chess board");
  board.setAttribute("tabindex", "0");

  const syncSquareLabels = () => {
    board.querySelectorAll<HTMLElement>(".sq[data-square]").forEach((el) => {
      const sq = el.dataset.square || "";
      const img = el.querySelector("img");
      const code = img?.getAttribute("data-piece") || img?.alt || "";
      const label = `${sq}, ${pieceLabel(code || null)}`;
      el.setAttribute("role", "gridcell");
      el.setAttribute("aria-label", label);
      if (!el.hasAttribute("tabindex")) el.tabIndex = -1;
    });
  };

  syncSquareLabels();
  const mo = new MutationObserver(() => syncSquareLabels());
  mo.observe(board, { childList: true, subtree: true });

  let focusSq = "e4";

  const focusSquare = (name: string) => {
    const el = board.querySelector<HTMLElement>(`.sq[data-square="${name}"]`);
    if (!el) return;
    board.querySelectorAll(".sq[data-focused]").forEach((n) => {
      n.removeAttribute("data-focused");
      (n as HTMLElement).tabIndex = -1;
    });
    el.dataset.focused = "1";
    el.tabIndex = 0;
    el.focus({ preventScroll: true });
    focusSq = name;
    announce(el.getAttribute("aria-label") || name);
  };

  board.addEventListener("keydown", (e) => {
    const tag = ((e.target as HTMLElement)?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;

    const cur = parseSquare(focusSq) || { file: 4, rank: 3 };
    let { file, rank } = cur;
    const orientationWhite =
      !document.getElementById("board")?.classList.contains("flipped");

    const up = () => {
      rank += orientationWhite ? 1 : -1;
    };
    const down = () => {
      rank += orientationWhite ? -1 : 1;
    };
    const left = () => {
      file += orientationWhite ? -1 : 1;
    };
    const right = () => {
      file += orientationWhite ? 1 : -1;
    };

    if (e.key === "ArrowUp") {
      e.preventDefault();
      up();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      down();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      left();
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      right();
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      const el = board.querySelector<HTMLElement>(
        `.sq[data-square="${focusSq}"]`,
      );
      el?.click();
      announce(`Selected ${focusSq}`);
      return;
    } else {
      return;
    }

    file = Math.max(0, Math.min(7, file));
    rank = Math.max(0, Math.min(7, rank));
    focusSquare(squareName(file, rank));
  });

  // Seed focus when tabbing into the board.
  board.addEventListener("focusin", () => {
    if (!board.querySelector(".sq[data-focused]")) focusSquare(focusSq);
  });
}

export function watchMovesForAnnouncements(): void {
  const list = document.getElementById("move-list") || document.getElementById("game-move-list");
  if (!list) return;
  const mo = new MutationObserver(() => {
    const last = list.querySelector(".move.active, .san.active, button.active");
    const text = last?.textContent?.trim();
    if (text) announce(`Move ${text}`);
  });
  mo.observe(list, { childList: true, subtree: true, attributes: true });
}
