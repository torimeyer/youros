#!/usr/bin/env bash
# build-frontend-atomic.sh
#
# Builds the frontend static bundle into an isolated staging directory on the
# same filesystem as app/dist, retains recent hashed asset chunks so open
# browser tabs can still lazy-load routes prior to refresh, validates the
# staged index.html, and atomically swaps app/dist in place (task ->3111).
#
# Configurable environment variables:
#   APP_DIR                  Frontend app directory (default: <repo>/app)
#   DIST_DIR                 Target static bundle directory (default: $APP_DIR/dist)
#   BUILD_CMD                Optional override command that populates $STAGE_DIR
#   CHUNK_RETENTION_MINUTES  Minutes to retain prior hashed chunks (default: 1440)

set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../app" && pwd)}"
DIST_DIR="${DIST_DIR:-$APP_DIR/dist}"
BUILD_CMD="${BUILD_CMD:-}"
CHUNK_RETENTION_MINUTES="${CHUNK_RETENTION_MINUTES:-1440}"

DIST_PARENT="$(dirname "$DIST_DIR")"
mkdir -p "$DIST_PARENT"
DIST_DIR="$(cd "$DIST_PARENT" && pwd)/$(basename "$DIST_DIR")"

STAGE_DIR="${DIST_DIR}.next.$$"
PREV_DIR="${DIST_DIR}.prev.$$"
LOCK_DIR="${DIST_DIR}.swap.lock"
LOCK_HELD=0

cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [ ! -e "$DIST_DIR" ] && [ -d "$PREV_DIR" ]; then
    mv "$PREV_DIR" "$DIST_DIR" 2>/dev/null || true
  fi
  if [ "$LOCK_HELD" -eq 1 ]; then
    rm -rf "$LOCK_DIR" 2>/dev/null || true
    LOCK_HELD=0
  fi
  rm -rf "$STAGE_DIR" "$PREV_DIR" 2>/dev/null || true
  exit "$rc"
}

on_signal() {
  exit 130
}

trap cleanup EXIT
trap on_signal INT TERM

acquire_swap_lock() {
  local attempts=0
  local max_attempts=600
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    if [ -f "$LOCK_DIR/pid" ]; then
      local holder_pid
      holder_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
      if [ -n "$holder_pid" ] && ! kill -0 "$holder_pid" 2>/dev/null; then
        rm -rf "$LOCK_DIR" 2>/dev/null || true
        continue
      fi
    fi
    attempts=$((attempts + 1))
    if [ "$attempts" -ge "$max_attempts" ]; then
      echo "build-frontend-atomic: timed out waiting for swap lock $LOCK_DIR" >&2
      exit 1
    fi
    sleep 0.05
  done
  LOCK_HELD=1
  printf '%s\n' "$$" > "$LOCK_DIR/pid"
}

release_swap_lock() {
  if [ "$LOCK_HELD" -eq 1 ]; then
    rm -rf "$LOCK_DIR" 2>/dev/null || true
    LOCK_HELD=0
  fi
}

# Kernel-level single-syscall directory exchange where available (APFS/HFS+
# renamex_np RENAME_SWAP on macOS; renameat2 RENAME_EXCHANGE on Linux). Falls
# back to POSIX two-step mv with rollback when unsupported.
atomic_exchange_dirs() {
  local src="$1"
  local dst="$2"
  if [ "${FORCE_POSIX_SWAP:-0}" = "1" ]; then
    return 1
  fi
  command -v python3 >/dev/null 2>&1 || return 1
  python3 - "$src" "$dst" <<'PY' 2>/dev/null
import ctypes, os, sys
src = os.fsencode(sys.argv[1])
dst = os.fsencode(sys.argv[2])
libc = ctypes.CDLL(None, use_errno=True)
if hasattr(libc, "renamex_np"):
    fn = libc.renamex_np
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    sys.exit(0 if fn(src, dst, 0x00000002) == 0 else 1)
if hasattr(libc, "renameat2"):
    fn = libc.renameat2
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    sys.exit(0 if fn(-100, src, -100, dst, 2) == 0 else 1)
sys.exit(1)
PY
}

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
export APP_DIR DIST_DIR STAGE_DIR CHUNK_RETENTION_MINUTES

if [ -n "$BUILD_CMD" ]; then
  if [ -d "$APP_DIR" ]; then
    (cd "$APP_DIR" && eval "$BUILD_CMD")
  else
    eval "$BUILD_CMD"
  fi
else
  (cd "$APP_DIR" && npx tsc -b && npx vite build --outDir "$STAGE_DIR" --emptyOutDir)
fi

if [ ! -s "$STAGE_DIR/index.html" ]; then
  echo "build-frontend-atomic: staged index.html is missing or empty at $STAGE_DIR/index.html" >&2
  exit 1
fi

acquire_swap_lock

if [ -d "$DIST_DIR/assets" ] && [ "${CHUNK_RETENTION_MINUTES:-0}" -gt 0 ]; then
  mkdir -p "$STAGE_DIR/assets"
  while IFS= read -r -d '' f; do
    base="$(basename "$f")"
    [ -e "$STAGE_DIR/assets/$base" ] || cp -p "$f" "$STAGE_DIR/assets/$base"
  done < <(find "$DIST_DIR/assets" -maxdepth 1 -type f -mmin "-$CHUNK_RETENTION_MINUTES" -print0)
fi

if [ -e "$DIST_DIR" ]; then
  if atomic_exchange_dirs "$STAGE_DIR" "$DIST_DIR"; then
    mv "$STAGE_DIR" "$PREV_DIR" 2>/dev/null && rm -rf "$PREV_DIR" || rm -rf "$STAGE_DIR"
  else
    mv "$DIST_DIR" "$PREV_DIR"
    if ! mv "$STAGE_DIR" "$DIST_DIR"; then
      mv "$PREV_DIR" "$DIST_DIR" || true
      echo "build-frontend-atomic: failed to move staged build into $DIST_DIR; rolled back" >&2
      exit 1
    fi
    rm -rf "$PREV_DIR"
  fi
else
  mv "$STAGE_DIR" "$DIST_DIR"
fi

release_swap_lock
