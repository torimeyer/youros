#!/usr/bin/env bash
# Install the ostk passive-claim post-commit hook (→1427) into the current
# repo's .git/hooks/post-commit.
#
# Called by scripts/dev-backend.sh on startup so every new checkout gets the
# hook without manual steps.  Safe to run multiple times (idempotent).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_SRC="${SCRIPT_DIR}/ostk_post_commit_hook.sh"
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null || true)

if [[ -z "$GIT_DIR" ]]; then
    echo "[install-post-commit-hook] Not inside a git repo — skipping." >&2
    exit 0
fi

HOOKS_DIR="${GIT_DIR}/hooks"
DEST="${HOOKS_DIR}/post-commit"

mkdir -p "$HOOKS_DIR"

if [[ -f "$DEST" && ! -L "$DEST" ]]; then
    # Existing non-symlink hook: chain instead of overwrite.
    if ! grep -q "ostk_post_commit_hook" "$DEST" 2>/dev/null; then
        echo "" >> "$DEST"
        echo "# ostk passive-claim hook (→1427)" >> "$DEST"
        echo "\"${HOOK_SRC}\" || true" >> "$DEST"
        chmod +x "$DEST"
        echo "[install-post-commit-hook] Chained into existing $DEST"
    else
        echo "[install-post-commit-hook] Already chained in $DEST — no-op."
    fi
else
    # No hook yet (or was a symlink we're replacing): symlink.
    ln -sf "$HOOK_SRC" "$DEST"
    chmod +x "$HOOK_SRC"
    echo "[install-post-commit-hook] Symlinked $HOOK_SRC → $DEST"
fi
