#!/usr/bin/env bash
# One-shot tracked Claude Code session for the current repo.
#
# Writes .claude/settings.local.json with the myOS register-agent
# hook, runs `claude`, and on exit restores whatever was there before.
# Unlike `myos-track`, this does NOT leave tracking enabled between
# sessions — next `claude` in this dir runs untracked again.
#
# Usage:
#   myos-claude [args...]     starts Claude Code with myOS tracking
#                             for this session only
#   myos-claude --help

set -u

if [ $# -gt 0 ] && [ "$1" = "--help" ]; then
    sed -n '2,/^$/ { s/^# \{0,1\}//; p; }' "$0"
    exit 0
fi

HOOK_FILE="$HOME/.youros/hooks/register-agent.sh"
SETTINGS=".claude/settings.local.json"

if [ ! -f "$HOOK_FILE" ]; then
    echo "Missing $HOOK_FILE — run ./install.sh in the myOS repo first." >&2
    exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
    echo "claude CLI not found in PATH. Install it with: npm install -g @anthropic-ai/claude-code" >&2
    exit 1
fi

# Snapshot the pre-existing state so we can restore exactly on exit.
BACKUP_DIR=$(mktemp -d -t myos-claude.XXXXXX)
SETTINGS_BACKUP="$BACKUP_DIR/settings.local.json"
HAD_SETTINGS=0
if [ -f "$SETTINGS" ]; then
    cp "$SETTINGS" "$SETTINGS_BACKUP"
    HAD_SETTINGS=1
fi

cleanup() {
    # Restore whatever was there before. This fires on any exit:
    # normal completion, Ctrl+C, SIGTERM, shell death. set -u above
    # means we need default-value syntax for unset vars.
    if [ "${HAD_SETTINGS:-0}" = "1" ]; then
        cp -f "$SETTINGS_BACKUP" "$SETTINGS" 2>/dev/null || true
    else
        rm -f "$SETTINGS" 2>/dev/null || true
        # Remove .claude/ if we created it and it's now empty.
        rmdir .claude 2>/dev/null || true
    fi
    rm -rf "$BACKUP_DIR" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Write or merge a settings.local.json that wires the hook.
mkdir -p "$(dirname "$SETTINGS")"
python3 - "$SETTINGS" "$HOOK_FILE" <<'PYEOF' || { echo "Failed to write $SETTINGS" >&2; exit 1; }
import json, os, sys
path, hook = sys.argv[1], sys.argv[2]
try:
    with open(path) as f: d = json.load(f)
except FileNotFoundError:
    d = {}
except json.JSONDecodeError:
    print(f"{path} is not valid JSON; refusing to overwrite.", file=sys.stderr)
    sys.exit(1)
hooks = d.setdefault("hooks", {})
pre = hooks.setdefault("PreToolUse", [])
# Skip if already wired.
already = False
for e in pre:
    for h in e.get("hooks", []):
        if "register-agent.sh" in str(h.get("command","")):
            already = True
if not already:
    pre.append({
        "matcher": "Agent",
        "hooks": [{"type": "command", "command": hook}]
    })
with open(path, "w") as f:
    json.dump(d, f, indent=2); f.write("\n")
PYEOF

echo "myos-claude: tracking active for this session only"
echo ""
exec claude "$@"
