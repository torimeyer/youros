#!/bin/bash
# Combined PostToolUse watch for Bash, Read, Edit, Write, Grep, Glob.
#
# Replaces three separate hooks with one parse-once dispatcher:
#   - api-probe-hint        (Bash response: hint on curl exit 52)
#   - native-block-recovery (Bash|Read|Edit|Write|Grep|Glob: nag after
#                            2+ ostk-first blocks in 60s)
#   - tool-retry-queue      (any tool: log connection-error retries)
#
# All checks are non-blocking (exit 0) and informational. They pull
# tool_name + tool_response once and dispatch.

set -u
INPUT=$(cat 2>/dev/null || true)

# Single-pass parse: tool_name, tool_response text, tool_input.command.
PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, sys, json
try:
    d = json.loads(os.environ.get("INPUT_JSON", "") or "{}", strict=False)
except Exception:
    sys.exit(0)
tool = (d.get("tool_name") or "").strip()
ti = d.get("tool_input") or {}
cmd = ""
if isinstance(ti, dict):
    cmd = ti.get("command") or ti.get("cmd") or ""
tr = d.get("tool_response") or {}
parts = []
if isinstance(tr, str):
    parts.append(tr)
elif isinstance(tr, dict):
    for key in ("stderr", "error", "output", "content", "message", "text"):
        v = tr.get(key)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    t = item.get("text") or ""
                    if t:
                        parts.append(t)
                elif isinstance(item, str):
                    parts.append(item)
resp = "\n".join(parts)
sys.stdout.write(tool + "\x1f" + cmd + "\x1f" + resp)
PY
)

if [ -z "$PARSED" ]; then
    exit 0
fi

IFS=$'\x1f' read -r TOOL CMD RESP <<<"$PARSED"

# ---------------------------------------------------------------------
# 1. api-probe-hint: Bash. curl exit 52 against :8000 = HTTPS mismatch.
# ---------------------------------------------------------------------
if [ "$TOOL" = "Bash" ] && echo "$CMD" | grep -qi curl; then
    case "$CMD" in
        *localhost:8000*|*127.0.0.1:8000*)
            RESP_LOWER=$(printf '%s' "$RESP" | tr '[:upper:]' '[:lower:]')
            case "$RESP_LOWER" in
                *"exit:52"*|*"exit code: 52"*|*"exit_code: 52"*|*"returned exit code 52"*|*"(52)"*)
                    echo "Tip: curl exit 52 on :8000 is the →914 HTTP-vs-HTTPS mismatch. Use scripts/api-probe.sh (or curl -k https://...) instead." >&2
                    ;;
            esac
            ;;
    esac
fi

# ---------------------------------------------------------------------
# 2. native-block-recovery: Bash|Read|Edit|Write|Grep|Glob.
#    Counts consecutive ostk-first blocks; nags after 2 within 60s.
# ---------------------------------------------------------------------
case "$TOOL" in
    Bash|Read|Edit|Write|Grep|Glob)
        case "$RESP" in
            *"Blocked: use mcp__ostk__"*)
                DIR="$HOME/.myos/subagents"
                mkdir -p "$DIR" 2>/dev/null || true
                SID="${CLAUDE_SESSION_ID:-}"
                if [ -z "$SID" ]; then
                    PTREE=$(ps -o ppid= -p $$ 2>/dev/null | tr -d ' ')
                    SID=$(echo "${PTREE:-$$}" | md5sum 2>/dev/null | awk '{print $1}' | cut -c1-12)
                    [ -z "$SID" ] && SID=$(echo "${PTREE:-$$}" | md5 2>/dev/null | cut -c1-12)
                    [ -z "$SID" ] && SID="$$"
                fi
                CFILE="$DIR/${SID}-blocks.count"
                NOW=$(date +%s)
                PREV_TS=0; PREV_COUNT=0; PREV_NAGGED=0
                if [ -f "$CFILE" ]; then
                    read -r PREV_TS PREV_COUNT PREV_NAGGED < "$CFILE" 2>/dev/null || true
                    PREV_TS=${PREV_TS:-0}; PREV_COUNT=${PREV_COUNT:-0}; PREV_NAGGED=${PREV_NAGGED:-0}
                fi
                AGE=$(( NOW - PREV_TS ))
                if [ "$AGE" -gt 60 ]; then
                    COUNT=1; NAGGED=0
                else
                    COUNT=$(( PREV_COUNT + 1 )); NAGGED=$PREV_NAGGED
                fi
                if [ "$COUNT" -gt 5 ]; then
                    echo "$NOW 0 0" > "$CFILE"
                else
                    if [ "$COUNT" -ge 2 ]; then
                        cat >&2 <<NAG
RECOVERY NAG: You have been blocked $COUNT times in a row by the ostk-first hook.
Stop retrying native Bash/Read/Edit/Write/Grep/Glob. Those tools are blocked while uvicorn is up.
Do this RIGHT NOW:
  1. Call ToolSearch('select:mcp__ostk__shell,mcp__ostk__fs_read,mcp__ostk__fs_write,mcp__ostk__edit,mcp__ostk__search')
  2. Retry your operation via mcp__ostk__* (shell for commands, fs_read/fs_write for files, edit for patches, search for grep/glob).
  3. Only fall back to native tools if pgrep shows uvicorn on :8000 is truly offline.
