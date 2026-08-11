/** Shared app state + start position. */
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
