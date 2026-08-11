/**
 * Module map (gaps 26–30). Most UI logic still lives in app.ts after the
 * mechanical extract; these entry points are the split targets going forward.
 *
 * - state.ts — shared AppState
 * - api.ts — API error helpers (fetch lives in app.ts for now)
 * - board/a11y.ts — keyboard + ARIA
 * - app.ts — board, report, auth, billing, repertoire, practice, analytics
 * - main.ts — boots styles + app + a11y
 */
export { state, START_FEN } from "./state";
export type { AppState } from "./state";
export { isLoginRequired, isProRequired } from "./api";
