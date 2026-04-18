#!/usr/bin/env bash
# test_myos_status.sh
#
# Seeded smoke test for status.sh.
#
# Uses MYOS_STATUS_FIXTURE to avoid hitting the live backend, and
# MYOS_STATUS_NOW to pin wall-clock for deterministic elapsed math.
#
# Cases:
#   1. Running agent with 0 lines, spawned 48s ago, must show elapsed
#      column and cold-start hint, not just "0 lines".
#   2. Running agent with 12 lines, spawned 2m03s ago, must show line
#      count in the smart field and a 2m03s elapsed column.
#   3. --count still returns just the number of running agents.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROBE="${SCRIPT_DIR}/status.sh"

if [[ ! -x "${PROBE}" ]]; then
  echo "status.sh not found or not executable: ${PROBE}" >&2
  exit 3
fi

TMP="$(mktemp -d /tmp/myos_status_test.XXXXXX)"
trap 'rm -rf "${TMP}"' EXIT

FAIL=0
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1" >&2; FAIL=1; }

# Fixture: two running agents plus a stopped one. "cold" has 0 transcript
# lines and was spawned 48 seconds before our pinned "now". "warm" has 12
# lines and was spawned 2 minutes 3 seconds before "now". "done" is not
# running and must not appear.
FIXTURE="${TMP}/agents.json"
cat > "${FIXTURE}" <<'JSON'
{
  "agents": [
    {
      "name": "cold-start-demo",
      "status": "running",
      "transcript_lines": 0,
      "spawned_at": "2026-04-16T04:00:12+00:00"
    },
    {
      "name": "warm-demo",
      "status": "running",
      "transcript_lines": 12,
      "spawned_at": "2026-04-16T03:58:57+00:00"
    },
    {
      "name": "done-demo",
      "status": "completed",
      "transcript_lines": 99,
      "spawned_at": "2026-04-16T03:00:00+00:00"
    }
  ]
}
JSON

# Pinned "now" = 48s after cold-start-demo's spawn, 2m03s after warm-demo's spawn.
PINNED_NOW="2026-04-16T04:01:00+00:00"

set +e
out="$(MYOS_STATUS_FIXTURE="${FIXTURE}" MYOS_STATUS_NOW="${PINNED_NOW}" "${PROBE}" 2>&1)"
ec=$?
set -e

if [[ "${ec}" -ne 0 ]]; then
  fail "status.sh exited ${ec}, expected 0"
  echo "${out}"
  exit 1
fi

# --- CASE 1: cold start row ---
cold_line="$(printf '%s\n' "${out}" | awk -F'\t' '$5=="cold-start-demo"')"
if [[ -z "${cold_line}" ]]; then
  fail "case1: cold-start-demo not in output"
  echo "${out}"
else
  # columns: status lines elapsed smart name
  col_status="$(printf '%s' "${cold_line}" | awk -F'\t' '{print $1}')"
  col_lines="$(printf '%s' "${cold_line}" | awk -F'\t' '{print $2}')"
  col_elapsed="$(printf '%s' "${cold_line}" | awk -F'\t' '{print $3}')"
  col_smart="$(printf '%s' "${cold_line}" | awk -F'\t' '{print $4}')"
  if [[ "${col_status}" == "running" \
        && "${col_lines}" == "0" \
        && "${col_elapsed}" == "48s" \
        && "${col_smart}" == "(elapsed:48s cold-start)" ]]; then
    pass "case1: cold start shows elapsed=48s and cold-start hint"
  else
    fail "case1: got status='${col_status}' lines='${col_lines}' elapsed='${col_elapsed}' smart='${col_smart}'"
  fi
fi

# --- CASE 2: warm row ---
warm_line="$(printf '%s\n' "${out}" | awk -F'\t' '$5=="warm-demo"')"
if [[ -z "${warm_line}" ]]; then
  fail "case2: warm-demo not in output"
  echo "${out}"
else
  col_lines="$(printf '%s' "${warm_line}" | awk -F'\t' '{print $2}')"
  col_elapsed="$(printf '%s' "${warm_line}" | awk -F'\t' '{print $3}')"
  col_smart="$(printf '%s' "${warm_line}" | awk -F'\t' '{print $4}')"
  if [[ "${col_lines}" == "12" \
        && "${col_elapsed}" == "2m03s" \
        && "${col_smart}" == "lines:12" ]]; then
    pass "case2: warm shows elapsed=2m03s and lines:12"
  else
    fail "case2: got lines='${col_lines}' elapsed='${col_elapsed}' smart='${col_smart}'"
  fi
fi

# --- CASE 3: stopped agent must not appear ---
if printf '%s\n' "${out}" | grep -q 'done-demo'; then
  fail "case3: stopped agent leaked into running list"
else
  pass "case3: stopped agent filtered out"
fi

# --- CASE 4: --count agrees ---
count_out="$(MYOS_STATUS_FIXTURE="${FIXTURE}" MYOS_STATUS_NOW="${PINNED_NOW}" "${PROBE}" --count)"
if [[ "${count_out}" == "2" ]]; then
  pass "case4: --count returns 2"
else
  fail "case4: --count returned '${count_out}', expected 2"
fi

# --- CASE 5: elapsed column header must be present in every running row ---
# Every running row must have five tab-separated columns.
rows="$(printf '%s\n' "${out}" | awk -F'\t' 'NF>0 {print NF}' | sort -u)"
if [[ "${rows}" == "5" ]]; then
  pass "case5: every row has 5 columns (status, lines, elapsed, smart, name)"
else
  fail "case5: column counts were '${rows}' (want exactly 5)"
  echo "${out}"
fi

echo ""
if [[ "${FAIL}" -eq 0 ]]; then
  echo "all myos_status cases passed"
  exit 0
else
  echo "one or more myos_status cases failed"
  exit 1
fi
