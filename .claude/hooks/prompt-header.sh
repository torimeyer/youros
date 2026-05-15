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

# === CONFIG PRE-LOAD (1 python3 call replaces all rule_enabled/rule_param invocations) ===
# Reads both JSON config files once and emits shell variable assignments.
eval "$(python3 - "${_LOAD_RULE_DIR}/default-rules.json" "${HOME}/.myos/rules.json" <<'_CFGEOF' 2>/dev/null
import json, sys

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

cfg = deep_merge(load(sys.argv[1]), load(sys.argv[2]))
rules = cfg.get("rules", {})

def emit(prefix, val):
    key = prefix.replace(".", "_").replace("-", "_")
    if not all(c.isalnum() or c == "_" for c in key):
        return
    if isinstance(val, bool):
        print(f"_MYOS_RULE_{key}={'true' if val else 'false'}")
    elif isinstance(val, (int, float)):
        print(f"_MYOS_RULE_{key}={int(val)}")
    elif isinstance(val, str) and all(c.isalnum() or c in '_-./' for c in val):
        print(f"_MYOS_RULE_{key}={val}")

def flatten(d, prefix=""):
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flatten(v, full)
        elif not isinstance(v, list):
            emit(full, v)

flatten(rules)
_CFGEOF
)"

# Override rule functions with fast cached env-var lookups (zero python3 per call).
# These replace the python3-spawning versions from load-rule.sh.
myos_rule_get() {
    local path_arg="${1#rules.}" default="$2"
    local varname="_MYOS_RULE_${path_arg//[.-]/_}"
    local val="${!varname}"
    [ -n "$val" ] && echo "$val" || echo "${default}"
}
rule_enabled() {
    local varname="_MYOS_RULE_${1//[.-]/_}_enabled"
    [ "${!varname:-false}" = "true" ]
}
rule_param() {
    local key_path="$1" default="$2"
    local varname="_MYOS_RULE_${key_path//[.-]/_}"
    local val="${!varname}"
    [ -n "$val" ] && echo "$val" || echo "${default}"
}

# Override log_rule_fire with a bash-only version (eliminates 2 python3 calls per log write).
log_rule_fire() {
    local rule_key="$1" tool_name="$2" decision="$3" reason="$4"
    local log_dir log_file log_bak max_bytes size ts
    log_dir="${HOME}/.claude/logs"
    log_file="${log_dir}/rule-fires.jsonl"
    log_bak="${log_file}.1"
    max_bytes=10485760

    mkdir -p "$log_dir" 2>/dev/null || return 0

    if [ -f "$log_file" ]; then
        size=$(stat -f %z "$log_file" 2>/dev/null || echo 0)
        if [ "${size:-0}" -gt "$max_bytes" ]; then
            mv "$log_file" "$log_bak" 2>/dev/null || true
        fi
    fi

    ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    local session_id="${CLAUDE_SESSION_ID:-unknown}"

    # JSON-escape a string (handles the simple strings used in rule keys/reasons)
    _je() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; echo -n "$s"; }

    printf '{"ts":"%s","rule":"%s","tool":"%s","decision":"%s","reason":"%s","session_id":"%s","tool_args_digest":""}\n' \
        "$ts" "$(_je "$rule_key")" "$(_je "$tool_name")" \
        "$(_je "$decision")" "$(_je "$reason")" "$(_je "$session_id")" \
        >> "$log_file" 2>/dev/null || true
    return 0
}

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
[ "${_MYOS_RULE_standing_rules_blocks_receipts_check:-false}" = "true" ] && _standing_rules_receipts_check

# ---- ZERO-BYTE TRANSCRIPT CHECK (gated, compressed) ----
if [ "${_MYOS_RULE_standing_rules_blocks_zero_byte_transcript_check:-false}" = "true" ]; then
    cat <<'EOF'

ZERO-BYTE TRANSCRIPT CLAIM CHECK (run before sending any reply):
Trigger: reply names a cause for a 0-byte transcript or cites memory to explain a silent/0-byte subagent failure.
Required in the same reply: (1) auth loggedIn value from a command run THIS turn; (2) completed_at + spawned_at for the agent from a live /api/agents call THIS turn; (3) if citing memory, a verbatim line from that file read THIS turn (path:line format). Quota cap valid only on Max subscription, NOT apiKeySource: ANTHROPIC_API_KEY. Any missing = regenerate. See feedback_zero_byte_transcript_diagnose_order.md.
EOF
    log_rule_fire "standing_rules_blocks" "UserPromptSubmit" "allow" "zero_byte_transcript_check block emitted"
fi

# ---- STALL/DEATH CHECK (gated, compressed) ----
if [ "${_MYOS_RULE_standing_rules_blocks_stall_death_check:-false}" = "true" ]; then
    cat <<'EOF'

