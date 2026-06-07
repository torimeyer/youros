#!/bin/bash
# Shared drain for the pending-register and pending-complete queues.
#
# Context: .claude/hooks/register-agent.sh parks subagent register
# bodies in $HOME/.youros/subagents/pending-register.jsonl when the
# backend is down or MCP is flapping. complete-agent.sh does the
# same with /complete calls in $HOME/.youros/subagents/pending-complete.jsonl
# so a transient outage does not leave a subagent row stuck at
# "running" forever. session-start.sh replays them on the next
# Claude Code restart, but the user rarely restarts, so entries can
# sit for hours. This lib drains both queues from the heartbeat
# hook and from successful registers, so a restart is never required.
#
# Usage:
#   source "$HOOKS_DIR/lib/drain-pending.sh"
#   myos_drain_pending          # drain register queue (honors throttle)
#   myos_drain_pending_complete # drain complete queue (honors throttle)
#
# Env overrides (for tests and manual runs):
#   MYOS_DRAIN_FORCE=1         bypass the throttle stamp
#   MYOS_DRAIN_INTERVAL=<sec>  minimum seconds between drains (default 60, floor 30)
#   MYOS_REGISTER_URL=<url>    register endpoint
#   MYOS_COMPLETE_URL_BASE=<url> base for /agents/{name}/complete (default https://127.0.0.1:8000/api/agents)
#   MYOS_DRAIN_QUEUE=<path>    register queue file
#   MYOS_DRAIN_COMPLETE_QUEUE=<path> complete queue file
#   MYOS_DRAIN_STAMP=<path>    last-run stamp file (register)
#   MYOS_DRAIN_COMPLETE_STAMP=<path> last-run stamp file (complete)
#   MYOS_DRAIN_BUDGET=<sec>    wall-clock budget per drain (default 2)

myos_drain_pending() {
    local queue stamp url interval force budget now last_run elapsed
    queue="${MYOS_DRAIN_QUEUE:-$HOME/.youros/subagents/pending-register.jsonl}"
    stamp="${MYOS_DRAIN_STAMP:-$HOME/.youros/subagents/last-drain.stamp}"
    url="${MYOS_REGISTER_URL:-https://127.0.0.1:8000/api/agents/register}"
    interval="${MYOS_DRAIN_INTERVAL:-60}"
    force="${MYOS_DRAIN_FORCE:-0}"
    budget="${MYOS_DRAIN_BUDGET:-2}"

    # Floor interval at 30s, reject non-numeric.
    case "$interval" in
        ''|*[!0-9]*) interval=60 ;;
    esac
    if [ "$interval" -lt 30 ]; then
        interval=30
    fi

    # Nothing to do if the queue is missing or empty.
    if [ ! -s "$queue" ]; then
        return 0
    fi

    # Throttle check.
    if [ "$force" != "1" ] && [ -f "$stamp" ]; then
        now=$(date +%s)
        last_run=$(cat "$stamp" 2>/dev/null)
        case "$last_run" in
            ''|*[!0-9]*) last_run=0 ;;
        esac
        elapsed=$((now - last_run))
        if [ "$elapsed" -lt "$interval" ]; then
            return 0
        fi
    fi

    mkdir -p "$(dirname "$stamp")" 2>/dev/null || true
    date +%s > "$stamp" 2>/dev/null || true

    local tmp deadline posted dropped kept line
    tmp="${queue}.tmp.$$"
    : > "$tmp" 2>/dev/null || return 0
    deadline=$(( $(date +%s) + budget ))
    posted=0
    dropped=0
    kept=0

    while IFS= read -r line; do
        if [ -z "$line" ]; then
            continue
        fi

        # Bail if we blew the budget. Preserve the rest of the queue.
        if [ "$(date +%s)" -ge "$deadline" ]; then
            printf '%s\n' "$line" >> "$tmp"
            kept=$((kept + 1))
            continue
        fi

        # Validate JSON. Malformed lines are dropped with a log line.
        if ! printf '%s' "$line" | python3 -c 'import sys, json; json.loads(sys.stdin.read())' >/dev/null 2>&1; then
            printf '[drain-pending] dropped malformed line\n' >&2
            dropped=$((dropped + 1))
            continue
        fi

        if curl -sSk --connect-timeout 2 -m 4 \
                -X POST "$url" \
                -H 'Content-Type: application/json' \
                -d "$line" > /dev/null 2>&1; then
            posted=$((posted + 1))
        else
            printf '%s\n' "$line" >> "$tmp"
            kept=$((kept + 1))
        fi
    done < "$queue"

    # Atomic swap.
    if [ -s "$tmp" ]; then
        mv "$tmp" "$queue" 2>/dev/null || rm -f "$tmp" 2>/dev/null
    else
        rm -f "$tmp" 2>/dev/null
        rm -f "$queue" 2>/dev/null
    fi

    if [ "$posted" -gt 0 ] || [ "$dropped" -gt 0 ]; then
        printf '[drain-pending] posted=%d dropped=%d kept=%d\n' "$posted" "$dropped" "$kept" >&2
    fi

    return 0
}

