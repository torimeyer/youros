#!/bin/bash
# UserPromptSubmit: if 5+ uncommitted files detected, surface an incremental-commit reminder.
# Non-blocking (exit 0). Rule 3 from 2026-04-27 retro.

if [ "${MYOS_GIT_STATUS_FIXTURE+x}" = "x" ]; then
  STATUS_OUT="$MYOS_GIT_STATUS_FIXTURE"
else
  STATUS_OUT=$(git status --short 2>/dev/null || echo "")
fi

UNCOMMITTED=$(echo "$STATUS_OUT" | awk 'NF{n++} END{print n+0}')

if [ "$UNCOMMITTED" -ge 5 ]; then
  echo ""
  echo "INCREMENTAL COMMIT REMINDER (non-blocking -- rule 3 from 2026-04-27 retro):"
  echo "  ${UNCOMMITTED} uncommitted changes detected. Commit verified wins before stacking more."
  echo "  Small commits enable easy rollback and reveal which change fixed the issue."
fi
exit 0
