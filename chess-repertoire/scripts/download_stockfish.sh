#!/usr/bin/env bash
# Download official Stockfish Linux AVX2 binary for Render builds.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$ROOT/engine/linux"
SF_TAG="${STOCKFISH_VERSION:-sf_17.1}"
ARCHIVE="stockfish-ubuntu-x86-64-avx2.tar"
URL="https://github.com/official-stockfish/Stockfish/releases/download/${SF_TAG}/${ARCHIVE}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading Stockfish ${SF_TAG} (${ARCHIVE})..."
curl -fsSL -o "$TMP/$ARCHIVE" "$URL"
tar -xf "$TMP/$ARCHIVE" -C "$TMP"

BIN="$(find "$TMP" -type f -name stockfish ! -name '*.exe' -print -quit)"
if [[ -z "$BIN" || ! -f "$BIN" ]]; then
  echo "ERROR: stockfish binary not found inside ${ARCHIVE}" >&2
  exit 1
fi

mkdir -p "$ENGINE_DIR"
install -m 755 "$BIN" "$ENGINE_DIR/stockfish"
echo "Installed Stockfish at ${ENGINE_DIR}/stockfish"
