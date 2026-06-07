#!/usr/bin/env bash
# yourOS worktree pre-merge gate.
#
# Runs the relevant test suite(s) against a subagent's worktree diff
# BEFORE the branch is rolled up to main. Writes a status file at
# .ostk/worktree_status/<branch>.json describing whether the worktree
# is "ready" or "parked" and why.
#
# This gate is advisory: it never merges, never resets, never
# force-pushes. It only writes a status file and exits 0 (ready) or
# 1 (parked).
#
# Usage:
#   scripts/worktree-gate.sh <worktree-path>
#
# See docs/spawn_premerge_gate.md for the design note.

set -u

if [ $# -lt 1 ]; then
  echo "usage: $0 <worktree-path>" >&2
  exit 2
fi

WT="$1"
if [ ! -d "${WT}" ]; then
  echo "[gate] not a directory: ${WT}" >&2
  exit 2
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WT="$(cd "${WT}" && pwd)"

if [ -t 1 ]; then
  C_OK=$'\033[0;32m'; C_ERR=$'\033[0;31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_OK=""; C_ERR=""; C_DIM=""; C_OFF=""
fi
say()  { echo "${C_DIM}[gate]${C_OFF} $*"; }
ok()   { echo "${C_OK}[gate ready]${C_OFF} $*"; }
fail() { echo "${C_ERR}[gate parked]${C_OFF} $*"; }

# --- Resolve branch ------------------------------------------------------
if BRANCH="$(git -C "${WT}" rev-parse --abbrev-ref HEAD 2>/dev/null)"; then
  :
else
  BRANCH=""
fi
if [ -z "${BRANCH}" ] || [ "${BRANCH}" = "HEAD" ]; then
  echo "[gate] cannot resolve branch for ${WT}" >&2
  exit 2
fi

# --- Resolve merge base --------------------------------------------------
BASE="$(git -C "${WT}" merge-base HEAD main 2>/dev/null || true)"
if [ -z "${BASE}" ]; then
  # Fall back to whole tree vs. empty tree.
  BASE="$(git -C "${WT}" hash-object -t tree /dev/null || true)"
fi

CHANGED="$(git -C "${WT}" diff --name-only "${BASE}" 2>/dev/null || true)"
say "branch=${BRANCH}"
say "changed files:"
if [ -n "${CHANGED}" ]; then
  echo "${CHANGED}" | sed 's/^/    /'
else
  say "  (none)"
fi

# --- Infer suite ---------------------------------------------------------
PY="${REPO_DIR}/api/.venv/bin/python"
if [ ! -x "${PY}" ]; then
  PY="python3"
fi

SUITE="$(
  printf '%s\n' "${CHANGED}" | "${PY}" -c '
import sys
sys.path.insert(0, "'"${REPO_DIR}"'/scripts")
from lib.worktree_suite import infer_suite
print(infer_suite(sys.stdin.read().splitlines()))
'
)"
say "suite=${SUITE}"

FAILED_TESTS_FILE="$(mktemp -t youros-gate-fail.XXXXXX)"
trap 'rm -f "${FAILED_TESTS_FILE}"' EXIT
: > "${FAILED_TESTS_FILE}"

RESULT="ready"

run_backend() {
  local venv="${WT}/api/.venv/bin/python"
  if [ ! -x "${venv}" ]; then
    venv="${REPO_DIR}/api/.venv/bin/python"
  fi
  if [ ! -x "${venv}" ]; then
    say "no api venv found, skipping backend suite (treating as ready)."
    return 0
  fi
  say "pytest in ${WT} ..."
  if ( cd "${WT}" && "${venv}" -m pytest -x -q --no-header --timeout=120 api/tests ) 2>&1 | tee /tmp/youros-gate-pytest.log; then
    return 0
  fi
  grep -E '^FAILED ' /tmp/youros-gate-pytest.log | awk '{print $2}' >> "${FAILED_TESTS_FILE}" || true
  return 1
}

run_frontend() {
  if [ ! -d "${WT}/app/node_modules" ]; then
    say "no app/node_modules in worktree, skipping frontend suite (treating as ready)."
    return 0
  fi
  say "vitest in ${WT} ..."
  if ( cd "${WT}/app" && npx --no-install vitest run --reporter=default ) 2>&1 | tee /tmp/youros-gate-vitest.log; then
    return 0
  fi
  grep -E '(FAIL|×)' /tmp/youros-gate-vitest.log | head -20 >> "${FAILED_TESTS_FILE}" || true
  return 1
}

case "${SUITE}" in
  backend) run_backend || RESULT="parked" ;;
  frontend) run_frontend || RESULT="parked" ;;
  both)
    run_backend || RESULT="parked"
    run_frontend || RESULT="parked"
    ;;
  none) say "no code changes, skipping tests." ;;
  *) echo "[gate] bad suite: ${SUITE}" >&2; exit 2 ;;
esac

# --- Write status file ---------------------------------------------------
FAIL_JSON=""
if [ -s "${FAILED_TESTS_FILE}" ]; then
  FAIL_JSON="$(sed 's/"/\\"/g' "${FAILED_TESTS_FILE}" | awk 'BEGIN{first=1} NF{if(!first)printf ","; printf "\""$0"\""; first=0}')"
fi

"${PY}" - "${REPO_DIR}" "${BRANCH}" "${WT}" "${RESULT}" "${SUITE}" "${FAIL_JSON}" <<'PYEOF'
import json, sys
sys.path.insert(0, sys.argv[1] + "/scripts")
from lib.worktree_suite import write_status

repo, branch, path, status, suite, fail_json = sys.argv[1:7]
failing = []
if fail_json:
    failing = [t for t in ("[" + fail_json + "]" and json.loads("[" + fail_json + "]")) if t]
out = write_status(
    repo_root=repo,
    branch=branch,
    path=path,
    status=status,
    suite=suite,
    failing_tests=failing,
)
print(f"[gate] wrote {out}")
PYEOF

if [ "${RESULT}" = "ready" ]; then
  ok "${BRANCH} (${SUITE})"
  exit 0
else
  fail "${BRANCH} (${SUITE}) - failing tests recorded in status file"
  exit 1
fi
