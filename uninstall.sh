#!/usr/bin/env bash
# Uninstall myOS.
#
# A fresh `git clone + ./install.sh` is NOT a clean slate: `~/.myos/`
# persists, the ostk daemon keeps running from a previous install, the
# localhost cert stays trusted in the Keychain, and the shell aliases
# (`myos`, `myos-update`) stick around. This script unwinds each layer
# in its own step.
#
# Usage:
#   ./uninstall.sh             # repo artifacts + aliases. keeps user data, ostk, cert.
#   ./uninstall.sh --purge     # also ~/.myos, ostk daemon/binary/cache, cert trust.
#   ./uninstall.sh --purge -y  # same, no prompts (for scripts; dangerous).
#   ./uninstall.sh --help

set -u

PURGE=0
YES=0
HELP=0

while [ $# -gt 0 ]; do
    case "$1" in
        --purge) PURGE=1; shift ;;
        --yes|-y) YES=1; shift ;;
        --help|-h) HELP=1; shift ;;
        *) echo "Unknown flag: $1" >&2; echo "Try --help." >&2; exit 2 ;;
    esac
done

if [ "$HELP" -eq 1 ]; then
    cat <<'EOF'
Usage: ./uninstall.sh [--purge] [--yes]

Default:
  Stops running myOS processes, removes the Python venv (api/.venv),
  npm packages (app/node_modules), built bundle (app/dist), and
  the generated .mcp.json. Removes the 'myos' and 'myos-update'
  aliases from ~/.zshrc (or ~/.bashrc). Also removes the global
  Claude Code hook at ~/.claude/hooks/register-agent.sh and its
  entry in ~/.claude/settings.json, if install.sh wired them.
  Keeps:
    - ~/.myos/ (your tasks, chats, labels, cert, settings)
    - ostk (binary, cache, daemon)
    - localhost cert trust in the Keychain
  These are preserved so another myOS install (or other tools using
  ostk) still works.

--purge:
  Everything above PLUS:
    - Deletes ~/.myos/ (ALL user data).
    - Stops the ostk daemon and kernel.
    - Removes ~/.local/bin/ostk and ~/.cache/ostk.
    - Removes the localhost cert from the macOS login Keychain.
  Each destructive step prompts before running.

--yes, -y:
  Skip confirmation prompts. For CI / scripted teardown.

The repo directory itself is never deleted. Remove it manually with
`rm -rf <path>` when you are done.
EOF
    exit 0
fi

