#!/usr/bin/env bash
# Watch /api/agents for a name pattern and print status changes.
# Designed for the Claude Code Monitor tool: each stdout line becomes one event.
#
# Usage:
#   scripts/monitor-agent.sh <name-pattern>
#
# Example:
#   scripts/monitor-agent.sh diagnose-fix-mychat
#
# Exit: when the matching row first appears then disappears (agent finished or aged out).
# Also prints the latest 5 commits on the current branch on exit so you can see what landed.
set -u
PATTERN="${1:?usage: monitor-agent.sh <name-pattern>}"
API="https://127.0.0.1:8000/api/agents?limit=30"
seen=0
prev=""
while true; do
    s=$(curl -sSk --connect-timeout 3 -m 5 "${API}" 2>/dev/null | python3 -c "
import sys, json
try:
    raw = sys.stdin.read()
    if not raw:
        print('EMPTY-RESPONSE'); sys.exit(0)
    d = json.loads(raw, strict=False)
    rows = [r for r in d.get('agents', []) if '${PATTERN}' in (r.get('name') or '')]
    if not rows:
        print('NO-ROW'); sys.exit(0)
    for r in rows[:3]:
        print(f\"{r.get('name','?')[:50]} status={r.get('status','?')} bytes={r.get('transcript_bytes',0)} step={(r.get('current_step') or '')[:80]}\")
except Exception as e:
    print(f'PARSE-ERR: {e}')
" 2>&1)
    if [ -n "${s}" ] && [ "${s}" != "${prev}" ]; then
        echo "${s}"
        prev="${s}"
    fi
    case "${s}" in
        NO-ROW)
            if [ "${seen}" -ge 1 ]; then
                echo "--- agent gone, latest commits: ---"
                git log --oneline -5 2>/dev/null
                break
            fi
            ;;
        EMPTY-RESPONSE|PARSE-ERR*)
            : # tolerate transient errors
            ;;
        *)
            seen=1
            ;;
    esac
    sleep 30
done
