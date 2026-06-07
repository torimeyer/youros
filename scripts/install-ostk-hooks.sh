#!/bin/bash
# OPT-IN installer: wire ostk lifecycle hooks into Claude Code.
#
# DEFAULT BEHAVIOR IS UNCHANGED. Running this script is optional. Nothing
# here alters any existing hook in .claude/hooks/ or any existing settings
# entry. You opt in by running the script when you are ready.
#
# WHAT IT DOES:
#   Adds four entries to ~/.claude/settings.json so Claude Code forwards
#   lifecycle events into the ostk kernel journal (via `ostk hook <event>`).
#   These run IN ADDITION TO the existing bash hooks in .claude/hooks/ —
#   both paths are active during the dual-source soak period.
#
#   Event mapping:
#     SessionStart          → ostk hook session-start
#     SessionEnd            → ostk hook session-end
#     PreToolUse[Agent]     → ostk hook agent-start
#     PostToolUse[Agent]    → ostk hook agent-stop
#
# WHAT IT DOES NOT DO:
#   - Does NOT touch .claude/hooks/ (register-agent.sh, heartbeat-agent.sh,
#     complete-agent.sh, session-start.sh, session-end.sh, etc. keep running).
#   - Does NOT install ostk's pre-tool/post-tool Edit|Write interception
#     (that is a separate opt-in step).
#   - Does NOT call `ostk hook install` (which also wires interception hooks
#     we have not adopted yet).
#   - Does NOT touch standing-rules.sh, bash-guards.sh,
#     adhd-mode-monitor-enforcer.sh, consult-memory.sh, or any other
#     rule-enforcement hook.
#
# HOW TO REVERT:
#   Remove the four entries this script adds from ~/.claude/settings.json.
#   Look for hooks with commands: ostk hook session-start, ostk hook session-end,
#   ostk hook agent-start, ostk hook agent-stop.
#
#   If you used `ostk hook install` separately, revert that with:
#     ostk hook uninstall
#   (That command targets what `ostk hook install` wrote, not this script's work.)
#
# HOW TO FULLY SWITCH OVER (after the soak period confirms kernel hooks work):
#   1. Remove .claude/hooks/{register,heartbeat,complete,session-start,session-end}-agent.sh
#      and agent-completion-drain.sh from .claude/hooks/.
#   2. Remove the PreToolUse[Agent] register-agent.sh entry from ~/.claude/settings.json.
#   3. Keep: standing-rules.sh, bash-guards.sh, adhd-mode-monitor-enforcer.sh,
#      consult-memory.sh, and all other rule-enforcement hooks — those are yourOS
#      judgment and have no ostk equivalent.
#
# USAGE:
#   scripts/install-ostk-hooks.sh [FLAGS]
#
# FLAGS:
#   --yes         Skip the confirmation prompt.
#   --dry-run     Print what would be written without touching any file.
#   --uninstall   Remove the ostk lifecycle hooks this script added.
#   -h, --help    Show this header and exit.
#
# EXIT CODES:
#   0   success (including idempotent no-ops and dry-run)
#   1   ostk not on PATH
#   2   settings.json is missing or malformed
#   3   file write error

set -u

# --- Flag parsing -----------------------------------------------------------

DRY_RUN=0
YES=0
UNINSTALL=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)   DRY_RUN=1; shift ;;
        --yes|-y)    YES=1; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        -h|--help)
            sed -n '2,60p' "$0"
            exit 0
            ;;
        *)
            printf 'install-ostk-hooks: unknown flag: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

# --- Check ostk is on PATH -------------------------------------------------

if ! command -v ostk >/dev/null 2>&1; then
    printf 'install-ostk-hooks: ERROR: ostk not found on PATH.\n' >&2
    printf 'install-ostk-hooks: Install ostk first, then re-run this script.\n' >&2
    printf 'install-ostk-hooks: (Expected at ~/.local/bin/ostk or similar.)\n' >&2
    exit 1
fi

OSTK_VERSION=$(ostk --version 2>/dev/null | awk '{print $2}' || echo "unknown")
printf 'install-ostk-hooks: ostk %s found at %s\n' "$OSTK_VERSION" "$(command -v ostk)"

