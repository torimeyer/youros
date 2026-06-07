#!/bin/bash
# OPT-IN installer: adopt yourOS under the ostk machined kernel supervisor.
#
# DEFAULT BEHAVIOR IS UNCHANGED. Running this script is optional.
# Nothing here alters any existing hook, settings file, or running daemon.
# You opt in by running this script when you are ready.
#
# WHAT IT DOES:
#   Runs the ostk machined adoption ceremony in three steps:
#
#   Step 1: Issue a MachinedInstanceCert (if none exists).
#           The cert is the identity token for this machine's supervisor.
#           Command: ostk machined cert issue
#
#   Step 2: Start the machined supervisor in the background (if not running).
#           machined watches the kernel for project roots and manages their
#           supervised daemon lifecycle. It runs in the foreground, so this
#           script wraps it with nohup to keep it alive after the terminal
#           closes. Log goes to ~/.ostk/machined.log.
#           Command: nohup ostk machined start > ~/.ostk/machined.log 2>&1 &
#
#   Step 3: Print a summary: cert status, supervisor status, fleet view.
#
# WHAT IT DOES NOT DO:
#   - Does NOT modify /api/agents, kernel_fleet.py, or any yourOS API.
#     The kernel fleet reader from tier 1.1A already merges kernel rows;
#     this script just gives it richer data to merge.
#   - Does NOT delete the homegrown worktree reaper or any lifecycle script.
#     Reaper retirement is a separate opt-in step (1.1B) after a soak period.
#   - Does NOT touch ~/.claude/settings.json or .claude/hooks/.
#   - Does NOT touch ostk hook wiring (that is install-ostk-hooks.sh's job).
#
# WHEN TO RUN THIS:
#   - Before a multi-machine yourOS setup where machined coordinates per-project
#     daemon lifecycle across machines.
#   - When you want the kernel's supervised view to reflect what /api/agents
#     already shows (machined is the proper long-term alternative to the reaper,
#     per Scott).
#   - Any time after the yourOS reaper has been running long enough to trust that
#     machined is stable on this machine.
#
# ADOPTION IS REVERSIBLE:
#   To undo: ostk machined stop   (sends SIGTERM to the supervisor)
#
#   The MachinedInstanceCert written to ~/.ostk/ is harmless to leave in place
#   (it is just an identity file for this machine). To fully clean up:
#     ostk machined stop
#     rm -f ~/.ostk/machined.log ~/.ostk/machined.lock
#     # If ~/.ostk-init-registry.jsonl exists, remove the entry for this project.
#     # (That file may not exist yet — ostk →1698 wires it; safe to skip if absent.)
#
# USAGE:
#   scripts/adopt-youros-via-machined.sh [FLAGS]
#
# FLAGS:
#   --dry-run     Preview all steps without executing any of them.
#   -h, --help    Show this header and exit.
#
# EXIT CODES:
#   0   success (including dry-run and no-op cases)
#   1   ostk not on PATH
#   2   unexpected error during ceremony

set -u

SCRIPT_NAME="adopt-youros-via-machined"

# --- Flag parsing -----------------------------------------------------------

DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '2,57p' "$0"
            exit 0
            ;;
        *)
            printf '%s: unknown flag: %s\n' "$SCRIPT_NAME" "$1" >&2
            printf '%s: run with --help for usage.\n' "$SCRIPT_NAME" >&2
            exit 2
            ;;
    esac
done

# --- Check ostk is on PATH -------------------------------------------------

if ! command -v ostk >/dev/null 2>&1; then
    printf '%s: ERROR: ostk not found on PATH.\n' "$SCRIPT_NAME" >&2
    printf '%s: Install ostk first, then re-run this script.\n' "$SCRIPT_NAME" >&2
    printf '%s: (Expected at ~/.local/bin/ostk or similar.)\n' "$SCRIPT_NAME" >&2
    exit 1
fi

OSTK_VERSION=$(ostk --version 2>/dev/null | awk '{print $2}' || echo "unknown")
printf '%s: ostk %s found at %s\n' "$SCRIPT_NAME" "$OSTK_VERSION" "$(command -v ostk)"
printf '\n'

# --- Check current machined state (read-only) ------------------------------

MACHINED_STATUS=$(ostk machined status 2>&1)

