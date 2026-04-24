#!/bin/bash
# Regression test: the Agent PreToolUse register hook must be wired
# EXACTLY ONCE, in the user-global ~/.claude/settings.json, and NOT
# in the project-local .claude/settings.json. If both match, Claude
# Code fires the hook twice per Task spawn and the /register POST
# duplicates rows (or 409s and we lose the bg-flag handoff).
#
# Fails if:
#   - ~/.claude/settings.json has zero PreToolUse matcher=Agent entries
#   - project .claude/settings.json has a PreToolUse matcher=Agent entry
#     pointing at register-agent.sh
#
# Usage: bash .claude/hooks/tests/no-double-register.sh

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PROJECT_SETTINGS="$REPO_ROOT/.claude/settings.json"
GLOBAL_SETTINGS="$HOME/.claude/settings.json"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

if [ ! -f "$PROJECT_SETTINGS" ]; then
    fail "project settings.json not found: $PROJECT_SETTINGS"
fi
if [ ! -f "$GLOBAL_SETTINGS" ]; then
    fail "global settings.json not found: $GLOBAL_SETTINGS (run scripts/install-claude-hooks.sh)"
fi

PROJECT_AGENT_COUNT=$(PATH_JSON="$PROJECT_SETTINGS" python3 <<'PY'
import json, os, sys
path = os.environ["PATH_JSON"]
try:
    with open(path) as f:
        d = json.load(f)
except Exception as e:
    sys.stderr.write(f"parse error: {e}\n")
    sys.exit(2)
pretool = (d.get("hooks") or {}).get("PreToolUse") or []
n = 0
for e in pretool:
    if not isinstance(e, dict):
        continue
    if e.get("matcher") != "Agent":
        continue
    for h in e.get("hooks") or []:
        if isinstance(h, dict) and "register-agent.sh" in (h.get("command") or ""):
            n += 1
print(n)
PY
)

GLOBAL_AGENT_COUNT=$(PATH_JSON="$GLOBAL_SETTINGS" python3 <<'PY'
import json, os, sys
path = os.environ["PATH_JSON"]
try:
    with open(path) as f:
        d = json.load(f)
except Exception as e:
    sys.stderr.write(f"parse error: {e}\n")
    sys.exit(2)
pretool = (d.get("hooks") or {}).get("PreToolUse") or []
n = 0
for e in pretool:
    if not isinstance(e, dict):
        continue
    if e.get("matcher") != "Agent":
        continue
    for h in e.get("hooks") or []:
        if isinstance(h, dict) and "register-agent.sh" in (h.get("command") or ""):
            n += 1
print(n)
PY
)

if [ "$PROJECT_AGENT_COUNT" != "0" ]; then
    fail "project .claude/settings.json has $PROJECT_AGENT_COUNT Agent register entries; expected 0 (must be global-only to prevent double-fire)"
fi

if [ "$GLOBAL_AGENT_COUNT" != "1" ]; then
    fail "global ~/.claude/settings.json has $GLOBAL_AGENT_COUNT Agent register entries; expected exactly 1"
fi

printf 'PASS: Agent register hook wired exactly once (global only)\n'
exit 0
