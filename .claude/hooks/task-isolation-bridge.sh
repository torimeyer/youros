#!/bin/bash
# Hook: Task-tool isolation bridge.
#
# Closes needles →915 and →916. Claude Code's native Task/Agent tool
# spawns subagents via the Anthropic SDK directly. It never posts to
# /api/agents/spawn, which is the only path that creates a worktree
# for real filesystem isolation. As a result every Task-tool spawn
# tagged isolation:"worktree" today silently writes to the parent
# checkout, regardless of the flag.
#
# This hook fires PreToolUse on the Task tool. It inspects the prompt
# and description for edit-capable verbs. If the prompt is likely to
# mutate the repo AND isolation is not explicitly "none", the hook
# blocks the native call and POSTs the same prompt to /api/agents/spawn
# where the REST path owns worktree creation. The LLM is told to track
# the REST-spawned agent via /api/agents/<name>.
#
# Read-only prompts (Read|Grep|Glob only) pass through native Task
# unchanged so the Task-tool sub-agent can continue to be used for
# quick broad searches.
#
# Fallback: if the backend is unreachable, the hook BLOCKS the call
# with a clear message. It never silently falls through. That is the
# whole bug the hook exists to fix.
#
# Design notes:
# - Exit 2 + stderr is the block pattern used by sibling hooks. The
#   stderr string is relayed back to the model so the "redirect"
#   wording below is what the LLM actually reads.
# - We deliberately run BEFORE register-agent.sh in settings.json, so
#   blocked calls never register a phantom row on /api/agents.
# - Edit-verb regex is whole-word and case-insensitive.
# - Env knob TASK_ISOLATION_BRIDGE_DISABLE=1 short-circuits the hook
#   for emergency recovery; not a normal operator knob.

set -u

if [ "${TASK_ISOLATION_BRIDGE_DISABLE:-}" = "1" ]; then
    exit 0
fi

INPUT=$(cat)

# Resolve API base the same way register-agent.sh does: env first,
# then ~/.myos/config.json, then default HTTPS local. Keeping this in
# sync with register-agent.sh is important because the two hooks run
# back-to-back.
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

# Extract tool_name, prompt, description, subagent_type, isolation.
# Any of these may be absent. Isolation flag is typically nested under
# tool_input but we also check the top-level payload for forward
# compatibility with experimental Claude Code versions.
#
# We use an ASCII unit-separator (US, \x1f) between fields, not a tab.
# Tabs are IFS whitespace in bash and consecutive tabs merge, which
# would silently drop empty fields (e.g. a blank SUBAGENT would eat
# the ISOLATION field). US is non-whitespace so each field is kept
# even when empty. Trailing newline from the heredoc is stripped by
# the surrounding command substitution.
PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, sys, json
raw = os.environ.get("INPUT_JSON", "")
try:
    d = json.loads(raw or "{}")
except Exception:
    sys.exit(0)
tool = (d.get("tool_name") or "").strip()
ti = d.get("tool_input", {}) or {}
prompt = (ti.get("prompt") or "").strip()
desc = (ti.get("description") or "").strip()
subagent = (ti.get("subagent_type") or "").strip()
iso = ti.get("isolation")
if iso is None:
    iso = d.get("isolation")
iso = (iso or "").strip().lower() if isinstance(iso, str) else ""

def flat(s):
    return " ".join((s or "").split())

US = "\x1f"
sys.stdout.write(
    f"{flat(tool)}{US}{flat(prompt)}{US}{flat(desc)}{US}{flat(subagent)}{US}{flat(iso)}"
)
PY
)

if [ -z "$PARSED" ]; then
    exit 0
fi

IFS=$'\x1f' read -r TOOL PROMPT DESCRIPTION SUBAGENT ISOLATION <<<"$PARSED"

# Only act on Task/Agent invocations. Other tool calls fall through.
case "$TOOL" in
    Task|Agent) : ;;
    *) exit 0 ;;
esac

# Explicit opt-out. An operator may tag a spawn isolation:"none" when
# they genuinely want the native Task path, e.g. a local grep agent.
if [ "$ISOLATION" = "none" ]; then
    exit 0
fi

# Edit-verb detection. Whole-word, case-insensitive. We check prompt
# AND description because real Task calls often put the verb in the
# description while the prompt is a long paragraph.
HAY=$(printf '%s\n%s\n' "$PROMPT" "$DESCRIPTION" | tr '[:upper:]' '[:lower:]')