# Drain the pending-complete queue. Each line is {"name": "<agent>",
# "body": "<json string>"} produced by complete-agent.sh when its
# /complete POST failed after 3 retries. This function replays each
# line against POST /api/agents/<name>/complete. Lines that 2xx or
# receive any HTTP response are dropped (the server is idempotent so
# "already completed" is a success); lines that fail at the transport
# layer stay in the queue for the next drain.
myos_drain_pending_complete() {
    local queue stamp base interval force budget now last_run elapsed
    queue="${MYOS_DRAIN_COMPLETE_QUEUE:-$HOME/.youros/subagents/pending-complete.jsonl}"
    stamp="${MYOS_DRAIN_COMPLETE_STAMP:-$HOME/.youros/subagents/last-drain-complete.stamp}"
    base="${MYOS_COMPLETE_URL_BASE:-https://127.0.0.1:8000/api/agents}"
    interval="${MYOS_DRAIN_INTERVAL:-60}"
    force="${MYOS_DRAIN_FORCE:-0}"
    budget="${MYOS_DRAIN_BUDGET:-2}"

    case "$interval" in
        ''|*[!0-9]*) interval=60 ;;
    esac
    if [ "$interval" -lt 30 ]; then
        interval=30
    fi

    if [ ! -s "$queue" ]; then
        return 0
    fi

    if [ "$force" != "1" ] && [ -f "$stamp" ]; then
        now=$(date +%s)
        last_run=$(cat "$stamp" 2>/dev/null)
        case "$last_run" in
            ''|*[!0-9]*) last_run=0 ;;
        esac
        elapsed=$((now - last_run))
        if [ "$elapsed" -lt "$interval" ]; then
            return 0
        fi
    fi

    mkdir -p "$(dirname "$stamp")" 2>/dev/null || true
    date +%s > "$stamp" 2>/dev/null || true

    local tmp deadline posted dropped kept line name body url
    tmp="${queue}.tmp.$$"
    : > "$tmp" 2>/dev/null || return 0
    deadline=$(( $(date +%s) + budget ))
    posted=0
    dropped=0
    kept=0

    while IFS= read -r line; do
        if [ -z "$line" ]; then
            continue
        fi

        if [ "$(date +%s)" -ge "$deadline" ]; then
            printf '%s\n' "$line" >> "$tmp"
            kept=$((kept + 1))
            continue
        fi

        # Parse the queue line. Drop malformed entries: they cannot
        # be replayed and we do not want them to clog the queue.
        parsed=$(printf '%s' "$line" | python3 -c '
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get("name", ""))
    print(d.get("body", ""))
except Exception:
    pass
' 2>/dev/null)
        name=$(printf '%s\n' "$parsed" | sed -n '1p')
        body=$(printf '%s\n' "$parsed" | sed -n '2p')

        if [ -z "$name" ] || [ -z "$body" ]; then
            printf '[drain-pending-complete] dropped malformed line\n' >&2
            dropped=$((dropped + 1))
            continue
        fi

        url="$base/$name/complete"
        if curl -sSk --connect-timeout 2 -m 4 \
                -X POST "$url" \
                -H 'Content-Type: application/json' \
                -d "$body" > /dev/null 2>&1; then
            posted=$((posted + 1))
        else
            printf '%s\n' "$line" >> "$tmp"
            kept=$((kept + 1))
        fi
    done < "$queue"

    if [ -s "$tmp" ]; then
        mv "$tmp" "$queue" 2>/dev/null || rm -f "$tmp" 2>/dev/null
    else
        rm -f "$tmp" 2>/dev/null
        rm -f "$queue" 2>/dev/null
    fi

    if [ "$posted" -gt 0 ] || [ "$dropped" -gt 0 ]; then
        printf '[drain-pending-complete] posted=%d dropped=%d kept=%d\n' "$posted" "$dropped" "$kept" >&2
    fi

    return 0
}
