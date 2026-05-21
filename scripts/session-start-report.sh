#!/bin/bash
# session-start-report.sh — "since last session" summary printed at every boot.
# Writes to stdout. Hook calls: bash session-start-report.sh <REPO_ROOT> >&2
# Always exits 0 (advisory; never blocks session start).
#
# Retro F2+F6, →1556

set -u

REPO_ROOT="${1:-}"
[ -z "$REPO_ROOT" ] && REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

# ---- 1. Commits on main in last 24h ----
COMMITS=$(git -C "$REPO_ROOT" log main --since='24 hours ago' --oneline 2>/dev/null | head -10 || true)
if [ -n "$COMMITS" ]; then
    COMMIT_COUNT=$(printf '%s\n' "$COMMITS" | wc -l | tr -d ' ')
else
    COMMIT_COUNT=0
fi

# ---- 2. Closed needles in last 24h ----
CLOSED_LINES=""
CLOSED_COUNT=0
if command -v ostk >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
    CLOSED_LINES=$(ostk work list --status closed --json 2>/dev/null | python3 -c "
import sys, json
from datetime import datetime, timezone, timedelta
try:
    data = json.load(sys.stdin)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = [n for n in data
              if n.get('closed_at') and
              datetime.fromisoformat(n['closed_at'].replace('Z', '+00:00')) >= cutoff]
    recent.sort(key=lambda n: n.get('closed_at', ''))
    for n in recent[-5:]:
        title = n.get('title', '?')[:55]
        print(f'  {n[\"id\"]} {title}')
except Exception:
    pass
" 2>/dev/null || true)
    if [ -n "$CLOSED_LINES" ]; then
        CLOSED_COUNT=$(printf '%s\n' "$CLOSED_LINES" | wc -l | tr -d ' ')
    fi
fi

# ---- 3. Worktrees with commits ahead of main (capped at 5) ----
WORKTREE_AHEAD=""
WORKTREE_COUNT=0
WORKTREE_TOTAL=0
WORKTREE_BASE="$REPO_ROOT/.claude/worktrees"
if [ -d "$WORKTREE_BASE" ]; then
    for wt in "$WORKTREE_BASE"/agent-*/; do
        [ -d "$wt" ] || continue
        ahead=$(git -C "$wt" log main..HEAD --oneline 2>/dev/null | wc -l | tr -d ' ')
        if [ "${ahead:-0}" -gt 0 ]; then
            WORKTREE_TOTAL=$((WORKTREE_TOTAL + 1))
            if [ "$WORKTREE_COUNT" -lt 5 ]; then
                name=$(basename "$wt")
                [ "${#name}" -gt 42 ] && name="${name:0:39}..."
                WORKTREE_AHEAD="${WORKTREE_AHEAD}  ${name}: ${ahead} commit(s) ahead
"
                WORKTREE_COUNT=$((WORKTREE_COUNT + 1))
            fi
        fi
    done
fi

# ---- 4. Today's handoff file ----
TODAY=$(date +%Y-%m-%d 2>/dev/null || echo "")
HANDOFF_FILE=""
if [ -n "$TODAY" ]; then
    for f in "$HOME/.claude/handoffs/${TODAY}"*.md; do
        [ -f "$f" ] && HANDOFF_FILE="$f" && break
    done
fi

# Skip if nothing to report
if [ "$COMMIT_COUNT" -eq 0 ] && [ "$CLOSED_COUNT" -eq 0 ] && \
   [ -z "$WORKTREE_AHEAD" ] && [ -z "$HANDOFF_FILE" ]; then
    exit 0
fi

# ---- Emit ----
echo "[session-report] ━━━ last 24h ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "$COMMIT_COUNT" -gt 0 ]; then
    echo "[session-report] Commits ($COMMIT_COUNT):"
    while IFS= read -r line; do
        [ -n "$line" ] && echo "[session-report]   $line"
    done <<< "$COMMITS"
else
    echo "[session-report] Commits:        none"
fi

if [ "$CLOSED_COUNT" -gt 0 ]; then
    echo "[session-report] Closed needles ($CLOSED_COUNT):"
    while IFS= read -r line; do
        [ -n "$line" ] && echo "[session-report]$line"
    done <<< "$CLOSED_LINES"
else
    echo "[session-report] Closed needles: none"
fi

if [ -n "$WORKTREE_AHEAD" ]; then
    EXTRA=$((WORKTREE_TOTAL - WORKTREE_COUNT))
    if [ "$EXTRA" -gt 0 ]; then
        echo "[session-report] Worktrees ahead ($WORKTREE_TOTAL, showing 5):"
    else
        echo "[session-report] Worktrees ahead ($WORKTREE_TOTAL):"
    fi
    while IFS= read -r line; do
        [ -n "$line" ] && echo "[session-report]$line"
    done <<< "$WORKTREE_AHEAD"
    [ "$EXTRA" -gt 0 ] && echo "[session-report]  … and $EXTRA more"
fi

if [ -n "$HANDOFF_FILE" ]; then
    echo "[session-report] Handoff:        $HANDOFF_FILE"
fi

echo "[session-report] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exit 0