# --- Locate settings.json --------------------------------------------------

SETTINGS="$HOME/.claude/settings.json"

if [ ! -f "$SETTINGS" ]; then
    printf 'install-ostk-hooks: ERROR: %s does not exist.\n' "$SETTINGS" >&2
    printf 'install-ostk-hooks: Open Claude Code at least once to create it, then re-run.\n' >&2
    exit 2
fi

printf 'install-ostk-hooks: target config: %s\n' "$SETTINGS"
printf '\n'

# --- Describe the change ---------------------------------------------------

if [ $UNINSTALL -eq 0 ]; then
    printf 'This will add four ostk lifecycle hooks to %s:\n' "$SETTINGS"
    printf '\n'
    printf '  SessionStart      → ostk hook session-start\n'
    printf '                      (kernel journal entry on session open)\n'
    printf '  SessionEnd        → ostk hook session-end\n'
    printf '                      (kernel journal entry on session close)\n'
    printf '  PreToolUse[Agent] → ostk hook agent-start\n'
    printf '                      (kernel journal entry when an Agent tool fires)\n'
    printf '  PostToolUse[Agent]→ ostk hook agent-stop\n'
    printf '                      (kernel journal entry when an Agent tool returns)\n'
    printf '\n'
    printf 'Existing hooks in .claude/hooks/ are NOT removed.\n'
    printf 'Both the bash hooks and these kernel hooks will run side-by-side.\n'
else
    printf 'This will REMOVE the four ostk lifecycle hooks from %s:\n' "$SETTINGS"
    printf '  ostk hook session-start, ostk hook session-end,\n'
    printf '  ostk hook agent-start, ostk hook agent-stop\n'
    printf '\n'
    printf 'Existing bash hooks in .claude/hooks/ are NOT affected.\n'
fi
printf '\n'

if [ $DRY_RUN -eq 1 ]; then
    printf 'install-ostk-hooks: dry-run — no files written.\n'
    exit 0
fi

# --- Confirmation prompt ---------------------------------------------------

if [ $YES -eq 0 ] && [ -t 0 ]; then
    printf 'Proceed? [y/N] '
    read -r answer
    case "$answer" in
        y|Y|yes|YES) ;;
        *)
            printf 'Aborted.\n'
            exit 0
            ;;
    esac
fi

# --- Merge / remove hook entries in settings.json -------------------------
# Uses Python for safe JSON handling. Existing keys and formatting are
# preserved; only the specific ostk lifecycle entries are added or removed.

