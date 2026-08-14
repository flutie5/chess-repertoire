#!/usr/bin/env bash
# Download official Stockfish Linux binaries for Render builds.
# Installs a primary + fallback binary so a bad ISA / corrupt primary
# can be swapped without a full redeploy dance.
#
# Prefer generic x86-64 builds (not AVX2/BMI2) so free-tier VMs without
# those instruction sets do not SIGILL at runtime.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$ROOT/engine/linux"
PRIMARY_TAG="${STOCKFISH_VERSION:-sf_17.1}"
FALLBACK_TAG="${STOCKFISH_FALLBACK_VERSION:-sf_16}"

# Portable first; sse41 as fallback if the generic archive ever disappears.
PRIMARY_ARCHIVES=(
  "${STOCKFISH_ARCHIVE:-stockfish-ubuntu-x86-64.tar}"
  "stockfish-ubuntu-x86-64-sse41-popcnt.tar"
)
FALLBACK_ARCHIVES=(
  "${STOCKFISH_FALLBACK_ARCHIVE:-stockfish-ubuntu-x86-64.tar}"
  "stockfish-ubuntu-x86-64-sse41-popcnt.tar"
)

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pick_binary() {
  local extract_dir="$1"
  local bin=""
  bin="$(find "$extract_dir" -type f -name 'stockfish-ubuntu-x86-64' -print | head -n 1 || true)"
  if [[ -z "$bin" ]]; then
    bin="$(find "$extract_dir" -type f -name 'stockfish-ubuntu-x86-64-sse41-popcnt' -print | head -n 1 || true)"
  fi
  if [[ -z "$bin" ]]; then
    bin="$(find "$extract_dir" -type f \( -name 'stockfish-ubuntu*' -o -name 'stockfish' \) \
      ! -name '*.exe' ! -name '*avx*' ! -name '*bmi*' ! -name '*vnni*' -print | head -n 1 || true)"
  fi
  printf '%s' "$bin"
}

uci_smoke() {
  local bin="$1"
  printf 'uci\nquit\n' | "$bin" >/dev/null 2>&1
}

download_and_install() {
  local tag="$1"
  local archive="$2"
  local dest_name="$3"
  local url="https://github.com/official-stockfish/Stockfish/releases/download/${tag}/${archive}"
  echo "Downloading Stockfish ${tag} (${archive}) → ${dest_name}..."
  curl -fsSL -o "$TMP/$archive" "$url"
  rm -rf "$TMP/extract"
  mkdir -p "$TMP/extract"
  tar -xf "$TMP/$archive" -C "$TMP/extract"

  local bin
  bin="$(pick_binary "$TMP/extract")"
  if [[ -z "$bin" || ! -f "$bin" ]]; then
    echo "ERROR: stockfish binary not found inside ${archive}" >&2
    echo "Archive contents:" >&2
    find "$TMP/extract" -type f | head -n 40 >&2
    return 1
  fi

  mkdir -p "$ENGINE_DIR"
  install -m 755 "$bin" "$ENGINE_DIR/$dest_name"

  if ! uci_smoke "$ENGINE_DIR/$dest_name"; then
    echo "ERROR: Stockfish failed UCI smoke test at ${ENGINE_DIR}/${dest_name}" >&2
    rm -f "$ENGINE_DIR/$dest_name"
    return 1
  fi
  echo "Installed Stockfish at ${ENGINE_DIR}/${dest_name} from ${tag}/${archive} ($(du -h "$ENGINE_DIR/$dest_name" | cut -f1))"
  return 0
}

install_role() {
  local tag="$1"
  local dest_name="$2"
  shift 2
  local archives=("$@")
  local archive
  for archive in "${archives[@]}"; do
    if download_and_install "$tag" "$archive" "$dest_name"; then
      return 0
    fi
    echo "WARNING: failed to install ${tag}/${archive} as ${dest_name}, trying next…" >&2
  done
  return 1
}

if ! install_role "$PRIMARY_TAG" "stockfish" "${PRIMARY_ARCHIVES[@]}"; then
  echo "ERROR: could not download a working primary Stockfish binary" >&2
  exit 1
fi

# Fallback is best-effort — primary alone is enough to boot, but we want
# a second binary when GitHub still has the older release.
if ! install_role "$FALLBACK_TAG" "stockfish-fallback" "${FALLBACK_ARCHIVES[@]}"; then
  echo "WARNING: fallback Stockfish (${FALLBACK_TAG}) unavailable; continuing with primary only" >&2
  # Mirror primary so resolve_engine_candidates always has two paths to try
  # after a partial pool spawn failure against a corrupted primary inode.
  if [[ -x "$ENGINE_DIR/stockfish" && ! -x "$ENGINE_DIR/stockfish-fallback" ]]; then
    cp -f "$ENGINE_DIR/stockfish" "$ENGINE_DIR/stockfish-fallback"
    chmod 755 "$ENGINE_DIR/stockfish-fallback"
    echo "Copied primary → stockfish-fallback as last-resort duplicate"
  fi
fi

echo "Stockfish binaries ready:"
ls -la "$ENGINE_DIR"/stockfish "$ENGINE_DIR"/stockfish-fallback 2>/dev/null || ls -la "$ENGINE_DIR"
