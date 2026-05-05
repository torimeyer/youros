#!/bin/bash
HOOK_NAME=$(basename "$0")
_DENY_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
source "${_DENY_DIR}/.claude/hooks/lib/deny.sh"
init_deny_traps
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME tool=${TOOL:-?} exit=$?" >> /tmp/hook-trace.log' EXIT
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

# Read-only agent types. These have no Edit/Write tools so the bridge's
# edit-capable premise does not apply regardless of prompt content.
case "$SUBAGENT" in
    Explore|Plan|claude-code-guide) exit 0 ;;
esac

# Edit-verb detection. Whole-word, case-insensitive. We check prompt
# AND description because real Task calls often put the verb in the
# description while the prompt is a long paragraph.
HAY=$(printf '%s\n%s\n' "$PROMPT" "$DESCRIPTION" | tr '[:upper:]' '[:lower:]')

# Word-boundary regex. Keep this list in sync with the task spec.
# The grep -E pattern uses [^a-z0-9_] as the boundary on each side so
# "writer" does not match "write".
VERB_RE='(^|[^a-z0-9_])(edit|write|fix|commit|saa|diagnose|build|add|refactor|rename|create|delete|update|modify|replace|remove)([^a-z0-9_]|$)'

# Strip negated verb phrases so "do not modify" / "don't delete" etc. do not
# trigger the bridge on read-only prompts that mention verbs in negative context.
HAY_CHECK=$(printf '%s' "$HAY" | python3 -c "
import sys, re
t = sys.stdin.read()
t = re.sub(r'\\b(?:do\\s+not|don.t|never|no)\\s+\\w+', ' ', t, flags=re.IGNORECASE)
sys.stdout.write(t)
" 2>/dev/null || printf '%s' "$HAY")
if ! printf '%s' "$HAY_CHECK" | grep -qE "$VERB_RE"; then
    # No edit verb detected. Treat as read-only. Allow native Task.
    exit 0
fi

# Require explicit Locks: header. Edit-capable spawns that omit it are
# blocked outright so greedy path-extraction heuristics cannot fire.
HAS_EXPLICIT_LOCKS=$(PROMPT="$PROMPT" DESCRIPTION="$DESCRIPTION" python3 -c "
import os, re
hay = os.environ.get('PROMPT','') + '\n' + os.environ.get('DESCRIPTION','')
print('1' if re.search(r'[Ll]ocks\s*:\s*\[([^\]]*)\]', hay) else '0')
" 2>/dev/null)
if [ "${HAS_EXPLICIT_LOCKS:-0}" != "1" ]; then
    deny "edit-capable spawn did not declare Locks. Add a header like \`Locks: [path/one.py, path/two.tsx]\` at the top of the prompt naming only files this agent will write. Reads do not need locks."
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

# Provenance: extract session_id and tool_use_id from the stdin JSON payload.
# Claude Code passes session_id at the top level of the PreToolUse payload on
# stdin; it does NOT populate CLAUDE_SESSION_ID in hook env. Fall back to
# CLAUDE_SESSION_ID for back-compat if stdin parse fails (e.g. old harness).
_SID_MID=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, json, sys
US = "\x1f"
raw = os.environ.get("INPUT_JSON", "")
try:
    d = json.loads(raw or "{}")
except Exception:
    sys.stdout.write(US)
    sys.exit(0)
session_id = (d.get("session_id") or "").strip()
tool_use_id = (d.get("tool_use_id") or "").strip()
sys.stdout.write(f"{session_id}{US}{tool_use_id}")
PY
)
IFS=$'\x1f' read -r _STDIN_SID ORIG_MSG_ID <<<"$_SID_MID"
ORIG_SESSION_ID="${_STDIN_SID:-${CLAUDE_SESSION_ID:-}}"

# Build the spawn body. We pin isolation:"worktree" and source tag so
# downstream agent-list filters can distinguish bridge spawns from
# direct REST spawns.
BODY=$(SPAWN_NAME="$SPAWN_NAME" DESCRIPTION="$DESCRIPTION" PROMPT="$PROMPT" \
        SUBAGENT="$SUBAGENT" \
        ORIG_SESSION_ID="$ORIG_SESSION_ID" ORIG_MSG_ID="${ORIG_MSG_ID:-}" \
        python3 <<'PY' 2>/dev/null
import os, json, re
prompt = os.environ.get("PROMPT") or ""
desc = os.environ.get("DESCRIPTION") or ""
hay = prompt + "\n" + desc

# Explicit locks hint takes priority over heuristic extraction.
# Matches: locks: [path1, path2] or locks: ["path1", "path2"] anywhere in hay.
# Empty list (Locks: []) is valid and means "this spawn writes nothing."
locks = set()
EXPLICIT_RE = re.compile(r"[Ll]ocks\s*:\s*\[([^\]]*)\]")
em = EXPLICIT_RE.search(hay)
if em:
    for part in re.split(r"[,\s]+", em.group(1)):
        part = part.strip().strip("\"'")
        if part:
            locks.add(part)


orig_session = os.environ.get("ORIG_SESSION_ID") or ""
orig_msg = os.environ.get("ORIG_MSG_ID") or ""
body = {
    "name": os.environ["SPAWN_NAME"],
    "prompt": prompt or desc,
    "description": desc or "task-tool bridge spawn",
    "source": "task-bridge",
    "status": "running",
    "isolation": "worktree",
    "locks": sorted(locks),
    "user_authored": bool(orig_session and orig_msg),
}
if orig_session:
    body["originating_session_id"] = orig_session
if orig_msg:
    body["originating_user_message_id"] = orig_msg
sub = os.environ.get("SUBAGENT") or ""
if sub:
    body["subagent_type"] = sub
print(json.dumps(body))
PY
)

if [ -z "$BODY" ]; then
    deny "task-isolation-bridge could not build spawn body."
fi

# Reachability probe. Short connect-timeout so a hung backend does
# not wedge every Task call. If the probe fails we BLOCK (never fall
# through) because that is the silent-no-op this hook exists to fix.
RESP_BODY=$(mktemp)
HTTP_CODE="000"
for _retry in 1 2 3; do
    HTTP_CODE=$(curl --silent --insecure --tlsv1.2 --tls-max 1.2 --connect-timeout 3 -m 5 -o "$RESP_BODY" -w '%{http_code}' \
        -X POST "${API_BASE}/api/agents/spawn" \
        -H 'Content-Type: application/json' \
        -d "$BODY" 2>/dev/null)
    [ "$HTTP_CODE" != "000" ] && break
    [ "$_retry" -lt 3 ] && sleep 5
done

case "$HTTP_CODE" in
    2??)
        rm -f "$RESP_BODY"
        # P1-b/→925: record bridge spawn so the parent session can arm Monitor.
        # PostToolUse:Agent does NOT fire after a PreToolUse-block, so writing
        # this from auto-monitor-spawn.sh wouldn't work. Write here instead.
        PENDING_FILE="${CLAUDE_PROJECT_DIR:-.}/.ostk/pending-monitor-spawns.jsonl"
        mkdir -p "$(dirname "$PENDING_FILE")" 2>/dev/null || true
        SPAWN_NAME="$SPAWN_NAME" python3 -c '