STALL/DEATH ASSERTION CHECK (run before sending any reply):
Trigger: reply uses "stalled", "died", "dead", "dropped", "abandoned", "no progress", or "agent went quiet" alongside an agent name.
Required: evidence from at least 3 of 5: ps -p <pid> (alive/dead), git log --all main..<branch> --oneline (any commit = not dead), git status of worktree (dirty = active), transcript tail via /api/agents/<name>/transcript (recent bytes = writing), /api/agents row from two reads. Flat bytes/step are hypotheses, not conclusions. Fewer than 3 = regenerate. See feedback_false_alarm_meta_pattern.md.
EOF
    log_rule_fire "standing_rules_blocks" "UserPromptSubmit" "allow" "stall_death_check block emitted"
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

# ---- LIVE AGENT SNAPSHOT + RETRY QUEUE + DENY LOG (single python3) ----
BACKEND_URL="${MYOS_BACKEND_URL:-https://127.0.0.1:8000}"
STAMP_FILE="${HOME}/.myos/hooks/agent-snapshot.stamp"

_USER_MSG_FILE=$(mktemp -t user-msg-XXXXXX)
trap 'rm -f "$_USER_MSG_FILE"' EXIT

AGENTS_JSON=$(curl --silent --insecure --tlsv1.2 --tls-max 1.2 --connect-timeout 1 -m 2 \
    "${BACKEND_URL}/api/agents" 2>/dev/null || echo "")

if [ -n "$AGENTS_JSON" ]; then
    _AGENTS_FILE=$(mktemp -t agents-snap-XXXXXX)
    trap 'rm -f "$_AGENTS_FILE"' EXIT
    printf '%s' "$AGENTS_JSON" > "$_AGENTS_FILE"
else
    _AGENTS_FILE=""
fi

_NOW_EPOCH=$(date +%s)
_STALE_CUTOFF=$((_NOW_EPOCH - 300))

STAMP_FILE_VAL="$STAMP_FILE" \
DENY_LOG_FILE="${MYOS_DENY_LOG:-${HOME}/.claude/logs/hook-denies.log}" \
RETRY_QUEUE_FILE="${HOME}/.myos/subagents/retry-queue.jsonl" \
STALE_CUTOFF="$_STALE_CUTOFF" \
INPUT_DATA="$INPUT" \
USER_MSG_FILE="$_USER_MSG_FILE" \
python3 - "${_AGENTS_FILE:-}" <<'MEGAPY' 2>/dev/null
import json, os, sys
from datetime import datetime, timezone

INCLUDE_SOURCES = {"claude-code", "task-bridge"}
MAX_ROWS = 8
TERMINAL_STATUSES = {"completed","failed","cancelled","terminated_stale","completed_timeout","stopped"}

agents_file     = sys.argv[1] if len(sys.argv) > 1 else ""
stamp_path      = os.environ.get("STAMP_FILE_VAL", "")
deny_log_file   = os.environ.get("DENY_LOG_FILE", "")
retry_queue_file= os.environ.get("RETRY_QUEUE_FILE", "")
stale_cutoff    = int(os.environ.get("STALE_CUTOFF", "0"))
input_data      = os.environ.get("INPUT_DATA", "")
user_msg_file   = os.environ.get("USER_MSG_FILE", "")

def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None

now = datetime.now(timezone.utc)

# Load agents data
data = {}
if agents_file:
    try:
        with open(agents_file) as f:
            data = json.load(f)
    except Exception:
        pass

agents = data.get("agents", []) or []

# Running agents snapshot
running = []
for a in agents:
    if a.get("source") not in INCLUDE_SOURCES:
        continue
    name = a.get("name") or ""
    if not name or name.startswith("claude-code-"):
        continue
    if a.get("status") == "running":
        running.append({"name": name})

if running:
    print()
    print("CURRENT RUNNING AGENTS (live from /api/agents, filter: source=claude-code, status=running, user-spawned):")
    for r in running:
        print(f"- {r['name']}")
    print("This list is authoritative. If an agent you spawned earlier is NOT listed above, it is done. Do not narrate it as still running.")

# Completed agents snapshot
highwater = None
try:
    with open(stamp_path) as f:
        highwater = parse_iso(f.read().strip())
except Exception:
    pass

rows = []
for a in agents:
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

if rows:
    rows.sort(key=lambda r: r["completed_dt"], reverse=True)
    shown = rows[:MAX_ROWS]
    print()
    print("AGENTS THAT FINISHED SINCE YOUR LAST TURN (task-notification may have been missed):")
    for r in shown:
        # Use fixed completed_at timestamp (volatile "Xm ago" breaks cache prefix)
        age = r["completed_at"][:16] if r["completed_at"] else ""
        if r["status"] == "completed":
            label = "done"
        elif r["status"] in {"terminated_stale", "completed_timeout"}:
            label = "auto-closed (agent exited without /complete)"
        else:
            label = r["status"]
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

