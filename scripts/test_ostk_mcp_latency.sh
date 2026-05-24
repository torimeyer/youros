#!/usr/bin/env bash
#
# test_ostk_mcp_latency.sh
#
# Measures round-trip latency for the ostk MCP server over stdio.
# Claude Code spawns `ostk kernel serve` and talks JSON-RPC. Every
# tool call the assistant makes goes through this pipe, so any
# latency here is paid on every single tool call.
#
# This test spawns the kernel, sends 10 trivial shell echo requests,
# and asserts p95 under the threshold. It also times the PreToolUse
# heartbeat hook in isolation to catch regressions in the per-tool
# hook budget (the hook was historically synchronous and could stall
# tool calls for up to 4 seconds when the backend was unreachable).
#
# Usage:
#   scripts/test_ostk_mcp_latency.sh [p95_threshold_ms] [hook_threshold_ms]
#
# Defaults: p95 400 ms for MCP round trip, 100 ms for the hook.

set -u

P95_MS="${1:-400}"
HOOK_MS="${2:-100}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v ostk >/dev/null 2>&1; then
    echo "test skipped: ostk binary not on PATH" >&2
    exit 0
fi

# 1. MCP stdio latency.
PROBE_OUT=$(python3 - <<'PY'
import subprocess, json, time, os, sys

proc = subprocess.Popen(
    ["ostk", "kernel", "serve"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    bufsize=0,
)

def send(req):
    proc.stdin.write((json.dumps(req) + "\n").encode())
    proc.stdin.flush()

def recv():
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("kernel closed stdout before replying")
    return json.loads(line.decode())

try:
    send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
        "protocolVersion":"2025-03-26","capabilities":{},
        "clientInfo":{"name":"latency-probe","version":"0"}}})
    recv()
    send({"jsonrpc":"2.0","method":"notifications/initialized"})

    times = []
    for i in range(10):
        t0 = time.time()
        send({"jsonrpc":"2.0","id":100+i,"method":"tools/call",
              "params":{"name":"shell","arguments":{"cmd":"echo hi"}}})
        recv()
        times.append((time.time() - t0) * 1000)
finally:
    try: proc.stdin.close()
    except Exception: pass
    proc.terminate()

times.sort()
p50 = times[len(times)//2]
p95 = times[int(len(times)*0.95)] if len(times) >= 20 else times[-1]
print(f"{p50:.0f} {p95:.0f} {sum(times)/len(times):.0f}")
PY
) || { echo "MCP probe failed" >&2; exit 1; }

MCP_P50=$(echo "$PROBE_OUT" | awk '{print $1}')
MCP_P95=$(echo "$PROBE_OUT" | awk '{print $2}')
MCP_MEAN=$(echo "$PROBE_OUT" | awk '{print $3}')

printf 'ostk MCP shell echo: p50=%sms p95=%sms mean=%sms (threshold p95 %sms)\n' \
    "$MCP_P50" "$MCP_P95" "$MCP_MEAN" "$P95_MS"

if [ "$MCP_P95" -gt "$P95_MS" ]; then
    echo "FAIL: MCP p95 over budget" >&2
    exit 2
fi

# 2. Heartbeat hook in isolation. The hook fires PreToolUse on every
#    tool and must never block the caller. We measure under two
#    conditions: backend reachable (fast path) and backend simulated
#    unreachable (slow path). Both must stay under the hook threshold.
#    Wave 2 of →1288 folded heartbeat-agent.sh into heartbeat-and-drain.sh.
HOOK="$ROOT/.claude/hooks/heartbeat-and-drain.sh"
if [ ! -x "$HOOK" ]; then
    echo "test skipped: $HOOK is not executable" >&2
    exit 0
fi

PAYLOAD='{"tool_name":"mcp__ostk__shell","session_id":"latency-test","tool_input":{}}'

run_hook_timing() {
    local script="$1"
    local -a samples=()
    for _ in 1 2 3 4 5; do
        local ns_start ns_end
        ns_start=$(python3 -c 'import time; print(time.monotonic_ns())')
        echo "$PAYLOAD" | bash "$script" >/dev/null 2>&1 || true
        ns_end=$(python3 -c 'import time; print(time.monotonic_ns())')
        samples+=( $(( (ns_end - ns_start) / 1000000 )) )
    done
    # max
    printf '%s\n' "${samples[@]}" | sort -n | tail -1
}

HOOK_MAX=$(run_hook_timing "$HOOK")
printf 'heartbeat hook (backend live): max=%sms (threshold %sms)\n' "$HOOK_MAX" "$HOOK_MS"
if [ "$HOOK_MAX" -gt "$HOOK_MS" ]; then
    echo "FAIL: heartbeat hook exceeded budget on live backend" >&2
    exit 3
fi

# Simulate an unreachable backend by patching the URL to a non-routable
# address. A non-async hook would block here for the curl timeout.
PATCHED=$(mktemp)
trap 'rm -f "$PATCHED"' EXIT
sed 's|localhost:8000|10.255.255.1:8000|g' "$HOOK" > "$PATCHED"
chmod +x "$PATCHED"

HOOK_MAX_DEAD=$(run_hook_timing "$PATCHED")
printf 'heartbeat hook (backend unreachable): max=%sms (threshold %sms)\n' "$HOOK_MAX_DEAD" "$HOOK_MS"
if [ "$HOOK_MAX_DEAD" -gt "$HOOK_MS" ]; then
    echo "FAIL: heartbeat hook blocks when backend is unreachable. The curl must be detached so tool calls never stall." >&2
    exit 4
fi

echo "ostk MCP latency and heartbeat hook within budget"
exit 0
