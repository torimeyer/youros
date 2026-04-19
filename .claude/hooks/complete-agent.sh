#!/bin/bash
# Hook: PostToolUse for the Agent tool. Closes the row that
# register-agent.sh opened so finished subagents do not stay
# RUNNING in the Agents page.
#
# Why this exists:
# The PreToolUse hook register-agent.sh creates the row when a
# subagent spawns. The subagent's brief asks it to call /complete
# when done, but that is fragile (context cutoff, interpreter kill,
# the model just forgets). Without a server-side close signal the
# row stayed RUNNING for 14+ minutes until the stale sweep fired.
# This hook is the suspender for the belt: as soon as the Agent
# tool call returns control to the parent session, the subagent is
# definitively done, and we can close the row from a place that the
# subagent's behavior cannot break.
#
# Name handoff (two-tier, newer first):
#   1. Per-tool-use: register-agent.sh writes the name to
#      ~/.myos/subagents/by-tool-use/<tool_use_id>.name at spawn.
#      We read that file using the same tool_use_id the Claude Code
#      harness passes in the PostToolUse payload. This is the only
#      lookup that survives parallel Task calls: last.name is a
#      single shared file and the newest spawn wins that race,
#      which would cause two subagents finishing together to both
#      /complete the newer row instead of their own.
#   2. Fallback: ~/.myos/subagents/last.name, for back-compat with
#      older hook runs that did not key by tool_use_id and for the
#      common single-agent case where the per-id file was never
#      written (e.g. Claude Code builds that do not emit tool_use_id).
#
# If neither file exists, exit cleanly: better to let the stale
# sweep handle it than to crash the hook chain.
#
# Idempotency:
# /api/agents/{name}/complete is idempotent server-side: a second call
# returns 200 with "already completed". Racing with a subagent that
# DID call /complete on its own is harmless.
#
# Transient-failure handling:
# complete-agent.sh runs on the parent's hot path and must not block,
# but a silent curl failure here was the root cause of the zombie
# running-agents list before the demo. We now:
#   * Retry the POST up to 3 times (1s, 2s, 4s backoff) so a brief
#     backend reload or MCP flap does not leave the row "running"
#     forever. Total wall-clock budget stays under 10s.
#   * On exhaustion, park the target name in
#     ~/.myos/subagents/pending-complete.jsonl so the next
#     register-agent.sh / heartbeat-agent.sh hook fires off the
#     drain and replays it as soon as the backend is reachable.

set -u

PER_ID_DIR="$HOME/.myos/subagents/by-tool-use"
NAME_FILE="$HOME/.myos/subagents/last.name"
PENDING_COMPLETE="$HOME/.myos/subagents/pending-complete.jsonl"

# Drain stdin so the hook system does not hold the pipe open, then
# derive tool_use_id, summary, and the AGENT_NAME to complete.
INPUT=$(cat 2>/dev/null || true)

# Debug: capture the PostToolUse payload so we can verify whether
# Claude Code echoes tool_input.run_in_background back in PostToolUse.
# The run_in_background guard below only skips /complete when we can
# see that flag; if the harness strips tool_input from PostToolUse we
# need to detect the background case some other way. Ring-buffer the
# last 200 lines (each payload truncated to 5000 bytes) at
# ~/.myos/subagents/complete-debug.log.
_DBG_LOG="$HOME/.myos/subagents/complete-debug.log"
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

# Extract tool_use_id from the hook payload. Used to resolve the
# per-tool-use name file written by register-agent.sh.
TOOL_USE_ID=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null || true
import os, sys, json
raw = os.environ.get("INPUT_JSON", "") or "{}"
try:
    d = json.loads(raw)
except Exception:
    sys.exit(0)
# Match register-agent.sh: Claude Code has used several key names
# across versions (tool_use_id, toolUseId, id, nested tool_use.id).
val = ""
for key in ("tool_use_id", "toolUseId", "id"):
    v = d.get(key)
    if isinstance(v, str) and v.strip():
        val = v.strip()
        break
if not val:
    tu = d.get("tool_use") or {}
    if isinstance(tu, dict):
        v = tu.get("id") or tu.get("tool_use_id") or ""
        if isinstance(v, str) and v.strip():
            val = v.strip()
sys.stdout.write(val)
PY
)

