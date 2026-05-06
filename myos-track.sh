#!/usr/bin/env bash
# Enable or disable myOS subagent tracking in the current repo.
#
# Adds a Claude Code PreToolUse hook at .claude/settings.local.json
# pointing at ~/.myos/hooks/register-agent.sh, so Task-tool subagents
# spawned in THIS repo register with the myOS backend and show up on
# the Agents page. Scoped to one project at a time; doesn't touch
# ~/.claude/ or any other directory.
#
# Usage:
#   myos-track            enable in the current directory
#   myos-track --remove   disable in the current directory
#   myos-track --status   report current state
#   myos-track --help

set -u

ACTION=enable
while [ $# -gt 0 ]; do
    case "$1" in
        --remove|--off) ACTION=remove; shift ;;
        --status) ACTION=status; shift ;;
        --help|-h)
            sed -n '2,/^$/ { s/^# \{0,1\}//; p; }' "$0"
            exit 0
            ;;
        *) echo "Unknown flag: $1" >&2; echo "Try --help." >&2; exit 2 ;;
    esac
done

HOOK_FILE="$HOME/.myos/hooks/register-agent.sh"
SETTINGS=".claude/settings.local.json"

if [ ! -f "$HOOK_FILE" ]; then
    echo "Missing $HOOK_FILE — run ./install.sh in the myOS repo first." >&2
    exit 1
fi

# Pretty-print the repo-root relative path for messaging.
REPO_DIR=$(pwd)

case "$ACTION" in
    status)
        if [ ! -f "$SETTINGS" ]; then
            echo "myos-track: NOT tracking in $REPO_DIR"
            exit 0
        fi
        if python3 -c "
import json, sys
d = json.load(open('$SETTINGS'))
pre = d.get('hooks', {}).get('PreToolUse', [])
for e in pre:
    for h in e.get('hooks', []):
        if 'register-agent.sh' in str(h.get('command','')):
            sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
            echo "myos-track: tracking in $REPO_DIR (via $SETTINGS)"
        else
            echo "myos-track: NOT tracking in $REPO_DIR ($SETTINGS exists but no hook entry)"
        fi
        exit 0
        ;;

    enable)
        mkdir -p "$(dirname "$SETTINGS")"
        # Merge into an existing settings.local.json if one is there, so
        # we don't clobber the user's other local settings. Otherwise
        # create a minimal file.
        python3 - "$SETTINGS" "$HOOK_FILE" <<'PYEOF' || { echo "Failed to update $SETTINGS" >&2; exit 1; }
import json, os, sys
path, hook = sys.argv[1], sys.argv[2]
try:
    with open(path) as f: d = json.load(f)
except FileNotFoundError:
    d = {}
except json.JSONDecodeError:
    print(f"{path} is not valid JSON; refusing to overwrite. Edit it manually or remove it first.", file=sys.stderr)
    sys.exit(1)
hooks = d.setdefault("hooks", {})
pre = hooks.setdefault("PreToolUse", [])
# Don't double-add if already wired.
for e in pre:
    for h in e.get("hooks", []):
        if "register-agent.sh" in str(h.get("command","")):
            print(f"already enabled in {path}")
            sys.exit(0)
pre.append({
    "matcher": "Agent",
    "hooks": [{"type": "command", "command": hook}]
})
with open(path, "w") as f:
    json.dump(d, f, indent=2); f.write("\n")
print(f"enabled: {path}")
PYEOF
        echo "myos-track: tracking enabled in $REPO_DIR"
        echo "Claude Code sessions opened here will register Task-tool subagents with myOS."
        ;;

    remove)
        if [ ! -f "$SETTINGS" ]; then
            echo "myos-track: nothing to remove in $REPO_DIR (no $SETTINGS)"
            exit 0
        fi
        python3 - "$SETTINGS" <<'PYEOF' || { echo "Failed to update $SETTINGS" >&2; exit 1; }
import json, os, sys
path = sys.argv[1]
with open(path) as f: d = json.load(f)
pre = d.get("hooks", {}).get("PreToolUse", [])
new_pre = []
removed = False
for e in pre:
    keep = [h for h in e.get("hooks", []) if "register-agent.sh" not in str(h.get("command",""))]
    if len(keep) != len(e.get("hooks", [])):
        removed = True
    if keep:
        new_e = dict(e); new_e["hooks"] = keep
        new_pre.append(new_e)
if removed:
    d.setdefault("hooks", {})["PreToolUse"] = new_pre
    # Clean up empty sections so the file stays minimal.
    if not d["hooks"]["PreToolUse"]:
        del d["hooks"]["PreToolUse"]
    if not d["hooks"]:
        del d["hooks"]
    if d:
        with open(path, "w") as f:
            json.dump(d, f, indent=2); f.write("\n")
    else:
        os.remove(path)
        print(f"removed empty {path}")
        sys.exit(0)
print(f"updated {path}")
PYEOF
        echo "myos-track: tracking disabled in $REPO_DIR"
        ;;
esac
