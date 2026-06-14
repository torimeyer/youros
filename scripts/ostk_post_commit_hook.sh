#!/usr/bin/env bash
# ostk post-commit hook (→1427): passive spec-claim detection.
#
# Reads the latest commit message, greps for spec slugs (file stems under
# ~/.youros/specs/*.md) and →NNNN / ->NNNN task IDs, then POSTs a passive
# claim to the yourOS backend for each match.
#
# Install: copy or symlink this file to .git/hooks/post-commit
# (scripts/install-post-commit-hook.sh does this automatically).
#
# Silent on failure. Logs to /tmp/ostk-post-commit.log for debugging.

set -euo pipefail

LOG=/tmp/ostk-post-commit.log
BACKEND="https://127.0.0.1:8000"
SPECS_DIR="${YOUROS_USER_SPECS_DIR:-${YOUROS_HOME:-${HOME}/.youros}/specs}"

# Only run when the backend is reachable (non-blocking probe).
if ! curl --connect-timeout 2 -m 3 -skI "${BACKEND}/api/agents" >/dev/null 2>&1; then
    exit 0
fi

# Grab the latest commit metadata.
COMMIT_MSG=$(git log -1 --pretty=%s 2>/dev/null || true)
COMMIT_AUTHOR=$(git log -1 --pretty=%an 2>/dev/null || true)

if [[ -z "$COMMIT_MSG" ]]; then
    exit 0
fi

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG" 2>/dev/null || true; }

claim() {
    local spec_path="$1"
    local author="$2"
    # Use absolute path so _validate_doc_path resolves it correctly.
    local abs_path
    abs_path=$(python3 -c "import os; print(os.path.realpath('$spec_path'))" 2>/dev/null || echo "$spec_path")
    log "claiming spec $abs_path for author $author"
    curl --connect-timeout 3 -m 5 -sk \
        -X POST "${BACKEND}/api/specs/${abs_path}/claim" \
        -H "Content-Type: application/json" \
        -d "{\"agent\": \"passive/${author}\", \"source\": \"passive\"}" \
        >> "$LOG" 2>&1 || true
    echo "" >> "$LOG" 2>/dev/null || true
}

# --- Slug matching ---
if [[ -d "$SPECS_DIR" ]]; then
    for spec_file in "$SPECS_DIR"/*.md; do
        [[ -f "$spec_file" ]] || continue
        slug=$(basename "$spec_file" .md)
        # Whole-word case-insensitive match.
        if echo "$COMMIT_MSG" | grep -iqE "(^|[^a-zA-Z0-9_-])${slug}([^a-zA-Z0-9_-]|$)"; then
            claim "$spec_file" "$COMMIT_AUTHOR"
        fi
    done
fi

# --- Task ID matching (→NNNN or ->NNNN) ---
# For task IDs we rely on the backend's _spec_task_origin map; the HTTP claim
# endpoint accepts any spec path the server knows about, so we pass the task
# reference as a query param hint via the backend's lookup.  Since the hook
# can't easily reverse-map task IDs to spec paths without the in-process map,
# we skip this path in the hook and let the 60s cron scanner handle it.

exit 0
