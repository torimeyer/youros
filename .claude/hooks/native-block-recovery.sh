#!/bin/bash
# PostToolUse recovery nag.
#
# Fires on Bash|Read|Edit|Write|Grep|Glob AFTER the tool ran.
# Detects when ostk-first.sh blocked a native tool (tool_response contains
# "Blocked: use mcp__ostk__"), increments a per-session block counter, and
# once the counter reaches 2 within 60s emits a stronger recovery message
# to STDERR so Claude sees it inline and stops retrying native tools.
#
# Counter file: $HOME/.myos/subagents/<session-id>-blocks.count
#   Format (one line): "<epoch_ts> <count> <nagged:0|1>"
# Log: /tmp/native-block-recovery.log (1MB rotation)

set -u

LOG=/tmp/native-block-recovery.log
DIR="$HOME/.myos/subagents"
mkdir -p "$DIR" 2>/dev/null

# Rotate at 1MB
if [ -f "$LOG" ]; then
  SIZE=$(stat -f%z "$LOG" 2>/dev/null || stat -c%s "$LOG" 2>/dev/null || echo 0)
  if [ "$SIZE" -gt 1048576 ]; then
    mv "$LOG" "${LOG}.old" 2>/dev/null
  fi
fi

trace() {
  # $1 = action, $2 = tool, $3 = count
  echo "$(date '+%Y-%m-%d %H:%M:%S') tool=${2:-?} count=${3:-?} action=$1" >> "$LOG" 2>/dev/null
}

# Session id: prefer explicit env, else hash of ppid chain, else $$.
SID="${CLAUDE_SESSION_ID:-}"
if [ -z "$SID" ]; then
  PTREE=$(ps -o ppid= -p $$ 2>/dev/null | tr -d ' ')
  SID=$(echo "${PTREE:-$$}" | md5sum 2>/dev/null | awk '{print $1}' | cut -c1-12)
  if [ -z "$SID" ]; then
    SID=$(echo "${PTREE:-$$}" | md5 2>/dev/null | cut -c1-12)
  fi
  if [ -z "$SID" ]; then
    SID="$$"
  fi
fi

CFILE="$DIR/${SID}-blocks.count"

INPUT=$(cat)

TOOL=$(echo "$INPUT" | python3 -c "import sys,json
try: print(json.load(sys.stdin).get('tool_name',''))
except Exception: print('')" 2>/dev/null)

RESP=$(echo "$INPUT" | python3 -c "import sys,json
try:
    d=json.load(sys.stdin).get('tool_response',{})
    if isinstance(d,str): print(d)
    elif isinstance(d,dict):
        parts=[]
        for k in ('stderr','error','content','output','message'):
            v=d.get(k)
            if isinstance(v,str): parts.append(v)
            elif isinstance(v,list):
                for it in v:
                    if isinstance(it,dict):
                        t=it.get('text','')
                        if t: parts.append(t)
        print('\n'.join(parts))
    else: print('')
except Exception: print('')" 2>/dev/null)

NOW=$(date +%s)

# Default state
PREV_TS=0
PREV_COUNT=0
PREV_NAGGED=0
if [ -f "$CFILE" ]; then
  read -r PREV_TS PREV_COUNT PREV_NAGGED < "$CFILE" 2>/dev/null || true
  PREV_TS=${PREV_TS:-0}
  PREV_COUNT=${PREV_COUNT:-0}
  PREV_NAGGED=${PREV_NAGGED:-0}
fi

is_block=0
case "$RESP" in
  *"Blocked: use mcp__ostk__"*) is_block=1 ;;
esac

if [ "$is_block" -eq 0 ]; then
  # Non-block response observed: reset counter so future blocks start fresh.
  if [ "$PREV_COUNT" -gt 0 ]; then
    echo "$NOW 0 0" > "$CFILE"
    trace "reset-on-clean-response" "$TOOL" "0"
  else
    trace "noop-clean" "$TOOL" "$PREV_COUNT"
  fi
  exit 0
fi

# Stale window: if last block >60s ago, start over at 1.
AGE=$(( NOW - PREV_TS ))
if [ "$AGE" -gt 60 ]; then
  COUNT=1
  NAGGED=0
else
  COUNT=$(( PREV_COUNT + 1 ))
  NAGGED=$PREV_NAGGED
fi

# Reset after >5 so we don't nag forever.
if [ "$COUNT" -gt 5 ]; then
  echo "$NOW 0 0" > "$CFILE"
  trace "reset-over-5" "$TOOL" "$COUNT"
  exit 0
fi

ACTION="incremented"
if [ "$COUNT" -ge 2 ]; then
  # Emit the nag to stderr so Claude sees it in the tool result.
  cat >&2 <<NAG
RECOVERY NAG: You have been blocked $COUNT times in a row by the ostk-first hook.
Stop retrying native Bash/Read/Edit/Write/Grep/Glob. Those tools are blocked while uvicorn is up.
Do this RIGHT NOW:
  1. Call ToolSearch('select:mcp__ostk__shell,mcp__ostk__fs_read,mcp__ostk__fs_write,mcp__ostk__edit,mcp__ostk__search')
  2. Retry your operation via mcp__ostk__* (shell for commands, fs_read/fs_write for files, edit for patches, search for grep/glob).
  3. Only fall back to native tools if pgrep shows uvicorn on :8000 is truly offline.
NAG
  if [ "$NAGGED" -eq 0 ]; then
    ACTION="nagged"
    NAGGED=1
  else
    ACTION="nagged-again"
  fi
fi

echo "$NOW $COUNT $NAGGED" > "$CFILE"
trace "$ACTION" "$TOOL" "$COUNT"
exit 0
