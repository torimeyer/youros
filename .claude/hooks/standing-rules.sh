#!/bin/bash
# UserPromptSubmit: prepend top non-negotiable rules to every user turn,
# then append a live "currently running agents" snapshot so the parent
# session does not narrate stale state from its append-only tool-call memory.

cat <<'EOF'
STANDING RULES (non-negotiable this turn):
1. ostk tools first. Bash/Read/Edit/Grep only if ostk MCP is offline. If ostk tools are deferred, reload via ToolSearch before falling through.
2. If the user says saa/diagnose/fix, spawn a subagent via Agent. No inline work, even for small items.
3. If ostk MCP drops, tell the user immediately. Reload via ToolSearch, do not silently fall back.
EOF

# Live agent snapshot. Backed by /api/agents on the myOS backend.
# Override via MYOS_BACKEND_URL for tests or alternate hosts.
BACKEND_URL="${MYOS_BACKEND_URL:-https://127.0.0.1:8000}"

# Use the compact summary-mode query so the 660KB+ full payload never
# trips the curl timeout. Server-side filter matches what the python
# renderer below expects (source=claude-code, status=running), capped
# at 20 rows so the payload stays under 5KB even on a busy fleet.
SUMMARY_PATH="/api/agents?summary=1&status=running&source=claude-code&limit=20"

# Spool curl output to a temp file so we keep binary fidelity (the payload
# can contain embedded control bytes that bash `echo "$var"` corrupts).
TMP_JSON="$(mktemp -t standing-rules-agents.XXXXXX 2>/dev/null)" || TMP_JSON="/tmp/standing-rules-agents.$$"
trap 'rm -f "$TMP_JSON"' EXIT

# 3s connect, 5s total. Safe now that summary-mode keeps the payload
# tiny; the old 2s/3s budget kept tripping on the full 660KB response
# even when the backend was healthy.
if ! curl -sSk --connect-timeout 3 -m 5 "${BACKEND_URL}${SUMMARY_PATH}" -o "$TMP_JSON" 2>/dev/null; then
  cat <<'EOF'

CURRENT RUNNING AGENTS: couldn't reach myOS backend to confirm current agents. Your in-memory list of running agents may be stale. Verify before reporting status.
EOF
  exit 0
fi

# Empty file is also a failure (connection refused / HTTP error body suppressed).
if [ ! -s "$TMP_JSON" ]; then
  cat <<'EOF'

CURRENT RUNNING AGENTS: couldn't reach myOS backend to confirm current agents. Your in-memory list of running agents may be stale. Verify before reporting status.
EOF
  exit 0
fi

# Render the snapshot. Filter: status=running, source=claude-code, name does
# NOT start with 'claude-code-' (that prefix marks main-session rows) and
# excluded sources (ack-bot, heartbeat-bot, e2e-smoke). Cap at 8 rows.
AGENTS_FILE="$TMP_JSON" python3 - <<'PYEOF'
import json, os, sys
from datetime import datetime, timezone

EXCLUDE_SOURCES = {"ack-bot", "heartbeat-bot", "e2e-smoke"}
MAX_ROWS = 8

def human_age(iso_ts):
    if not iso_ts:
        return "age ?"
    try:
        t = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except Exception:
        return "age ?"
    now = datetime.now(timezone.utc)
    secs = int((now - t).total_seconds())
    if secs < 0:
        secs = 0
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h ago"

def human_bytes(n):
    try:
        n = int(n or 0)
    except Exception:
        return "0B"
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n // 1024}KB"
    return f"{n // (1024*1024)}MB"

path = os.environ.get("AGENTS_FILE", "")
try:
    with open(path, "r") as f:
        data = json.load(f)
except Exception:
    print()
    print("CURRENT RUNNING AGENTS: myOS backend returned an unexpected response. Your in-memory list may be stale.")
    sys.exit(0)

rows = []
for a in data.get("agents", []) or []:
    if a.get("status") != "running":
        continue
    src = a.get("source")
    if src != "claude-code":
        continue
    if src in EXCLUDE_SOURCES:
        continue
    name = a.get("name") or ""
    if not name or name.startswith("claude-code-"):
        # Main-session rows, not user-spawned subagents.
        continue
    rows.append({
        "name": name,
        "spawned_at": a.get("spawned_at") or a.get("timestamp"),
        "bytes": a.get("transcript_bytes", 0),
    })

# Sort oldest first so long-runners surface at the top.
rows.sort(key=lambda r: r["spawned_at"] or "")

print()
if not rows:
    print("CURRENT RUNNING AGENTS: none. If you still think a subagent is working, your in-memory picture is stale.")
    sys.exit(0)

print("CURRENT RUNNING AGENTS (live from /api/agents, filter: source=claude-code, status=running, user-spawned):")
shown = rows[:MAX_ROWS]
for r in shown:
    print(f"- {r['name']} (spawned {human_age(r['spawned_at'])}, {human_bytes(r['bytes'])})")
extra = len(rows) - len(shown)
if extra > 0:
    print(f"+{extra} more (see Agents page)")
print("This list is authoritative. If an agent you spawned earlier is NOT listed above, it is done. Do not narrate it as still running.")
PYEOF

exit 0
