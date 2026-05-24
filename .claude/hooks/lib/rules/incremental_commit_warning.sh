#!/usr/bin/env bash
# Rule: incremental_commit_warning
# Replaces: incremental-commit.sh
# Called from: prompt-header.sh (UserPromptSubmit)
# Assumes: load-rule.sh and log-fire.sh already sourced by caller.

_incremental_commit_warning_check() {
  local max_files
  max_files=$(rule_param "incremental_commit_warning.max_uncommitted_files" "5")

  local STATUS_OUT
  if [ "${MYOS_GIT_STATUS_FIXTURE+x}" = "x" ]; then
    STATUS_OUT="$MYOS_GIT_STATUS_FIXTURE"
  else
    STATUS_OUT=$(git status --short 2>/dev/null || echo "")
  fi

  local UNCOMMITTED
  UNCOMMITTED=$(echo "$STATUS_OUT" | awk 'NF{n++} END{print n+0}')

  if [ "$UNCOMMITTED" -ge "${max_files:-5}" ]; then
    log_rule_fire "incremental_commit_warning" "UserPromptSubmit" "block" "${UNCOMMITTED} uncommitted files"
    echo ""
    echo "INCREMENTAL COMMIT REMINDER (non-blocking -- rule 3 from 2026-04-27 retro):"
    echo "  ${UNCOMMITTED} uncommitted changes detected. Commit verified wins before stacking more."
    echo "  Small commits enable easy rollback and reveal which change fixed the issue."
  else
    log_rule_fire "incremental_commit_warning" "UserPromptSubmit" "allow" "${UNCOMMITTED} uncommitted files below threshold"
  fi
  return 0
}
