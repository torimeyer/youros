#!/bin/bash
# UserPromptSubmit: prepend top non-negotiable rules to every user turn,
# then append a live "currently running agents" snapshot so the parent
# session does not narrate stale state from its append-only tool-call memory.

# Write user-turn stamp so edit-cycle hooks can reset their per-turn counters.
mkdir -p "${HOME}/.myos/hooks" 2>/dev/null || true
date +%s > "${HOME}/.myos/hooks/last-user-turn.stamp" 2>/dev/null || true

cat <<'EOF'
STANDING RULES (non-negotiable this turn):
1. ostk tools first. Bash/Read/Edit/Grep only if ostk MCP is offline. If ostk tools are deferred, reload via ToolSearch before falling through.
2. If the user says saa/diagnose/fix, spawn a subagent via Agent. No inline work, even for small items.
3. If ostk MCP drops, tell the user immediately. Reload via ToolSearch, do not silently fall back.
4. If iterating over N things is slow, ask why there are N first. Reduce N before optimizing the loop.
5. Commit verified wins incrementally. Five or more uncommitted files is a slow-down signal.

RECEIPTS CHECK (run before sending any reply):
Trigger words: "done", "fixed", "landed", "committed", "passing", "shipped", "complete", "resolved", or any relay of a subagent saying the same.
If your reply uses any trigger word, it MUST include at least one of:
  - A commit hash + message from `git log` run this turn
  - Verbatim command output (pytest summary, curl response, grep hit, tsc exit line) from a tool call this turn
  - A testid / aria-label verified via `grep` this turn
  - A direct quote from a file read this turn, with path
If none of the above are in the reply, regenerate. Relaying a subagent's "tests pass" without re-running the tests yourself is banned. See feedback_receipts_for_every_done_claim.md.

ZERO-BYTE TRANSCRIPT CLAIM CHECK (run before sending any reply):
Trigger: your reply names a cause for a 0-byte transcript OR cites a memory entry to explain a 0-byte/silent subagent failure.
Before stating any cause from memory, you MUST show ALL of the following in the same reply:
  - What \{
  "loggedIn": true,
  "authMethod": "claude.ai",
  "apiProvider": "firstParty",
  "apiKeySource": "ANTHROPIC_API_KEY",
  "email": null,
  "orgId": null,
  "orgName": null,
  "subscriptionType": null
} returned THIS turn (run the command now, do not quote session-start context)
  - The value of completed_at and spawned_at for the failing agent row, from a live /api/agents call THIS turn
  - If citing a memory entry: a verbatim line from that file read THIS turn (path:line format)
Quota cap (feedback_quota_silent_fail.md) is only valid when auth status shows a Max subscription, NOT apiKeySource: ANTHROPIC_API_KEY. Missing these = regenerate. See feedback_zero_byte_transcript_diagnose_order.md for the correct ordered checklist.
EOF

# --- Curated HUMANFILE excerpt ---
# Re-inject high-signal behavioral rules from ~/.ostk/HUMANFILE every turn.
# The session-start hook loads HUMANFILE once, but by turn 20 of a long session
# those rules blur into noise and default Claude behavior creeps back. The fix:
# re-emit the curated subset on every UserPromptSubmit so the rules stay in the
# model's active decision context, not just its session-start memory.
# Skips: vault, long standing-instructions catalog, technical probes, memory-layer infra.
HUMANFILE_PATH="${MYOS_HUMANFILE_PATH:-${HOME}/.ostk/HUMANFILE}"
if [ -f "$HUMANFILE_PATH" ]; then
  cat <<'EOF'

ACTIVE HUMANFILE RULES (read every turn):

## Communication style
- Warm, direct, brief. Lowercase casual is fine. Never use her name.
- No preambles like "no vibes" or "honest read." Announcing the absence of vibes is itself a vibe. Just give the calibrated answer.
- No em-dashes. Periods, commas, or rewrite.
- Plain language for user-facing text. No finance or engineering jargon.
- Lead with the recommendation and main tradeoff. Present it as redirectable, not decided.
- ADHD plus fast feedback. Silence reads as agent died. 60s check-ins when work is in flight.

## Autonomy grants
- File writes, test runs, needle filing: act, do not ask.
- Git commits: only when explicitly requested.
- Destructive ops (rm, force push, deleting branches, dropping tables): confirm first.
- External API calls that spend money or send messages: confirm first.

