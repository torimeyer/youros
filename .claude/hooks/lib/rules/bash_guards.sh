#!/usr/bin/env bash
# Rule: bash_guards
# Replaces: bash-guards.sh (PreToolUse Bash|Monitor|mcp__ostk__bash)
# Called from: pre-tool-guard.sh
# Assumes: load-rule.sh, log-fire.sh, deny.sh already sourced by caller.
# Top-level: _bash_guards_check dispatches 9 sub-rules.

# ---- Sub-rule 1: zsh-reserved-var-guard (Bash|Monitor|mcp__ostk__bash) ----
_bg_zsh_reserved_var() {
  local tool="$1" cmd="$2"
  local OFFENDER
  OFFENDER=$(HOOK_CMD="$cmd" python3 <<'PY' 2>/dev/null
import os, re
cmd = os.environ.get("HOOK_CMD", "")
RESERVED = ['status', 'pipestatus', 'path', 'cdpath', 'fpath',
            'manpath', 'prompt', 'psvar', 'argv', 'signals', 'options']

def strip_quoted(s):
    """Replace content inside single/double quotes with X placeholders.
    Prevents false positives when a reserved name appears inside a string
    literal (e.g. 'where status=completed' or "status=done")."""
    result = []
    i = 0
    while i < len(s):
        c = s[i]
        if c in ('"', "'"):
            quote = c
            result.append(c)
            i += 1
            while i < len(s) and s[i] != quote:
                if s[i] == '\\' and quote == '"' and i + 1 < len(s):
                    result.append('XX')
                    i += 2
                else:
                    result.append('X')
                    i += 1
            if i < len(s):
                result.append(s[i])  # closing quote
                i += 1
        else:
            result.append(c)
            i += 1
    return ''.join(result)

# Scan the command with quoted content stripped so we only detect real
# shell-level assignments, not reserved names inside string arguments.
stripped = strip_quoted(cmd)
strict = re.compile(
    r'(?:(?:^|[;\s|&(]))'
    r'(' + '|'.join(re.escape(v) for v in RESERVED) + r')'
    r'(?=[^a-zA-Z0-9_])',
    re.MULTILINE,
)
for m in strict.finditer(stripped):
    var = m.group(1)
    after = stripped[m.end():]
    if re.match(r'\s*=(?!=)', after):
        print(var)
        break
PY
  )
  if [ -n "$OFFENDER" ]; then
    local _HINT
    case "$OFFENDER" in
      status)     _HINT="Replace: status=\$(...) with: exit_status=\$(...) or result=\$(...)" ;;
      path)       _HINT="Replace: path=... with: dir_path=... or target_path=..." ;;
      pipestatus) _HINT="Replace: pipestatus=... with: pipe_rc=... or pipe_codes=..." ;;
      prompt)     _HINT="Replace: prompt=... with: user_prompt=... or input_prompt=..." ;;
      argv)       _HINT="Replace: argv=... with: cli_args=... or args_arr=..." ;;
      *)          _HINT="Replace: ${OFFENDER}=... with: my_${OFFENDER}=... or ${OFFENDER}_val=..." ;;
    esac
    log_rule_fire "bash_guards" "$tool" "block" "zsh reserved var: $OFFENDER"
    deny "\`${OFFENDER}=\` assigns to a zsh read-only variable. zsh declares \`${OFFENDER}\` read-only at startup; assigning crashes with 'read-only variable: ${OFFENDER}' before any output. $_HINT (reserved: status pipestatus path cdpath fpath manpath prompt psvar argv signals options)"
  fi
}

