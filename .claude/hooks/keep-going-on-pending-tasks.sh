#!/bin/bash
# UserPromptSubmit: emit a FIRE-NOW directive when open tasks exist and no agents are running.
# ONLY fires when the user explicitly says keep going / continue / next / what's next / go,
# so the hook never pushes Claude into autonomous spawning on idle turns.
# See feedback_pending_task_hook_not_autonomous.md.

BACKEND_URL="${MYOS_BACKEND_URL:-https://127.0.0.1:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Read the user prompt from the JSON payload on stdin and gate on
# keep-going phrases at the start of the message. Test fixture
# MYOS_USER_PROMPT_FIXTURE overrides for the test path.
if [ "${MYOS_USER_PROMPT_FIXTURE+x}" = "x" ]; then
  USER_MSG="$MYOS_USER_PROMPT_FIXTURE"
else
  HOOK_INPUT="$(cat)"
  USER_MSG=$(HOOK_INPUT="$HOOK_INPUT" python3 -c "
import os, json, sys
raw = os.environ.get('HOOK_INPUT', '') or '{}'
try:
    d = json.loads(raw, strict=False)
except Exception:
    sys.exit(0)
p = d.get('prompt') or ''
if isinstance(p, str):
    sys.stdout.write(p)
" 2>/dev/null)
fi

# Lowercase + trim leading whitespace.
MSG_LOWER=$(printf '%s' "$USER_MSG" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//')

case "$MSG_LOWER" in
  "keep going"*|"continue"*|"next"*|"what's next"*|"what next"*|"whats next"*|"go"|"go "*)
    : # gate passed, run the FIRE-NOW directive logic below
    ;;
  *)
    exit 0
    ;;
esac

if [ "${MYOS_TASKS_FIXTURE+x}" = "x" ]; then
  TASKS_JSON="$MYOS_TASKS_FIXTURE"
else
  TASKS_JSON="$(curl --silent --insecure --tlsv1.2 --tls-max 1.2 --connect-timeout 3 -m 5     "${BACKEND_URL}/api/tasks?limit=30" 2>/dev/null)"
fi

if [ "${MYOS_AGENTS_FIXTURE+x}" = "x" ]; then
  AGENTS_JSON="$MYOS_AGENTS_FIXTURE"
else
  AGENTS_JSON="$(curl --silent --insecure --tlsv1.2 --tls-max 1.2 --connect-timeout 3 -m 8     "${BACKEND_URL}/api/agents" 2>/dev/null)"
fi

[ -z "$TASKS_JSON" ] && exit 0
[ -z "$AGENTS_JSON" ] && exit 0

# Pipe via temp files so 500KB+ agent payloads do not overflow the env-var
# argument list (caused "Argument list too long" with 950+ agents in the system).
TMP_T="$(mktemp -t kgo-tasks-XXXXXX)"
TMP_A="$(mktemp -t kgo-agents-XXXXXX)"
trap 'rm -f "$TMP_T" "$TMP_A"' EXIT
printf '%s' "$TASKS_JSON" > "$TMP_T"
printf '%s' "$AGENTS_JSON" > "$TMP_A"

KGO_TASKS_FILE="$TMP_T" KGO_AGENTS_FILE="$TMP_A" \
  python3 "${SCRIPT_DIR}/lib/keep-going-check.py"

exit 0
