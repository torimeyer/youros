#!/usr/bin/env bash
# Tests for scripts/entityfile_drift_guard.sh (→908).
#
# Covers:
#   1. Signed, unmodified ENTITYFILE + matching OAE envelope → guard passes.
#   2. ENTITYFILE edited after signing → guard refuses.
#   3. Missing envelope → guard refuses.
#
# Usage: scripts/test_entityfile_drift_guard.sh

set -u

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GUARD="${REPO_DIR}/scripts/entityfile_drift_guard.sh"

if [ ! -x "${GUARD}" ]; then
  echo "FAIL: guard script not executable at ${GUARD}" >&2
  exit 2
fi

SCRATCH="$(mktemp -d -t entityfile-drift.XXXXXX)"
trap 'rm -rf "${SCRATCH}"' EXIT
mkdir -p "${SCRATCH}/.ostk"

# Copy real envelope + entityfile into scratch for tests 1 & 2.
cp "${REPO_DIR}/.ostk/ENTITYFILE"          "${SCRATCH}/.ostk/ENTITYFILE"
cp "${REPO_DIR}/.ostk/ENTITYFILE.oae.json" "${SCRATCH}/.ostk/ENTITYFILE.oae.json"

# Relocate guard's REPO_DIR via a wrapper script so we don't touch real state.
# The guard derives REPO_DIR from its own location; run a copy inside scratch.
mkdir -p "${SCRATCH}/scripts"
cp "${GUARD}" "${SCRATCH}/scripts/entityfile_drift_guard.sh"
chmod +x "${SCRATCH}/scripts/entityfile_drift_guard.sh"
GUARD_LOCAL="${SCRATCH}/scripts/entityfile_drift_guard.sh"

pass=0
fail=0

check() {
  local name="$1"; local want="$2"; local got="$3"
  if [ "${want}" = "${got}" ]; then
    echo "  ok: ${name}"
    pass=$((pass+1))
  else
    echo "  FAIL: ${name} (expected exit=${want}, got=${got})"
    fail=$((fail+1))
  fi
}

# --- test 1: unchanged ENTITYFILE + matching envelope → exit 0 ----------
echo "test 1: signed ENTITYFILE with matching OAE envelope"
"${GUARD_LOCAL}" --path "${SCRATCH}/.ostk/ENTITYFILE"
check "guard passes on unmodified signed file" 0 $?

# --- test 2: ENTITYFILE edited after signing → exit 1 -------------------
echo "test 2: drift. ENTITYFILE edited, envelope stale"
printf '\n# drifted %s\n' "$(date +%s)" >> "${SCRATCH}/.ostk/ENTITYFILE"
"${GUARD_LOCAL}" --path "${SCRATCH}/.ostk/ENTITYFILE" >/dev/null 2>&1
check "guard refuses when digest mismatches envelope" 1 $?

# --- test 3: no envelope at all → exit 1 --------------------------------
echo "test 3: no envelope present"
rm -f "${SCRATCH}/.ostk/ENTITYFILE.oae.json"
# restore file to a clean state so the only failure is missing envelope
cp "${REPO_DIR}/.ostk/ENTITYFILE" "${SCRATCH}/.ostk/ENTITYFILE"
"${GUARD_LOCAL}" --path "${SCRATCH}/.ostk/ENTITYFILE" >/dev/null 2>&1
check "guard refuses when no signature envelope exists" 1 $?

echo ""
echo "results: ${pass} passed, ${fail} failed"
[ "${fail}" -eq 0 ] || exit 1
exit 0