## Vocabulary
- saa = spawn agent(s). Every item to its own subagent. Tests required.
- diagnose = root cause + fix + tests + commit. Not "find the bug and stop."
- fix = same as diagnose when it is code. Spawn a builder.
- elit = explain in plain language with analogies, no jargon.
- arrive / arrival = emergence. Not location, not completion.
- needle = tracked unit of work. hay = unfiled insight. set = parallel work bucket.
- shut down = ostk kernel shutdown.

## Agent rules
- saa = spawn for everything. No exceptions, even for small items.
- Scope is my call. Never ask "should I split this?"
- Tests required on every saa. Never ship without green tests.
- Do NOT include /register or /heartbeat instructions in subagent briefs. Hooks handle it.
- Verify agent status before reporting. Curl /api/agents. Absence of notification is not proof of running.

## Task state hygiene
- in_progress = actively working this turn or next. Not parked, not waiting.
- Waiting on user answer = pending, not in_progress.
- Completed = deliverable done AND verified. Completing without verifying is lying.
- Abandoned = delete.

## Slow-down signals (stop spawning when ANY fire)
- Meta-question about pacing or state ("you good?", "take a step back"). Stop. Summarize. Ask ONE question. Wait.
- Same bug recurs after a fix I claimed landed. READ the prior diff before respawning.
- About to spawn a replacement because an agent went quiet or disappeared. Run `git log --oneline -10` first. If the commits landed, it finished — no replacement needed.
- 3+ agents spawned in last 30 min. Batch or sequence the next round.
- Torios interrupts a tool call. Interrupt = reconsider scope, not retry.
- About to answer an exploratory question by spawning. Spawn only when code changes.
- Two consecutive saa on related scope. Consolidate.
- A single subagent runs past 5 min wall-clock. Summarize and check the approach.
- 3+ fix-repro-fix cycles on the same bug. Not a surface bug. New hypothesis before spawning again.

## Positioning
- ostk value = coordination at kernel level, work compounding across sessions, invisible infrastructure.
- NOT "we call Claude too." Lead with coordination demos.
- ostk should make model choice mostly irrelevant for routine work. Opus on a normal task means ostk is underperforming.
EOF
else
  echo ""
  echo "ACTIVE HUMANFILE RULES: ~/.ostk/HUMANFILE not found. Session-start rules may be stale."
fi

# Live agent snapshot. Backed by /api/agents on the myOS backend.
# Override via MYOS_BACKEND_URL for tests or alternate hosts.
BACKEND_URL="${MYOS_BACKEND_URL:-https://127.0.0.1:8000}"

# Use the compact summary-mode query so the 660KB+ full payload never
# trips the curl timeout. Server-side filter matches what the python
# renderer below expects (source=claude-code, status=running), capped
# at 20 rows so the payload stays under 5KB even on a busy fleet.
SUMMARY_PATH="/api/agents?summary=1&status=running&source=claude-code&limit=20"

# Companion query for newly-terminal agents the parent hasn't seen yet.
# Claude Code's native <task-notification> reminder fires when the Agent
# tool call returns, but for run_in_background:true that's at spawn time,
# and for agents that die/exit without calling /complete the notification
# may never fire at all. The server-side _autocomplete_exited_subagents
# sweep eventually transitions those rows to a terminal state, but the
# parent session has no way to learn about the transition without being
# told. This block surfaces terminal-status agents whose completed_at is
# more recent than the last highwater the parent acknowledged, so the
# narrative stays in sync even when the notification layer fails.
#
# Highwater file: ~/.myos/subagents/last-seen-completions.stamp.
# Contents: ISO-8601 UTC timestamp of the newest completed_at we've
# already shown. Missing/empty = show up to MAX_COMPLETED recent ones.
COMPLETED_STAMP="${HOME}/.myos/subagents/last-seen-completions.stamp"
COMPLETED_PATH="/api/agents?source=claude-code&limit=200"

# Spool curl output to a temp file so we keep binary fidelity (the payload
# can contain embedded control bytes that bash `echo "$var"` corrupts).
TMP_JSON="$(mktemp -t standing-rules-agents.XXXXXX 2>/dev/null)" || TMP_JSON="/tmp/standing-rules-agents.$$"
trap 'rm -f "$TMP_JSON"' EXIT

# 3s connect, 5s total. Safe now that summary-mode keeps the payload
# tiny; the old 2s/3s budget kept tripping on the full 660KB response
# even when the backend was healthy.
if ! curl --silent --insecure --tlsv1.2 --connect-timeout 3 -m 5 "${BACKEND_URL}${SUMMARY_PATH}" -o "$TMP_JSON" 2>/dev/null; then
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

