#!/bin/bash
# Hook: run when Claude Code exits a session (SessionEnd event).
#
# Responsibility: close the auto-filed session task that the
# SessionStart hook opened when this session began. Without this
# the row stays open on the Tasks page forever even after the
# terminal window closes.
#
# The hook is best-effort. Any API or network failure logs and
# exits 0 so the session shutdown is never blocked.

set -u

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('')
    sys.exit(0)
sid = d.get('session_id', '') or d.get('session', {}).get('id', '')
print(sid)
" 2>/dev/null)

# Nothing to do if we cannot identify the session.
if [ -z "$SESSION_ID" ]; then
    exit 0
fi

# Try http first (dev), then https (release). A short timeout so the
# session never hangs on shutdown when the backend is offline.
curl -sS --connect-timeout 2 -m 3 \
    -X POST "http://localhost:8000/api/tasks/close-by-session/$SESSION_ID" \
    -H 'Content-Type: application/json' \
    > /dev/null 2>&1 || true

curl -sSk --connect-timeout 2 -m 3 \
    -X POST "https://localhost:8000/api/tasks/close-by-session/$SESSION_ID" \
    -H 'Content-Type: application/json' \
    > /dev/null 2>&1 || true

exit 0
