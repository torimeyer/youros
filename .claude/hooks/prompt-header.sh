#!/usr/bin/env bash
# Consolidated UserPromptSubmit hook.
# Replaces: standing-rules.sh, scaffold-commit-alert.sh, incremental-commit.sh,
#           keep-going-on-pending-tasks.sh, permission-deny-detector.sh

HOOK_NAME=$(basename "$0")
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
. "$LIB/load-rule.sh"
. "$LIB/log-fire.sh"
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME exit=$?" >> /tmp/hook-trace.log' EXIT

INPUT=$(cat)

# Write user-turn stamp so edit-cycle hooks can reset per-turn counters.
mkdir -p "${HOME}/.myos/hooks" 2>/dev/null || true
_SR_PREV_EPOCH=$(cat "${HOME}/.myos/hooks/last-user-turn.stamp" 2>/dev/null | tr -d '[:space:]')
date +%s > "${HOME}/.myos/hooks/last-user-turn.stamp" 2>/dev/null || true

# ---- STANDING RULES (always emitted, infra) ----
cat <<'EOF'
STANDING RULES (non-negotiable this turn):
1. ostk tools first. Bash/Read/Edit/Grep only if ostk MCP is offline. If ostk tools are deferred, reload via ToolSearch before falling through.
2. If the user says saa/diagnose/fix, spawn a subagent via Agent. No inline work, even for small items.
3. If ostk MCP drops, tell the user immediately. Reload via ToolSearch, do not silently fall back.
4. If iterating over N things is slow, ask why there are N first. Reduce N before optimizing the loop.
5. Commit verified wins incrementally. Five or more uncommitted files is a slow-down signal.
EOF

# ---- RECEIPTS CHECK (gated on standing_rules_blocks.receipts_check) ----
. "$LIB/rules/standing_rules_receipts.sh"
if python3 -c "
import json, os, sys
def load(p):
    try:
        with open(p) as f: return json.load(f)
    except: return {}
def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict): out[k] = deep_merge(out[k], v)
        else: out[k] = v
    return out
ldr = os.path.join(os.environ.get('_LOAD_RULE_DIR', '${LIB}'), 'default-rules.json')
cfg = deep_merge(load(ldr), load(os.path.expanduser('~/.myos/rules.json')))
val = cfg.get('rules', {}).get('standing_rules_blocks', {}).get('receipts_check', False)
sys.exit(0 if val is True else 1)
" 2>/dev/null; then
    _standing_rules_receipts_check
fi

# ---- ZERO-BYTE TRANSCRIPT CHECK (gated on standing_rules_blocks.zero_byte_transcript_check) ----
. "$LIB/rules/standing_rules_zero_byte.sh"
if python3 -c "
import json, os, sys
def load(p):
    try:
        with open(p) as f: return json.load(f)
    except: return {}
def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict): out[k] = deep_merge(out[k], v)
        else: out[k] = v
    return out
ldr = os.path.join(os.environ.get('_LOAD_RULE_DIR', '${LIB}'), 'default-rules.json')
cfg = deep_merge(load(ldr), load(os.path.expanduser('~/.myos/rules.json')))
val = cfg.get('rules', {}).get('standing_rules_blocks', {}).get('zero_byte_transcript_check', False)
sys.exit(0 if val is True else 1)
" 2>/dev/null; then
    _standing_rules_zero_byte_check
fi

# ---- STALL/DEATH CHECK (gated on standing_rules_blocks.stall_death_check) ----
. "$LIB/rules/standing_rules_stall_death.sh"
if python3 -c "
import json, os, sys
def load(p):
    try:
        with open(p) as f: return json.load(f)
    except: return {}
def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict): out[k] = deep_merge(out[k], v)
        else: out[k] = v
    return out
ldr = os.path.join(os.environ.get('_LOAD_RULE_DIR', '${LIB}'), 'default-rules.json')
cfg = deep_merge(load(ldr), load(os.path.expanduser('~/.myos/rules.json')))
val = cfg.get('rules', {}).get('standing_rules_blocks', {}).get('stall_death_check', False)
sys.exit(0 if val is True else 1)
" 2>/dev/null; then
    _standing_rules_stall_death_check
fi

# ---- ACTIVE HUMANFILE RULES (gated on humanfile_render.enabled) ----
. "$LIB/rules/humanfile_render.sh"
rule_enabled humanfile_render && _humanfile_render_check