# Tool retry queue
if retry_queue_file and os.path.isfile(retry_queue_file) and os.path.getsize(retry_queue_file) > 0:
    entries = []
    try:
        with open(retry_queue_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if e.get("ts", 0) >= stale_cutoff:
                        entries.append(e)
                except Exception:
                    pass
    except Exception:
        pass
    if entries:
        print()
        print("TOOL RETRY QUEUE (these were interrupted, NOT denied by the user):")
        for e in entries:
            print(f"  - {e['tool']}: {e.get('reason','?')} ({e.get('input_summary','')[:80]})")
        print("Retry these tool calls now. The interruption was caused by a connection issue, not user rejection.")
    try:
        os.remove(retry_queue_file)
    except Exception:
        pass

# Recent hook denies
if deny_log_file and os.path.isfile(deny_log_file) and os.path.getsize(deny_log_file) > 0:
    cutoff_secs = 300
    recent = []
    try:
        with open(deny_log_file) as f:
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
        pass
    if recent:
        shown_d = sorted(recent, key=lambda x: x[0])[-5:]
        print()
        print("RECENT HOOK DENIES (last 5 min):")
        for ts, entry in shown_d:
            hms = ts.strftime("%H:%M:%S")
            hook = entry.get("hook", "?")
            tool = entry.get("tool", "?")
            if entry.get("mode") == "crash":
                last_cmd = entry.get("last_cmd", "?")
                print(f"- {hms} hook={hook} tool={tool} [CRASH] last_cmd={last_cmd}")
            else:
                reason = entry.get("reason", "?")
                print(f'- {hms} hook={hook} tool={tool} reason="{reason}"')

# Extract user message for keep_going_pending_tasks check
if user_msg_file and input_data:
    try:
        d = json.loads(input_data, strict=False)
        p = d.get("prompt") or ""
        if isinstance(p, str) and p:
            with open(user_msg_file, "w") as f:
                f.write(p)
    except Exception:
        pass
MEGAPY

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
    USER_MSG=$(cat "$_USER_MSG_FILE" 2>/dev/null || echo "")
    _keep_going_pending_tasks_check "${USER_MSG:-}"
fi

# permission_deny_detector — redefine AFTER source to use a single find call.
# The original nested loop spawns one find subprocess per project dir (274+), which is slow.
# A single find -newer avoids all per-dir subprocess spawns and per-file stat calls.
. "$LIB/rules/permission_deny_detector.sh"
_permission_deny_detector_check() {
  local lookback
  lookback=$(rule_param "permission_deny_detector.lookback_seconds" "60")

  local CANNED="user doesn't want to proceed with this tool use"
  local DENY_LOG_LOCAL="${MYOS_DENY_LOG:-${HOME}/.claude/logs/hook-denies.log}"
  local NOW_D
  NOW_D=$(date +%s)
  local CUTOFF=$(( NOW_D - ${lookback:-60} ))
  local EMITTED=0

  # Create a reference file with mtime=CUTOFF for -newer filtering (single find, no per-file stat)
  local _REF
  _REF=$(mktemp -t deny-ref-XXXXXX)
  trap 'rm -f "$_REF"' RETURN
  touch -t "$(date -j -f "%s" "$CUTOFF" "+%Y%m%d%H%M.%S" 2>/dev/null || date '+%Y%m%d%H%M.%S')" "$_REF" 2>/dev/null || true

  while IFS= read -r -d '' FILE; do
    [ "$EMITTED" -eq 1 ] && break
    grep -qF "$CANNED" "$FILE" 2>/dev/null || continue

    if [ -f "$DENY_LOG_LOCAL" ] && [ -s "$DENY_LOG_LOCAL" ]; then
      local FILE_MTIME
      FILE_MTIME=$(stat -f %m "$FILE" 2>/dev/null || echo 0)
      local MATCH
      MATCH=$(DENY_LOG="$DENY_LOG_LOCAL" FILE_MTIME="$FILE_MTIME" python3 <<'PY' 2>/dev/null
import os, json
from datetime import datetime
file_mtime = int(os.environ["FILE_MTIME"])
deny_log = os.environ["DENY_LOG"]
found = False
try:
    with open(deny_log) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line, strict=False)
            except Exception:
                continue
            ts_raw = entry.get("ts", "")
            try:
                dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                ts = int(dt.timestamp())
            except Exception:
                continue
            if abs(ts - file_mtime) <= 2:
                found = True
                break
except Exception:
    pass
print("yes" if found else "no")
PY
      )
      [ "$MATCH" = "yes" ] && continue
    fi

    EMITTED=1
    printf 'SETTINGS-LEVEL DENY DETECTED:\n'
    printf 'A tool was denied by a permissions rule in settings.json, not a hook.\n'
    printf 'Check ~/.claude/settings.json and .claude/settings.local.json permissions.deny.\n'
    printf 'Run: jq '"'"'.permissions.deny // []'"'"' ~/.claude/settings.json .claude/settings.json .claude/settings.local.json\n'
    break
  done < <(find "${HOME}/.claude/projects" -maxdepth 4 -path '*/tool-results/*.txt' -newer "$_REF" -print0 2>/dev/null)

  log_rule_fire "permission_deny_detector" "UserPromptSubmit" "allow" "checked tool results"
  return 0
}
rule_enabled permission_deny_detector && _permission_deny_detector_check

exit 0
