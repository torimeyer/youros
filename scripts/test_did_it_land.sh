#!/usr/bin/env bash
# test_did_it_land.sh
#
# Seeded smoke test for did_it_land.sh.
#
# We create a throwaway git repo in a temp dir, stage a known diff, and run
# the probe against a fake agent name. Since the probe hits the live myOS
# API for agent metadata, we accept an "unknown" status in the output and
# assert only on the change-detection and outcome branches.
#
# Cases:
#   1. No changes               -> exit 1 (NOT LANDED)
#   2. Changes + passing test   -> exit 0 (LANDED)
#   3. Changes + failing test   -> exit 1 (NOT LANDED, tests red)
#   4. Changes + no test match  -> exit 2 (NO-TEST-COVERAGE)

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROBE="${SCRIPT_DIR}/did_it_land.sh"

if [[ ! -x "${PROBE}" ]]; then
  echo "probe script not found or not executable: ${PROBE}" >&2
  exit 3
fi

TMP="$(mktemp -d /tmp/did_it_land_test.XXXXXX)"
trap 'rm -rf "${TMP}"' EXIT

FAIL=0
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1" >&2; FAIL=1; }

# Helper: build a tiny repo with an api/ and api/.venv/bin/pytest stub.
seed_repo() {
  local dir="$1"
  mkdir -p "${dir}/api/tests" "${dir}/api/.venv/bin"
  cd "${dir}"
  git init -q
  git config user.email "t@t" && git config user.name "t"
  echo "def add(a, b): return a + b" > api/calc.py
  cat > api/tests/test_calc.py <<'PY'
from api.calc import add
def test_add():
    assert add(1, 2) == 3
PY
  touch api/tests/__init__.py api/__init__.py
  # pytest stub that always passes (honors --tb and -q and prints summary)
  cat > api/.venv/bin/pytest <<'SH'
#!/usr/bin/env bash
# stub pytest that pretends to run tests and prints a pytest-style summary.
# Exits 0 with "N passed" if STUB_MODE=pass (default), 1 with "N failed, M passed" if STUB_MODE=fail.
MODE="${STUB_MODE:-pass}"
if [[ "${MODE}" == "pass" ]]; then
  echo "================== 3 passed, 0 warnings in 0.01s =================="
  exit 0
else
  echo "FAILED api/tests/test_calc.py::test_add - AssertionError"
  echo "================== 1 failed, 2 passed, 0 warnings in 0.01s =================="
  exit 1
fi
SH
  chmod +x api/.venv/bin/pytest
  # Also gitignore the venv so untracked stub doesn't dirty the status.
  echo "api/.venv/" > .gitignore
  git add -A && git commit -q -m "seed"
}

# CASE 1: No changes -> exit 1
CASE1_DIR="${TMP}/case1"
seed_repo "${CASE1_DIR}"
# Run probe pointed at this repo. The probe hard-codes REPO_ROOT, so we override
# by writing a thin wrapper that sets REPO_ROOT via env patch is not supported.
# Instead, we rewrite the probe to honor MYOS_DID_IT_LAND_REPO if set.
# (Check below — if the probe doesn't support it, the test prints a hint and skips.)

if ! grep -q 'MYOS_DID_IT_LAND_REPO' "${PROBE}"; then
  echo "NOTE: probe does not yet honor MYOS_DID_IT_LAND_REPO. Enabling test-override in probe..." >&2
  exit 4
fi

run_probe() {
  local repo="$1"
  local agent="${2:-test-agent}"
  MYOS_DID_IT_LAND_REPO="${repo}" "${PROBE}" "${agent}"
}

# CASE 1 assertion
set +e
out1="$(run_probe "${CASE1_DIR}" 2>&1)"
ec1=$?
set -e
if [[ "${ec1}" -eq 1 ]] && echo "${out1}" | grep -q 'NOT LANDED'; then
  pass "case1: no changes -> exit 1, NOT LANDED"
else
  fail "case1 expected exit 1 + NOT LANDED, got exit ${ec1}"
  echo "${out1}" | head -10
fi

# CASE 2: Changes + tests pass -> exit 0
CASE2_DIR="${TMP}/case2"
seed_repo "${CASE2_DIR}"
cd "${CASE2_DIR}"
echo "def sub(a, b): return a - b" >> api/calc.py
set +e
out2="$(STUB_MODE=pass run_probe "${CASE2_DIR}" 2>&1)"
ec2=$?
set -e
if [[ "${ec2}" -eq 0 ]] && echo "${out2}" | grep -q 'LANDED' && echo "${out2}" | grep -q 'test result: PASS'; then
  pass "case2: changes + tests pass -> exit 0, LANDED"
else
  fail "case2 expected exit 0 + LANDED + PASS, got exit ${ec2}"
  echo "${out2}" | head -15
fi

# CASE 3: Changes + tests fail -> exit 1
CASE3_DIR="${TMP}/case3"
seed_repo "${CASE3_DIR}"
cd "${CASE3_DIR}"
echo "def broken(): return 1/0" >> api/calc.py
set +e
out3="$(STUB_MODE=fail run_probe "${CASE3_DIR}" 2>&1)"
ec3=$?
set -e
if [[ "${ec3}" -eq 1 ]] && echo "${out3}" | grep -q 'tests red'; then
  pass "case3: changes + tests fail -> exit 1, tests red"
else
  fail "case3 expected exit 1 + tests red, got exit ${ec3}"
  echo "${out3}" | head -15
fi

# CASE 4: Changes to file with no matching test -> exit 2 (NO-TEST-COVERAGE)
CASE4_DIR="${TMP}/case4"
seed_repo "${CASE4_DIR}"
cd "${CASE4_DIR}"
echo "def helper(): pass" > api/orphan.py
git add -N api/orphan.py
set +e
out4="$(STUB_MODE=pass run_probe "${CASE4_DIR}" 2>&1)"
ec4=$?
set -e
if [[ "${ec4}" -eq 2 ]] && echo "${out4}" | grep -q 'NO-TEST-COVERAGE'; then
  pass "case4: changes without matching tests -> exit 2"
else
  fail "case4 expected exit 2 + NO-TEST-COVERAGE, got exit ${ec4}"
  echo "${out4}" | head -15
fi

echo ""
if [[ "${FAIL}" -eq 0 ]]; then
  echo "all did_it_land cases passed"
  exit 0
else
  echo "one or more did_it_land cases failed"
  exit 1
fi