# ---- ADHD DEPTH-PROBE RULE (gated on adhd_monitor_pairing.enabled) ----
if rule_enabled adhd_monitor_pairing; then
    CADENCE=$(rule_param "adhd_monitor_pairing.depth_probe_cadence_seconds" "60")
    echo ""
    echo "[ADHD DEPTH-PROBE RULE] Every ${CADENCE:-60}s when a background agent is in flight, emit a status update with at least 2 of:"
    echo "- pid alive + CPU/elapsed advance vs prior probe"
    echo "- transcript_bytes delta vs prior probe"
    echo "- worktree git status changes since prior probe"
    echo "- /api/agents row delta (status, current_step)"
    echo '"no change" Monitor ticks are not proof of life. If 2+ flatline for 70s, surface "agent stalled" and ask whether to cancel.'
fi

# ---- LIVE AGENT SNAPSHOT (infra, always) ----
BACKEND_URL="${MYOS_BACKEND_URL:-https://127.0.0.1:8000}"
STAMP_FILE="${HOME}/.myos/hooks/agent-snapshot.stamp"
AGENTS_FILE=$(mktemp -t agents-snap-XXXXXX)
trap 'rm -f "$AGENTS_FILE"' EXIT

AGENTS_JSON=$(curl --silent --insecure --tlsv1.2 --tls-max 1.2 --connect-timeout 2 -m 5 \
    "${BACKEND_URL}/api/agents" 2>/dev/null || echo "")

if [ -n "$AGENTS_JSON" ]; then
    printf '%s' "$AGENTS_JSON" > "$AGENTS_FILE"

    # Running agents snapshot
    python3 - "$AGENTS_FILE" <<'PYEOF' 2>/dev/null
import json, sys
from datetime import datetime, timezone

INCLUDE_SOURCES = {"claude-code", "task-bridge"}
path = sys.argv[1]
try:
    with open(path) as f:
        data = json.load(f)
except Exception:
    sys.exit(0)

running = []
for a in data.get("agents", []) or []:
    if a.get("source") not in INCLUDE_SOURCES:
        continue
    name = a.get("name") or ""
    if not name or name.startswith("claude-code-"):
        continue
    if a.get("status") == "running":
        spawned_at = a.get("spawned_at") or ""
        step = a.get("current_step") or ""
        tb = a.get("transcript_bytes") or 0
        running.append({"name": name, "spawned_at": spawned_at, "step": step, "tb": tb})

if running:
    now = datetime.now(timezone.utc)
    print()
    print("CURRENT RUNNING AGENTS (live from /api/agents, filter: source=claude-code, status=running, user-spawned):")
    for r in running:
        spawned_str = ""
        if r["spawned_at"]:
            try:
                dt = datetime.fromisoformat(r["spawned_at"].replace("Z", "+00:00"))
                secs = int((now - dt).total_seconds())
                spawned_str = f"{secs // 60}m ago" if secs >= 60 else f"{secs}s ago"
            except Exception:
                spawned_str = r["spawned_at"]
        kb = r["tb"] // 1024 if r["tb"] else 0
        print(f"- {r['name']} (spawned {spawned_str}, {kb}KB)")
    print("This list is authoritative. If an agent you spawned earlier is NOT listed above, it is done. Do not narrate it as still running.")
PYEOF

    # Completed-since-last-turn snapshot
    STAMP_FILE_VAL="$STAMP_FILE" python3 - "$AGENTS_FILE" <<'PYEOF2' 2>/dev/null
import json, os, sys
from datetime import datetime, timezone

MAX_ROWS = 8
TERMINAL_STATUSES = {"completed","failed","cancelled","terminated_stale","completed_timeout","stopped"}
INCLUDE_SOURCES = {"claude-code", "task-bridge"}

def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None

path = sys.argv[1]
stamp_path = os.environ.get("STAMP_FILE_VAL", "")
try:
    with open(path) as f:
        data = json.load(f)
except Exception:
    sys.exit(0)

highwater = None
try:
    with open(stamp_path) as f:
        highwater = parse_iso(f.read().strip())
except Exception:
    highwater = None

rows = []
for a in data.get("agents", []) or []:
    if a.get("source") not in INCLUDE_SOURCES:
        continue
    name = a.get("name") or ""
    if not name or name.startswith("claude-code-"):
        continue
    status = a.get("status")
    if status not in TERMINAL_STATUSES:
        continue
    completed_dt = parse_iso(a.get("completed_at"))
    if not completed_dt:
        continue
    if highwater and completed_dt <= highwater:
        continue
    rows.append({"name": name, "status": status, "completed_at": a.get("completed_at"),
                 "completed_dt": completed_dt, "summary": (a.get("summary") or "")[:140]})

if not rows:
    sys.exit(0)

