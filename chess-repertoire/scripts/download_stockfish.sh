#!/usr/bin/env bash
# Download official Stockfish Linux binary for Render builds.
# Prefer the generic x86-64 build (not AVX2/BMI2) so free-tier VMs without
# those instruction sets do not SIGILL at runtime.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$ROOT/engine/linux"
SF_TAG="${STOCKFISH_VERSION:-sf_17.1}"
# Portable first; sse41 as fallback if the generic archive ever disappears.
ARCHIVES=(
  "${STOCKFISH_ARCHIVE:-stockfish-ubuntu-x86-64.tar}"
  "stockfish-ubuntu-x86-64-sse41-popcnt.tar"
)

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

download_and_install() {
  local archive="$1"
  local url="https://github.com/official-stockfish/Stockfish/releases/download/${SF_TAG}/${archive}"
  echo "Downloading Stockfish ${SF_TAG} (${archive})..."
  curl -fsSL -o "$TMP/$archive" "$url"
  rm -rf "$TMP/extract"
  mkdir -p "$TMP/extract"
  tar -xf "$TMP/$archive" -C "$TMP/extract"

  # Prefer the baseline binary name; never pick *avx* / *bmi* / *vnni* by accident.
  local bin=""
  bin="$(find "$TMP/extract" -type f -name 'stockfish-ubuntu-x86-64' -print | head -n 1 || true)"
  if [[ -z "$bin" ]]; then
    bin="$(find "$TMP/extract" -type f -name 'stockfish-ubuntu-x86-64-sse41-popcnt' -print | head -n 1 || true)"
  fi
  if [[ -z "$bin" ]]; then
    bin="$(find "$TMP/extract" -type f \( -name 'stockfish-ubuntu*' -o -name 'stockfish' \) \
      ! -name '*.exe' ! -name '*avx*' ! -name '*bmi*' ! -name '*vnni*' -print | head -n 1 || true)"
  fi
  if [[ -z "$bin" || ! -f "$bin" ]]; then
    echo "ERROR: stockfish binary not found inside ${archive}" >&2
    echo "Archive contents:" >&2
    find "$TMP/extract" -type f | head -n 40 >&2
    return 1
  fi

  mkdir -p "$ENGINE_DIR"
  install -m 755 "$bin" "$ENGINE_DIR/stockfish"
  echo "Installed Stockfish at ${ENGINE_DIR}/stockfish from ${archive} ($(du -h "$ENGINE_DIR/stockfish" | cut -f1))"

  # Smoke-test: binary must start and accept UCI (wrong ISA / missing libs).
  if ! printf 'uci\nquit\n' | "$ENGINE_DIR/stockfish" >/dev/null 2>&1; then
    echo "ERROR: Stockfish failed UCI smoke test at ${ENGINE_DIR}/stockfish" >&2
    return 1
  fi
  echo "Stockfish UCI smoke test OK"
  return 0
}

INSTALLED=0
for ARCHIVE in "${ARCHIVES[@]}"; do
  if download_and_install "$ARCHIVE"; then
    INSTALLED=1
    break
  fi
  echo "WARNING: failed to install ${ARCHIVE}, trying next fallback…" >&2
done

if [[ "$INSTALLED" -ne 1 ]]; then
  echo "ERROR: could not download a working Stockfish binary" >&2
  exit 1
fi
