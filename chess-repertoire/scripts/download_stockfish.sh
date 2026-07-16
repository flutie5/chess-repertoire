#!/usr/bin/env bash
# Download official Stockfish Linux binary for Render builds.
# Use the generic x86-64 build (not AVX2) so free-tier VMs without AVX2 still run.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$ROOT/engine/linux"
SF_TAG="${STOCKFISH_VERSION:-sf_17.1}"
# Portable binary: works on any 64-bit x86. AVX2 builds SIGILL on some hosts.
ARCHIVE="${STOCKFISH_ARCHIVE:-stockfish-ubuntu-x86-64.tar}"
URL="https://github.com/official-stockfish/Stockfish/releases/download/${SF_TAG}/${ARCHIVE}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading Stockfish ${SF_TAG} (${ARCHIVE})..."
curl -fsSL -o "$TMP/$ARCHIVE" "$URL"
tar -xf "$TMP/$ARCHIVE" -C "$TMP"

# Release tarball names the binary stockfish-ubuntu-x86-64* (not plain "stockfish").
BIN="$(find "$TMP" -type f \( -name 'stockfish-ubuntu*' -o -name 'stockfish' \) ! -name '*.exe' -print | head -n 1)"
if [[ -z "${BIN}" || ! -x "$BIN" && ! -f "$BIN" ]]; then
  echo "ERROR: stockfish binary not found inside ${ARCHIVE}" >&2
  echo "Archive contents:" >&2
  find "$TMP" -type f | head -n 40 >&2
  exit 1
fi

mkdir -p "$ENGINE_DIR"
install -m 755 "$BIN" "$ENGINE_DIR/stockfish"
echo "Installed Stockfish at ${ENGINE_DIR}/stockfish ($(du -h "$ENGINE_DIR/stockfish" | cut -f1))"

# Smoke-test: binary must start and accept UCI (catches wrong ISA / missing libs at build time).
if ! printf 'uci\nquit\n' | "$ENGINE_DIR/stockfish" >/dev/null 2>&1; then
  echo "ERROR: Stockfish failed UCI smoke test at ${ENGINE_DIR}/stockfish" >&2
  exit 1
fi
echo "Stockfish UCI smoke test OK"