RESULT=$(DRY_RUN="$DRY_RUN" UNINSTALL="$UNINSTALL" SETTINGS="$SETTINGS" python3 <<'PY'
import json, os, sys

path    = os.environ["SETTINGS"]
uninstall = os.environ.get("UNINSTALL", "0") == "1"

# Lifecycle events: (claude_event, matcher_or_None, ostk_cmd)
LIFECYCLE = [
    ("SessionStart",  None,    "ostk hook session-start"),
    ("SessionEnd",    None,    "ostk hook session-end"),
    ("PreToolUse",    "Agent", "ostk hook agent-start"),
    ("PostToolUse",   "Agent", "ostk hook agent-stop"),
]

try:
    with open(path) as f:
        data = json.load(f)
except json.JSONDecodeError as e:
    sys.stderr.write(f"install-ostk-hooks: ERROR: settings.json parse error: {e}\n")
    sys.exit(2)
except Exception as e:
    sys.stderr.write(f"install-ostk-hooks: ERROR: cannot read settings.json: {e}\n")
    sys.exit(2)

if not isinstance(data, dict):
    sys.stderr.write("install-ostk-hooks: ERROR: settings.json is not a JSON object\n")
    sys.exit(2)

hooks = data.setdefault("hooks", {})
if not isinstance(hooks, dict):
    sys.stderr.write("install-ostk-hooks: ERROR: hooks is not an object in settings.json\n")
    sys.exit(2)


def cmd_in_entry(entry, matcher, cmd):
    if not isinstance(entry, dict):
        return False
    if matcher is not None and entry.get("matcher") != matcher:
        return False
    if matcher is None and "matcher" in entry:
        return False
    for h in entry.get("hooks") or []:
        if isinstance(h, dict) and h.get("command") == cmd:
            return True
    return False


changed = []
skipped = []

for event, matcher, cmd in LIFECYCLE:
    label = f"{event}[{matcher}]" if matcher else event
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        sys.stderr.write(
            f"install-ostk-hooks: ERROR: hooks.{event} is not a list in settings.json\n"
        )
        sys.exit(2)

    if uninstall:
        before = len(entries)
        hooks[event] = [e for e in entries if not cmd_in_entry(e, matcher, cmd)]
        if len(hooks[event]) < before:
            changed.append(label)
        else:
            skipped.append(label)
        # Clean up empty event lists to keep settings.json tidy.
        if not hooks[event]:
            del hooks[event]
    else:
        if any(cmd_in_entry(e, matcher, cmd) for e in entries):
            skipped.append(label)
            continue
        if matcher is not None:
            entry = {"matcher": matcher, "hooks": [{"type": "command", "command": cmd}]}
        else:
            entry = {"hooks": [{"type": "command", "command": cmd}]}
        entries.append(entry)
        changed.append(label)

if not changed:
    print("NOOP")
    sys.exit(0)

tmp = path + ".tmp"
try:
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
except Exception as e:
    sys.stderr.write(f"install-ostk-hooks: ERROR: cannot write settings.json: {e}\n")
    sys.exit(3)

parts = []
if changed:
    parts.append("CHANGED:" + ",".join(changed))
if skipped:
    parts.append("SKIPPED:" + ",".join(skipped))
print("|".join(parts))
PY
)
RC=$?

if [ $RC -ne 0 ]; then
    printf 'install-ostk-hooks: failed (rc=%d). No changes written.\n' "$RC" >&2
    exit $RC
fi

# --- Report results --------------------------------------------------------

case "$RESULT" in
    NOOP)
        if [ $UNINSTALL -eq 1 ]; then
            printf 'install-ostk-hooks: no ostk lifecycle hooks found to remove (already clean).\n'
        else
            printf 'install-ostk-hooks: all four hooks already wired — nothing to do.\n'
        fi
        exit 0
        ;;
esac

IFS='|' read -ra PARTS <<<"$RESULT"
for part in "${PARTS[@]}"; do
    case "$part" in
        CHANGED:*)
            events="${part#CHANGED:}"
            IFS=',' read -ra EV <<<"$events"
            for e in "${EV[@]}"; do
                if [ $UNINSTALL -eq 1 ]; then
                    printf 'install-ostk-hooks: removed  %s\n' "$e"
                else
                    printf 'install-ostk-hooks: wired    %s\n' "$e"
                fi
            done
            ;;
        SKIPPED:*)
            events="${part#SKIPPED:}"
            IFS=',' read -ra EV <<<"$events"
            for e in "${EV[@]}"; do
                if [ $UNINSTALL -eq 1 ]; then
                    printf 'install-ostk-hooks: not found %s (already absent)\n' "$e"
                else
                    printf 'install-ostk-hooks: already   %s (no-op)\n' "$e"
                fi
            done
            ;;
    esac
done

printf '\n'

if [ $UNINSTALL -eq 1 ]; then
    printf 'install-ostk-hooks: uninstall complete.\n'
    printf 'Bash hooks in .claude/hooks/ are still active.\n'
else
    printf 'install-ostk-hooks: done.\n'
    printf '\n'
    printf 'Restart Claude Code for the new hooks to take effect.\n'
    printf '\n'
    printf 'To revert:\n'
    printf '  %s --uninstall\n' "$0"
    printf '\n'
    printf 'To fully switch over later (after soak period):\n'
    printf '  1. Remove .claude/hooks/{register,heartbeat,complete,session-start,session-end}-agent.sh\n'
    printf '  2. Remove the PreToolUse[Agent] register-agent.sh entry from ~/.claude/settings.json\n'
    printf '  Keep: standing-rules.sh, bash-guards.sh, adhd-mode-monitor-enforcer.sh, consult-memory.sh\n'
fi

exit 0
