#!/bin/bash
# Install the torios Claude Code hooks into the user's global
# ~/.claude/ directory so EVERY Claude Code session on this machine
# registers its Task-tool subagents with the torios backend. This
# keeps the terminal "N background tasks" counter in sync with the
# torios sidebar badge + Agents page, even when Claude Code runs
# from a non-torios project folder.
#
# What it does:
#   1. Copies .claude/hooks/register-agent.sh to
#      ~/.claude/hooks/register-agent.sh (+x).
#   2. Copies .claude/hooks/lib/drain-pending.sh to
#      ~/.claude/hooks/lib/drain-pending.sh. If the repo lacks the
#      lib file, the global copy is pulled back into the repo first
#      (belt-and-suspenders for a half-reset working tree).
#   3. Surgically merges a PreToolUse matcher=Agent entry into
#      ~/.claude/settings.json pointing at the global hook. Uses
#      python3 for the merge so unrelated keys (permissions,
#      statusLine, enabledPlugins, theme, hooks.PostToolUse, etc.)
#      are never touched. Idempotent: a second run is a no-op and
#      prints "already wired".
#   4. Records torios_repo=<absolute path> in ~/.youros/config.json,
#      merging into the existing JSON when present.
#
# Flags:
#   --from <path>   Source repo override. Defaults to
#                   `git rev-parse --show-toplevel` from cwd.
#
# Exit codes:
#   0 on success (including idempotent no-ops)
#   non-zero on fatal error (bad source, unwritable target, etc.)

set -u

# --- Flag parsing -----------------------------------------------------
FROM=""
while [ $# -gt 0 ]; do
    case "$1" in
        --from)
            FROM="${2:-}"
            shift 2
            ;;
        --from=*)
            FROM="${1#--from=}"
            shift
            ;;
        -h|--help)
            sed -n '2,32p' "$0"
            exit 0
            ;;
        *)
            printf 'install-claude-hooks: unknown flag: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

# --- Resolve source repo ---------------------------------------------
# Source of hook files: current worktree is fine since its contents
# mirror the main repo once a merge happens. The recorded
# torios_repo value, however, should point at the main working tree
# (not an ephemeral agent worktree under .claude/worktrees/), so the
# heartbeat helpers still work after the worktree is pruned.
if [ -z "$FROM" ]; then
    FROM=$(git rev-parse --show-toplevel 2>/dev/null || true)
fi
if [ -z "$FROM" ] || [ ! -d "$FROM/.claude/hooks" ]; then
    printf 'install-claude-hooks: could not resolve source repo (.claude/hooks not found). Use --from <path>.\n' >&2
    exit 1
fi
FROM=$(cd "$FROM" && pwd)
printf 'install-claude-hooks: source repo = %s\n' "$FROM"

