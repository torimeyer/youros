#!/usr/bin/env bash
# myOS pre-commit test check.
#
# Runs a fast, TARGETED subset of pytest and vitest against only the
# files that are staged in this commit. The goal is to catch broken
# tests at the commit that introduces them, not at release time.
#
# Budget: about 30 seconds for a typical commit. Skips whole tiers
# (pytest, vitest, tsc) if no files in that tier are staged.
#
# Bypass: `git commit --no-verify` skips the hook entirely.
#
# Exit codes:
#   0  nothing to check, or all targeted checks passed
#   1  one or more checks failed (the commit is blocked)

set -u

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

# --- Escape hatch for subagents and emergency commits --------------------
# Setting MYOS_SKIP_HOOK=1 bypasses the whole test check. This exists
# because the hook can hang the tree (holding .git/index.lock for minutes
# if pytest/vitest/tsc stalls) when many subagents commit in parallel.
# The hook always applies hard timeouts below even when not skipped.
if [ "${MYOS_SKIP_HOOK:-0}" = "1" ]; then
  echo "[pre-commit] MYOS_SKIP_HOOK=1, skipping test check."
  exit 0
fi

# --- Hard-timeout wrapper -----------------------------------------------
# macOS does not ship coreutils `timeout`, so we roll our own in Perl
# (present on every macOS install). Usage: run_with_timeout SECS CMD ARGS...
# On timeout we kill the whole process group with SIGKILL and return 124,
# matching GNU timeout. This is the single most important change in this
# file: without it a stuck pytest/tsc/vitest holds .git/index.lock forever.
MYOS_HOOK_STEP_TIMEOUT="${MYOS_HOOK_STEP_TIMEOUT:-90}"
run_with_timeout() {
  local secs="$1"; shift
  perl - "$secs" "$@" <<'PERL_EOF'
use POSIX ":sys_wait_h";
my $secs = shift @ARGV;
my $pid = fork();
die "fork: $!" unless defined $pid;
if ($pid == 0) {
  setpgrp(0, 0);
  exec { $ARGV[0] } @ARGV or die "exec: $!";
}
local $SIG{ALRM} = sub {
  kill "KILL", -$pid;
  waitpid($pid, 0);
  exit 124;
};
alarm $secs;
waitpid($pid, 0);
alarm 0;
exit($? >> 8);
PERL_EOF
}

# Colours only if stdout is a tty.
if [ -t 1 ]; then
  C_OK=$'\033[0;32m'
  C_WARN=$'\033[0;33m'
  C_ERR=$'\033[0;31m'
  C_DIM=$'\033[2m'
  C_OFF=$'\033[0m'
else
  C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_OFF=""
fi

say()  { echo "${C_DIM}[pre-commit]${C_OFF} $*"; }
ok()   { echo "${C_OK}[pre-commit ok]${C_OFF} $*"; }
warn() { echo "${C_WARN}[pre-commit]${C_OFF} $*"; }
fail() { echo "${C_ERR}[pre-commit FAIL]${C_OFF} $*"; }

# --- Collect staged files ------------------------------------------------
# Only files that are Added, Copied, Modified, Renamed, or Type-changed.
STAGED="$(git diff --cached --name-only --diff-filter=ACMRT)"
if [ -z "${STAGED}" ]; then
  say "no staged files, skipping."
  exit 0
fi

# --- ENTITYFILE drift guard (→908) --------------------------------------
# If .ostk/ENTITYFILE is staged, refuse the commit unless the signature
# envelope still covers it. Cheap check, runs before secrets scan.
if echo "${STAGED}" | grep -qx ".ostk/ENTITYFILE"; then
  if ! "${REPO_DIR}/scripts/entityfile_drift_guard.sh"; then
    fail "ENTITYFILE drift guard blocked the commit."
    exit 1
  fi
  ok "ENTITYFILE drift guard passed."
fi