# --- Unacknowledged completions block ---
# Fetches the full agent list (cheap enough: capped at 200 rows, summary
# mode omitted so completed_at is included), filters to source=claude-code
# user-spawned agents with terminal status AND completed_at newer than the
# highwater stamp, renders the top N, then bumps the stamp to the newest
# completed_at shown. The next user turn sees the stamp and skips those
# rows, so each completion is surfaced exactly once even if the parent
# misses the native task-notification.
TMP_COMPLETED="$(mktemp -t standing-rules-completed.XXXXXX 2>/dev/null)" || TMP_COMPLETED="/tmp/standing-rules-completed.$$"
trap 'rm -f "$TMP_JSON" "$TMP_COMPLETED"' EXIT

if curl --silent --insecure --tlsv1.2 --connect-timeout 3 -m 5 "${BACKEND_URL}${COMPLETED_PATH}" -o "$TMP_COMPLETED" 2>/dev/null && [ -s "$TMP_COMPLETED" ]; then
    AGENTS_FILE="$TMP_COMPLETED" STAMP_FILE="$COMPLETED_STAMP" python3 - <<'PYEOF2'
import json, os, sys
from datetime import datetime, timezone

MAX_ROWS = 8
# Terminal statuses worth surfacing. 'stopped' / 'terminated_stale' /
# 'completed_timeout' mean the sweeper closed the row; 'failed' /
# 'cancelled' mean something went wrong. All are states the parent
# should know about.
TERMINAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "terminated_stale",
    "completed_timeout",
    "stopped",
}
EXCLUDE_SOURCES = {"ack-bot", "heartbeat-bot", "e2e-smoke"}

def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None

path = os.environ.get("AGENTS_FILE", "")
stamp_path = os.environ.get("STAMP_FILE", "")
try:
    with open(path, "r") as f:
        data = json.load(f)
except Exception:
    sys.exit(0)

# Read highwater. Missing/unparseable means show everything recent.
highwater = None
try:
    with open(stamp_path, "r") as f:
        highwater = parse_iso(f.read().strip())
except Exception:
    highwater = None

rows = []
for a in data.get("agents", []) or []:
    if a.get("source") != "claude-code":
        continue
    if a.get("source") in EXCLUDE_SOURCES:
        continue
    name = a.get("name") or ""
    if not name or name.startswith("claude-code-"):
        continue
    status = a.get("status")
    if status not in TERMINAL_STATUSES:
        continue
    completed_at_raw = a.get("completed_at")
    completed_dt = parse_iso(completed_at_raw)
    if not completed_dt:
        continue
    if highwater and completed_dt <= highwater:
        continue
    rows.append({
        "name": name,
        "status": status,
        "completed_at": completed_at_raw,
        "completed_dt": completed_dt,
        "summary": (a.get("summary") or a.get("last_summary") or "")[:140],
    })

if not rows:
    sys.exit(0)

# Newest first so the most recent completion is most visible.
rows.sort(key=lambda r: r["completed_dt"], reverse=True)

shown = rows[:MAX_ROWS]
now = datetime.now(timezone.utc)
print()
print("AGENTS THAT FINISHED SINCE YOUR LAST TURN (task-notification may have been missed):")
for r in shown:
    secs = int((now - r["completed_dt"]).total_seconds())
    if secs < 60:
        age = f"{max(secs, 0)}s ago"
    elif secs < 3600:
        age = f"{secs // 60}m ago"
    else:
        age = f"{secs // 3600}h ago"
    label = r["status"]
    if label == "completed":
        label = "done"
    elif label in {"failed", "cancelled", "stopped"}:
        label = label
    elif label in {"terminated_stale", "completed_timeout"}:
        label = "auto-closed (agent exited without /complete)"
    suffix = f" - {r['summary']}" if r["summary"] else ""
    print(f"- {r['name']} [{label}] {age}{suffix}")
extra = len(rows) - len(shown)
if extra > 0:
    print(f"+{extra} more (see Agents page)")
print("These completed between your last turn and now. Check their commits / transcripts before assuming they are still running.")

# Bump the highwater to the newest completed_at we just showed so the
# next turn does not re-surface the same rows. Write the ISO string
# back as-is so round-trip equality works regardless of tz suffix.
newest = shown[0]["completed_dt"].isoformat()
try:
    os.makedirs(os.path.dirname(stamp_path), exist_ok=True)
    with open(stamp_path, "w") as f:
        f.write(newest)
except Exception:
    pass
PYEOF2
fi

exit 0
