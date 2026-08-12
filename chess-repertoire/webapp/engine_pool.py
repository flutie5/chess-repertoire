"""In-process Stockfish engine pool with tunable Threads/Hash."""

from __future__ import annotations

import atexit
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import chess.engine


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


class EnginePool:
    """Pool of SimpleEngine processes; acquire/release with timeout."""

    def __init__(
        self,
        engine_path: Path | None,
        *,
        size: int | None = None,
        threads: int | None = None,
        hash_mb: int | None = None,
        acquire_timeout: float = 30.0,
    ) -> None:
        self._path = engine_path
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

    def _spawn(self) -> chess.engine.SimpleEngine | None:
        if self._path is None:
            return None
        try:
            engine = chess.engine.SimpleEngine.popen_uci(str(self._path))
            self.configure_full_strength(engine)
            return engine
        except Exception as exc:
            print(f"WARNING: failed to start Stockfish at {self._path}: {exc}", flush=True)
            return None

    def _ensure_started(self) -> bool:
        with self._lock:
            if self._started:
                return not self._failed and bool(self._all)
            self._started = True
            if self._path is None:
                self._failed = True
                print("WARNING: Stockfish binary not found under engine/", flush=True)
                return False
            print(
                f"Starting Stockfish pool size={self._size} at {self._path} "
                f"(Threads={self._threads}, Hash={self._hash_mb})",
                flush=True,
            )
            for _ in range(self._size):
                engine = self._spawn()
                if engine is None:
                    self._failed = True
                    break
                self._all.append(engine)
                self._available.append(engine)
            if not self._all:
                self._failed = True
                return False
            return True

    def status(self, *, start: bool = False) -> dict:
        if start:
            ok = self._ensure_started()
        else:
            with self._lock:
                if not self._started:
                    return {
                        "ok": self._path is not None,
                        "path": str(self._path) if self._path else None,
                        "pool_size": 0,
                        "available": 0,
                        "threads": self._threads,
                        "hash_mb": self._hash_mb,
                        "started": False,
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
            }

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
        with self._cond:
            if broken or engine not in self._all:
                try:
                    engine.quit()
                except Exception:
                    pass
                if engine in self._all:
                    self._all.remove(engine)
                replacement = self._spawn()
                if replacement is not None:
                    self._all.append(replacement)
                    self._available.append(replacement)
            else:
                try:
                    self.configure_full_strength(engine)
                except Exception:
                    try:
                        engine.quit()
                    except Exception:
                        pass
                    if engine in self._all:
                        self._all.remove(engine)
                    replacement = self._spawn()
                    if replacement is not None:
                        self._all.append(replacement)
                        self._available.append(replacement)
                    self._cond.notify()
                    return
                self._available.append(engine)
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
            try:
                engine.quit()
            except Exception:
                pass


def resolve_engine_path(root: Path) -> Path | None:
    override = os.environ.get("STOCKFISH_PATH", "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None
    engine_dir = root / "engine"
    exes = sorted(engine_dir.rglob("stockfish*.exe"))
    if exes:
        return exes[0]
    for path in sorted(engine_dir.rglob("stockfish")):
        if path.is_file() and path.suffix.lower() != ".exe":
            return path
    return None
