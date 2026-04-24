#!/bin/bash
# Hook: auto-register Claude Code subagents with torios/myOS.
#
# GLOBAL version: installed at ~/.claude/hooks/register-agent.sh and
# wired via the user-global ~/.claude/settings.json so EVERY Claude
# Code session on this machine (regardless of project directory)
# registers its Task-tool subagents with the local torios backend.
# This keeps the terminal "N background tasks" counter in sync with
# the torios sidebar badge and Agents page, even when Claude Code
# runs inside a non-torios project folder.
#
# Fires on PreToolUse for the Agent tool. Reads the tool_input JSON
# from stdin, derives a stable agent name from the description, and
# POSTs /api/agents/register so the subagent shows up on the Agents
# page before it even starts working.
#
# Also spawns a fully detached background heartbeat loop that pings
# /api/agents/<name>/heartbeat every 60s until the row flips to a
# terminal status (completed, failed, cancelled, terminated_stale)
# or the 45 minute TTL expires. Without this the stale sweep culls
# the row at 15 minutes even though the subagent is still running.
#
# Portability notes for the global install:
#   - All state lives under ~/.myos/ (not inside the project repo),
#     so it works identically for every project directory.
#   - API base URL: read from ~/.myos/config.json ("api_base") or
#     the TORIOS_API_BASE environment variable, defaulting to the
#     local HTTPS listener at https://127.0.0.1:8000.
#   - heartbeat_idle.py lives inside the torios repo. We resolve the
#     repo via ~/.myos/config.json ("torios_repo"), the TORIOS_REPO
#     env var, a handful of well-known install paths, and finally
#     $CLAUDE_PROJECT_DIR (only if it already looks like the torios
#     repo). If none of those succeed the heartbeat loop still runs
#     and pings the backend; it just skips the transcript-idle
#     check and relies on the backend stale sweep + explicit
#     /complete from complete-agent.sh.
#   - The hook MUST fail silently when torios is down so non-torios
#     projects never see a user-visible error.

set -u

TRANSCRIPT_IDLE_SECONDS=120

INPUT=$(cat)

# --- Resolve API base (portable, non-torios friendly) -----------------
# Order: TORIOS_API_BASE env > ~/.myos/config.json > default HTTPS local.
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

