#!/bin/bash
# Hook: SubagentStop dual-write for →1304 pilot.
#
# Purpose: when a subagent subprocess stops, write to BOTH the ostk
# kernel journal (via `ostk hook agent-stop`) AND to /api/agents/complete
# so the myOS Agents UI row is closed. This is the SubagentStop companion
# to ostk-agent-start.sh.
#
# Why this matters: complete-agent.sh fires on PostToolUse Agent in the
# PARENT session. If the parent session itself exits (context cutoff, kill)
# before PostToolUse fires, the row stays running forever. SubagentStop
# fires in the CHILD session, so it catches cases where the parent died.
#
# Name resolution: reads the side-channel files written by register-agent.sh
# at PreToolUse time. Tries per-tool-use ID file first (race-safe), then
# falls back to last.name. If neither resolves, exits silently — the stale
# sweep or next heartbeat cycle will clean up.
#
# Idempotency: /api/agents/{name}/complete returns 200 for already-completed
# rows. Racing with complete-agent.sh PostToolUse is harmless.
#
# Wired in: ~/.claude/settings.json SubagentStop (global)
#           also added to .claude/settings.json for project-level coverage

set -u

INPUT=$(cat 2>/dev/null || true)

# 1. Kernel journal write (ostk native)
printf '%s' "$INPUT" | ostk hook agent-stop 2>/dev/null || true

