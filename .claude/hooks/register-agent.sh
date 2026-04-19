#!/bin/bash
# Hook: auto-register Claude Code subagents with myOS.
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
# Why heartbeat from here and not from heartbeat-agent.sh:
# heartbeat-agent.sh runs in response to the PARENT session's tool
# calls and has no idea which subagent is currently in flight. The
# subagent's own internal tool calls fire hooks in the same project
# config but identify as a different Claude Code session_id which
# we cannot correlate back to the name we registered here. A
# dedicated side process is the simplest reliable path.

set -u

TRANSCRIPT_IDLE_SECONDS=120

INPUT=$(cat)

# Debug: capture the raw PreToolUse payload so we can verify whether
# Claude Code emits tool_use_id in practice. The by-tool-use fix only
# works if the harness puts tool_use_id on the PreToolUse JSON. Ring-
# buffer the last 20 payloads at ~/.myos/subagents/register-debug.log
# (truncate to 5000 bytes each) so this never grows unbounded.
_DBG_LOG="$HOME/.myos/subagents/register-debug.log"
mkdir -p "$(dirname "$_DBG_LOG")" 2>/dev/null || true
{
    printf '=== %s ===\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s' "$INPUT" | head -c 5000
    printf '\n'
} >> "$_DBG_LOG" 2>/dev/null || true
# Trim log to last 400 lines so it never blows up disk.
if [ -f "$_DBG_LOG" ]; then
    _DBG_LINES=$(wc -l < "$_DBG_LOG" 2>/dev/null || echo 0)
    if [ "${_DBG_LINES:-0}" -gt 400 ]; then
        tail -n 400 "$_DBG_LOG" > "$_DBG_LOG.tmp" 2>/dev/null && mv "$_DBG_LOG.tmp" "$_DBG_LOG" 2>/dev/null || true
    fi
fi

PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, sys, json, re, secrets
raw = os.environ.get("INPUT_JSON", "")
try:
    d = json.loads(raw or "{}")
except Exception:
    sys.exit(0)
ti = d.get("tool_input", {}) or {}
desc = (ti.get("description") or "").strip()
prompt = (ti.get("prompt") or "").strip()
subagent = (ti.get("subagent_type") or "").strip()
# Tool-use-id lets the PostToolUse hook find the exact name we registered
# here, even when several Task calls are in flight concurrently (last.name
# is a single shared file that gets clobbered by the newest spawn).
# Claude Code hook payloads have varied across versions: top-level
# tool_use_id is the documented key, but older/newer builds have used
# nested locations (tool_use.id, id, toolUseId). Try all of them so the
# race-free handoff works across harness versions.
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
# No random suffix: cosmetic clutter on the Agents page (e.g. "Roadmap G3egyo").
# If a real name collision happens, /api/agents/register returns 409 and the
# next register call from this hook can fall back to a "-2" / "-3" suffix.
# In practice subagents are created infrequently enough that bare names work.

MODEL_MAP = {
    "opus": "claude-opus-4-7",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}
model = ti.get("model") or ""
if model and model in MODEL_MAP:
    model = MODEL_MAP[model]
if not model:
    for c in (
        os.path.join(d.get("cwd", "") or "", ".ostk", "current_model"),
        os.path.join(os.environ.get("CLAUDE_PROJECT_DIR", "") or "", ".ostk", "current_model"),
        os.path.expanduser("~/claude/torios/.ostk/current_model"),
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

# Flatten embedded newlines and tabs in every field before piping
# through `read -r ... <<<"$PARSED"`. Bash's `read` stops at the
# first newline and our multi-line user prompts contain many, which
# caused TOOL_USE_ID to be parsed as empty for every real subagent
# spawn (prompt body swallowed the tabs that followed it). Replace
# any whitespace run with a single space so every field arrives on
# one line, then delimit with tabs.
def flat(s):
    return " ".join((s or "").split())

sys.stdout.write(
    f"{flat(name)}\t{flat(model)}\t{flat(short_desc)}\t{flat(short_prompt)}\t{flat(tool_use_id)}"
)
PY
)

if [ -z "$PARSED" ]; then
    exit 0
fi

IFS=$'\t' read -r AGENT_NAME MODEL DESCRIPTION PROMPT TOOL_USE_ID <<<"$PARSED"

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
    # Marks this as the pre-registration written BEFORE the subagent boots.
    # The /register handler uses this flag to detect the duplicate-row case:
    # when the subagent later self-registers under a different name (from
    # its own prompt body), the backend merges that second call into this
    # row instead of creating a second Active Sessions entry.
    "hook_preregister": True,
}
print(json.dumps(body))
' 2>/dev/null)

if [ -z "$BODY" ]; then
    exit 0
fi

