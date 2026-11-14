#!/bin/bash
# Hook: run on every Claude Code SessionStart.
#
# Responsibilities:
#   1. Write the current model to .ostk/current_model so downstream
#      hooks know which model the parent session is using.
#   2. Register this session as an agent in ToriOS so it shows up
#      live on the Agents page (Tori's rule: "every claude code
#      session must register").
#   3. Create an ostk needle for this session so it has a tracked
#      work item from the start.
#
# The hook is best-effort: any API or ostk failure logs and exits 0
# so the session is never blocked.

set -u

INPUT=$(cat)

HOST_SHORT=$(hostname -s 2>/dev/null || echo "host")
PARSED=$(INPUT_JSON="$INPUT" HOST_SHORT="$HOST_SHORT" HOOK_PID="$$" python3 <<'PY' 2>/dev/null
import json, os, re, sys

raw = os.environ.get("INPUT_JSON", "") or "{}"
try:
    d = json.loads(raw, strict=False)
except Exception:
    d = {}

event_name = (d.get("hook_event_name") or "").strip()
session_obj = d.get("session") if isinstance(d.get("session"), dict) else {}
raw_model = (d.get("model") or session_obj.get("model") or "").strip()

if event_name in {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop"}:
    runtime = "claude"
elif event_name in {
    "BeforeTool", "AfterTool", "BeforeAgent", "AfterAgent",
    "BeforeModel", "AfterModel", "BeforeToolSelection", "PreCompress",
}:
    runtime = "gemini"
elif raw_model.lower().startswith("gemini"):
    runtime = "gemini"
elif raw_model.lower().startswith("claude"):
    runtime = "claude"
elif os.environ.get("GEMINI_PROJECT_DIR") or os.environ.get("GEMINI_SESSION_ID"):
    runtime = "gemini"
else:
    runtime = "claude"

if runtime == "gemini":
    proj_dir = os.environ.get("GEMINI_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or ""
    default_model = "gemini-3.1-pro-preview"
    prefix = "gemini-cli"
    source_val = "gemini-cli"
    label = "Gemini CLI"
    env_sid = os.environ.get("GEMINI_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""
else:
    proj_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("GEMINI_PROJECT_DIR") or ""
    default_model = "claude-sonnet-4-6"
    prefix = "claude-code"
    source_val = "claude-code"
    label = "Claude Code"
    env_sid = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("GEMINI_SESSION_ID") or ""

cwd = (d.get("cwd") or proj_dir or "").strip()
model = raw_model or default_model
sid = (d.get("session_id") or session_obj.get("id") or env_sid or "").strip()

if sid:
    raw_name = f"{prefix}-{sid[:10]}"
else:
    raw_name = f"{prefix}-{os.environ.get('HOST_SHORT', 'host')}-{os.environ.get('HOOK_PID', '0')}"

agent_name = re.sub(r"[^a-z0-9-]+", "-", raw_name.lower())
agent_name = re.sub(r"-+", "-", agent_name).strip("-")

reg_body = json.dumps({
    "name": agent_name,
    "model": model,
    "budget": 0,
    "status": "running",
    "source": source_val,
    "description": f"{label} session (cwd: {cwd})",
})

US = "\x1f"
sys.stdout.write(f"{runtime}{US}{cwd}{US}{model}{US}{agent_name}{US}{reg_body}")
PY
)

RUNTIME="claude"
CWD="${CLAUDE_PROJECT_DIR:-${GEMINI_PROJECT_DIR:-}}"
MODEL="claude-sonnet-4-6"
AGENT_NAME="claude-code-${HOST_SHORT}-$$"
REGISTER_BODY=""
if [ -n "$PARSED" ]; then
    IFS=$'\x1f' read -r RUNTIME CWD MODEL AGENT_NAME REGISTER_BODY <<<"$PARSED"
fi

# ---- 1. Record the current model for downstream hooks. ----
if [ -n "$CWD" ] && [ -d "$CWD/.ostk" ]; then
    echo "$MODEL" > "$CWD/.ostk/current_model" 2>/dev/null || true
fi

# ---- 2. Register as an agent in ToriOS. ----
# HTTPS by default -- plain HTTP returns empty reply (HTTP 000) because the backend
# requires TLS. YOUROS_BACKEND_URL / TORIOS_API_BASE override for isolated tests.
API_BASE="${YOUROS_BACKEND_URL:-${TORIOS_API_BASE:-https://localhost:8000}}"
if [ -n "$REGISTER_BODY" ]; then
    curl -sSk --connect-timeout 2 -m 3 \
        -X POST "${API_BASE}/api/agents/register" \
        -H 'Content-Type: application/json' \
        -d "$REGISTER_BODY" > /dev/null 2>&1 || true
fi

# ---- 2b. Drain any pending subagent registrations. ----
# When the backend was down or the MCP was flapping at spawn time,
# register-agent.sh parks the POST body in this file. Replay each
# line now. Lines that succeed are dropped; lines that still fail
# stay in the queue for the next session.
DRAIN_BASE="${YOUROS_BACKEND_URL:-${TORIOS_API_BASE:-https://127.0.0.1:8000}}"
PENDING_QUEUE="$HOME/.youros/subagents/pending-register.jsonl"
if [ -f "$PENDING_QUEUE" ] && [ -s "$PENDING_QUEUE" ]; then
    TMP_REMAIN=$(mktemp 2>/dev/null || echo "${PENDING_QUEUE}.tmp.$$")
    : > "$TMP_REMAIN"
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        if curl -sSk --connect-timeout 2 -m 4 \
                -X POST "${DRAIN_BASE}/api/agents/register" \
                -H 'Content-Type: application/json' \
                -d "$line" > /dev/null 2>&1; then
            # Succeeded. Do not keep this line.
            continue
        fi
        printf '%s\n' "$line" >> "$TMP_REMAIN"
    done < "$PENDING_QUEUE"
    mv "$TMP_REMAIN" "$PENDING_QUEUE" 2>/dev/null || rm -f "$TMP_REMAIN" 2>/dev/null
    # If everything drained, remove the empty file to keep the queue tidy.
    if [ ! -s "$PENDING_QUEUE" ]; then
        rm -f "$PENDING_QUEUE" 2>/dev/null
    fi
fi

# ---- 3. Worktree hygiene: auto-reap absorbed worktrees. ----
# Guard (→1194): skip inside subagent worktree sessions. The reaper compares
# the worktree basename (which may be a short_worktree_id-truncated ID) against
# registered agent names (always full-length). A long agent name produces a
# truncated worktree ID that never matches its own full name, so the
# active-agent guard fails and the reaper deletes the current worktree while
# the agent is running inside it. The parent session runs this on its next
# start — no hygiene is lost.
REAPER="$CWD/scripts/worktree-reaper.sh"
if [ -n "$CWD" ] && [ -x "$REAPER" ] && [ -z "${YOUROS_AGENT_NAME:-}" ]; then
    REAPER_OUT=$("$REAPER" --apply 2>&1) || true

    ABSORBED=$(echo "$REAPER_OUT" | grep -oP 'absorbed=\K[0-9]+' 2>/dev/null || echo 0)
    UNIQUE=$(echo "$REAPER_OUT" | grep -oP 'unique=\K[0-9]+' 2>/dev/null || echo 0)

    if [ "${ABSORBED:-0}" -gt 0 ] || [ "${UNIQUE:-0}" -gt 0 ]; then
        echo "[worktree-hygiene] Reaped $ABSORBED absorbed worktrees. $UNIQUE unique worktrees remain." >&2
    fi

    if [ "${UNIQUE:-0}" -gt 0 ]; then
        STALE_UNIQUE=0
        SEVEN_DAYS_AGO=$(date -v-7d +%s 2>/dev/null || date -d '7 days ago' +%s 2>/dev/null || echo 0)
        WORKTREE_BASE="$CWD/.claude/worktrees"
        for d in "$WORKTREE_BASE"/agent-*/; do
            [ -d "$d" ] || continue
            DIR_MTIME=$(stat -f %m "$d" 2>/dev/null || stat -c %Y "$d" 2>/dev/null || echo 0)
            if [ "$DIR_MTIME" -gt 0 ] && [ "$SEVEN_DAYS_AGO" -gt 0 ] && [ "$DIR_MTIME" -lt "$SEVEN_DAYS_AGO" ]; then
                STALE_UNIQUE=$((STALE_UNIQUE + 1))
            fi
        done
        if [ "$STALE_UNIQUE" -gt 0 ]; then
            echo "[worktree-hygiene] WARNING: $STALE_UNIQUE unique worktrees are older than 7 days. Run 'scripts/worktree-reaper.sh' to review." >&2
        fi
    fi
fi

# ---- 4. Fleet hygiene: reap stale agents.jsonl entries. ----
# The ostk fleet-active gate counts all rows in .ostk/agents.jsonl.
# Entries older than STALE_THRESHOLD_MINUTES (default 5) accumulate and
# falsely block git mutations (→1522). Run the reaper at every session
# start so the gate never sees a bloated count.
FLEET_REAPER="$CWD/scripts/reap-stale-fleet.sh"
if [ -n "$CWD" ] && [ -x "$FLEET_REAPER" ] && [ -z "${YOUROS_AGENT_NAME:-}" ]; then
    FLEET_OUT=$("$FLEET_REAPER" --apply 2>&1) || true
    REAPED=$(echo "$FLEET_OUT" | grep -oP 'Reaped:\s+\K[0-9]+' 2>/dev/null || echo 0)
    if [ "${REAPED:-0}" -gt 0 ]; then
        echo "[fleet-hygiene] Reaped $REAPED stale agent rows from .ostk/agents.jsonl" >&2
    fi
fi

# ---- 5. Start agent-completion-watcher daemon. ----
# Ensure the daemon is always running at session start so the parent session
# gets mid-turn push notifications when watched agents complete.
# Kill any stale daemon first (may be from a previous session or an old worktree).
# →1726: resolve via main worktree so the canonical (committed) copy is always
# used — not a stale working-tree copy from an agent worktree.
_GIT_MAIN_WT=$(git -C "${CWD:-.}" worktree list --porcelain 2>/dev/null \
    | awk '/^worktree /{print $2; exit}')
WATCHER_SCRIPT="${_GIT_MAIN_WT:-$CWD}/.claude/hooks/lib/agent-completion-watcher.sh"
WATCHER_PID_FILE="$HOME/.youros/subagents/completion-watcher.pid"

if [ "${YOUROS_DISABLE_COMPLETION_WATCHER:-0}" != "1" ] && [ -x "$WATCHER_SCRIPT" ] && [ -z "${YOUROS_AGENT_NAME:-}" ]; then
    if [ -f "$WATCHER_PID_FILE" ]; then
        OLD_PID=$(cat "$WATCHER_PID_FILE" 2>/dev/null)
        case "$OLD_PID" in
            ''|*[!0-9]*) ;;
            *) kill "$OLD_PID" 2>/dev/null || true ;;
        esac
        rm -f "$WATCHER_PID_FILE" 2>/dev/null || true
    fi
    (
        YOUROS_BACKEND_URL="${YOUROS_BACKEND_URL:-https://127.0.0.1:8000}" \
        YOUROS_COMPLETION_ANNC="${HOME}/.youros/subagents/pending-completion-announcements.jsonl" \
        YOUROS_COMPLETION_STATE="${HOME}/.youros/subagents/completion-watcher-state.json" \
        YOUROS_COMPLETION_PID="$WATCHER_PID_FILE" \
        YOUROS_COMPLETION_WATCHER_INTERVAL="5" \
            bash "$WATCHER_SCRIPT" </dev/null >/dev/null 2>&1 &
    )
    disown 2>/dev/null || true
fi

# ---- 6. Liveness check: warn immediately if vite or uvicorn is down. ----
# Retro F10, →1545: vite was dark all day and nobody noticed until 10:55 AM.
# This check fires at every session start so a dead server shows up in the
# standing-rules header before any work begins. Advisory only — exits 0.
LIVENESS_SCRIPT="$CWD/scripts/liveness-check.sh"
if [ -n "$CWD" ] && [ -x "$LIVENESS_SCRIPT" ] && [ -z "${YOUROS_AGENT_NAME:-}" ]; then
    bash "$LIVENESS_SCRIPT" >&2 || true
fi

# ---- 7. Session-start report: "since last session" summary. ----
# Retro F2+F6, →1556: show commits, closed needles, live worktrees, and
# any handoff file so Tori can orient quickly without re-reading git log.
REPORT_SCRIPT="$CWD/scripts/session-start-report.sh"
if [ -n "$CWD" ] && [ -x "$REPORT_SCRIPT" ] && [ -z "${YOUROS_AGENT_NAME:-}" ]; then
    bash "$REPORT_SCRIPT" "$CWD" >&2 || true
fi

if [ "$RUNTIME" = "gemini" ]; then
    printf '{}\n'
fi

exit 0
