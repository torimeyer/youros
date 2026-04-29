#!/bin/bash
# UserPromptSubmit: emit a FIRE-NOW directive when open tasks exist and no agents are running.
# Mechanical enforcement that memory entries alone cannot provide -- fires every turn.

BACKEND_URL="${MYOS_BACKEND_URL:-https://127.0.0.1:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
