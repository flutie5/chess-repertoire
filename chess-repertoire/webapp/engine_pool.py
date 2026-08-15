"""In-process Stockfish engine pool with tunable Threads/Hash.

Resolves a primary binary plus optional fallbacks (STOCKFISH_PATH,
STOCKFISH_FALLBACK_PATH, engine/linux/stockfish, stockfish-fallback).
On spawn failure the pool advances to the next candidate so a corrupt
or wrong-ISA primary does not brick eval/annotate for the whole process.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import chess
import chess.engine


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def resolve_engine_candidates(root: Path) -> list[Path]:
    """Ordered unique Stockfish binaries to try (primary → fallbacks)."""
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path | None) -> None:
        if path is None or not path.is_file():
            return
        key = str(path.resolve())
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    override = os.environ.get("STOCKFISH_PATH", "").strip()
    if override:
        add(Path(override))
    fallback_env = os.environ.get("STOCKFISH_FALLBACK_PATH", "").strip()
    if fallback_env:
        add(Path(fallback_env))

    engine_dir = root / "engine"
    # Prefer the canonical Linux install layout first.
    add(engine_dir / "linux" / "stockfish")
    add(engine_dir / "linux" / "stockfish-fallback")

    # Windows local builds (any nested stockfish*.exe).
    for path in sorted(engine_dir.rglob("stockfish*.exe")):
        add(path)

    # Any other non-.exe stockfish binary under engine/ (Linux builds, etc.).
    # Skip docs/scripts — rglob("stockfish*") otherwise picks wiki/*.md.
    _skip_suffixes = {".md", ".txt", ".html", ".htm", ".json", ".yml", ".yaml", ".py", ".sh"}
    for path in sorted(engine_dir.rglob("stockfish*")):
        if not path.is_file() or path.suffix.lower() == ".exe":
            continue
        if path.suffix.lower() in _skip_suffixes:
            continue
        if not os.access(path, os.X_OK) and not sys.platform.startswith("win"):
            continue
        add(path)

    return found


def resolve_engine_path(root: Path) -> Path | None:
    """Back-compat: first candidate, or None."""
    cands = resolve_engine_candidates(root)
    return cands[0] if cands else None


def ensure_engine_binaries(root: Path) -> list[Path]:
    """Return candidates, downloading on Linux when none are present.

    Controlled by ENGINE_DOWNLOAD_ON_MISSING (default on for Linux).
    Never raises — returns [] if download fails.
    """
    cands = resolve_engine_candidates(root)
    if cands:
        return cands

    download = _env_flag(
        "ENGINE_DOWNLOAD_ON_MISSING",
        default=sys.platform.startswith("linux"),
    )
    if not download:
        return []

    script = root / "scripts" / "download_stockfish.sh"
    if not script.is_file():
        print(f"WARNING: Stockfish missing and no download script at {script}", flush=True)
        return []

    print(f"Stockfish missing — running {script} …", flush=True)
    try:
        subprocess.run(
            ["bash", str(script)],
            cwd=str(root),
            check=False,
            timeout=180,
        )
    except Exception as exc:
        print(f"WARNING: Stockfish download failed: {exc}", flush=True)
        return resolve_engine_candidates(root)

    return resolve_engine_candidates(root)


def uci_smoke_test(path: Path, timeout: float = 10.0) -> bool:
    """True if binary starts and answers UCI."""
    try:
        proc = subprocess.run(
            [str(path)],
            input="uci\nquit\n",
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return "uciok" in out.lower() or proc.returncode == 0
    except Exception as exc:
        print(f"WARNING: UCI smoke failed for {path}: {exc}", flush=True)
        return False


class EnginePool:
    """Pool of SimpleEngine processes; acquire/release with timeout."""

    def __init__(
        self,
        engine_path: Path | None = None,
        *,
        candidates: list[Path] | None = None,
        size: int | None = None,
        threads: int | None = None,
        hash_mb: int | None = None,
        acquire_timeout: float = 30.0,
    ) -> None:
        if candidates is not None:
            self._candidates = [p for p in candidates if p is not None]
        elif engine_path is not None:
            self._candidates = [engine_path]
        else:
            self._candidates = []
        self._path = self._candidates[0] if self._candidates else None
        self._size = size if size is not None else _env_int("ENGINE_POOL_SIZE", 2)
        self._threads = threads if threads is not None else _env_int("ENGINE_THREADS", 1)
        self._hash_mb = hash_mb if hash_mb is not None else _env_int("ENGINE_HASH_MB", 16)
        self._acquire_timeout = acquire_timeout
        self._lock = threading.Lock()
        self._available: list[chess.engine.SimpleEngine] = []
        self._all: list[chess.engine.SimpleEngine] = []
        self._cond = threading.Condition(self._lock)
        self._started = False
        self._failed = False
        self._active_index = 0

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def size(self) -> int:
        return self._size

    def available_count(self) -> int:
        with self._lock:
            return len(self._available)

    def configure_full_strength(self, engine: chess.engine.SimpleEngine) -> None:
        try:
            engine.configure({
                "Threads": self._threads,
                "Hash": self._hash_mb,
                "UCI_LimitStrength": False,
                "Skill Level": 20,
            })
        except Exception as exc:
            print(f"WARNING: Stockfish configure failed: {exc}", flush=True)

    def _spawn_at(self, path: Path) -> chess.engine.SimpleEngine | None:
        try:
            engine = chess.engine.SimpleEngine.popen_uci(str(path))
            self.configure_full_strength(engine)
            return engine
        except Exception as exc:
            print(f"WARNING: failed to start Stockfish at {path}: {exc}", flush=True)
            return None

    def _spawn(self) -> chess.engine.SimpleEngine | None:
        if self._path is None:
            return None
        return self._spawn_at(self._path)

    def _try_next_candidate(self) -> bool:
        """Advance to the next binary candidate. Returns True if one remains."""
        while self._active_index + 1 < len(self._candidates):
            self._active_index += 1
            nxt = self._candidates[self._active_index]
            print(f"Stockfish: trying fallback binary {nxt}", flush=True)
            if not uci_smoke_test(nxt):
                print(f"WARNING: fallback failed UCI smoke: {nxt}", flush=True)
                continue
            self._path = nxt
            return True
        return False

    def _ensure_started(self) -> bool:
        with self._lock:
            if self._started:
                return not self._failed and bool(self._all)
            self._started = True
            if not self._candidates:
                self._failed = True
                print("WARNING: Stockfish binary not found under engine/", flush=True)
                return False

            # Skip candidates that fail a cheap UCI smoke before pool spawn.
            while self._active_index < len(self._candidates):
                cand = self._candidates[self._active_index]
                if uci_smoke_test(cand):
                    self._path = cand
                    break
                print(f"WARNING: Stockfish UCI smoke failed for {cand}", flush=True)
                self._active_index += 1
            else:
                self._failed = True
                self._path = None
                return False

            print(
                f"Starting Stockfish pool size={self._size} at {self._path} "
                f"(Threads={self._threads}, Hash={self._hash_mb}, "
                f"candidates={len(self._candidates)})",
                flush=True,
            )

            while True:
                self._all.clear()
                self._available.clear()
                spawn_ok = True
                for _ in range(self._size):
                    engine = self._spawn()
                    if engine is None:
                        spawn_ok = False
                        break
                    self._all.append(engine)
                    self._available.append(engine)
                if spawn_ok and self._all:
                    self._failed = False
                    return True
                # Tear down partial pool and try next binary.
                for engine in list(self._all):
                    _force_kill_engine(engine, wait=1.0)
                self._all.clear()
                self._available.clear()
                if not self._try_next_candidate():
                    self._failed = True
                    return False

    def status(self, *, start: bool = False) -> dict:
        if start:
            ok = self._ensure_started()
        else:
            with self._lock:
                if not self._started:
                    return {
                        "ok": self._path is not None or bool(self._candidates),
                        "path": str(self._path) if self._path else None,
                        "pool_size": 0,
                        "available": 0,
                        "threads": self._threads,
                        "hash_mb": self._hash_mb,
                        "started": False,
                        "candidates": [str(p) for p in self._candidates],
                    }
                ok = not self._failed and bool(self._all)
        with self._lock:
            return {
                "ok": ok if start or self._started else (self._path is not None),
                "path": str(self._path) if self._path else None,
                "pool_size": len(self._all),
                "available": len(self._available),
                "threads": self._threads,
                "hash_mb": self._hash_mb,
                "started": self._started,
                "candidates": [str(p) for p in self._candidates],
            }

    def warmup(self) -> dict:
        """Start the pool and run a timed analysis smoke test."""
        st = self.status(start=True)
        if not st.get("ok"):
            return {**st, "warmup": False, "error": "engine failed to start"}
        try:
            board = chess.Board()
            with self.acquire(timeout=20) as engine:
                # Time-capped so boot cannot hang the web process.
                engine.analyse(board, chess.engine.Limit(depth=4, time=1.0))
            print(f"Stockfish warmup OK at {self._path}", flush=True)
            return {**self.status(), "warmup": True}
        except Exception as exc:
            print(f"WARNING: Stockfish warmup failed: {exc}", flush=True)
            return {**self.status(), "warmup": False, "error": str(exc)}

    def try_acquire(
        self, timeout: float | None = None
    ) -> chess.engine.SimpleEngine | None:
        import time as _time

        if not self._ensure_started():
            return None
        wait = self._acquire_timeout if timeout is None else timeout
        with self._cond:
            end = _time.monotonic() + wait if wait is not None and wait >= 0 else None
            while not self._available:
                if end is None:
                    self._cond.wait()
                    continue
                remaining = end - _time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=remaining)
            return self._available.pop()

    def release(
        self,
        engine: chess.engine.SimpleEngine,
        *,
        broken: bool = False,
    ) -> None:
        # IMPORTANT: never call quit()/kill while holding _cond — a wedged
        # Stockfish would block every other acquire on the only Gunicorn worker.
        dispose = False
        with self._cond:
            if broken or engine not in self._all:
                dispose = True
                if engine in self._all:
                    self._all.remove(engine)
                if engine in self._available:
                    self._available.remove(engine)
            else:
                try:
                    self.configure_full_strength(engine)
                except Exception:
                    dispose = True
                    if engine in self._all:
                        self._all.remove(engine)
                    if engine in self._available:
                        self._available.remove(engine)
                else:
                    self._available.append(engine)
                    self._cond.notify()
                    return

        if dispose:
            _force_kill_engine(engine, wait=1.0)
            replacement = self._spawn()
            if replacement is None and self._try_next_candidate():
                replacement = self._spawn()
            with self._cond:
                if replacement is not None:
                    self._all.append(replacement)
                    self._available.append(replacement)
                self._cond.notify()

    @contextmanager
    def acquire(
        self, timeout: float | None = None
    ) -> Iterator[chess.engine.SimpleEngine]:
        if not self._ensure_started():
            raise RuntimeError("Stockfish engine not found")
        engine = self.try_acquire(timeout=timeout)
        if engine is None:
            raise TimeoutError("engine pool saturated")
        broken = False
        try:
            yield engine
        except (chess.engine.EngineTerminatedError, chess.engine.EngineError):
            broken = True
            raise
        finally:
            self.release(engine, broken=broken)

    def shutdown(self) -> None:
        with self._lock:
            engines = list(self._all)
            self._all.clear()
            self._available.clear()
            self._failed = False
            self._started = False
        for engine in engines:
            _force_kill_engine(engine, wait=1.0)


def _engine_proc(engine: chess.engine.SimpleEngine):
    """Return the underlying subprocess.Popen if available."""
    transport = getattr(engine, "transport", None)
    if transport is None:
        return None
    return getattr(transport, "_proc", None) or getattr(transport, "process", None)


def _force_kill_engine(engine: chess.engine.SimpleEngine, *, wait: float = 1.0) -> None:
    """Dispose a Stockfish process without blocking the request thread.

    engine.quit() can hang forever if analyse is wedged. Prefer a timed quit
    thread, then OS-level kill via the asyncio subprocess transport.
    """
    done = threading.Event()

    def _quit() -> None:
        try:
            engine.quit()
        except Exception:
            pass
        finally:
            done.set()

    threading.Thread(target=_quit, daemon=True, name="sf-quit").start()
    if done.wait(timeout=wait):
        return

    # quit() hung — hard-kill the OS process.
    try:
        transport = getattr(engine, "transport", None)
        if transport is not None and hasattr(transport, "kill"):
            transport.kill()
    except Exception as exc:
        print(f"WARNING: transport.kill failed: {exc}", flush=True)
    proc = _engine_proc(engine)
    if proc is not None:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception as exc:
            print(f"WARNING: proc.kill failed: {exc}", flush=True)


def create_engine_pool(root: Path) -> EnginePool:
    """Resolve/download binaries and build a pool (does not start processes)."""
    cands = ensure_engine_binaries(root)
    return EnginePool(candidates=cands)
