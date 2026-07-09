#!/usr/bin/env bash
# Rule: isolation_bridge
# Replaces: task-isolation-bridge.sh (PreToolUse:Agent/Task)
# Called from: pre-agent-guard.sh
# Assumes: load-rule.sh, log-fire.sh, deny.sh already sourced by caller.
# Reads HOOK_INPUT env var for full payload.
# Args: none (uses HOOK_INPUT, HOOK_TOOL, HOOK_PROMPT, HOOK_DESCRIPTION, HOOK_SUBAGENT, HOOK_ISOLATION, HOOK_MODEL env vars set by caller)

_isolation_bridge_check() {
  local tool="${HOOK_TOOL:-Agent}"

  if [ "${TASK_ISOLATION_BRIDGE_DISABLE:-}" = "1" ]; then
    log_rule_fire "isolation_bridge" "$tool" "allow" "TASK_ISOLATION_BRIDGE_DISABLE=1"
    return 0
  fi

  # Parse from HOOK_INPUT
  local PARSED
  PARSED=$(INPUT_JSON="${HOOK_INPUT:-}" python3 <<'PY' 2>/dev/null
import os, sys, json
raw = os.environ.get("INPUT_JSON", "")
try:
    d = json.loads(raw or "{}")
except Exception:
    sys.exit(0)
tool = (d.get("tool_name") or "").strip()
ti = d.get("tool_input", {}) or {}
prompt = (ti.get("prompt") or "").strip()
desc = (ti.get("description") or "").strip()
subagent = (ti.get("subagent_type") or "").strip()
iso = ti.get("isolation")
if iso is None:
    iso = d.get("isolation")
iso = (iso or "").strip().lower() if isinstance(iso, str) else ""
model = (ti.get("model") or "").strip()
session_id = (d.get("session_id") or "").strip()
tool_use_id = (d.get("tool_use_id") or "").strip()

def flat(s):
    return " ".join((s or "").split())

US = "\x1f"
sys.stdout.write(
    f"{flat(tool)}{US}{flat(prompt)}{US}{flat(desc)}{US}{flat(subagent)}{US}{flat(iso)}{US}{flat(model)}{US}{flat(session_id)}{US}{flat(tool_use_id)}"
)
PY
  )

  if [ -z "$PARSED" ]; then
    log_rule_fire "isolation_bridge" "$tool" "allow" "parse failed, allowing native Task"
    return 0
  fi

  local TOOL PROMPT DESCRIPTION SUBAGENT ISOLATION MODEL ORIG_SESSION_ID ORIG_MSG_ID
  IFS=$'\x1f' read -r TOOL PROMPT DESCRIPTION SUBAGENT ISOLATION MODEL ORIG_SESSION_ID ORIG_MSG_ID <<<"$PARSED"

  case "$TOOL" in
    Task|Agent) : ;;
    *) log_rule_fire "isolation_bridge" "$tool" "allow" "not Task/Agent"; return 0 ;;
  esac

  # Explicit opt-out
  if [ "$ISOLATION" = "none" ]; then
    log_rule_fire "isolation_bridge" "$tool" "allow" "isolation:none explicit"
    return 0
  fi

  # Read-only agent types
  case "$SUBAGENT" in
    Explore|Plan|claude-code-guide)
      log_rule_fire "isolation_bridge" "$tool" "allow" "read-only subagent type"
      return 0
      ;;
  esac

  # Resolve API base
  local API_BASE="${TORIOS_API_BASE:-}"
  if [ -z "$API_BASE" ] && [ -f "$HOME/.youros/config.json" ]; then
    API_BASE=$(python3 -c "
import json, os
try:
    d = json.load(open(os.path.expanduser('~/.youros/config.json')))
    v = d.get('api_base')
    if isinstance(v, str) and v.strip():
        print(v.strip())
except Exception:
    pass
" 2>/dev/null)
  fi
  if [ -z "$API_BASE" ]; then
    API_BASE="https://127.0.0.1:8000"
  fi

  # Edit-verb detection
  local HAY
  HAY=$(printf '%s\n%s\n' "$PROMPT" "$DESCRIPTION" | tr '[:upper:]' '[:lower:]')
  local VERB_RE='(^|[^a-z0-9_])(edit|write|fix|commit|saa|diagnose|build|add|refactor|rename|create|delete|update|modify|replace|remove)([^a-z0-9_]|$)'
  local HAY_CHECK
  HAY_CHECK=$(printf '%s' "$HAY" | python3 -c "
import sys, re
t = sys.stdin.read()
t = re.sub(r'\\b(?:do\\s+not|don.t|never|no)\\s+\\w+', ' ', t, flags=re.IGNORECASE)
sys.stdout.write(t)
" 2>/dev/null || printf '%s' "$HAY")

  if ! printf '%s' "$HAY_CHECK" | grep -qE "$VERB_RE"; then
    log_rule_fire "isolation_bridge" "$tool" "allow" "no edit verb detected (read-only)"
    return 0
  fi

  # Locks: header handling
  local LOCKS_STATUS
  LOCKS_STATUS=$(PROMPT="$PROMPT" DESCRIPTION="$DESCRIPTION" python3 -c "
import os, re
hay = os.environ.get('PROMPT','') + '\n' + os.environ.get('DESCRIPTION','')
m = re.search(r'[Ll]ocks\s*:\s*\[([^\]]*)\]', hay)
if not m:
    print('missing')
else:
    content = m.group(1).strip()
    if not content:
        print('empty')
    elif content.strip() == '*':
        print('wildcard')
    else:
        print('ok')
" 2>/dev/null)

  case "${LOCKS_STATUS:-missing}" in
    empty|wildcard)
      log_rule_fire "isolation_bridge" "$tool" "block" "Locks: [] or Locks: [*]"
      advise "edit-capable spawn declared \`Locks: []\` or \`Locks: [*]\`. Name only the specific files this agent will write, e.g. \`Locks: [/tmp/my-task.log]\`."
      ;;
    missing)
      local AUTO_LOCK
      AUTO_LOCK=$(DESCRIPTION="$DESCRIPTION" PROMPT="$PROMPT" python3 -c "
import os, re
desc = os.environ.get('DESCRIPTION','') or os.environ.get('PROMPT','')[:40]
base = re.sub(r'[^a-z0-9-]', '-', desc.lower().replace(' ','-'))[:32]
base = re.sub(r'-+', '-', base).strip('-') or 'task'
print(f'/tmp/auto-{base}.log')
" 2>/dev/null)
      AUTO_LOCK="${AUTO_LOCK:-/tmp/auto-task.log}"
      echo "task-isolation-bridge: no Locks header found -- auto-injecting \`Locks: [${AUTO_LOCK}]\`. Add a Locks header to your spawn prompt to silence this warning." >&2
      PROMPT="Locks: [${AUTO_LOCK}]
${PROMPT}"
      ;;
    ok)
      : ;;
  esac

  # ── Gate c: Upstream guard (→2506) ────────────────────────────────────
  # When description/prompt signals "report upstream / ostk / scott", write a
  # draft file and block the native Task without POSTing to /api/agents/spawn.
  local _UG_MATCH
  _UG_MATCH=$(DESCRIPTION="$DESCRIPTION" PROMPT="$PROMPT" python3 -c "
import os, re
hay = os.environ.get('DESCRIPTION','') + ' ' + os.environ.get('PROMPT','')
# Real upstream-report intent only (→2538): bare 'ostk' appears in every
# compliant brief (the mandatory Use-ostk-MCP-tools line, mcp__ostk__*,
# .ostk/ paths) and 'upstream' shows up in benign phrases like 'upstream
# dependency'. Trigger on: scott, ostk-kernel, a report-ish verb near
# 'upstream', or 'upstream <task|bug|issue|report|draft>'.
if re.search(
    r'\bscott\b'
    r'|\bostk-kernel\b'
    r'|\b(report|file|send|escalate|draft)\w*\b[^.\n]{0,40}\bupstream\b'
    r'|\bupstream\b[^.\n]{0,40}\b(task|bug|issue|report|draft)\b',
    hay, re.IGNORECASE,
):
    print('yes')
" 2>/dev/null)
  if [ "${_UG_MATCH:-}" = "yes" ]; then
    # Scott drafts must never land in ~/.youros/drafts or ~/.youros/specs:
    # the specs API reads both, so they show up as Draft specs in the app
    # (Tori, 2026-07-09: "keep them out of youros").
    local _DRAFT_DIR="$HOME/scott-drafts"
    mkdir -p "$_DRAFT_DIR" 2>/dev/null || true
    local _DRAFT_SLUG _DRAFT_DATE _DRAFT_FILE
    _DRAFT_SLUG=$(DESC="$DESCRIPTION" python3 -c "
import os, re
d = os.environ.get('DESC','')
s = re.sub(r'[^a-z0-9]+', '-', d.lower())[:40].strip('-') or 'upstream'
print(s)
" 2>/dev/null || echo "upstream")
    _DRAFT_DATE=$(date +%Y%m%d 2>/dev/null || echo "unknown")
    _DRAFT_FILE="${_DRAFT_DIR}/upstream-${_DRAFT_DATE}-${_DRAFT_SLUG}.md"
    {
      printf '# Upstream draft: %s\n\n' "$DESCRIPTION"
      printf '**Date:** %s\n\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo unknown)"
      printf '## Prompt\n\n%s\n' "$PROMPT"
    } > "$_DRAFT_FILE" 2>/dev/null || true
    log_rule_fire "isolation_bridge" "$tool" "block" "upstream-guard: drafted to $_DRAFT_FILE"
    echo "task-isolation-bridge: upstream-guard — upstream/ostk/scott spawn drafted to: $_DRAFT_FILE" >&2
    exit 2
  fi

  # ── Gate a: Evidence gate (→2506) ──────────────────────────────────────
  # Skip diagnose spawns when a recent fix commit already covers the same
  # area. Prevents re-diagnosing bugs that were fixed in the last 24h.
  local _EG_DIAGNOSE
  _EG_DIAGNOSE=$(DESCRIPTION="$DESCRIPTION" PROMPT="$PROMPT" python3 -c "
import os, re
hay = os.environ.get('DESCRIPTION','') + ' ' + os.environ.get('PROMPT','')
if re.search(r'\bdiagnose\b', hay, re.IGNORECASE):
    print('yes')
" 2>/dev/null)
  if [ "${_EG_DIAGNOSE:-}" = "yes" ]; then
    local _EG_KW
    _EG_KW=$(DESC="$DESCRIPTION" python3 -c "
import os, re
stop = {'the','a','an','and','or','fix','diagnose','issue','bug','this',
        'that','for','to','of','in','is','it','with','was','has','been',
        'be','by','at','as','on','not'}
desc = os.environ.get('DESC','')
words = [w for w in re.findall(r'[a-z0-9]+', desc.lower())
         if len(w) > 2 and w not in stop]
print(words[0] if words else '')
" 2>/dev/null)
    if [ -n "${_EG_KW:-}" ]; then
      local _EG_HIT
      _EG_HIT=$(git -C "${CLAUDE_PROJECT_DIR:-.}" log --oneline --since="24 hours ago" --all 2>/dev/null \
        | python3 -c "
import sys, re
fix_re = re.compile(r'\bfix\b|fixed|closes', re.IGNORECASE)
kw = sys.argv[1].lower()
for line in sys.stdin:
    if fix_re.search(line) and kw in line.lower():
        print(line.strip())
        break
" "$_EG_KW" 2>/dev/null || echo "")
      if [ -n "${_EG_HIT:-}" ]; then
        log_rule_fire "isolation_bridge" "$tool" "allow" "evidence-gate: recent fix for '${_EG_KW}': ${_EG_HIT}"
        echo "task-isolation-bridge: evidence-gate skip — recent fix commit covers '${_EG_KW}': ${_EG_HIT}" >&2
        return 0
      fi
    fi
  fi

  # ── Gate b: Dedup gate (→2506) ─────────────────────────────────────────
  # Skip spawn when the task store already has an open or recently closed
  # task covering the same fingerprint keywords.
  local _DD_KW
  _DD_KW=$(DESC="$DESCRIPTION" python3 -c "
import os, re
stop = {'the','a','an','and','or','fix','diagnose','issue','bug','this',
        'that','for','to','of','in','is','it','with','was','has','been',
        'be','by','at','as','on','not'}
desc = os.environ.get('DESC','')
words = [w for w in re.findall(r'[a-z0-9]+', desc.lower())
         if len(w) > 2 and w not in stop]
print('+'.join(words[:3]) if words else '')
" 2>/dev/null)
  if [ -n "${_DD_KW:-}" ]; then
    local _DD_RESP
    _DD_RESP=$(curl --silent --insecure --connect-timeout 2 -m 4 \
      "${API_BASE}/api/tasks?q=${_DD_KW}" 2>/dev/null || echo "")
    local _DD_HIT
    _DD_HIT=$(RESP="$_DD_RESP" DESC="$DESCRIPTION" python3 -c "
import os, json, re
resp = os.environ.get('RESP','')
desc = os.environ.get('DESC','').lower()
stop = {'the','a','an','and','or','fix','diagnose','issue','bug','this'}
kws = [w for w in re.findall(r'[a-z0-9]+', desc) if len(w)>2 and w not in stop]
try:
    data = json.loads(resp)
    tasks = data.get('tasks') or []
    for t in tasks:
        title = (t.get('title') or '').lower()
        if any(k in title for k in kws):
            print(str(t.get('id','?')) + ':' + str(t.get('status','?')))
            break
except Exception:
    pass
" 2>/dev/null)
    if [ -n "${_DD_HIT:-}" ]; then
      log_rule_fire "isolation_bridge" "$tool" "allow" "dedup-gate: existing task ${_DD_HIT}"
      echo "task-isolation-bridge: dedup-gate skip — existing task matches description: ${_DD_HIT}" >&2
      return 0
    fi
  fi

  # Generate spawn name
  local SPAWN_NAME
  SPAWN_NAME=$(DESC="$DESCRIPTION" PROMPT="$PROMPT" python3 <<'PY' 2>/dev/null
import os, re, uuid
desc = os.environ.get("DESC", "") or os.environ.get("PROMPT", "")[:60]
base = re.sub(r"[^a-z0-9]+", "-", desc.lower())[:32]
base = re.sub(r"-+", "-", base).strip("-") or "task-bridge"
print(f"{base}-{uuid.uuid4().hex[:6]}")
PY
  )
  if [ -z "$SPAWN_NAME" ]; then
    SPAWN_NAME="task-bridge-$$"
  fi

  # Build spawn body
  local BODY
  BODY=$(SPAWN_NAME="$SPAWN_NAME" DESCRIPTION="$DESCRIPTION" PROMPT="$PROMPT" \
          SUBAGENT="$SUBAGENT" MODEL="$MODEL" \
          ORIG_SESSION_ID="${ORIG_SESSION_ID:-${CLAUDE_SESSION_ID:-}}" ORIG_MSG_ID="${ORIG_MSG_ID:-}" \
          python3 <<'PY' 2>/dev/null
import os, json, re
prompt = os.environ.get("PROMPT") or ""
desc = os.environ.get("DESCRIPTION") or ""
hay = prompt + "\n" + desc

locks = set()
EXPLICIT_RE = re.compile(r"[Ll]ocks\s*:\s*\[([^\]]*)\]")
em = EXPLICIT_RE.search(hay)
if em:
    for part in re.split(r"[,\s]+", em.group(1)):
        part = part.strip().strip("\"'")
        if part:
            locks.add(part)

orig_session = os.environ.get("ORIG_SESSION_ID") or ""
orig_msg = os.environ.get("ORIG_MSG_ID") or ""

COMPLETION_CLAUSE = (
    "\n\n"
    "COMPLETION REQUIREMENT: Before your process exits you MUST do one of:\n"
    "  (a) git commit -m \"...\" (commit all file changes to your worktree branch), OR\n"
    "  (b) ostk work add \"...\" --priority P0 (file a task with the evidence).\n"
    "Exiting after analysis only — no commit, no task — is a failed run.\n"
    "If running tests: commit your work BEFORE running any verify step\n"
    "(e.g. git commit -m 'wip(→NNN): implementation' then pytest path/to/test.py -x -q).\n"
    "A failed verify still leaves a recoverable commit. Do not block a commit on full-suite pass."
)
injected_prompt = (prompt or desc) + COMPLETION_CLAUSE

body = {
    "name": os.environ["SPAWN_NAME"],
    "prompt": injected_prompt,
    "description": desc or "task-tool bridge spawn",
    "source": "task-bridge",
    "status": "running",
    "isolation": "worktree",
    "locks": sorted(locks),
    "user_authored": bool(orig_session and orig_msg),
}
if orig_session:
    body["originating_session_id"] = orig_session
if orig_msg:
    body["originating_user_message_id"] = orig_msg
sub = os.environ.get("SUBAGENT") or ""
if sub:
    body["subagent_type"] = sub
mdl = os.environ.get("MODEL") or ""
if mdl:
    body["model"] = mdl
print(json.dumps(body))
PY
  )

  if [ -z "$BODY" ]; then
    log_rule_fire "isolation_bridge" "$tool" "block" "could not build spawn body"
    advise "task-isolation-bridge could not build spawn body."
  fi

  # Fast health probe: 3 retries at 300 ms each — survives a transient connect blip
  # on a healthy backend without false-blocking the spawn. (→self-heal)
  local _PROBE_CODE="000"
  for _probe in 1 2 3; do
    _PROBE_CODE=$(curl --silent --insecure --connect-timeout 1 -m 2 \
        -o /dev/null -w '%{http_code}' "${API_BASE}/api/health" 2>/dev/null)
    [ "$_PROBE_CODE" = "200" ] && break
    [ "$_probe" -lt 3 ] && sleep 0.3
  done
  if [ "$_PROBE_CODE" != "200" ]; then
    local _CPD="${CLAUDE_PROJECT_DIR:-.}"
    if [ -d "${_CPD}/.ostk" ]; then
      log_rule_fire "isolation_bridge" "$tool" "block" "backend health probe failed 3× (has .ostk)"
      advise "myOS backend unreachable at ${API_BASE}. Native Task would leak subagent commits into the parent checkout because no .ostk/ is created in the worktree without the REST spawn path (→1200). Restart the backend (scripts/dev-backend.sh) and retry."
    fi
    echo "task-isolation-bridge: myOS backend unreachable at ${API_BASE} after 3 health probes." >&2
    echo "Allowing native Task tool. Worktree isolation skipped — sequential edits only." >&2
    log_rule_fire "isolation_bridge" "$tool" "allow" "backend unreachable after 3 probes, fail-open (no .ostk)"
    return 0
  fi

  # POST to backend (probe already confirmed backend is alive)
  local RESP_BODY HTTP_CODE
  RESP_BODY=$(mktemp)
  HTTP_CODE="000"
  for _retry in 1 2 3; do
    HTTP_CODE=$(curl --silent --insecure --tlsv1.2 --tls-max 1.2 --connect-timeout 3 -m 5 -o "$RESP_BODY" -w '%{http_code}' \
        -X POST "${API_BASE}/api/agents/spawn" \
        -H 'Content-Type: application/json' \
        -d "$BODY" 2>/dev/null)
    [ "$HTTP_CODE" != "000" ] && break
    [ "$_retry" -lt 3 ] && sleep 5
  done

  case "$HTTP_CODE" in
    2??)
      rm -f "$RESP_BODY"
      local PENDING_FILE="${CLAUDE_PROJECT_DIR:-.}/.ostk/pending-monitor-spawns.jsonl"
      mkdir -p "$(dirname "$PENDING_FILE")" 2>/dev/null || true
      SPAWN_NAME="$SPAWN_NAME" python3 -c '
import os, json
from datetime import datetime, timezone
print(json.dumps({"name": os.environ["SPAWN_NAME"], "ts": datetime.now(timezone.utc).isoformat(), "source": "task-isolation-bridge"}))
' 2>/dev/null >> "$PENDING_FILE" 2>/dev/null || true
      echo "redirected through /api/agents/spawn for worktree isolation (this is expected, not an error)." >&2
      echo "Spawned REST agent name: ${SPAWN_NAME}" >&2
      echo "Status: curl -sSk ${API_BASE}/api/agents | jq '.agents[] | select(.name==\"${SPAWN_NAME}\")'" >&2
      log_rule_fire "isolation_bridge" "$tool" "block" "redirected to REST spawn: $SPAWN_NAME"
      exit 2
      ;;
    ""|"000")
      rm -f "$RESP_BODY"
      local _CPD="${CLAUDE_PROJECT_DIR:-.}"
      if [ -d "${_CPD}/.ostk" ]; then
        log_rule_fire "isolation_bridge" "$tool" "block" "backend unreachable, fail-closed (has .ostk)"
        advise "myOS backend unreachable at ${API_BASE}. Native Task would leak subagent commits into the parent checkout because no .ostk/ is created in the worktree without the REST spawn path (→1200). Restart the backend (scripts/dev-backend.sh) and retry."
      fi
      echo "task-isolation-bridge: myOS backend unreachable at ${API_BASE} (connect-timeout/refused)." >&2
      echo "Allowing native Task tool. Worktree isolation skipped — sequential edits only." >&2
      log_rule_fire "isolation_bridge" "$tool" "allow" "backend unreachable, fail-open (no .ostk)"
      return 0
      ;;
    *)
      if [ -s "$RESP_BODY" ]; then
        RESP_BODY="$RESP_BODY" python3 -c '
import os, sys, json
try:
    body = json.load(open(os.environ["RESP_BODY"]))
    conflicts = (body.get("detail") or {}).get("conflicts") or []
    for c in conflicts:
        held = c.get("held_by_spawn", "?")
        path = c.get("held_path", "?")
        print("  conflict: held_by_spawn=" + str(held) + " held_path=" + str(path))
except Exception:
    pass
' >&2 2>/dev/null
      fi
      rm -f "$RESP_BODY"
      log_rule_fire "isolation_bridge" "$tool" "block" "backend HTTP $HTTP_CODE"
      advise "/api/agents/spawn returned HTTP ${HTTP_CODE}. Native Task tool would silently write to the parent checkout. Fix the backend error and retry."
      ;;
  esac
}