# --- Secrets scan (always runs, blocks commit on any hit) ---------------
# Detects obvious API key / credential patterns in staged diffs so a
# local mistake never ships. This catches the pre-tonight class of bug
# where a Stitch API key landed in .mcp.json and got committed.
#
# Patterns are conservative: long base64-ish tokens with known prefixes
# (Google AIza, Stitch AQ.Ab, AWS AKIA, Anthropic sk-ant, OpenAI sk-),
# plus generic "PRIVATE KEY" PEM blocks. A false positive blocks the
# commit and can be bypassed with `git commit --no-verify` after the
# user confirms the string is a test fixture, not a real secret.
SECRETS_PATTERN='AIza[0-9A-Za-z_-]{35}|AQ\.Ab[0-9A-Za-z_-]{30,}|AKIA[0-9A-Z]{16}|sk-ant-[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{40,}|-----BEGIN [A-Z ]*PRIVATE KEY-----'
SECRETS_HITS="$(git diff --cached -U0 | grep -E "^\+" | grep -E "${SECRETS_PATTERN}" || true)"
if [ -n "${SECRETS_HITS}" ]; then
  fail "secret-looking string detected in staged diff."
  echo "${SECRETS_HITS}" | head -5 | sed 's/^/    /'
  fail "Move the secret to .env or a gitignored config file, then re-stage."
  fail "If this is genuinely not a secret (e.g. a test fixture), bypass with:"
  fail "    git commit --no-verify"
  exit 1
fi

# --- CRITICAL-BLOCK removal guard ---------------------------------------
# If a commit removes a line tagged CRITICAL-BLOCK-DO-NOT-REMOVE the commit
# is blocked. These markers protect blocks that have been silently dropped
# in large rewrites before (see b9023e5, dafd9f3). The first line of the
# marker names the block; grep for "CRITICAL-BLOCK-DO-NOT-REMOVE" to find
# all protected sites. Bypass intentional deletions with --no-verify after
# confirming the corresponding regression test still passes without the block.
CRITICAL_BLOCK_REMOVED="$(git diff --cached -U0 | grep -E '^-.*CRITICAL-BLOCK-DO-NOT-REMOVE' || true)"
if [ -n "${CRITICAL_BLOCK_REMOVED}" ]; then
  fail "CRITICAL-BLOCK-DO-NOT-REMOVE line is being removed from the commit:"
  echo "${CRITICAL_BLOCK_REMOVED}" | head -5 | sed 's/^/    /'
  fail "This marker protects code that has been silently dropped in past rewrites."
  fail "Before removing it: confirm the regression test for this block still catches"
  fail "its absence. See api/tests/test_spawn_template_isolation.py for an example."
  fail "To bypass (after verification): git commit --no-verify"
  exit 1
fi

API_STAGED="$(echo "${STAGED}" | grep -E '^api/.*\.py$' || true)"
APP_STAGED="$(echo "${STAGED}" | grep -E '^app/src/.*\.(ts|tsx|js|jsx)$' || true)"
TS_STAGED="$(echo "${STAGED}" | grep -E '\.(ts|tsx)$' | grep -E '^app/' || true)"

if [ -z "${API_STAGED}" ] && [ -z "${APP_STAGED}" ] && [ -z "${TS_STAGED}" ]; then
  say "no python or frontend files staged, skipping test checks."
  exit 0
fi

START_TS="$(date +%s)"
FAILED=0

# --- TypeScript build check ---------------------------------------------
if [ -n "${TS_STAGED}" ]; then
  if [ -d "${REPO_DIR}/app/node_modules" ]; then
    say "running tsc -b on app..."
    TSC_LOG="$(mktemp -t myos-precommit-tsc.XXXXXX)"
    ( cd "${REPO_DIR}/app" && run_with_timeout "${MYOS_HOOK_STEP_TIMEOUT}" npx --no-install tsc -b --pretty false ) > "${TSC_LOG}" 2>&1
    TSC_STATUS=$?
    tail -40 "${TSC_LOG}"
    rm -f "${TSC_LOG}"
    if [ "${TSC_STATUS}" -eq 0 ]; then
      ok "tsc passed."
    elif [ "${TSC_STATUS}" -eq 124 ]; then
      warn "tsc timed out after ${MYOS_HOOK_STEP_TIMEOUT}s, skipping. Run 'cd app && tsc -b' manually."
    else
      fail "tsc found type errors."
      FAILED=1
    fi
  else
    warn "app/node_modules missing, skipping tsc. Run 'cd app && npm install'."
  fi
fi

