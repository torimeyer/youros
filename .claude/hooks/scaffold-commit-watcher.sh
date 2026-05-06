#!/bin/bash
# PostToolUse hook: after an Agent tool spawn, remind Claude about the
# scaffold-commit rule and start a background watcher that nudges the
# parent if no commit lands in the spawned worktree within 120 seconds.
#
# Fires PostToolUse on Agent. Exit 0 always (informational, never blocks).
#
# Per feedback_subagent_prompt_template.md Rule 3: every saa brief that
# involves code changes must instruct the agent to make a scaffold commit
# within 2 minutes of starting. This hook enforces the rule passively:
#   1. Emits an immediate reminder to stderr so Claude sees it inline.
#   2. Starts a detached background watcher that checks the worktree
#      after SCAFFOLD_WAIT_SECONDS (default 120) and nudges the parent
#      agent if no commit has landed.
#
# Background process is fully detached (setsid + disown) so a backend
# hang or slow git operation cannot stall the tool call.

set -u

SCAFFOLD_WAIT_SECONDS="${MYOS_SCAFFOLD_WAIT_SECONDS:-120}"

INPUT=$(cat)

# --- Parse spawned agent name and session_id from tool response --------
PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, sys, json, re

US = "\x1f"
raw = os.environ.get("INPUT_JSON", "") or "{}"
try:
    d = json.loads(raw, strict=False)
except Exception:
    sys.exit(0)

# Harvest text from tool_response (bridge stderr surfaces here).
tr = d.get("tool_response") or {}
haystack = ""
if isinstance(tr, str):
    haystack = tr
elif isinstance(tr, dict):
    for key in ("stderr", "error", "output", "content", "message", "text"):
        v = tr.get(key)
        if isinstance(v, str):
            haystack += v + "\n"
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    haystack += (item.get("text") or "") + "\n"
                elif isinstance(item, str):
                    haystack += item + "\n"

spawn_name = ""
m = re.search(r"Spawned REST agent name:\s*(\S+)", haystack)
if m:
    spawn_name = m.group(1).strip()

# Fall back to last registered subagent name.
if not spawn_name:
    last_file = os.path.expanduser("~/.myos/subagents/last.name")
    try:
        spawn_name = open(last_file).read().strip()
    except Exception:
        pass

session_id = (d.get("session_id") or "").strip()

sys.stdout.write(f"{spawn_name}{US}{session_id}")
PY
)

if [ -z "$PARSED" ]; then
    exit 0
fi

IFS=$'\x1f' read -r AGENT_NAME SESSION_ID <<<"$PARSED"

if [ -z "$AGENT_NAME" ]; then
    exit 0
fi

# --- Derive parent agent name (same logic as heartbeat-agent.sh) -------
if [ -n "${MYOS_AGENT_NAME:-}" ]; then
    PARENT_NAME="${MYOS_AGENT_NAME}"
elif [ -n "${SESSION_ID:-}" ]; then
    _raw="claude-code-${SESSION_ID:0:10}"
    PARENT_NAME=$(printf '%s' "$_raw" | tr '[:upper:]' '[:lower:]' | \
                  tr -c 'a-z0-9-' '-' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')
else
    PARENT_NAME=""
fi

# --- Resolve API base --------------------------------------------------
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
: "${API_BASE:=https://127.0.0.1:8000}"

SPAWNED_AT="${MYOS_SPAWNED_AT:-$(date +%s)}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
WORKTREE_PATH="${PROJECT_DIR}/.claude/worktrees/agent-${AGENT_NAME}"
WARN_FILE="${HOME}/.myos/subagents/scaffold-warnings.jsonl"

# --- Immediate stderr reminder ----------------------------------------
echo "scaffold-watcher: agent '${AGENT_NAME}' must scaffold-commit within 2 min (empty test file + /tmp/${AGENT_NAME}.plan + commit 'scaffold: ...'). Watcher will nudge parent at ${SCAFFOLD_WAIT_SECONDS}s if no commit found." >&2

# --- Detached background watcher --------------------------------------
(
    sleep "$SCAFFOLD_WAIT_SECONDS"

    # Check whether the worktree has any commits made after SPAWNED_AT.
    # git understands @<unix-timestamp> as a date specifier.
    commit_found=0
    if [ -d "$WORKTREE_PATH" ]; then
        recent=$(git -C "$WORKTREE_PATH" log \
            --after="@${SPAWNED_AT}" \
            --format="%H" 2>/dev/null | head -1)
        if [ -n "$recent" ]; then
            commit_found=1
        fi
    fi

    if [ "$commit_found" -eq 0 ]; then
        # Write to pending warnings file for the next UserPromptSubmit to surface.
        mkdir -p "$(dirname "$WARN_FILE")" 2>/dev/null || true
        LINE=$(AGENT_NAME="$AGENT_NAME" SPAWNED_AT="$SPAWNED_AT" python3 -c '
import os, json
from datetime import datetime, timezone
agent = os.environ["AGENT_NAME"]
print(json.dumps({
    "agent": agent,
    "spawned_at": int(os.environ["SPAWNED_AT"]),
    "ts": datetime.now(timezone.utc).isoformat(),
    "message": (
        f"[scaffold-watcher] Agent \"{agent}\" has been running 2+ min "
        "with no scaffold commit on its worktree. Nudge it to commit a "
        "scaffold, or run `git log` in the worktree before cancelling."
    ),
}))
' 2>/dev/null)
        if [ -n "$LINE" ]; then
            printf '%s\n' "$LINE" >> "$WARN_FILE" 2>/dev/null || true
        fi

        # Nudge the parent agent directly if we know its name.
        if [ -n "$PARENT_NAME" ]; then
            MSG="[scaffold-watcher] Agent '${AGENT_NAME}' has been running for 2 minutes with no scaffold commit on its worktree. Check git log in the worktree before cancelling — it may be in the research phase. If still 0 commits after 5 min, consider a nudge."
            BODY=$(python3 -c "
import json, os
print(json.dumps({'message': os.environ['MSG'], 'kind': 'user_message'}))
" MSG="$MSG" 2>/dev/null)
            if [ -n "$BODY" ]; then
                curl -sSk --connect-timeout 2 -m 4 \
                    -X POST "${API_BASE}/api/agents/${PARENT_NAME}/nudge" \
                    -H 'Content-Type: application/json' \
                    -d "$BODY" > /dev/null 2>&1 || true
            fi
        fi
    fi
) </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true

exit 0
