#!/bin/bash
HOOK_NAME=$(basename "$0")
_DENY_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
source "${_DENY_DIR}/.claude/hooks/lib/deny.sh"
init_deny_traps
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME tool=${TOOL:-?} exit=$?" >> /tmp/hook-trace.log' EXIT
# Combined PreToolUse guard for Bash, Monitor, mcp__ostk__bash.
#
# Replaces seven separate hooks with one shared parse-once dispatcher:
#   - zsh-reserved-var-guard (Bash|Monitor|mcp__ostk__bash)
#   - curl-timeouts          (Bash)
#   - no-npm-dev             (Bash)
#   - safe-vitest            (Bash)
#   - no-open-source         (Bash)
#   - skip-hook-audit        (Bash)
#   - stash-label-audit      (Bash, tori-gated)
#
# Each sub-check runs in sequence and blocks (exit 2) on first hit.
# All checks fail-open against missing infrastructure: no scripts/dev-backend.sh
# means no-npm-dev skips; no scripts/run-vitest.sh means safe-vitest skips;
# no ~/.myos/config.json with enable_tori_rules:true means stash-label-audit
# skips.
#
# Why one hook: each separate hook spawns its own bash + python3 to parse
# the JSON payload. Six python forks per Bash call adds 500-1200ms on
# macOS. Parsing once and dispatching internally is ~5x faster on the
# hot path.

set -u
INPUT=$(cat)

# Single-pass JSON parse: extract tool_name + command (or cmd for ostk_bash).
PARSED=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, json, sys
try:
    d = json.loads(os.environ.get("INPUT_JSON", "") or "{}", strict=False)
except Exception:
    sys.exit(0)
tool = (d.get("tool_name") or "").strip()
ti = d.get("tool_input", {}) or {}
cmd = (ti.get("command") or ti.get("cmd") or "")
# Use \x1f as separator so newlines in cmd are preserved.
sys.stdout.write(tool + "\x1f" + cmd)
PY
)

if [ -z "$PARSED" ]; then
    exit 0
fi

IFS=$'\x1f' read -r TOOL CMD <<<"$PARSED"

# ---------------------------------------------------------------------
# 1. zsh-reserved-var-guard: Bash|Monitor|mcp__ostk__bash
#    Block assignments to zsh read-only special vars (status, path, ...).
# ---------------------------------------------------------------------
case "$TOOL" in
  Bash|Monitor|mcp__ostk__bash)
    OFFENDER=$(HOOK_CMD="$CMD" python3 <<'PY' 2>/dev/null
import os, re
cmd = os.environ.get("HOOK_CMD", "")
RESERVED = ['status', 'pipestatus', 'path', 'cdpath', 'fpath',
            'manpath', 'prompt', 'psvar', 'argv', 'signals', 'options']
strict = re.compile(
    r'(?:(?:^|[;\s|&(]))'
    r'(' + '|'.join(re.escape(v) for v in RESERVED) + r')'
    r'(?=[^a-zA-Z0-9_])',
    re.MULTILINE,
)
for m in strict.finditer(cmd):
    var = m.group(1)
    after = cmd[m.end():]
    if re.match(r'\s*=(?!=)', after):
        print(var)
        break
PY
)
    if [ -n "$OFFENDER" ]; then
        case "$OFFENDER" in
          status)     _HINT="Replace: status=\$(...) with: exit_status=\$(...) or result=\$(...)" ;;
          path)       _HINT="Replace: path=... with: dir_path=... or target_path=..." ;;
          pipestatus) _HINT="Replace: pipestatus=... with: pipe_rc=... or pipe_codes=..." ;;
          prompt)     _HINT="Replace: prompt=... with: user_prompt=... or input_prompt=..." ;;
          argv)       _HINT="Replace: argv=... with: cli_args=... or args_arr=..." ;;
          *)          _HINT="Replace: ${OFFENDER}=... with: my_${OFFENDER}=... or ${OFFENDER}_val=..." ;;
        esac
        deny "\`${OFFENDER}=\` assigns to a zsh read-only variable. zsh declares \`${OFFENDER}\` read-only at startup; assigning crashes with 'read-only variable: ${OFFENDER}' before any output. $_HINT (reserved: status pipestatus path cdpath fpath manpath prompt psvar argv signals options)"
    fi
    ;;
