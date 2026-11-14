#!/bin/bash
# Hook: run when Claude Code or Gemini CLI exits a session (SessionEnd event).
#
# Responsibilities:
#   1. Mark the session agent row (claude-code-<sid> or gemini-cli-<sid>)
#      completed via POST /api/agents/<name>/complete so finished sessions do
#      not stay RUNNING on the Agents page.
#   2. Close any auto-filed session task linked to SESSION_ID via
#      POST /api/tasks/close-by-session/<session_id>.
#
# The hook is best-effort. Any API or network failure exits 0 so session
# shutdown is never blocked.

set -u

INPUT=$(cat 2>/dev/null || true)

PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
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
    prefix = "gemini-cli"
    label = "Gemini CLI"
    env_sid = os.environ.get("GEMINI_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""
else:
    prefix = "claude-code"
    label = "Claude Code"
    env_sid = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("GEMINI_SESSION_ID") or ""

sid = (d.get("session_id") or session_obj.get("id") or env_sid or "").strip()
explicit_name = (os.environ.get("YOUROS_AGENT_NAME") or "").strip()

if explicit_name:
    raw_name = explicit_name
elif sid:
    raw_name = f"{prefix}-{sid[:10]}"
else:
    raw_name = ""

agent_name = re.sub(r"[^a-z0-9-]+", "-", raw_name.lower())
agent_name = re.sub(r"-+", "-", agent_name).strip("-")

reason = (d.get("reason") or "").strip()
summary = f"{label} session ended ({reason})" if reason else f"{label} session ended"
complete_body = json.dumps({"summary": summary})

US = "\x1f"
sys.stdout.write(f"{runtime}{US}{sid}{US}{agent_name}{US}{complete_body}")
PY
)

RUNTIME="claude"
SESSION_ID=""
AGENT_NAME=""
COMPLETE_BODY='{"summary":"Session ended"}'
if [ -n "$PARSED" ]; then
    IFS=$'\x1f' read -r RUNTIME SESSION_ID AGENT_NAME COMPLETE_BODY <<<"$PARSED"
fi

API_BASE="${YOUROS_BACKEND_URL:-${TORIOS_API_BASE:-https://localhost:8000}}"

if [ -n "$AGENT_NAME" ]; then
    curl -sSk --connect-timeout 2 -m 3 \
        -X POST "${API_BASE}/api/agents/${AGENT_NAME}/complete" \
        -H 'Content-Type: application/json' \
        -d "$COMPLETE_BODY" \
        > /dev/null 2>&1 || true
fi

if [ -n "$SESSION_ID" ]; then
    curl -sSk --connect-timeout 2 -m 3 \
        -X POST "${API_BASE}/api/tasks/close-by-session/${SESSION_ID}" \
        -H 'Content-Type: application/json' \
        > /dev/null 2>&1 || true
fi

if [ "$RUNTIME" = "gemini" ]; then
    printf '{}\n'
fi

exit 0