# Word-boundary regex. Keep this list in sync with the task spec.
# The grep -E pattern uses [^a-z0-9_] as the boundary on each side so
# "writer" does not match "write".
VERB_RE='(^|[^a-z0-9_])(edit|write|fix|commit|saa|diagnose|build|add|refactor|rename|create|delete)([^a-z0-9_]|$)'

if ! printf '%s' "$HAY" | grep -qE "$VERB_RE"; then
    # No edit verb detected. Treat as read-only. Allow native Task.
    exit 0
fi

# At this point we know the prompt looks edit-capable. Route it
# through the REST spawn path. Generate a stable-ish name from the
# description (same rule as register-agent.sh) plus a short uuid so
# parallel spawns do not collide on the backend side.
SPAWN_NAME=$(DESC="$DESCRIPTION" PROMPT="$PROMPT" python3 <<'PY' 2>/dev/null
import os, re, uuid
desc = os.environ.get("DESC", "") or os.environ.get("PROMPT", "")[:60]
base = re.sub(r"[^a-z0-9-]", "", desc.lower().replace(" ", "-"))[:32]
base = re.sub(r"-+", "-", base).strip("-") or "task-bridge"
print(f"{base}-{uuid.uuid4().hex[:6]}")
PY
)

if [ -z "$SPAWN_NAME" ]; then
    SPAWN_NAME="task-bridge-$$"
fi

# Build the spawn body. We pin isolation:"worktree" and source tag so
# downstream agent-list filters can distinguish bridge spawns from
# direct REST spawns.
BODY=$(SPAWN_NAME="$SPAWN_NAME" DESCRIPTION="$DESCRIPTION" PROMPT="$PROMPT" \
        SUBAGENT="$SUBAGENT" python3 <<'PY' 2>/dev/null
import os, json
body = {
    "name": os.environ["SPAWN_NAME"],
    "prompt": os.environ.get("PROMPT") or os.environ.get("DESCRIPTION") or "",
    "description": os.environ.get("DESCRIPTION") or "task-tool bridge spawn",
    "source": "task-bridge",
    "status": "running",
    "budget": 5,
    "isolation": "worktree",
    "locks": ["*"],
}
sub = os.environ.get("SUBAGENT") or ""
if sub:
    body["subagent_type"] = sub
print(json.dumps(body))
PY
)

if [ -z "$BODY" ]; then
    echo "Blocked: task-isolation-bridge could not build spawn body." >&2
    exit 2
fi

# Reachability probe. Short connect-timeout so a hung backend does
# not wedge every Task call. If the probe fails we BLOCK (never fall
# through) because that is the silent-no-op this hook exists to fix.
HTTP_CODE=$(curl -sSk --connect-timeout 3 -m 5 -o /dev/null -w '%{http_code}' \
    -X POST "${API_BASE}/api/agents/spawn" \
    -H 'Content-Type: application/json' \
    -d "$BODY" 2>/dev/null)

case "$HTTP_CODE" in
    2??)
        echo "Blocked: Task tool call redirected through /api/agents/spawn for worktree isolation." >&2
        echo "Spawned REST agent name: ${SPAWN_NAME}" >&2
        echo "Poll status via: curl -sSk ${API_BASE}/api/agents | jq '.agents[] | select(.name==\"${SPAWN_NAME}\")'" >&2
        echo "The native Task call was NOT run. This is needle ->915 / ->916." >&2
        exit 2
        ;;
    ""|"000")
        # curl prints 000 on connect-refused and leaves it empty only
        # on some platforms. Both mean "no HTTP response at all", i.e.
        # backend unreachable.
        echo "Blocked: torios backend unreachable at ${API_BASE}/api/agents/spawn (connect-timeout or refused)." >&2
        echo "Native Task tool would silently write to the parent checkout, breaking isolation." >&2
        echo "Start the backend with scripts/dev-backend.sh and retry." >&2
        exit 2
        ;;
    *)
        echo "Blocked: /api/agents/spawn returned HTTP ${HTTP_CODE}." >&2
        echo "Native Task tool would silently write to the parent checkout. Fix the backend error and retry." >&2
        exit 2
        ;;
esac
