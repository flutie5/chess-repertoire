"""Build state.ts + app.ts from _extracted_app.js."""

from __future__ import annotations

import re
from pathlib import Path

src = Path(__file__).resolve().parent.parent / "webapp" / "frontend" / "src"
js = (src / "_extracted_app.js").read_text(encoding="utf-8")

m = re.search(r"const START_FEN = .*?;\n\nconst state = \{.*?\n\};", js, re.S)
if not m:
    raise SystemExit("state block not found")
rest = js[: m.start()] + js[m.end() :]
rest = re.sub(
    r'import \{ Chess \} from "/vendor/chess\.js";\n*',
    "",
    rest,
    count=1,
)

state_ts = '''/** Shared app state + start position. */
export const START_FEN =
  "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

export type BoardPlayers = {
  white: { name: string; rating?: number | string | null };
  black: { name: string; rating?: number | string | null };
} | null;

export type AppState = {
  report: unknown;
  range: { preset: string; since: string | null; until: string | null };
  color: string;
  orientation: string;
  ply: number;
  sans: string[];
  fens: string[];
  moves: unknown[];
  moveFlags: unknown[];
  mainLineSans: string[];
  exploreAnns: Record<string | number, unknown>;
  lastOpeningName: string | null;
  editMode: boolean;
  boardPlayers: BoardPlayers;
  repertoireOpen: boolean;
  repColor: string;
  myRepertoire: { white: unknown[]; black: unknown[] };
  practiceMode: boolean;
  practiceElo: number;
  openingSort: string;
  gameView: boolean;
  activeGame: unknown;
  reviewLesson: unknown;
};

export const state: AppState = {
  report: null,
  range: { preset: "30d", since: null, until: null },
  color: "white",
  orientation: "white",
  ply: 0,
  sans: [],
  fens: [START_FEN],
  moves: [],
  moveFlags: [],
  mainLineSans: [],
  exploreAnns: {},
  lastOpeningName: null,
  editMode: false,
  boardPlayers: null,
  repertoireOpen: false,
  repColor: "white",
  myRepertoire: { white: [], black: [] },
  practiceMode: false,
  practiceElo: 1800,
  openingSort: "frequent",
  gameView: false,
  activeGame: null,
  reviewLesson: null,
};
'''
(src / "state.ts").write_text(state_ts, encoding="utf-8")

app_ts = (
    "//@ts-nocheck\n"
    "/** Legacy SPA logic extracted from the monolith index.html. */\n"
    'import { Chess } from "/vendor/chess.js";\n'
    'import { state, START_FEN } from "./state";\n'
    "\n"
    + rest.lstrip()
    + "\n"
)
(src / "app.ts").write_text(app_ts, encoding="utf-8")
print("wrote state.ts + app.ts; app lines", app_ts.count("\n"))