# ---- Sub-rule 2: curl-timeouts (Bash only) ----
_bg_curl_timeouts() {
  local tool="$1" cmd="$2"
  if echo "$cmd" | grep -q curl; then
    case "$cmd" in
      *scripts/*|*bash\ *scripts*|*\.sh*) return 0 ;;
      *)
        if ! echo "$cmd" | grep -qE '\-\-connect-timeout|\-m [0-9]|--max-time'; then
          log_rule_fire "bash_guards" "$tool" "block" "curl without --connect-timeout"
          deny "curl without --connect-timeout. Add --connect-timeout 3 -m 5 (or shorter) to prevent hangs. Command: $cmd"
        fi
        ;;
    esac
  fi
}

# ---- Sub-rule 3: no-npm-dev (Bash only) ----
_bg_no_npm_dev() {
  local tool="$1" cmd="$2"
  local PROJ_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
  if [ -n "$PROJ_DIR" ] && [ -f "$PROJ_DIR/scripts/dev-backend.sh" ]; then
    case "$cmd" in
      *npm\ run\ dev*|*pnpm\ run\ dev*|*yarn\ dev*)
        log_rule_fire "bash_guards" "$tool" "block" "npm run dev"
        deny "do not use npm/pnpm/yarn run dev. Use scripts/dev-backend.sh and scripts/dev-frontend.sh instead. npm run dev forks a child process that survives kill signals, leaving zombie listeners on port 3010."
        ;;
    esac
  fi
}

# ---- Sub-rule 4: safe-vitest (Bash only) ----
_bg_safe_vitest() {
  local tool="$1" cmd="$2"
  local PROJ_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
  if [ -n "$PROJ_DIR" ] && [ -f "$PROJ_DIR/scripts/run-vitest.sh" ]; then
    case "$cmd" in
      *run-vitest.sh*|*scripts/run-vitest*) return 0 ;;
      *)
        if [[ "$cmd" =~ ^[[:space:]]*(pgrep|ps|lsof|kill[[:space:]]+-0)[[:space:]] ]]; then
          return 0
        elif [[ "$cmd" =~ (^|[[:space:]\|\;\&\(])(vitest|npx[[:space:]]+vitest|pnpm[[:space:]]+(test|vitest)|npm[[:space:]]+(test|run[[:space:]]+vitest)|yarn[[:space:]]+(test|vitest))([[:space:]]|$) ]]; then
          log_rule_fire "bash_guards" "$tool" "block" "bare vitest"
          deny "use scripts/run-vitest.sh instead of bare vitest. Bare vitest commands can spawn orphan worker storms."
        fi
        ;;
    esac
  fi
}

# ---- Sub-rule 5: no-open-source (Bash only) ----
_bg_no_open_source() {
  local tool="$1" cmd="$2"
  case "$cmd" in
    open\ *)
      local FILE
      FILE=$(echo "$cmd" | sed 's/^open //' | sed 's/ .*//')
      case "$FILE" in
        *.md|*.html|*.htm|*.pdf|*.png|*.jpg|*.jpeg|*.gif|*.svg|*.webp|*.txt) return 0 ;;
        /tmp/*|/private/tmp/*) return 0 ;;
        http://*|https://*) return 0 ;;
        *.py|*.ts|*.tsx|*.js|*.jsx|*.sh|*.json|*.yaml|*.yml|*.toml|*.css|*.scss|*.cfg|*.ini|*.env)
          log_rule_fire "bash_guards" "$tool" "block" "open source file: $FILE"
          deny "do not auto-open source files ($FILE). Only open generated reports, PDFs, images, or HTML output."
          ;;
      esac
      ;;
  esac
}

# ---- Sub-rule 6: skip-hook-audit (Bash only) ----
_bg_skip_hook_audit() {
  local tool="$1" cmd="$2"
  case "$cmd" in
    *MYOS_SKIP_HOOK=1*)
      case "$cmd" in
        *git\ commit*)
          local DECISIONS="${DECISIONS_PATH:-.ostk/decisions.jsonl}"
          if [ ! -f "$DECISIONS" ]; then
            log_rule_fire "bash_guards" "$tool" "block" "MYOS_SKIP_HOOK=1 no decisions file"
            deny "MYOS_SKIP_HOOK=1 requires a recent \`ostk decide\` entry tagged skip-hook. No decisions file found at: $DECISIONS. Run: ostk decide key=skip-hook-<short-reason> value=skip reason=\"...why...\""
          fi
          local NOW CUTOFF FOUND
          NOW=$(date +%s)
          CUTOFF=$((NOW - 600))
          FOUND=$(DECISIONS="$DECISIONS" CUTOFF="$CUTOFF" python3 <<'PY' 2>/dev/null
import os, sys, json
from datetime import datetime
decisions_path = os.environ["DECISIONS"]
cutoff = int(os.environ["CUTOFF"])
try:
    with open(decisions_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            key = str(entry.get("key", ""))
            value = str(entry.get("value", ""))
            ts_raw = (entry.get("timestamp") or entry.get("ts") or entry.get("created_at") or "")
            ts = 0
            if ts_raw:
                try:
                    dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    ts = int(dt.timestamp())
                except Exception:
                    try:
                        ts = int(float(str(ts_raw)))
                    except Exception:
                        pass
            if ts < cutoff:
                continue
            if key.startswith("skip-hook") or value in ("skip-hook", "skip"):
                print("yes")
                sys.exit(0)
except Exception:
    pass
print("no")
PY
          )
          if [ "$FOUND" != "yes" ]; then
            log_rule_fire "bash_guards" "$tool" "block" "MYOS_SKIP_HOOK=1 no valid decision"
            deny "MYOS_SKIP_HOOK=1 requires a recent \`ostk decide\` entry tagged skip-hook. No matching entry found within 10 minutes. Run: ostk decide key=skip-hook-<short-reason> value=skip reason=\"...why...\""
          fi
          ;;
      esac
      ;;
  esac
}

# ---- Sub-rule 7: stash-label-audit (Bash only) ----
_bg_stash_label_audit() {
  local tool="$1" cmd="$2"
  local min_chars
  min_chars=$(rule_param "bash_guards.stash_min_label_chars" "6")
  case "$cmd" in
    *git\ stash*)
      if echo "$cmd" | grep -qE 'git stash (list|show|drop|pop|apply|branch|clear)\b'; then
        return 0
      fi
      local LABEL
      LABEL=$(echo "$cmd" | python3 -c '
import sys, re
cmd = sys.stdin.read().strip()
m = re.search(r"-m\s+[\x22\x27](.*?)[\x22\x27]", cmd)
if m:
    print(m.group(1)); sys.exit()
m2 = re.search(r"-m\s+(\S+)", cmd)
if m2:
    print(m2.group(1))
' 2>/dev/null)
      if [ -z "$LABEL" ]; then
        log_rule_fire "bash_guards" "$tool" "block" "git stash without label"
        deny "git stash without a label. Bare \`git stash\` uses the HEAD commit subject, making stashes impossible to identify. Use: git stash push -m \"<descriptive-label>\" (at least ${min_chars:-6} characters, not starting with \"WIP on \")"
      fi
      local LABEL_LEN=${#LABEL}
      if [ "$LABEL_LEN" -lt "${min_chars:-6}" ]; then
        log_rule_fire "bash_guards" "$tool" "block" "stash label too short: $LABEL"
        deny "stash label \"$LABEL\" is too short ($LABEL_LEN chars, need >=${min_chars:-6}). Use: git stash push -m \"<descriptive-label>\" (at least ${min_chars:-6} characters)"
      fi
      case "$LABEL" in
        "WIP on "*)
          log_rule_fire "bash_guards" "$tool" "block" "stash label starts with WIP on"
          deny "stash label starts with \"WIP on \", which is what bare git stash generates automatically. Use a descriptive label: git stash push -m \"<what you are stashing and why>\""
          ;;
      esac
      local FIRST_WORD
      FIRST_WORD=$(echo "$LABEL" | awk '{print tolower($1)}')
      case "$FIRST_WORD" in
        temp|tmp|baseline|wip|scratch|test|misc|stuff|x|fix)
          log_rule_fire "bash_guards" "$tool" "block" "stash label generic filler word: $FIRST_WORD"
          deny "stash label \"$LABEL\" starts with a generic filler word (\"$FIRST_WORD\"). Use a descriptive label, e.g. \"drive-preview-overlay-rework\"."
          ;;
      esac
      ;;
  esac
}

# ---- Sub-rule 8: no-auto-server-start (Bash only) ----
_bg_no_auto_server_start() {
  local tool="$1" cmd="$2"
  case "$cmd" in
    *scripts/dev-backend.sh*|*scripts/dev-frontend.sh*)
      if [[ "${MYOS_EXPLICIT_START:-}" != "1" ]] && ! echo "$cmd" | grep -q 'MYOS_EXPLICIT_START=1'; then
        log_rule_fire "bash_guards" "$tool" "block" "auto-start without MYOS_EXPLICIT_START=1"
        deny "Blocked auto-start of server — run with MYOS_EXPLICIT_START=1 or ask explicitly"
      fi
      ;;
  esac
}

# ---- Sub-rule 9: long-run-cache-hint (Bash only, non-blocking) ----
_bg_long_run_cache_hint() {
  local tool="$1" cmd="$2"
  local CACHE_INFO
  CACHE_INFO=$(HOOK_CMD="$cmd" python3 <<'PY' 2>/dev/null
import os, re, sys, time
cmd = os.environ.get("HOOK_CMD", "")
LONG_SCRIPTS = [
    (r'scripts/e2e_smoke\.sh', 'e2e_smoke'),
    (r'\bpytest\b', 'pytest'),
    (r'npm\s+run\s+build\b', 'npm_build'),
    (r'npx\s+playwright\s+test\b', 'playwright'),
    (r'pnpm\s+test\b', 'pnpm_test'),
    (r'pnpm\s+vitest\b', 'pnpm_vitest'),
]
FILTER_PAT = re.compile(r'\|\s*(grep|head|tail|awk|sed)\b', re.IGNORECASE)
matched_key = None
for pat, key in LONG_SCRIPTS:
    if re.search(pat, cmd):
        if key == 'pytest' and re.search(r'(-k\s|\S+\.py\b)', cmd):
            continue
        matched_key = key
        break
if not matched_key:
    sys.exit(0)
if not FILTER_PAT.search(cmd):
    sys.exit(0)
cache_path = f"/tmp/last-{matched_key}.log"
try:
    age = int(time.time()) - int(os.path.getmtime(cache_path))
    if 0 <= age < 600:
        print(f"{matched_key}\x1f{cache_path}\x1f{age}")
except Exception:
    pass
PY
  )
  if [ -n "$CACHE_INFO" ]; then
    local CACHE_KEY CACHE_PATH CACHE_AGE
    IFS=$'\x1f' read -r CACHE_KEY CACHE_PATH CACHE_AGE <<<"$CACHE_INFO"
    echo "Hint: this command (or one matching its key) ran ${CACHE_AGE}s ago. Cached output at ${CACHE_PATH}. Grep that file instead of re-running." >&2
  fi
}

# ---- Top-level dispatcher ----
_bash_guards_check() {
  local tool="$1" cmd="$2"

  # Sub-rules that apply to Bash|Monitor|mcp__ostk__bash
  case "$tool" in
    Bash|Monitor|mcp__ostk__bash)
      _bg_zsh_reserved_var "$tool" "$cmd"
      ;;
  esac

  # Sub-rules that apply to Bash only
  case "$tool" in
    Bash)
      _bg_curl_timeouts "$tool" "$cmd"
      _bg_no_npm_dev "$tool" "$cmd"
      _bg_safe_vitest "$tool" "$cmd"
      _bg_no_open_source "$tool" "$cmd"
      _bg_skip_hook_audit "$tool" "$cmd"
      _bg_stash_label_audit "$tool" "$cmd"
      _bg_no_auto_server_start "$tool" "$cmd"
      _bg_long_run_cache_hint "$tool" "$cmd"
      ;;
  esac

  log_rule_fire "bash_guards" "$tool" "allow" "all sub-rules passed"
  return 0
}