# Resolve the canonical main working tree. When FROM is under a
# worktree (.../.claude/worktrees/agent-<id>), walk up via
# git-common-dir to the main repo root.
CANONICAL_REPO="$FROM"
GIT_COMMON_DIR=$(unset GIT_DIR; git -C "$FROM" rev-parse --git-common-dir 2>/dev/null || true)
if [ -n "$GIT_COMMON_DIR" ]; then
    case "$GIT_COMMON_DIR" in
        /*) ;;
        *) GIT_COMMON_DIR=$(cd "$FROM" && cd "$GIT_COMMON_DIR" && pwd) ;;
    esac
    # git-common-dir points at the main repo's .git directory; the
    # main working tree is its parent.
    CANDIDATE=$(dirname "$GIT_COMMON_DIR")
    if [ -d "$CANDIDATE/.claude/hooks" ]; then
        CANONICAL_REPO="$CANDIDATE"
    fi
fi
if [ "$CANONICAL_REPO" != "$FROM" ]; then
    printf 'install-claude-hooks: canonical repo = %s (source is a worktree)\n' "$CANONICAL_REPO"
fi

# --- Prepare target dirs ---------------------------------------------
GLOBAL_HOOKS_DIR="$HOME/.claude/hooks"
GLOBAL_LIB_DIR="$GLOBAL_HOOKS_DIR/lib"
mkdir -p "$GLOBAL_LIB_DIR"

# --- Copy register-agent.sh ------------------------------------------
SRC_REGISTER="$FROM/.claude/hooks/register-agent.sh"
DST_REGISTER="$GLOBAL_HOOKS_DIR/register-agent.sh"
if [ ! -f "$SRC_REGISTER" ]; then
    printf 'install-claude-hooks: missing %s\n' "$SRC_REGISTER" >&2
    exit 1
fi
cp "$SRC_REGISTER" "$DST_REGISTER"
chmod +x "$DST_REGISTER"
printf 'install-claude-hooks: copied register-agent.sh -> %s\n' "$DST_REGISTER"

# --- Copy drain-pending.sh (lib) -------------------------------------
SRC_DRAIN="$FROM/.claude/hooks/lib/drain-pending.sh"
DST_DRAIN="$GLOBAL_LIB_DIR/drain-pending.sh"
if [ ! -f "$SRC_DRAIN" ]; then
    # Half-reset working tree: pull the lib back from the global
    # install so the repo source-of-truth is restored before we
    # continue.
    if [ -f "$DST_DRAIN" ]; then
        mkdir -p "$FROM/.claude/hooks/lib"
        cp "$DST_DRAIN" "$SRC_DRAIN"
        chmod +x "$SRC_DRAIN"
        printf 'install-claude-hooks: repo was missing lib/drain-pending.sh; restored from %s\n' "$DST_DRAIN"
    else
        printf 'install-claude-hooks: missing %s and no global fallback\n' "$SRC_DRAIN" >&2
        exit 1
    fi
fi
cp "$SRC_DRAIN" "$DST_DRAIN"
chmod +x "$DST_DRAIN"
printf 'install-claude-hooks: copied lib/drain-pending.sh -> %s\n' "$DST_DRAIN"

# --- Merge PreToolUse matcher=Agent into ~/.claude/settings.json ------
SETTINGS="$HOME/.claude/settings.json"
HOOK_CMD='$HOME/.claude/hooks/register-agent.sh'
MERGE_STATUS=$(HOME_SETTINGS="$SETTINGS" HOOK_CMD="$HOOK_CMD" python3 <<'PY'
import json, os, sys

path = os.environ["HOME_SETTINGS"]
cmd = os.environ["HOOK_CMD"]

data = {}
if os.path.exists(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        # Do not stomp on a corrupt settings.json. Let the user fix it.
        sys.stderr.write(f"settings.json parse error: {e}\n")
        sys.exit(2)

if not isinstance(data, dict):
    sys.stderr.write("settings.json is not a JSON object\n")
    sys.exit(2)

hooks = data.setdefault("hooks", {})
if not isinstance(hooks, dict):
    sys.stderr.write("hooks is not an object\n")
    sys.exit(2)

pretool = hooks.setdefault("PreToolUse", [])
if not isinstance(pretool, list):
    sys.stderr.write("PreToolUse is not a list\n")
    sys.exit(2)

def entry_matches(e):
    if not isinstance(e, dict):
        return False
    if e.get("matcher") != "Agent":
        return False
    hs = e.get("hooks") or []
    if not isinstance(hs, list):
        return False
    for h in hs:
        if isinstance(h, dict) and h.get("command") == cmd:
            return True
    return False

if any(entry_matches(e) for e in pretool):
    print("ALREADY_WIRED")
    sys.exit(0)

# Remove any stale Agent-matcher entries that point at a different
# register-agent.sh path (e.g. an older $CLAUDE_PROJECT_DIR one).
pretool[:] = [
    e for e in pretool
    if not (isinstance(e, dict) and e.get("matcher") == "Agent")
]

pretool.append({
    "matcher": "Agent",
    "hooks": [
        {"type": "command", "command": cmd},
    ],
})

os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
print("WIRED")
PY
)
MERGE_RC=$?
if [ $MERGE_RC -ne 0 ]; then
    printf 'install-claude-hooks: settings.json merge failed (rc=%s)\n' "$MERGE_RC" >&2
    exit $MERGE_RC
fi
case "$MERGE_STATUS" in
    ALREADY_WIRED)
        printf 'install-claude-hooks: ~/.claude/settings.json already wired\n'
        ;;
    WIRED)
        printf 'install-claude-hooks: merged Agent PreToolUse hook into %s\n' "$SETTINGS"
        ;;
    *)
        printf 'install-claude-hooks: unexpected merge status: %s\n' "$MERGE_STATUS" >&2
        ;;
esac

# --- Record torios_repo in ~/.youros/config.json -----------------------
CONFIG="$HOME/.youros/config.json"
mkdir -p "$HOME/.youros"
CONFIG_STATUS=$(CONFIG="$CONFIG" REPO="$CANONICAL_REPO" python3 <<'PY'
import json, os, sys

path = os.environ["CONFIG"]
repo = os.environ["REPO"]

data = {}
if os.path.exists(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        sys.stderr.write(f"config.json parse error: {e}\n")
        sys.exit(2)
if not isinstance(data, dict):
    sys.stderr.write("config.json is not an object\n")
    sys.exit(2)

if data.get("torios_repo") == repo:
    print("ALREADY_SET")
    sys.exit(0)

data["torios_repo"] = repo
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
print("UPDATED")
PY
)
CONFIG_RC=$?
if [ $CONFIG_RC -ne 0 ]; then
    printf 'install-claude-hooks: config.json merge failed (rc=%s)\n' "$CONFIG_RC" >&2
    exit $CONFIG_RC
fi
case "$CONFIG_STATUS" in
    ALREADY_SET)
        printf 'install-claude-hooks: ~/.youros/config.json torios_repo already set\n'
        ;;
    UPDATED)
        printf 'install-claude-hooks: recorded torios_repo=%s in %s\n' "$CANONICAL_REPO" "$CONFIG"
        ;;
esac

printf 'install-claude-hooks: done\n'
exit 0