# Retry the register POST with backoff so a brief backend reload or
# MCP flap (5 to 30 seconds) does not silently drop the subagent row.
# Five attempts at 1s, 2s, 4s, 8s, 16s still total under 35 seconds of
# wall clock and each probe stays short (connect 2s, max 4s). If every
# attempt fails we stash the body to a pending queue so the next
# SessionStart (or a cron sweep) can replay it.
mkdir -p "$HOME/.myos/subagents" 2>/dev/null || true
PENDING_QUEUE="$HOME/.myos/subagents/pending-register.jsonl"
REGISTER_OK=0
for delay in 1 2 4 8 16; do
    if curl -sSk --connect-timeout 2 -m 4 \
            -X POST "https://127.0.0.1:8000/api/agents/register" \
            -H 'Content-Type: application/json' \
            -d "$BODY" > /dev/null 2>&1; then
        REGISTER_OK=1
        break
    fi
    sleep "$delay"
done
if [ "$REGISTER_OK" -eq 0 ]; then
    # Save the body on one line so a replay sweep can POST each line.
    # Do NOT drain here: if our own register failed, the backend is
    # still unreachable, so draining would just retry and fail again.
    printf '%s\n' "$BODY" >> "$PENDING_QUEUE" 2>/dev/null || true
else
    # Piggyback: the backend is live right now, so replay any parked
    # register bodies AND any parked /complete bodies while we have a
    # working connection. Throttled inside the lib so the heartbeat
    # hook and this path share state.
    HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$HOOKS_DIR/lib/drain-pending.sh" ]; then
        # shellcheck source=lib/drain-pending.sh
        . "$HOOKS_DIR/lib/drain-pending.sh"
        myos_drain_pending >/dev/null 2>&1 || true
        myos_drain_pending_complete >/dev/null 2>&1 || true
    fi
fi

REGISTERED_AT=$(date +%s)

printf '%s' "$AGENT_NAME" > "$HOME/.myos/subagents/last.name" 2>/dev/null || true
# Per-tool-use-id pointer: complete-agent.sh reads the matching file so
# parallel Task calls (three at once during the demo) never complete the
# wrong row just because last.name got clobbered by the newest spawn.
if [ -n "$TOOL_USE_ID" ]; then
    mkdir -p "$HOME/.myos/subagents/by-tool-use" 2>/dev/null || true
    # Sanitize tool_use_id: keep a-z0-9_- only to prevent path escapes.
    SAFE_TUI=$(printf '%s' "$TOOL_USE_ID" | tr -c 'a-zA-Z0-9_-' '_' | cut -c1-128)
    printf '%s' "$AGENT_NAME" \
        > "$HOME/.myos/subagents/by-tool-use/$SAFE_TUI.name" 2>/dev/null || true
fi
printf '%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$AGENT_NAME" \
    >> "$HOME/.myos/subagents/history.log" 2>/dev/null || true

# Detached heartbeat loop. Keeps the subagent row alive while it
# works. Exits when the agent reaches a terminal status, the 45
# minute TTL is hit, or the transcript has been idle for
# TRANSCRIPT_IDLE_SECONDS (indicating the subagent exited without
# calling /complete).
(
    ttl=2700
    interval=60
    elapsed=0
    iteration=0
    while [ $elapsed -lt $ttl ]; do
        # Idle-check: skip on the first iteration so a freshly spawned
        # agent is not killed before its transcript file even exists.
        if [ $iteration -gt 0 ]; then
            python3 "${CLAUDE_PROJECT_DIR}/api/services/heartbeat_idle.py" \
                "$AGENT_NAME" "$TRANSCRIPT_IDLE_SECONDS" "$REGISTERED_AT" >/dev/null 2>&1
            if [ $? -eq 1 ]; then
                curl -sSk --connect-timeout 2 -m 3 \
                    -X POST "https://127.0.0.1:8000/api/agents/${AGENT_NAME}/complete" \
                    -H 'Content-Type: application/json' \
                    -d '{"summary":"Subagent exited, auto-completed by heartbeat idle detector"}' \
                    > /dev/null 2>&1 || true
                exit 0
            fi
        fi

        # Transient backend failures (502, empty reply, connection reset)
        # during a uvicorn reload must NOT kill this loop. Swallow and
        # let the next cycle try again. Only a terminal status on the
        # agent row, or the TTL expiring, ends the loop.
        curl -sSk --connect-timeout 2 -m 4 \
            -X POST "https://127.0.0.1:8000/api/agents/${AGENT_NAME}/heartbeat" \
            > /dev/null 2>&1 || true

        status=$(AGENT_NAME="$AGENT_NAME" curl -sSk --connect-timeout 2 -m 4 \
            "https://127.0.0.1:8000/api/agents" 2>/dev/null \
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
        # Only exit on a confirmed terminal status. An empty status
        # (backend down or returning garbage) is treated as "keep
        # looping" rather than "stop", so a 30-second backend outage
        # never prematurely closes the heartbeat.
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
