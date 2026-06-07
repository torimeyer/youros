#!/bin/bash
# Toggle ADHD mode for the standing-rules cadence enforcement.
#
# When ADHD mode is on, the cadence target injected into every user turn
# drops from 60s to 30s, and the model sees an OVERDUE warning faster.
#
# Usage:
#   scripts/adhd-mode.sh on      # touch ~/.youros/.adhd_mode
#   scripts/adhd-mode.sh off     # remove ~/.youros/.adhd_mode
#   scripts/adhd-mode.sh status  # print current state

set -uo pipefail

ADHD_FILE="${HOME}/.youros/.adhd_mode"

case "${1:-status}" in
    on)
        mkdir -p "$(dirname "$ADHD_FILE")"
        touch "$ADHD_FILE"
        echo "ADHD mode ON — cadence target: 30s"
        ;;
    off)
        rm -f "$ADHD_FILE"
        echo "ADHD mode OFF — cadence target: 60s"
        ;;
    status)
        if [ -f "$ADHD_FILE" ]; then
            echo "ADHD mode: ON (target 30s)"
        else
            echo "ADHD mode: OFF (target 60s)"
        fi
        ;;
    *)
        echo "Usage: $0 on|off|status" >&2
        exit 1
        ;;
esac