esac

# Everything below is Bash-only.
case "$TOOL" in
  Bash) : ;;
  *) exit 0 ;;
esac

# Resolve project dir once for fail-open checks.
PROJ_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"

# ---------------------------------------------------------------------
# 2. curl-timeouts: block curl without --connect-timeout (Bash only).
#    Allows curl inside scripts/ since those handle their own timeouts.
# ---------------------------------------------------------------------
if echo "$CMD" | grep -q curl; then
    case "$CMD" in
        *scripts/*|*bash\ *scripts*|*\.sh*) : ;;
        *)
            if ! echo "$CMD" | grep -qE '\-\-connect-timeout|\-m [0-9]|--max-time'; then
                deny "curl without --connect-timeout. Add --connect-timeout 3 -m 5 (or shorter) to prevent hangs. Command: $CMD"
            fi
            ;;
    esac
fi

# ---------------------------------------------------------------------
# 3. no-npm-dev: block npm/pnpm/yarn run dev when scripts/dev-backend.sh
#    exists. Skip otherwise (other repos don't have the wrapper).
# ---------------------------------------------------------------------
if [ -n "$PROJ_DIR" ] && [ -f "$PROJ_DIR/scripts/dev-backend.sh" ]; then
    case "$CMD" in
        *npm\ run\ dev*|*pnpm\ run\ dev*|*yarn\ dev*)
            deny "do not use npm/pnpm/yarn run dev. Use scripts/dev-backend.sh and scripts/dev-frontend.sh instead. npm run dev forks a child process that survives kill signals, leaving zombie listeners on port 3010."
            ;;
    esac
fi

# ---------------------------------------------------------------------
# 4. safe-vitest: block bare vitest when scripts/run-vitest.sh exists.
#    Allow process probes (pgrep/ps/lsof/kill -0) that mention vitest.
# ---------------------------------------------------------------------
if [ -n "$PROJ_DIR" ] && [ -f "$PROJ_DIR/scripts/run-vitest.sh" ]; then
    case "$CMD" in
        *run-vitest.sh*|*scripts/run-vitest*) : ;;
        *)
            if [[ "$CMD" =~ ^[[:space:]]*(pgrep|ps|lsof|kill[[:space:]]+-0)[[:space:]] ]]; then
                : # process probes are safe
            elif [[ "$CMD" =~ (^|[[:space:]\|\;\&\(])(vitest|npx[[:space:]]+vitest|pnpm[[:space:]]+(test|vitest)|npm[[:space:]]+(test|run[[:space:]]+vitest)|yarn[[:space:]]+(test|vitest))([[:space:]]|$) ]]; then
                deny "use scripts/run-vitest.sh instead of bare vitest. Bare vitest commands can spawn orphan worker storms."
            fi
            ;;
    esac
fi

# ---------------------------------------------------------------------
# 5. no-open-source: block `open <source-file>`. Allow generated outputs.
# ---------------------------------------------------------------------
case "$CMD" in
    open\ *)
        FILE=$(echo "$CMD" | sed 's/^open //' | sed 's/ .*//')
        case "$FILE" in
            *.md|*.html|*.htm|*.pdf|*.png|*.jpg|*.jpeg|*.gif|*.svg|*.webp|*.txt) : ;;
            /tmp/*|/private/tmp/*) : ;;
            http://*|https://*) : ;;
            *.py|*.ts|*.tsx|*.js|*.jsx|*.sh|*.json|*.yaml|*.yml|*.toml|*.css|*.scss|*.cfg|*.ini|*.env)
                deny "do not auto-open source files ($FILE). Only open generated reports, PDFs, images, or HTML output."
                ;;
        esac
        ;;
esac

# ---------------------------------------------------------------------
# 6. skip-hook-audit: MYOS_SKIP_HOOK=1 git commit needs a recent
#    `ostk decide` entry tagged skip-hook (within 600 seconds).
#    Inert when user never sets MYOS_SKIP_HOOK=1.
# ---------------------------------------------------------------------
case "$CMD" in
    *MYOS_SKIP_HOOK=1*)
        case "$CMD" in
            *git\ commit*)
                DECISIONS="${DECISIONS_PATH:-.ostk/decisions.jsonl}"
                if [ ! -f "$DECISIONS" ]; then
                    deny "MYOS_SKIP_HOOK=1 requires a recent \`ostk decide\` entry tagged skip-hook. No decisions file found at: $DECISIONS. Run: ostk decide key=skip-hook-<short-reason> value=skip reason=\"...why...\""
                fi
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
            ts_raw = (entry.get("timestamp") or entry.get("ts")
                      or entry.get("created_at") or "")
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
                    deny "MYOS_SKIP_HOOK=1 requires a recent \`ostk decide\` entry tagged skip-hook. No matching entry found within 10 minutes. Run: ostk decide key=skip-hook-<short-reason> value=skip reason=\"...why...\""
                fi
                ;;
        esac
        ;;
esac

# ---------------------------------------------------------------------
# 7. stash-label-audit: bare `git stash` requires -m "<6+ char label>"
#    that doesn't start with "WIP on" or a generic filler word.
#    Tori-gated: no-op unless ~/.myos/config.json has enable_tori_rules.
# ---------------------------------------------------------------------
TORI_CONFIG="${MYOS_CONFIG_PATH:-$HOME/.myos/config.json}"
if [ -f "$TORI_CONFIG" ] && grep -q '"enable_tori_rules"[[:space:]]*:[[:space:]]*true' "$TORI_CONFIG" 2>/dev/null; then
    case "$CMD" in
        *git\ stash*)
            # Pass through subcommands that aren't push/bare.
            if echo "$CMD" | grep -qE 'git stash (list|show|drop|pop|apply|branch|clear)\b'; then
                :
            else
                LABEL=$(echo "$CMD" | python3 -c '
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
                    deny "git stash without a label. Bare \`git stash\` uses the HEAD commit subject, making stashes impossible to identify. Use: git stash push -m \"<descriptive-label>\" (at least 6 characters, not starting with \"WIP on \")"
                fi
                LABEL_LEN=${#LABEL}
                if [ "$LABEL_LEN" -lt 6 ]; then
                    deny "stash label \"$LABEL\" is too short ($LABEL_LEN chars, need >=6). Use: git stash push -m \"<descriptive-label>\" (at least 6 characters)"
                fi
                case "$LABEL" in
                    "WIP on "*)
                        deny "stash label starts with \"WIP on \", which is what bare git stash generates automatically. Use a descriptive label: git stash push -m \"<what you are stashing and why>\""
                        ;;
                esac
                FIRST_WORD=$(echo "$LABEL" | awk '{print tolower($1)}')
                case "$FIRST_WORD" in
                    temp|tmp|baseline|wip|scratch|test|misc|stuff|x|fix)
                        deny "stash label \"$LABEL\" starts with a generic filler word (\"$FIRST_WORD\"). Use a descriptive label, e.g. \"drive-preview-overlay-rework\"."
                        ;;
                esac
            fi
            ;;
    esac
fi

# ---------------------------------------------------------------------
# 8. long-run-cache: Bash.
#    If a known-long script is being re-run with a grep/head/tail/awk/sed
#    filter pipe, and a fresh cache at /tmp/last-<key>.log (< 600s old)
#    exists, emit a non-blocking hint. Never blocks (exit 0).
# ---------------------------------------------------------------------
if [ "$TOOL" = "Bash" ]; then
    CACHE_INFO=$(HOOK_CMD="$CMD" python3 <<'PY' 2>/dev/null
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
        IFS=$'\x1f' read -r CACHE_KEY CACHE_PATH CACHE_AGE <<<"$CACHE_INFO"
        echo "Hint: this command (or one matching its key) ran ${CACHE_AGE}s ago. Cached output at ${CACHE_PATH}. Grep that file instead of re-running." >&2
    fi
fi

exit 0