rows.sort(key=lambda r: r["completed_dt"], reverse=True)
shown = rows[:MAX_ROWS]
now = datetime.now(timezone.utc)
print()
print("AGENTS THAT FINISHED SINCE YOUR LAST TURN (task-notification may have been missed):")
for r in shown:
    secs = int((now - r["completed_dt"]).total_seconds())
    age = f"{max(secs,0)}s ago" if secs < 60 else f"{secs//60}m ago"
    label = "done" if r["status"] == "completed" else ("auto-closed (agent exited without /complete)" if r["status"] in {"terminated_stale","completed_timeout"} else r["status"])
    suffix = f" - {r['summary']}" if r["summary"] else ""
    print(f"- {r['name']} [{label}] {age}{suffix}")
extra = len(rows) - len(shown)
if extra > 0:
    print(f"+{extra} more (see Agents page)")
print("These completed between your last turn and now. Check their commits / transcripts before assuming they are still running.")

newest = shown[0]["completed_dt"].isoformat()
try:
    os.makedirs(os.path.dirname(stamp_path), exist_ok=True)
    with open(stamp_path, "w") as f:
        f.write(newest)
except Exception:
    pass
PYEOF2
fi

# ---- TOOL RETRY QUEUE (infra, always) ----
RETRY_QUEUE="$HOME/.myos/subagents/retry-queue.jsonl"
if [ -f "$RETRY_QUEUE" ] && [ -s "$RETRY_QUEUE" ]; then
    NOW=$(date +%s)
    STALE_CUTOFF=$((NOW - 300))
    RETRIES=$(python3 -c "
import json, sys
entries = []
with open('$RETRY_QUEUE') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try:
            e = json.loads(line)
            if e.get('ts', 0) >= $STALE_CUTOFF:
                entries.append(e)
        except: pass
if entries:
    print('TOOL RETRY QUEUE (these were interrupted, NOT denied by the user):')
    for e in entries:
        print(f\"  - {e['tool']}: {e.get('reason','?')} ({e.get('input_summary','')[:80]})\")
    print('Retry these tool calls now. The interruption was caused by a connection issue, not user rejection.')
" 2>/dev/null)
    if [ -n "$RETRIES" ]; then
        echo ""
        echo "$RETRIES"
    fi
    rm -f "$RETRY_QUEUE" 2>/dev/null
fi

# ---- RECENT HOOK DENIES (infra, always) ----
DENY_LOG="${MYOS_DENY_LOG:-${HOME}/.claude/logs/hook-denies.log}"
if [ -f "$DENY_LOG" ] && [ -s "$DENY_LOG" ]; then
    DENY_LOG_FILE="$DENY_LOG" python3 - <<'PYEOF_DENIES' 2>/dev/null
import json, os, sys
from datetime import datetime, timezone

log_path = os.environ.get("DENY_LOG_FILE", "")
if not log_path:
    sys.exit(0)

now = datetime.now(timezone.utc)
cutoff_secs = 300
recent = []
try:
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            ts_str = entry.get("ts", "")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                continue
            if 0 <= (now - ts).total_seconds() <= cutoff_secs:
                recent.append((ts, entry))
except Exception:
    sys.exit(0)

if not recent:
    sys.exit(0)

shown = sorted(recent, key=lambda x: x[0])[-5:]
print()
print("RECENT HOOK DENIES (last 5 min):")
for ts, entry in shown:
    hms = ts.strftime("%H:%M:%S")
    hook = entry.get("hook", "?")
    tool = entry.get("tool", "?")
    if entry.get("mode") == "crash":
        last_cmd = entry.get("last_cmd", "?")
        print(f"- {hms} hook={hook} tool={tool} [CRASH] last_cmd={last_cmd}")
    else:
        reason = entry.get("reason", "?")
        print(f'- {hms} hook={hook} tool={tool} reason="{reason}"')
PYEOF_DENIES
fi

# ---- RULE-GATED PROMPT BLOCKS ----

# scaffold_commit_alert
. "$LIB/rules/scaffold_commit_alert.sh"
rule_enabled scaffold_commit_alert && _scaffold_commit_alert_check

# incremental_commit_warning
. "$LIB/rules/incremental_commit_warning.sh"
rule_enabled incremental_commit_warning && _incremental_commit_warning_check

# keep_going_pending_tasks
. "$LIB/rules/keep_going_pending_tasks.sh"
if rule_enabled keep_going_pending_tasks; then
    USER_MSG=$(INPUT_JSON="$INPUT" python3 -c "
import os, json, sys
raw = os.environ.get('INPUT_JSON', '') or '{}'
try:
    d = json.loads(raw, strict=False)
except Exception:
    sys.exit(0)
p = d.get('prompt') or ''
if isinstance(p, str):
    sys.stdout.write(p)
" 2>/dev/null)
    _keep_going_pending_tasks_check "${USER_MSG:-}"
fi

# permission_deny_detector
. "$LIB/rules/permission_deny_detector.sh"
rule_enabled permission_deny_detector && _permission_deny_detector_check

exit 0
