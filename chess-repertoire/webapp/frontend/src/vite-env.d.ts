/// <reference types="vite/client" />

declare module "@vendor/chess.js" {
  export class Chess {
    constructor(fen?: string);
    fen(): string;
    turn(): "w" | "b";
    moves(options?: { verbose?: boolean; square?: string }): unknown;
    move(move: unknown, options?: unknown): unknown;
    undo(): unknown;
    isGameOver(): boolean;
    isCheck(): boolean;
    isCheckmate(): boolean;
    isDraw(): boolean;
    load(fen: string): void;
    reset(): void;
    history(options?: { verbose?: boolean }): unknown;
    board(): unknown;
    get(square: string): unknown;
    put(piece: unknown, square: string): boolean;
    remove(square: string): unknown;
    ascii(): string;
  }
}

declare module "/vendor/chess.js" {
  export { Chess } from "@vendor/chess.js";
}