MACHINED_RUNNING=0
if printf '%s\n' "$MACHINED_STATUS" | grep -q "machined: running"; then
    MACHINED_RUNNING=1
fi

CERT_PRESENT=0
if ! printf '%s\n' "$MACHINED_STATUS" | grep -q "cert.current: <none"; then
    CERT_PRESENT=1
fi

# --- Describe what will happen ---------------------------------------------

printf 'Adoption ceremony — three steps:\n'
printf '\n'

if [ $CERT_PRESENT -eq 1 ]; then
    printf '  Step 1 [skip] MachinedInstanceCert already issued.\n'
else
    printf '  Step 1 [run]  Issue MachinedInstanceCert\n'
    printf '                Command: ostk machined cert issue\n'
    printf '                Effect:  writes kernel identity token to ~/.ostk/\n'
fi
printf '\n'

if [ $MACHINED_RUNNING -eq 1 ]; then
    printf '  Step 2 [skip] machined supervisor is already running.\n'
else
    printf '  Step 2 [run]  Start machined supervisor (background)\n'
    printf '                Command: nohup ostk machined start > ~/.ostk/machined.log 2>&1 &\n'
    printf '                Effect:  kernel supervisor watches for adopted project roots\n'
fi
printf '\n'

printf '  Step 3 [read] Print status + fleet view (always runs, read-only).\n'
printf '\n'

printf 'Adoption is reversible: run `ostk machined stop` to shut down the supervisor.\n'
printf 'yourOS /api/agents and kernel_fleet.py are not changed by this script.\n'
printf 'The homegrown reaper keeps running alongside machined during the soak period.\n'
printf '\n'

if [ $DRY_RUN -eq 1 ]; then
    printf '%s: dry-run — no commands executed.\n' "$SCRIPT_NAME"
    exit 0
fi

# --- Confirmation prompt ---------------------------------------------------

if [ -t 0 ]; then
    printf 'Proceed with adoption? [y/N] '
    read -r answer
    case "$answer" in
        y|Y|yes|YES) ;;
        *)
            printf 'Aborted.\n'
            exit 0
            ;;
    esac
fi

printf '\n'

# --- Step 1: Issue MachinedInstanceCert (idempotent) -----------------------

if [ $CERT_PRESENT -eq 0 ]; then
    printf '%s: step 1 — issuing MachinedInstanceCert...\n' "$SCRIPT_NAME"
    if ! ostk machined cert issue; then
        RC=$?
        printf '%s: ERROR: cert issue failed (rc=%d).\n' "$SCRIPT_NAME" "$RC" >&2
        exit 2
    fi
    printf '%s: cert issued.\n' "$SCRIPT_NAME"
else
    printf '%s: step 1 — cert already present, skipping.\n' "$SCRIPT_NAME"
fi

printf '\n'

# --- Step 2: Start machined supervisor (idempotent) ------------------------

if [ $MACHINED_RUNNING -eq 0 ]; then
    MACHINED_LOG="$HOME/.ostk/machined.log"
    printf '%s: step 2 — starting machined supervisor (log: %s)...\n' "$SCRIPT_NAME" "$MACHINED_LOG"
    nohup ostk machined start > "$MACHINED_LOG" 2>&1 &
    MACHINED_PID=$!
    printf '%s: machined started (pid %d).\n' "$SCRIPT_NAME" "$MACHINED_PID"
    sleep 1
else
    printf '%s: step 2 — machined already running, skipping.\n' "$SCRIPT_NAME"
fi

printf '\n'

# --- Step 3: Summary (read-only) -------------------------------------------

printf '=== Adoption summary ===\n'
printf '\n'
ostk machined status
printf '\n'
printf 'Fleet view:\n'
ostk machined fleets
printf '\n'

printf '%s: done.\n' "$SCRIPT_NAME"
printf '\n'
printf 'To revert:  ostk machined stop\n'
printf 'Logs:       %s\n' "$HOME/.ostk/machined.log"
printf '\n'
printf 'When machined supervision has soaked in (a few days of clean operation),\n'
printf 'the homegrown reaper (scripts/worktree-reaper.sh) can be retired in favour\n'
printf 'of kernel-managed supervision. That is a separate opt-in step (1.1B).\n'

exit 0