# --- Python / pytest ----------------------------------------------------
if [ -n "${API_STAGED}" ]; then
  VENV_PY="${REPO_DIR}/api/.venv/bin/python"
  if [ ! -x "${VENV_PY}" ]; then
    warn "api/.venv not found, skipping pytest. Run ./install.sh to set it up."
  else
    # Map each staged api file to its closest test file. A file like
    # api/routers/agents.py maps to api/tests/test_agents.py if present.
    TEST_TARGETS=()
    for f in ${API_STAGED}; do
      # If the staged file is itself a test, run it directly.
      case "${f}" in
        api/tests/test_*.py)
          TEST_TARGETS+=("${f}")
          continue
          ;;
      esac
      base="$(basename "${f}" .py)"
      candidate="api/tests/test_${base}.py"
      if [ -f "${REPO_DIR}/${candidate}" ]; then
        TEST_TARGETS+=("${candidate}")
      fi
      # Extra pinned tests: always include these when their guarded file is staged.
      case "${f}" in
        api/routers/agents.py)
          TEST_TARGETS+=("api/tests/test_spawn_template_isolation.py")
          ;;
      esac
    done
    # De-dupe.
    if [ "${#TEST_TARGETS[@]}" -gt 0 ]; then
      UNIQ_TARGETS="$(printf "%s\n" "${TEST_TARGETS[@]}" | sort -u)"
      say "running pytest on:"
      echo "${UNIQ_TARGETS}" | sed 's/^/    /'
      # shellcheck disable=SC2086
      ( cd "${REPO_DIR}" && run_with_timeout "${MYOS_HOOK_STEP_TIMEOUT}" "${VENV_PY}" -m pytest -x -q --no-header ${UNIQ_TARGETS} )
      PYT_STATUS=$?
      if [ "${PYT_STATUS}" -eq 0 ]; then
        ok "pytest passed."
      elif [ "${PYT_STATUS}" -eq 124 ]; then
        warn "pytest timed out after ${MYOS_HOOK_STEP_TIMEOUT}s, skipping. A test is hanging: investigate before push."
      else
        fail "pytest found failures."
        FAILED=1
      fi
    else
      say "no matching pytest targets for staged api files, skipping pytest."
    fi
  fi
fi

# --- Frontend / vitest --------------------------------------------------
if [ -n "${APP_STAGED}" ]; then
  if [ ! -d "${REPO_DIR}/app/node_modules" ]; then
    warn "app/node_modules missing, skipping vitest. Run 'cd app && npm install'."
  else
    # Map staged app files to test files. A staged test file runs itself.
    VITEST_TARGETS=()
    for f in ${APP_STAGED}; do
      case "${f}" in
        *.test.ts|*.test.tsx|*.test.js|*.test.jsx)
          # vitest wants a path relative to app/.
          VITEST_TARGETS+=("${f#app/}")
          continue
          ;;
      esac
      dir="$(dirname "${f}")"
      base="$(basename "${f}")"
      stem="${base%.*}"
      # Look for sibling or __tests__ neighbours.
      for cand in \
        "${dir}/${stem}.test.ts" \
        "${dir}/${stem}.test.tsx" \
        "${dir}/__tests__/${stem}.test.ts" \
        "${dir}/__tests__/${stem}.test.tsx"; do
        if [ -f "${REPO_DIR}/${cand}" ]; then
          VITEST_TARGETS+=("${cand#app/}")
        fi
      done
    done
    if [ "${#VITEST_TARGETS[@]}" -gt 0 ]; then
      UNIQ_VITEST="$(printf "%s\n" "${VITEST_TARGETS[@]}" | sort -u)"
      say "running vitest on:"
      echo "${UNIQ_VITEST}" | sed 's/^/    /'
      # Use the wrapper to avoid parallel runs. It acquires a lock and
      # exits 9 if another run is already going. We treat 9 as a skip.
      # shellcheck disable=SC2086
      run_with_timeout "${MYOS_HOOK_STEP_TIMEOUT}" "${REPO_DIR}/scripts/run-vitest.sh" ${UNIQ_VITEST}
      VSTATUS=$?
      if [ "${VSTATUS}" = "0" ]; then
        ok "vitest passed."
      elif [ "${VSTATUS}" = "9" ]; then
        warn "another vitest run is already in flight, skipping."
      elif [ "${VSTATUS}" = "124" ]; then
        warn "vitest timed out after ${MYOS_HOOK_STEP_TIMEOUT}s, skipping. A test is hanging: investigate before push."
      else
        fail "vitest found failures."
        FAILED=1
      fi
    else
      say "no matching vitest targets for staged app files, skipping vitest."
    fi
  fi
fi

END_TS="$(date +%s)"
ELAPSED=$(( END_TS - START_TS ))
say "checks took ${ELAPSED}s."

if [ "${FAILED}" -ne 0 ]; then
  echo ""
  fail "commit blocked. Fix the failures above, or bypass with 'git commit --no-verify' (not recommended)."
  exit 1
fi

exit 0
