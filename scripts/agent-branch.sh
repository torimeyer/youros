#!/usr/bin/env bash
# agent-branch.sh — map an agent name OR worktree path to its git branch (→1553)
#
# Usage:
#   agent-branch.sh <agent-name>           derive branch via spawn_isolation formula
#   agent-branch.sh /path/to/worktree      ask git directly
#   agent-branch.sh --wt-id <agent-name>   print only the derived worktree id (no fs check)
#
# Exits 0 and prints the branch name (or wt-id) on success.
# Exits 1 with a message to stderr on failure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --wt-id: print derived worktree id without checking the filesystem.
WT_ID_ONLY=0
if [[ "${1:-}" == "--wt-id" ]]; then
    WT_ID_ONLY=1
    shift
fi

INPUT="${1:-}"
if [[ -z "$INPUT" ]]; then
    echo "Usage: $(basename "$0") [--wt-id] <agent-name|worktree-path>" >&2
    exit 1
fi

# --- Path input ---
# Treat as a worktree path when the arg starts with / or ./ or names an existing dir.
if [[ "$INPUT" == /* || "$INPUT" == ./* || -d "$INPUT" ]]; then
    if [[ ! -d "$INPUT" ]]; then
        echo "Error: directory not found: $INPUT" >&2
        exit 1
    fi
    git -C "$INPUT" branch --show-current
    exit $?
fi

# --- Agent-name input ---
# Mirror the formula in api/services/spawn_isolation.py::short_worktree_id().
# max_len=30; names longer than that get a blake2s-4-byte hex suffix appended.

derive_wt_id() {
    local name="$1"
    python3 - "$name" <<'PYEOF'
import hashlib, sys
name = sys.argv[1]
max_len = 30
if len(name) <= max_len:
    print(name)
else:
    digest = hashlib.blake2s(name.encode(), digest_size=4).hexdigest()
    prefix_len = max_len - 9  # reserve 1 dash + 8 hex chars
    prefix = name[:prefix_len].rstrip("-_")
    print(f"{prefix}-{digest}")
PYEOF
}

# Find the main repo root via the git common dir so this works from a worktree too.
GIT_COMMON_DIR="$(git -C "$SCRIPT_DIR" rev-parse --git-common-dir 2>/dev/null)"
PROJECT_ROOT="${GIT_COMMON_DIR%/.git}"
if [[ -z "$PROJECT_ROOT" || "$PROJECT_ROOT" == "$GIT_COMMON_DIR" ]]; then
    # Fallback: script parent is probably the repo root
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

WTP_ID=$(derive_wt_id "$INPUT")
BRANCH="worktree-agent-$WTP_ID"

if [[ "$WT_ID_ONLY" -eq 1 ]]; then
    echo "$WTP_ID"
    exit 0
fi

WT_PATH="$PROJECT_ROOT/.claude/worktrees/agent-$WTP_ID"
if [[ ! -d "$WT_PATH" ]]; then
    echo "Error: worktree not found: $WT_PATH" >&2
    echo "       agent '$INPUT' → wt_id '$WTP_ID'" >&2
    # Fuzzy hint: show worktrees whose dir starts with the same prefix token
    PREFIX_TOKEN="${INPUT%%-*}"  # e.g. "n9" from "n9-some-task-abc123"
    MATCHES=$(git -C "$PROJECT_ROOT" worktree list --porcelain \
        | awk '/^worktree /{print $2}' \
        | grep "/agent-${PREFIX_TOKEN}-" 2>/dev/null || true)
    if [[ -n "$MATCHES" ]]; then
        echo "       Nearby worktrees with same prefix '$PREFIX_TOKEN':" >&2
        echo "$MATCHES" | sed 's/^/         /' >&2
    fi
    exit 1
fi

echo "$BRANCH"
