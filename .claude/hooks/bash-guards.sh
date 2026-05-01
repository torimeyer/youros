#!/bin/bash
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
        echo "Blocked: \`${OFFENDER}=\` assigns to a zsh read-only variable."
        echo ""
        echo "zsh declares \`${OFFENDER}\` read-only at startup. Assigning to it"
        echo "crashes the script immediately with \"read-only variable: ${OFFENDER}\""
        echo "before any output is produced."
        echo ""
        case "$OFFENDER" in
          status)     echo "  Replace: status=\$(...)   with: exit_status=\$(...) or result=\$(...)" ;;
          path)       echo "  Replace: path=...         with: dir_path=... or target_path=..." ;;
          pipestatus) echo "  Replace: pipestatus=...   with: pipe_rc=... or pipe_codes=..." ;;
          prompt)     echo "  Replace: prompt=...       with: user_prompt=... or input_prompt=..." ;;
          argv)       echo "  Replace: argv=...         with: cli_args=... or args_arr=..." ;;
          *)          echo "  Replace: ${OFFENDER}=...  with: my_${OFFENDER}=... or ${OFFENDER}_val=..." ;;
        esac
        echo ""
        echo "zsh reserved names: status pipestatus path cdpath fpath manpath prompt psvar argv signals options"
        exit 2
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
                echo "Blocked: curl without --connect-timeout."
                echo "Add --connect-timeout 3 -m 5 (or shorter) to prevent hangs."
                echo "Command: $CMD"
                exit 2
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
            echo "Blocked: do not use npm/pnpm/yarn run dev."
            echo "Use scripts/dev-backend.sh and scripts/dev-frontend.sh instead."
            echo "npm run dev forks a child process that survives kill signals,"
            echo "leaving zombie listeners on port 3010."
            exit 2
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
                echo "Blocked: use scripts/run-vitest.sh instead of bare vitest."
                echo "Bare vitest commands can spawn orphan worker storms."
                exit 2
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
                echo "Blocked: do not auto-open source files ($FILE)."
                echo "Only open generated reports, PDFs, images, or HTML output."
                exit 2
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
                    echo "MYOS_SKIP_HOOK=1 requires a recent \`ostk decide\` entry tagged skip-hook explaining why." >&2
                    echo "No decisions file found at: $DECISIONS" >&2
                    echo "Run: ostk decide key=skip-hook-<short-reason> value=skip reason=\"...why...\"" >&2
                    echo "Then retry." >&2
                    exit 2
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
                    echo "MYOS_SKIP_HOOK=1 requires a recent \`ostk decide\` entry tagged skip-hook explaining why." >&2
                    echo "Run: ostk decide key=skip-hook-<short-reason> value=skip reason=\"...why...\"" >&2
                    echo "Then retry within 10 minutes." >&2
                    exit 2
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
                    echo "Blocked: git stash without a label." >&2
                    echo "Bare \`git stash\` uses the HEAD commit subject, making stashes impossible to identify later." >&2
                    echo "Use: git stash push -m \"<descriptive-label>\"  (at least 6 characters, not starting with \"WIP on \")" >&2
                    exit 2
                fi
                LABEL_LEN=${#LABEL}
                if [ "$LABEL_LEN" -lt 6 ]; then
                    echo "Blocked: stash label \"$LABEL\" is too short ($LABEL_LEN chars, need >=6)." >&2
                    echo "Use: git stash push -m \"<descriptive-label>\"  (at least 6 characters)" >&2
                    exit 2
                fi
                case "$LABEL" in
                    "WIP on "*)
                        echo "Blocked: stash label starts with \"WIP on \", which is what bare git stash generates automatically." >&2
                        echo "Use a descriptive label instead: git stash push -m \"<what you are stashing and why>\"" >&2
                        exit 2
                        ;;
                esac
                FIRST_WORD=$(echo "$LABEL" | awk '{print tolower($1)}')
                case "$FIRST_WORD" in
                    temp|tmp|baseline|wip|scratch|test|misc|stuff|x|fix)
                        echo "Blocked: stash label \"$LABEL\" starts with a generic filler word (\"$FIRST_WORD\")." >&2
                        echo "Use a descriptive label instead, e.g. \"drive-preview-overlay-rework\"." >&2
                        exit 2
                        ;;
                esac
            fi
            ;;
    esac
fi

exit 0