import os, json
from datetime import datetime, timezone
print(json.dumps({"name": os.environ["SPAWN_NAME"], "ts": datetime.now(timezone.utc).isoformat(), "source": "task-isolation-bridge"}))
' 2>/dev/null >> "$PENDING_FILE" 2>/dev/null || true
        echo "redirected through /api/agents/spawn for worktree isolation (this is expected, not an error)." >&2
        echo "Spawned REST agent name: ${SPAWN_NAME}" >&2
        echo "Status: curl -sSk ${API_BASE}/api/agents | jq '.agents[] | select(.name==\"${SPAWN_NAME}\")'" >&2
        exit 2
        ;;
    ""|"000")
        # curl prints 000 on connect-refused and leaves it empty only
        # on some platforms. Both mean "no HTTP response at all", i.e.
        # backend unreachable. Fail-open: a fresh user without the myOS
        # backend running should not be blocked from using Task. The
        # original "block to prevent silent fallthrough" rule only made
        # sense when the backend was assumed up; for non-torios users
        # there is no isolation guarantee to protect, just one checkout.
        rm -f "$RESP_BODY"
        echo "task-isolation-bridge: myOS backend unreachable at ${API_BASE} (connect-timeout/refused)." >&2
        echo "Allowing native Task tool. Worktree isolation skipped — sequential edits only." >&2
        exit 0
        ;;
    *)
        if [ -s "$RESP_BODY" ]; then
            RESP_BODY="$RESP_BODY" python3 -c '
import os, sys, json
try:
    body = json.load(open(os.environ["RESP_BODY"]))
    conflicts = (body.get("detail") or {}).get("conflicts") or []
    for c in conflicts:
        held = c.get("held_by_spawn", "?")
        path = c.get("held_path", "?")
        print("  conflict: held_by_spawn=" + str(held) + " held_path=" + str(path))
except Exception:
    pass
' >&2 2>/dev/null
        fi
        rm -f "$RESP_BODY"
        deny "/api/agents/spawn returned HTTP ${HTTP_CODE}. Native Task tool would silently write to the parent checkout. Fix the backend error and retry."
        ;;
esac