# Background subagents: Claude Code fires PostToolUse the moment the Agent
# tool CALL returns, not when the subagent's work actually finishes. For
# run_in_background:true the call returns immediately while the subagent
# keeps running in the background, so closing the row here would mark the
# agent completed within seconds of spawn and the torios UI would always
# show 0 running. Skip /complete when we can prove the parent set
# run_in_background:true - the register-agent.sh heartbeat loop already
# watches the subagent's transcript file and will emit /complete when the
# jsonl actually goes idle (TRANSCRIPT_IDLE_SECONDS).
#
# Two independent signals, because Claude Code's PostToolUse payload for
# Agent in some builds DROPS the tool_input field entirely (leaving only
# tool_response + tool_use_id). When that happens the in-payload check
# always says False and every bg subagent gets prematurely completed -
# this was the 4-in-CC-vs-1-in-torios bug class.
#
# Belt (in-payload check): authoritative when tool_input is present.
# Suspenders (side-channel file): register-agent.sh writes
#   ~/.myos/subagents/by-tool-use/<tool_use_id>.bg (content "1") for every
#   bg spawn at PreToolUse time, plus a legacy-fallback last.bg pointer
#   for the tool_use_id-less payload case. Reading that file here means
#   we can tell bg from fg even when the PostToolUse payload is gutted.
RUN_IN_BACKGROUND=$(INPUT_JSON="$INPUT" python3 <<'INNERPY' 2>/dev/null || true
import os, sys, json
raw = os.environ.get("INPUT_JSON", "") or "{}"
try:
    d = json.loads(raw)
except Exception:
    sys.exit(0)
ti = d.get("tool_input") or {}
if isinstance(ti, dict) and ti.get("run_in_background") is True:
    sys.stdout.write("1")
INNERPY
)

# Suspenders: fall back to the register-written bg flag files when the
# in-payload check could not prove bg. Per-tool-use file first (race-
# safe for parallel Task calls), then last.bg (single-agent / legacy).
if [ -z "$RUN_IN_BACKGROUND" ] && [ -n "$TOOL_USE_ID" ]; then
    SAFE_TUI_BG=$(printf '%s' "$TOOL_USE_ID" | tr -c 'a-zA-Z0-9_-' '_' | cut -c1-128)
    BG_FILE="$PER_ID_DIR/$SAFE_TUI_BG.bg"
    if [ -f "$BG_FILE" ] && [ -s "$BG_FILE" ]; then
        RUN_IN_BACKGROUND="1"
    fi
fi
if [ -z "$RUN_IN_BACKGROUND" ]; then
    LAST_BG_FILE="$HOME/.myos/subagents/last.bg"
    if [ -f "$LAST_BG_FILE" ] && [ -s "$LAST_BG_FILE" ]; then
        RUN_IN_BACKGROUND="1"
    fi
fi

if [ "$RUN_IN_BACKGROUND" = "1" ]; then
    # Keep the per-tool-use .name pointer and last.name so the heartbeat
    # idle-detector branch inside register-agent.sh still has a name
    # to /complete when the transcript goes idle. But DO clear the .bg
    # flag files: a single tool_use_id only ever fires PostToolUse once,
    # so leaving the flag would only matter if a later fg /complete
    # collided by name reuse, which the .bg would then falsely skip.
    if [ -n "$TOOL_USE_ID" ]; then
        SAFE_TUI_BG_CLEANUP=$(printf '%s' "$TOOL_USE_ID" | tr -c 'a-zA-Z0-9_-' '_' | cut -c1-128)
        rm -f "$PER_ID_DIR/$SAFE_TUI_BG_CLEANUP.bg" 2>/dev/null || true
    fi
    : > "$HOME/.myos/subagents/last.bg" 2>/dev/null || true
    exit 0
fi

AGENT_NAME=""
PER_ID_FILE=""
if [ -n "$TOOL_USE_ID" ]; then
    # Sanitize the same way register-agent.sh did so lookup matches.
    SAFE_TUI=$(printf '%s' "$TOOL_USE_ID" | tr -c 'a-zA-Z0-9_-' '_' | cut -c1-128)
    PER_ID_FILE="$PER_ID_DIR/$SAFE_TUI.name"
    if [ -f "$PER_ID_FILE" ]; then
        AGENT_NAME=$(cat "$PER_ID_FILE" 2>/dev/null | tr -d '[:space:]')
    fi