# --- Resolve torios repo root (for heartbeat_idle.py) -----------------
# Only used by the heartbeat loop. If nothing resolves, the heartbeat
# loop skips the idle-check and relies on the server stale sweep.
TORIOS_REPO_DIR="${TORIOS_REPO:-}"
if [ -z "$TORIOS_REPO_DIR" ] && [ -f "$HOME/.myos/config.json" ]; then
    TORIOS_REPO_DIR=$(python3 -c "
import json, os
try:
    d = json.load(open(os.path.expanduser('~/.myos/config.json')))
    v = d.get('torios_repo')
    if isinstance(v, str) and v.strip():
        print(os.path.expanduser(v.strip()))
except Exception:
    pass
" 2>/dev/null)
fi
if [ -z "$TORIOS_REPO_DIR" ] || [ ! -f "$TORIOS_REPO_DIR/api/services/heartbeat_idle.py" ]; then
    for candidate in \
            "$HOME/claude/torios" \
            "$HOME/myos" \
            "$HOME/torios"; do
        if [ -f "$candidate/api/services/heartbeat_idle.py" ]; then
            TORIOS_REPO_DIR="$candidate"
            break
        fi
    done
fi
if [ -z "$TORIOS_REPO_DIR" ] || [ ! -f "$TORIOS_REPO_DIR/api/services/heartbeat_idle.py" ]; then
    # Last resort: if the parent session is running INSIDE the torios
    # repo we can still find heartbeat_idle.py via $CLAUDE_PROJECT_DIR.
    if [ -n "${CLAUDE_PROJECT_DIR:-}" ] \
            && [ -f "${CLAUDE_PROJECT_DIR}/api/services/heartbeat_idle.py" ]; then
        TORIOS_REPO_DIR="${CLAUDE_PROJECT_DIR}"
    fi
fi

# Debug: capture the raw PreToolUse payload. Always lives under ~/.myos/
# so it works no matter which project this hook fires from. Ring-buffer
# the last 400 lines; truncate each payload to 5000 bytes.
_DBG_LOG="$HOME/.myos/subagents/register-debug.log"
mkdir -p "$(dirname "$_DBG_LOG")" 2>/dev/null || true
{
    printf '=== %s ===\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s' "$INPUT" | head -c 5000
    printf '\n'
} >> "$_DBG_LOG" 2>/dev/null || true
if [ -f "$_DBG_LOG" ]; then
    _DBG_LINES=$(wc -l < "$_DBG_LOG" 2>/dev/null || echo 0)
    if [ "${_DBG_LINES:-0}" -gt 400 ]; then
        tail -n 400 "$_DBG_LOG" > "$_DBG_LOG.tmp" 2>/dev/null && mv "$_DBG_LOG.tmp" "$_DBG_LOG" 2>/dev/null || true
    fi
fi

PARSED=$(INPUT_JSON="$INPUT" TORIOS_REPO_DIR="$TORIOS_REPO_DIR" python3 <<'PY' 2>/dev/null
import os, sys, json, re
raw = os.environ.get("INPUT_JSON", "")
try:
    d = json.loads(raw or "{}")
except Exception:
    sys.exit(0)
ti = d.get("tool_input", {}) or {}
desc = (ti.get("description") or "").strip()
prompt = (ti.get("prompt") or "").strip()
subagent = (ti.get("subagent_type") or "").strip()
# Background-task flag. Claude Code's PostToolUse payload often strips
# tool_input entirely, which means complete-agent.sh cannot tell whether
# the parent Task call was run_in_background=true at PostToolUse time.
# We stash the flag here and complete-agent.sh reads it back via the
# tool_use_id so the bg-skip guard stays reliable even when the harness
# drops tool_input from PostToolUse. Accept any truthy serialization:
# bool True, int 1, or string "true"/"1"/"yes". Matching check lives in
# complete-agent.sh's in-payload belt so the two stay symmetric.
_rib = ti.get("run_in_background")
if _rib is True or _rib == 1 or (isinstance(_rib, str) and _rib.strip().lower() in ("true", "1", "yes")):
    run_in_background = "1"
else:
    run_in_background = ""
tool_use_id = ""
for key in ("tool_use_id", "toolUseId", "id"):
    v = d.get(key)
    if isinstance(v, str) and v.strip():
        tool_use_id = v.strip()
        break
if not tool_use_id:
    tu = d.get("tool_use") or {}
    if isinstance(tu, dict):
        v = tu.get("id") or tu.get("tool_use_id") or ""
        if isinstance(v, str) and v.strip():
            tool_use_id = v.strip()

name = re.sub(r"[^a-z0-9-]", "", desc.lower().replace(" ", "-"))[:40]
name = re.sub(r"-+", "-", name).strip("-")
if not name:
    name = "claude-code-subagent"

MODEL_MAP = {
    "opus": "claude-opus-4-7",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}
model = ti.get("model") or ""
if model and model in MODEL_MAP:
    model = MODEL_MAP[model]
if not model:
    # Search, in order: current cwd's .ostk/current_model,
    # CLAUDE_PROJECT_DIR's .ostk/current_model, torios repo's
    # .ostk/current_model. All optional, all best-effort.
    torios_repo = os.environ.get("TORIOS_REPO_DIR", "") or ""
    for c in (
        os.path.join(d.get("cwd", "") or "", ".ostk", "current_model"),
        os.path.join(os.environ.get("CLAUDE_PROJECT_DIR", "") or "", ".ostk", "current_model"),
        os.path.join(torios_repo, ".ostk", "current_model") if torios_repo else "",
    ):
        if c and os.path.exists(c):
            try:
                model = open(c).read().strip()
                break
            except Exception:
                pass
if not model:
    model = "claude-sonnet-4-6"

short_desc = desc or (prompt[:140] if prompt else subagent or "claude-code subagent")
short_prompt = prompt[:500] if prompt else short_desc

def flat(s):
    return " ".join((s or "").split())

sys.stdout.write(
    f"{flat(name)}\t{flat(model)}\t{flat(short_desc)}\t{flat(short_prompt)}\t{flat(tool_use_id)}\t{run_in_background}"
)
PY
)

if [ -z "$PARSED" ]; then
    exit 0
fi

IFS=$'\t' read -r AGENT_NAME MODEL DESCRIPTION PROMPT TOOL_USE_ID RUN_IN_BACKGROUND <<<"$PARSED"

if [ -z "$AGENT_NAME" ]; then
    exit 0
fi

BODY=$(AGENT_NAME="$AGENT_NAME" MODEL="$MODEL" DESCRIPTION="$DESCRIPTION" PROMPT="$PROMPT" python3 -c '
import os, json
body = {
    "name": os.environ["AGENT_NAME"],
    "model": os.environ["MODEL"],
    "budget": 5,
    "status": "running",
    "source": "claude-code",
    "description": os.environ.get("DESCRIPTION") or "claude-code subagent",
    "prompt": os.environ.get("PROMPT") or os.environ.get("DESCRIPTION") or "claude-code subagent",
    "hook_preregister": True,
}
print(json.dumps(body))
' 2>/dev/null)

if [ -z "$BODY" ]; then
    exit 0
fi

mkdir -p "$HOME/.myos/subagents" 2>/dev/null || true
PENDING_QUEUE="$HOME/.myos/subagents/pending-register.jsonl"
REGISTER_OK=0
for delay in 1 2 4 8 16; do
    if curl -sSk --connect-timeout 2 -m 4 \
            -X POST "${API_BASE}/api/agents/register" \
            -H 'Content-Type: application/json' \
            -d "$BODY" > /dev/null 2>&1; then
        REGISTER_OK=1
        break
    fi
    sleep "$delay"
done
if [ "$REGISTER_OK" -eq 0 ]; then
    printf '%s\n' "$BODY" >> "$PENDING_QUEUE" 2>/dev/null || true
else
    HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$HOOKS_DIR/lib/drain-pending.sh" ]; then
        # shellcheck source=lib/drain-pending.sh
        . "$HOOKS_DIR/lib/drain-pending.sh"
        MYOS_REGISTER_URL="${API_BASE}/api/agents/register" \
        MYOS_COMPLETE_URL_BASE="${API_BASE}/api/agents" \
            myos_drain_pending >/dev/null 2>&1 || true
        MYOS_REGISTER_URL="${API_BASE}/api/agents/register" \
        MYOS_COMPLETE_URL_BASE="${API_BASE}/api/agents" \
            myos_drain_pending_complete >/dev/null 2>&1 || true
    fi
fi

REGISTERED_AT=$(date +%s)

printf '%s' "$AGENT_NAME" > "$HOME/.myos/subagents/last.name" 2>/dev/null || true
if [ "$RUN_IN_BACKGROUND" = "1" ]; then
    printf '1' > "$HOME/.myos/subagents/last.bg" 2>/dev/null || true
else
    : > "$HOME/.myos/subagents/last.bg" 2>/dev/null || true
fi
if [ -n "$TOOL_USE_ID" ]; then
    mkdir -p "$HOME/.myos/subagents/by-tool-use" 2>/dev/null || true
    SAFE_TUI=$(printf '%s' "$TOOL_USE_ID" | tr -c 'a-zA-Z0-9_-' '_' | cut -c1-128)
    printf '%s' "$AGENT_NAME" \
        > "$HOME/.myos/subagents/by-tool-use/$SAFE_TUI.name" 2>/dev/null || true
    if [ "$RUN_IN_BACKGROUND" = "1" ]; then
        printf '1' \
            > "$HOME/.myos/subagents/by-tool-use/$SAFE_TUI.bg" 2>/dev/null || true
    fi
fi
printf '%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$AGENT_NAME" \
    >> "$HOME/.myos/subagents/history.log" 2>/dev/null || true

# Detached heartbeat loop. Keeps the subagent row alive while it works.
# Exits when the agent reaches a terminal status, the 45 minute TTL is
# hit, or the transcript has been idle for TRANSCRIPT_IDLE_SECONDS
# (indicating the subagent exited without calling /complete).
(
    ttl=2700
    interval=60
    elapsed=0
    iteration=0
    while [ $elapsed -lt $ttl ]; do
        if [ $iteration -gt 0 ] \
                && [ -n "$TORIOS_REPO_DIR" ] \
                && [ -f "$TORIOS_REPO_DIR/api/services/heartbeat_idle.py" ]; then
            python3 "$TORIOS_REPO_DIR/api/services/heartbeat_idle.py" \
                "$AGENT_NAME" "$TRANSCRIPT_IDLE_SECONDS" "$REGISTERED_AT" >/dev/null 2>&1
            if [ $? -eq 1 ]; then
                curl -sSk --connect-timeout 2 -m 3 \
                    -X POST "${API_BASE}/api/agents/${AGENT_NAME}/complete" \
                    -H 'Content-Type: application/json' \
                    -d '{"summary":"Subagent exited, auto-completed by heartbeat idle detector"}' \
                    > /dev/null 2>&1 || true
                exit 0
            fi
        fi

        curl -sSk --connect-timeout 2 -m 4 \
            -X POST "${API_BASE}/api/agents/${AGENT_NAME}/heartbeat" \
            > /dev/null 2>&1 || true

        status=$(AGENT_NAME="$AGENT_NAME" curl -sSk --connect-timeout 2 -m 4 \
            "${API_BASE}/api/agents" 2>/dev/null \
            | python3 -c "
import os, sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
name = os.environ.get('AGENT_NAME')
for a in d.get('agents', []) or []:
    if a.get('name') == name:
        print(a.get('status', ''))
        break
" 2>/dev/null)
        case "$status" in
            completed|failed|cancelled|terminated_stale|completed_timeout|stopped)
                exit 0
                ;;
        esac

        sleep $interval
        elapsed=$((elapsed + interval))
        iteration=$((iteration + 1))
    done
) </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true

exit 0