# 2. Resolve API base
API_BASE="${TORIOS_API_BASE:-}"
if [ -z "$API_BASE" ] && [ -f "$HOME/.myos/config.json" ]; then
    API_BASE=$(python3 -c "
import json, os
try:
    d = json.load(open(os.path.expanduser('~/.myos/config.json')))
    v = d.get('api_base')
    if isinstance(v, str) and v.strip():
        print(v.strip())
except Exception:
    pass
" 2>/dev/null)
fi
if [ -z "$API_BASE" ]; then
    API_BASE="https://127.0.0.1:8000"
fi

# 3. Read agent name from side-channel (per-tool-use file, then last.name)
AGENT_NAME=""
PER_ID_DIR="${HOME}/.myos/subagents/by-tool-use"

# Extract session_id from SubagentStop payload — Claude Code includes it
# and it matches the tool_use_id used at PreToolUse time in some builds.
SESSION_ID=$(INPUT_JSON="$INPUT" python3 -c "
import os, sys, json
raw = os.environ.get('INPUT_JSON', '') or '{}'
try:
    d = json.loads(raw)
except Exception:
    sys.exit(0)
for k in ('session_id', 'tool_use_id', 'toolUseId', 'id'):
    v = d.get(k)
    if isinstance(v, str) and v.strip():
        sys.stdout.write(v.strip())
        break
" 2>/dev/null)

if [ -n "$SESSION_ID" ]; then
    SAFE_ID=$(printf '%s' "$SESSION_ID" | tr -c 'a-zA-Z0-9_-' '_' | cut -c1-128)
    PER_FILE="${PER_ID_DIR}/${SAFE_ID}.name"
    if [ -f "$PER_FILE" ]; then
        AGENT_NAME=$(cat "$PER_FILE" 2>/dev/null | tr -d '[:space:]')
    fi
fi

if [ -z "$AGENT_NAME" ]; then
    AGENT_NAME=$(cat "${HOME}/.myos/subagents/last.name" 2>/dev/null | tr -d '[:space:]')
fi

# ── Auto-merge: SubagentStop fires in the child (worktree context) ────────────
# Runs BEFORE the AGENT_NAME early-exit so it fires even for agents where
# register-agent.sh exited via the bridge guard (most real saa agents have
# edit verbs in their prompt → bridge guard → last.name never written →
# AGENT_NAME stays empty). The merge itself uses git plumbing only; AGENT_NAME
# is used solely for diagnostic log labels. See →1548 for root cause detail.
# complete-agent.sh (PostToolUse) runs in the PARENT session and must skip
# background agents because PostToolUse fires at LAUNCH, not at finish.
# SubagentStop fires when the child subprocess actually exits — the right
# place to auto-merge background worktree agents without guessing the branch.
#
# The child is inside the git worktree so:
#   git branch --show-current  → the worktree branch (worktree-agent-<slug>)
#   git rev-parse --git-common-dir → absolute path to main .git
# No side-channel files or description-slug matching required.
#
# Guards (same policy as complete-agent.sh, never destructive):
#   - branch must start with worktree-agent-
#   - must exist as a ref in the main repo
#   - merge must be --ff-only; diverged branches stay parked

_SA_DEBT_LOG="$HOME/.myos/logs/merge-debt.log"
mkdir -p "$(dirname "$_SA_DEBT_LOG")" 2>/dev/null || true

_SA_BRANCH=$(git branch --show-current 2>/dev/null || true)
_SA_GIT_COMMON=$(git rev-parse --git-common-dir 2>/dev/null || true)

if [ -n "$_SA_BRANCH" ] && [ -n "$_SA_GIT_COMMON" ]; then
    case "$_SA_BRANCH" in
        worktree-agent-*)
            # Strip /.git suffix to get the main repo root
            _SA_MAIN_REPO=$(dirname "$_SA_GIT_COMMON")
            if [ -n "$_SA_MAIN_REPO" ] && [ "$_SA_MAIN_REPO" != "." ]; then
                if git -C "$_SA_MAIN_REPO" rev-parse --verify "refs/heads/$_SA_BRANCH" >/dev/null 2>&1; then
                    _SA_OUT=$(git -C "$_SA_MAIN_REPO" merge --ff-only "$_SA_BRANCH" 2>&1) && _SA_RC=0 || _SA_RC=$?
                    if [ "$_SA_RC" -eq 0 ]; then
                        if echo "$_SA_OUT" | grep -qi "already up.to.date"; then
                            printf '%s\tALREADY-MERGED\t%s\t%s\n' \
                                "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$_SA_BRANCH" "${AGENT_NAME:-unknown}" \
                                >> "$_SA_DEBT_LOG" 2>/dev/null || true
                        else
                            _SA_TIP=$(git -C "$_SA_MAIN_REPO" rev-parse --short HEAD 2>/dev/null || true)
                            printf '%s\tMERGED\t%s\t%s\t%s\n' \
                                "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$_SA_BRANCH" "${AGENT_NAME:-unknown}" "$_SA_TIP" \
                                >> "$_SA_DEBT_LOG" 2>/dev/null || true
                            echo "ostk-agent-stop: auto-merged $_SA_BRANCH onto main (HEAD $_SA_TIP)" >&2
                        fi
                    else
                        printf '%s\tATTN-NOT-FF\t%s\t%s\n' \
                            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$_SA_BRANCH" "${AGENT_NAME:-unknown}" \
                            >> "$_SA_DEBT_LOG" 2>/dev/null || true
                        echo "ostk-agent-stop: ATTN: ${AGENT_NAME:-unknown} finished on $_SA_BRANCH — not fast-forward, parked (needs manual merge)" >&2
                    fi
                else
                    printf '%s\tATTN-BRANCH-NOT-FOUND\t%s\t%s\n' \
                        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$_SA_BRANCH" "${AGENT_NAME:-unknown}" \
                        >> "$_SA_DEBT_LOG" 2>/dev/null || true
                    echo "ostk-agent-stop: ATTN: ${AGENT_NAME:-unknown} finished on $_SA_BRANCH — branch not found in main repo" >&2
                fi
            fi
            ;;
    esac
fi

# 4. POST /complete — only if AGENT_NAME resolved; idempotent server-side.
[ -z "$AGENT_NAME" ] && exit 0

curl -sSk --connect-timeout 3 -m 5 \
    -X POST "${API_BASE}/api/agents/${AGENT_NAME}/complete" \
    -H 'Content-Type: application/json' \
    -d '{"summary":"completed via ostk-agent-stop hook"}' \
    >/dev/null 2>&1 || true

exit 0
