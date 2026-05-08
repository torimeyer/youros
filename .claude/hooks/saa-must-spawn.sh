#!/bin/bash
HOOK_NAME=$(basename "$0")
_HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_HOOKS_DIR/lib/deny.sh"
init_deny_traps
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME tool=${TOOL:-?} exit=$?" >> /tmp/hook-trace.log' EXIT
# If the most recent user message started with saa/diagnose/fix, the only
# acceptable next tool is Agent (or Task). Block anything else UNLESS:
#   (a) an Agent/Task call already appears in the transcript this turn, OR
#   (b) the bash command is clearly ancillary (server start, needle filing).
#
# Tori-personal gate: vocabulary is specific to tori's workflow. NR users
# saying "fix this bug" should not be silently rejected. Hook is a no-op
# unless ~/.myos/config.json has "enable_tori_rules": true.
TORI_CONFIG="${MYOS_CONFIG_PATH:-$HOME/.myos/config.json}"
if [ ! -f "$TORI_CONFIG" ] || ! grep -q '"enable_tori_rules"[[:space:]]*:[[:space:]]*true' "$TORI_CONFIG" 2>/dev/null; then
    exit 0
fi
# Subagent skip: subagent Claude Code processes inherit $HOME so they read
# tori's config and see the flag. Without this skip, the hook tells the
# subagent it must spawn ANOTHER agent — wasted session, cascade. Detect
# subagent context via CLAUDE_PROJECT_DIR pointing inside .claude/worktrees/.
case "${CLAUDE_PROJECT_DIR:-}" in
    */.claude/worktrees/*) exit 0 ;;
esac

INPUT=$(cat)

# Extract tool name and command in one python pass (strict=False for robustness).
PARSED=$(INPUT_JSON="$INPUT" python3 -c "
import os, json, sys
try:
    d = json.loads(os.environ.get('INPUT_JSON', '') or '{}', strict=False)
except Exception:
    sys.exit(0)
tool = (d.get('tool_name') or '').strip()
ti = d.get('tool_input', {}) or {}
cmd = (ti.get('command') or ti.get('cmd') or '')
sys.stdout.write(tool + '\x1f' + cmd)
" 2>/dev/null)
[ -z "$PARSED" ] && exit 0
IFS=$'\x1f' read -r TOOL CMD <<<"$PARSED"

case "$TOOL" in
  Agent|Task) exit 0 ;;
esac

# Locate this session's transcript directory. Claude Code stores each
# project's session logs under ~/.claude/projects/<slug>/ where <slug>
# is the absolute project path with '/' replaced by '-'. Derive the
# slug from $CLAUDE_PROJECT_DIR (set by the harness) or fall back to
# the git toplevel so the hook works for any user, not just the one
# it was originally written for.
PROJ_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
[ -z "$PROJ_DIR" ] && exit 0
SLUG=$(echo "$PROJ_DIR" | sed 's|/|-|g')
LAST=$(ls -t "$HOME/.claude/projects/$SLUG/"*.jsonl 2>/dev/null | head -1)
[ -z "$LAST" ] && exit 0
MSG=$(grep '"type":"user"' "$LAST" | tail -1 | python3 -c "import sys,json;d=json.loads(sys.stdin.read());c=d.get('message',{}).get('content');print((c if isinstance(c,str) else '').lower())" 2>/dev/null)
case "$MSG" in
  "saa "*|"diagnose "*|"fix "*)
    VERB="${MSG%% *}"

    # Allow if Agent/Task already appears in the transcript this turn.
    # Scan lines after the last real user message (string content) for an
    # assistant tool_use of name Agent or Task. Covers both same-message
    # (Agent + Bash emitted together) and sequential (Agent result written
    # before the next Bash call).
    AGENT_FIRED=$(LAST_JSONL="$LAST" python3 - <<'PY' 2>/dev/null
import os, json, sys
path = os.environ.get("LAST_JSONL", "")
try:
    with open(path) as f:
        lines = f.readlines()
except Exception:
    sys.exit(0)
last_user_pos = -1
for i, line in enumerate(lines):
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
        if d.get("type") == "user":
            c = d.get("message", {}).get("content", "")
            if isinstance(c, str):
                last_user_pos = i
    except Exception:
        continue
if last_user_pos < 0:
    sys.exit(0)
for line in lines[last_user_pos + 1:]:
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
        if d.get("type") != "assistant":
            continue
        content = d.get("message", {}).get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    if item.get("name") in ("Agent", "Task"):
                        print("yes")
                        sys.exit(0)
    except Exception:
        continue
PY
)
    [ "$AGENT_FIRED" = "yes" ] && exit 0

    # Allow clearly ancillary commands even before Agent fires:
    # server/script management and needle filing.
    case "$CMD" in
      *scripts/dev-*|*scripts/run-vitest.sh*|*scripts/e2e_smoke.sh*) exit 0 ;;
      *"ostk work "*) exit 0 ;;
    esac

    deny "the user said '$VERB'. Rule: spawn a subagent via Agent, no inline work." ;;
esac
exit 0
