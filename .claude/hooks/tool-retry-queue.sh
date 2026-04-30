#!/bin/bash
# PostToolUse: detect tool interruptions and queue for retry.
#
# When a tool call fails due to connection issues (wifi drop, MCP
# disconnect, timeout) rather than user denial, log it to a retry
# queue. The standing-rules hook reads this queue on the next
# UserPromptSubmit and reminds Claude to retry.
#
# Queue file: ~/.myos/subagents/retry-queue.jsonl
# Each line: {"tool":"<name>","input_summary":"<truncated>","reason":"<pattern>","ts":<epoch>}

set -u

QUEUE_DIR="$HOME/.myos/subagents"
QUEUE_FILE="$QUEUE_DIR/retry-queue.jsonl"
mkdir -p "$QUEUE_DIR" 2>/dev/null

INPUT=$(cat)

TOOL=$(echo "$INPUT" | python3 -c "
import sys, json
try: print(json.load(sys.stdin).get('tool_name', ''))
except: print('')
" 2>/dev/null)

# Extract tool response text for pattern matching
RESP=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin).get('tool_response', {})
    if isinstance(d, str):
        print(d[:500])
    elif isinstance(d, dict):
        parts = []
        for k in ('stderr', 'error', 'content', 'output', 'message', 'text'):
            v = d.get(k)
            if isinstance(v, str):
                parts.append(v[:200])
            elif isinstance(v, list):
                for it in v:
                    if isinstance(it, dict):
                        t = it.get('text', '')
                        if t: parts.append(t[:200])
        print(' '.join(parts)[:500])
    else:
        print('')
except: print('')
" 2>/dev/null)

# Extract a short summary of the tool input for context
INPUT_SUMMARY=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    ti = json.load(sys.stdin).get('tool_input', {})
    if isinstance(ti, dict):
        s = json.dumps(ti)
        print(s[:120])
    else:
        print(str(ti)[:120])
except: print('?')
" 2>/dev/null)

# Pattern-match for interruption (not user denial)
REASON=""
case "$RESP" in
    *"connection reset"*|*"Connection reset"*)
        REASON="connection_reset" ;;
    *"timed out"*|*"Timed out"*|*"ETIMEDOUT"*|*"timeout"*|*"TIMEOUT"*)
        REASON="timeout" ;;
    *"MCP connection"*|*"mcp connection"*|*"server disconnected"*)
        REASON="mcp_disconnect" ;;
    *"ECONNREFUSED"*|*"ECONNRESET"*|*"EPIPE"*|*"Broken pipe"*)
        REASON="connection_error" ;;
    *"network error"*|*"Network error"*|*"fetch failed"*)
        REASON="network_error" ;;
    *"tool is not connected"*|*"Tool is not connected"*)
        REASON="tool_disconnected" ;;
    *"InputValidationError"*|*"schema"*)
        # Not an interruption, just a bad call. Skip.
        exit 0 ;;
esac

if [ -z "$REASON" ]; then
    exit 0
fi

NOW=$(date +%s)

# Deduplicate: don't queue the same tool+reason within 30s
if [ -f "$QUEUE_FILE" ]; then
    RECENT=$(python3 -c "
import json, sys
tool, reason, now = '$TOOL', '$REASON', $NOW
with open('$QUEUE_FILE') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try:
            e = json.loads(line)
            if e.get('tool') == tool and e.get('reason') == reason and now - e.get('ts', 0) < 30:
                print('dup')
                break
        except: pass
" 2>/dev/null)
    if [ "$RECENT" = "dup" ]; then
        exit 0
    fi
fi

# Append to queue
python3 -c "
import json
entry = {
    'tool': '$TOOL',
    'input_summary': '''$INPUT_SUMMARY'''[:120],
    'reason': '$REASON',
    'ts': $NOW
}
with open('$QUEUE_FILE', 'a') as f:
    f.write(json.dumps(entry) + '\n')
" 2>/dev/null

echo "[tool-retry] Queued $TOOL for retry (reason: $REASON). This was an interruption, not a rejection." >&2

exit 0