NAG
                        NAGGED=1
                    fi
                    echo "$NOW $COUNT $NAGGED" > "$CFILE"
                fi
                ;;
            *)
                # Clean response: reset the counter.
                CFILE="$HOME/.myos/subagents/${CLAUDE_SESSION_ID:-$$}-blocks.count"
                if [ -f "$CFILE" ]; then
                    read -r _ PREV_COUNT _ < "$CFILE" 2>/dev/null || true
                    if [ -n "${PREV_COUNT:-}" ] && [ "$PREV_COUNT" != "0" ]; then
                        echo "$(date +%s) 0 0" > "$CFILE" 2>/dev/null || true
                    fi
                fi
                ;;
        esac
        ;;
esac

# ---------------------------------------------------------------------
# 3. tool-retry-queue: log connection-error responses for any tool.
#    The next user prompt's standing-rules surfaces the queue.
# ---------------------------------------------------------------------
QUEUE_DIR="$HOME/.myos/subagents"
QUEUE_FILE="$QUEUE_DIR/retry-queue.jsonl"
mkdir -p "$QUEUE_DIR" 2>/dev/null || true

REASON=""
case "$RESP" in
    *"connection reset"*|*"Connection reset"*) REASON="connection_reset" ;;
    *"timed out"*|*"Timed out"*|*"ETIMEDOUT"*|*"TIMEOUT"*) REASON="timeout" ;;
    *"MCP connection"*|*"mcp connection"*|*"server disconnected"*) REASON="mcp_disconnect" ;;
    *"ECONNREFUSED"*|*"ECONNRESET"*|*"EPIPE"*|*"Broken pipe"*) REASON="connection_error" ;;
    *"network error"*|*"Network error"*|*"fetch failed"*) REASON="network_error" ;;
    *"tool is not connected"*|*"Tool is not connected"*) REASON="tool_disconnected" ;;
    *"InputValidationError"*|*"schema"*) REASON="" ;; # not a retry case
    *"timeout"*) REASON="timeout" ;;
esac

if [ -n "$REASON" ]; then
    NOW=$(date +%s)
    INPUT_SUMMARY=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    ti = json.load(sys.stdin).get('tool_input', {})
    s = json.dumps(ti) if isinstance(ti, dict) else str(ti)
    print(s[:120])
except Exception:
    print('?')
" 2>/dev/null)

    # Dedup: skip if same tool+reason within 30s
    DUP="no"
    if [ -f "$QUEUE_FILE" ]; then
        DUP=$(TOOL="$TOOL" REASON="$REASON" NOW="$NOW" QF="$QUEUE_FILE" python3 -c "
import os, json
tool = os.environ['TOOL']; reason = os.environ['REASON']
now = int(os.environ['NOW']); qf = os.environ['QF']
try:
    with open(qf) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                e = json.loads(line)
                if e.get('tool') == tool and e.get('reason') == reason and now - e.get('ts', 0) < 30:
                    print('yes'); break
            except Exception: pass
    else:
        print('no')
except Exception:
    print('no')
" 2>/dev/null)
    fi
    if [ "$DUP" != "yes" ]; then
        TOOL="$TOOL" REASON="$REASON" NOW="$NOW" SUMMARY="$INPUT_SUMMARY" QF="$QUEUE_FILE" python3 -c "
import os, json
entry = {'tool': os.environ['TOOL'], 'input_summary': os.environ['SUMMARY'][:120],
         'reason': os.environ['REASON'], 'ts': int(os.environ['NOW'])}
with open(os.environ['QF'], 'a') as f:
    f.write(json.dumps(entry) + '\n')
" 2>/dev/null
        echo "[tool-retry] Queued $TOOL for retry (reason: $REASON). This was an interruption, not a rejection." >&2
    fi
fi

# ---------------------------------------------------------------------
# 4. long-run-cache-write: Bash.
#    When a known-long script completes, write its full response to
#    /tmp/last-<key>.log. Parses $INPUT directly to get the full
#    (multi-line) response text; $RESP is truncated by read at newline.
# ---------------------------------------------------------------------
if [ "$TOOL" = "Bash" ] && [ -n "$CMD" ]; then
    INPUT_JSON="$INPUT" HOOK_CMD="$CMD" python3 <<'PY' 2>/dev/null
import os, re, sys, json
cmd = os.environ.get("HOOK_CMD", "")
inp = os.environ.get("INPUT_JSON", "")
LONG_SCRIPTS = [
    (r'scripts/e2e_smoke\.sh', 'e2e_smoke'),
    (r'\bpytest\b', 'pytest'),
    (r'npm\s+run\s+build\b', 'npm_build'),
    (r'npx\s+playwright\s+test\b', 'playwright'),
    (r'pnpm\s+test\b', 'pnpm_test'),
    (r'pnpm\s+vitest\b', 'pnpm_vitest'),
]
matched_key = None
for pat, key in LONG_SCRIPTS:
    if re.search(pat, cmd):
        if key == 'pytest' and re.search(r'(-k\s|\S+\.py\b)', cmd):
            continue
        matched_key = key
        break
if not matched_key:
    sys.exit(0)
try:
    d = json.loads(inp, strict=False)
except Exception:
    sys.exit(0)
tr = d.get("tool_response") or {}
parts = []
if isinstance(tr, str):
    parts.append(tr)
elif isinstance(tr, dict):
    for k in ("stderr", "error", "output", "content", "message", "text"):
        v = tr.get(k)
        if isinstance(v, str) and v:
            parts.append(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    t = item.get("text") or ""
                    if t:
                        parts.append(t)
                elif isinstance(item, str) and item:
                    parts.append(item)
full_resp = "\n".join(parts)
if not full_resp:
    sys.exit(0)
cache_path = f"/tmp/last-{matched_key}.log"
try:
    with open(cache_path, 'w') as f:
        f.write(full_resp + '\n')
except Exception:
    pass
PY
fi

exit 0