confirm() {
    local msg="$1"
    if [ "$YES" -eq 1 ]; then
        echo "  ($msg — auto-yes)"
        return 0
    fi
    read -r -p "  $msg [y/N] " reply
    [ "$reply" = "y" ] || [ "$reply" = "Y" ]
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== myOS uninstall ==="
echo "Repo: $SCRIPT_DIR"
echo ""

# 1. Stop any running servers so we don't delete files out from under them.
if [ -x "$SCRIPT_DIR/stop.sh" ]; then
    echo "-> Stopping running myOS processes..."
    "$SCRIPT_DIR/stop.sh" >/dev/null 2>&1 || true
fi

# 2. Remove in-repo artifacts.
echo "-> Removing api/.venv, app/node_modules, app/dist, .mcp.json..."
rm -rf api/.venv app/node_modules app/dist .mcp.json

# 3. Remove shell aliases.
SHELL_RC="$HOME/.zshrc"
[ -f "$HOME/.bashrc" ] && [ ! -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.bashrc"
if [ -f "$SHELL_RC" ] && grep -qE "alias myos=|alias myos-update=" "$SHELL_RC"; then
    cp "$SHELL_RC" "$SHELL_RC.bak.myos-uninstall"
    # Remove the alias lines. Also remove a blank line that immediately
    # precedes them if install.sh added one.
    sed -i.tmp -e '/alias myos=/d' -e '/alias myos-update=/d' "$SHELL_RC"
    rm -f "$SHELL_RC.tmp"
    echo "-> Removed myos aliases from $SHELL_RC"
    echo "   (backup at $SHELL_RC.bak.myos-uninstall)"
fi

# 4. Remove the global Claude Code hook, if install.sh wired it.
# The global hook is opt-in (install.sh --with-claude-hooks), so this
# is a no-op unless it was actually installed. Removes the hook
# script, its lib/, and the matching PreToolUse.Agent entry from
# ~/.claude/settings.json.
GLOBAL_HOOK="$HOME/.claude/hooks/register-agent.sh"
GLOBAL_HOOK_LIB="$HOME/.claude/hooks/lib/drain-pending.sh"
GLOBAL_SETTINGS="$HOME/.claude/settings.json"
if [ -f "$GLOBAL_HOOK" ] || [ -f "$GLOBAL_HOOK_LIB" ]; then
    echo "-> Removing global Claude Code hook..."
    rm -f "$GLOBAL_HOOK" "$GLOBAL_HOOK_LIB"
    # Clean up lib/ if it's now empty.
    rmdir "$HOME/.claude/hooks/lib" 2>/dev/null || true
    echo "   removed $GLOBAL_HOOK"
fi
if [ -f "$GLOBAL_SETTINGS" ]; then
    python3 - "$GLOBAL_SETTINGS" <<'PYEOF' || echo "   (couldn't edit $HOME/.claude/settings.json; remove the register-agent entry manually)"
import json, sys
p = sys.argv[1]
with open(p) as f:
    d = json.load(f)
changed = False
pre = d.get("hooks", {}).get("PreToolUse", [])
filtered = []
for e in pre:
    hooks_list = e.get("hooks", []) if isinstance(e, dict) else []
    keep_hooks = [h for h in hooks_list if "register-agent.sh" not in str(h.get("command", ""))]
    if len(keep_hooks) != len(hooks_list):
        changed = True
    if keep_hooks:
        e2 = dict(e)
        e2["hooks"] = keep_hooks
        filtered.append(e2)
    else:
        # The whole matcher entry was just register-agent; drop it.
        if not hooks_list:
            filtered.append(e)
if changed:
    d.setdefault("hooks", {})["PreToolUse"] = filtered
    with open(p, "w") as f:
        json.dump(d, f, indent=2)
        f.write("\n")
    print(f"   cleaned register-agent entry from {p}")
PYEOF
fi

if [ "$PURGE" -eq 0 ]; then
    echo ""
    echo "Done. Kept (run with --purge to also remove):"
    [ -d "$HOME/.myos" ]         && echo "  - $HOME/.myos"
    [ -x "$HOME/.local/bin/ostk" ] && echo "  - $HOME/.local/bin/ostk"
    [ -d "$HOME/.cache/ostk" ]   && echo "  - $HOME/.cache/ostk"
    if [ "$(uname)" = "Darwin" ] && security find-certificate -c localhost "$HOME/Library/Keychains/login.keychain-db" >/dev/null 2>&1; then
        echo "  - localhost cert trust (macOS login Keychain)"
    fi
    if pgrep -f 'ostk (daemon|kernel)' >/dev/null 2>&1; then
        echo "  - running ostk daemon/kernel processes"
    fi
    echo ""
    echo "The repo itself is untouched. Delete it with:"
    echo "  rm -rf $SCRIPT_DIR"
    exit 0
fi

# --- --purge path ---
echo ""
echo "=== --purge: removing user data and ostk ==="

if [ -d "$HOME/.myos" ]; then
    echo "-> $HOME/.myos contains all your tasks, chats, settings, and cert."
    if confirm "Delete $HOME/.myos?"; then
        rm -rf "$HOME/.myos"
        echo "   removed."
    else
        echo "   kept."
    fi
fi

# Stop ostk processes before removing the binary.
if pgrep -f 'ostk (daemon|kernel)' >/dev/null 2>&1; then
    echo "-> ostk daemon / kernel processes are running."
    if confirm "Stop them?"; then
        pkill -TERM -f 'ostk kernel' 2>/dev/null || true
        pkill -TERM -f 'ostk daemon' 2>/dev/null || true
        sleep 0.5
        pkill -9 -f 'ostk kernel' 2>/dev/null || true
        pkill -9 -f 'ostk daemon' 2>/dev/null || true
        echo "   stopped."
    fi
fi

if [ -f "$HOME/.local/bin/ostk" ]; then
    if confirm "Remove ostk binary at $HOME/.local/bin/ostk?"; then
        rm -f "$HOME/.local/bin/ostk"
        echo "   removed."
    fi
fi

if [ -d "$HOME/.cache/ostk" ]; then
    if confirm "Remove ostk cache at $HOME/.cache/ostk?"; then
        rm -rf "$HOME/.cache/ostk"
        echo "   removed."
    fi
fi

# macOS: remove the localhost cert from the login Keychain so the next
# install's cert isn't silently trusted by a stale entry.
if [ "$(uname)" = "Darwin" ]; then
    KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
    if security find-certificate -c localhost "$KEYCHAIN" >/dev/null 2>&1; then
        if confirm "Remove localhost cert from the login Keychain?"; then
            # security delete-certificate removes one matching cert per call;
            # loop in case there are multiples.
            while security find-certificate -c localhost "$KEYCHAIN" >/dev/null 2>&1; do
                security delete-certificate -c localhost "$KEYCHAIN" >/dev/null 2>&1 || break
            done
            echo "   removed."
        fi
    fi
fi

echo ""
echo "=== Done. ==="
echo "The repo itself is untouched. Delete it with:"
echo "  rm -rf $SCRIPT_DIR"
