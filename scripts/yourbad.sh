#!/usr/bin/env bash
# yourbad.sh - workaround shim for `ostk run agents/self-review.agent <desc>`
#
# Why this exists: `ostk run` parses Agentfiles cleanly (ab752f0) but still
# rejects positional args and prints "spawning not yet implemented" on the
# dry-run path. Upstream fix tracked in needle →907.
#
# What it does: reads the PROMPT block out of agents/self-review.agent,
# appends the mistake description, POSTs to /api/agents/spawn, prints the
# spawned agent id.
#
# Usage:
#   scripts/yourbad.sh "I re-spawned an agent whose work had already landed"

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 \"<mistake description>\"" >&2
  exit 2
fi

DESC="$1"

# Resolve repo root from this script's location so the wrapper works from
# any cwd (tack invocation from anywhere in the tree).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
AGENT_FILE="${REPO_ROOT}/agents/self-review.agent"

if [[ ! -f "${AGENT_FILE}" ]]; then
  echo "error: ${AGENT_FILE} not found" >&2
  exit 1
fi

# Backend auto-detect: try HTTPS first (dev default), fall back to HTTP.
# Both commonly run on port 8000. Override with API_BASE env var if needed.
if [[ -z "${API_BASE:-}" ]]; then
  if curl -sS -k --connect-timeout 2 -m 3 -o /dev/null -w '%{http_code}' \
      https://localhost:8000/api/agents 2>/dev/null | grep -qE '^[23]'; then
    API_BASE="https://localhost:8000"
  else
    API_BASE="http://localhost:8000"
  fi
fi

# Extract the PROMPT block. The Agentfile format uses `PROMPT "..."` on a
# single logical line with embedded newlines inside the quoted string.
# Python does the parsing so we handle multi-line quoted strings cleanly.
PROMPT_BODY="$(python3 - "${AGENT_FILE}" <<'PY'
import re, sys
with open(sys.argv[1], "r") as f:
    text = f.read()
# Match PROMPT "..." where the quoted value may span multiple lines.
m = re.search(r'^PROMPT\s+"((?:[^"\\]|\\.)*)"\s*$', text, re.MULTILINE | re.DOTALL)
if not m:
    sys.stderr.write("error: no PROMPT block found in agentfile\n")
    sys.exit(1)
# Un-escape the minimal set of escapes the parser understands.
body = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
sys.stdout.write(body)
PY
)"

# Compose the final prompt. The description goes on a labeled input line so
# the self-review prompt's "Input: a plain-language description..." sentence
# has something concrete to chew on.
FULL_PROMPT=$(printf '%s\n\nMISTAKE DESCRIPTION:\n%s\n' "${PROMPT_BODY}" "${DESC}")

# Unique agent name per invocation so re-runs don't collide.
AGENT_NAME="yourbad-$(date +%Y%m%d-%H%M%S)-$$"

# Build the JSON body with python so quoting and newlines are safe.
BODY_JSON="$(python3 - "${AGENT_NAME}" "${FULL_PROMPT}" <<'PY'
import json, sys
name, prompt = sys.argv[1], sys.argv[2]
print(json.dumps({
    "name": name,
    "prompt": prompt,
    "model": "sonnet",
    "template": "self-review",
    "source": "yourbad-shim",
    "description": "self-review agent (yourbad tack)",
    "budget": 2.0,
}))
PY
)"

CURL_OPTS=(-sS --connect-timeout 3 -m 15)
[[ "${API_BASE}" == https://* ]] && CURL_OPTS+=(-k)

RESP="$(curl "${CURL_OPTS[@]}" \
  -X POST "${API_BASE}/api/agents/spawn" \
  -H "Content-Type: application/json" \
  -d "${BODY_JSON}")"

# Extract the spawned agent name. The spawn endpoint returns the row; fall
# back to echoing raw response if jq/python parsing fails.
SPAWNED="$(printf '%s' "${RESP}" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print(d.get("name") or d.get("agent",{}).get("name") or "")' 2>/dev/null || true)"

if [[ -z "${SPAWNED}" ]]; then
  echo "spawn request sent but could not parse response. raw:" >&2
  echo "${RESP}" >&2
  exit 1
fi

echo "spawned: ${SPAWNED}"
echo "track: curl -sS ${API_BASE}/api/agents | jq '.agents[] | select(.name==\"${SPAWNED}\")'"