fi

# Fallback to the legacy last.name pointer. Safe for single-agent
# flows; racy for parallel Task calls but no worse than before.
if [ -z "$AGENT_NAME" ] && [ -f "$NAME_FILE" ]; then
    AGENT_NAME=$(cat "$NAME_FILE" 2>/dev/null | tr -d '[:space:]')
fi

if [ -z "$AGENT_NAME" ]; then
    exit 0
fi

SUMMARY=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null || true
import os, sys, json
raw = os.environ.get("INPUT_JSON", "") or "{}"
try:
    d = json.loads(raw)
except Exception:
    sys.exit(0)
out = ""
tr = d.get("tool_response") or d.get("tool_result") or {}
if isinstance(tr, dict):
    out = tr.get("output") or tr.get("text") or tr.get("result") or ""
elif isinstance(tr, str):
    out = tr
if isinstance(out, list):
    parts = []
    for item in out:
        if isinstance(item, dict):
            parts.append(item.get("text") or "")
        elif isinstance(item, str):
            parts.append(item)
    out = " ".join(parts)
out = (out or "").strip().splitlines()
first = (out[0] if out else "").strip()
sys.stdout.write(first[:200])
PY
)

if [ -z "$SUMMARY" ]; then
    SUMMARY="Agent tool returned"
fi

BODY=$(SUMMARY="$SUMMARY" python3 -c '
import os, json
print(json.dumps({"summary": os.environ.get("SUMMARY", "")}))
' 2>/dev/null)

if [ -z "$BODY" ]; then
    BODY='{"summary":"Agent tool returned"}'
fi

# Retry with backoff. Transient backend unavailability (uvicorn reload,
# MCP flap, brief socket error) is the direct cause of zombie rows
# before this change. Three attempts total 7 seconds at most.
COMPLETE_URL="${MYOS_COMPLETE_URL_BASE:-https://127.0.0.1:8000/api/agents}/${AGENT_NAME}/complete"
COMPLETE_OK=0
for delay in 1 2 4; do
    if curl -sSk --connect-timeout 2 -m 5 \
            -X POST "$COMPLETE_URL" \
            -H 'Content-Type: application/json' \
            -d "$BODY" > /dev/null 2>&1; then
        COMPLETE_OK=1
        break
    fi
    sleep "$delay"
done

if [ "$COMPLETE_OK" -eq 0 ]; then
    # Park the failed complete so a later hook or session-start
    # replay can finish the job. Store the fields needed to replay:
    # name (URL path) and the JSON body.
    mkdir -p "$(dirname "$PENDING_COMPLETE")" 2>/dev/null || true
    QUEUE_LINE=$(AGENT_NAME="$AGENT_NAME" BODY="$BODY" python3 -c '
import os, json
print(json.dumps({
    "name": os.environ["AGENT_NAME"],
    "body": os.environ["BODY"],
}))
' 2>/dev/null)
    if [ -n "$QUEUE_LINE" ]; then
        printf '%s\n' "$QUEUE_LINE" >> "$PENDING_COMPLETE" 2>/dev/null || true
    fi
fi

mkdir -p "$HOME/.myos/subagents" 2>/dev/null || true
printf '%s\t%s\tcompleted\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$AGENT_NAME" \
    >> "$HOME/.myos/subagents/history.log" 2>/dev/null || true

# Clear the per-tool-use pointer so a retry on the same id cannot
# double-fire, and clear last.name so a stale value cannot misfire
# the next /complete if the next Agent tool call fails before
# register-agent.sh updates the file. Also clear the matching .bg
# files so a stale bg marker cannot cause a later fg /complete to
# be skipped.
if [ -n "$PER_ID_FILE" ] && [ -f "$PER_ID_FILE" ]; then
    rm -f "$PER_ID_FILE" 2>/dev/null || true
fi
if [ -n "$TOOL_USE_ID" ]; then
    SAFE_TUI_CLEANUP=$(printf '%s' "$TOOL_USE_ID" | tr -c 'a-zA-Z0-9_-' '_' | cut -c1-128)
    rm -f "$PER_ID_DIR/$SAFE_TUI_CLEANUP.bg" 2>/dev/null || true
fi
: > "$NAME_FILE" 2>/dev/null || true
: > "$HOME/.myos/subagents/last.bg" 2>/dev/null || true

exit 0
