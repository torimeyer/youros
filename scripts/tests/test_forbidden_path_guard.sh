#!/usr/bin/env bash
# Regression tests for scripts/forbidden-path-guard.sh.
#
# Verifies that the guard blocks staged session/working-state paths and
# allows normal source files and the explicitly allow-listed .claude/ paths.
#
# Run: scripts/tests/test_forbidden_path_guard.sh
# Exit 0 = all pass, 1 = at least one failure.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
GUARD="${REPO_DIR}/scripts/forbidden-path-guard.sh"

if [ ! -f "${GUARD}" ]; then
    echo "FAIL: ${GUARD} not found. Create it first."
    exit 1
fi

FAILED=0
pass() { echo "  ok: $1"; }
fail() { echo "FAIL: $1"; FAILED=1; }

SCRATCH=$(mktemp -d -t forbidden-path-guard-test.XXXXXX)
cleanup() { rm -rf "${SCRATCH}"; }
trap cleanup EXIT

setup_repo() {
    local dir="$1"
    mkdir -p "${dir}"
    git -C "${dir}" init -q
    git -C "${dir}" config user.email "test@test"
    git -C "${dir}" config user.name "Test"
    echo "init" > "${dir}/README.md"
    git -C "${dir}" add README.md
    git -C "${dir}" commit -q -m "initial" --no-verify
}

run_guard() {
    local repo="$1"
    REPO_DIR="${repo}" "${GUARD}" 2>&1
    echo "EXIT:$?"
}

# --- TEST 1: blocks docs/draft/ -------------------------------------------
REPO1="${SCRATCH}/repo1"
setup_repo "${REPO1}"
mkdir -p "${REPO1}/docs/draft"
echo "session data" > "${REPO1}/docs/draft/test.md"
git -C "${REPO1}" add docs/draft/test.md
R=$(run_guard "${REPO1}")
if echo "${R}" | grep -q "EXIT:1"; then
    pass "test1: docs/draft/ file blocked"
else
    fail "test1: docs/draft/ NOT blocked (got: ${R})"
fi

# --- TEST 2: blocks transcripts/ ------------------------------------------
REPO2="${SCRATCH}/repo2"
setup_repo "${REPO2}"
mkdir -p "${REPO2}/transcripts"
echo "session content" > "${REPO2}/transcripts/session.log"
git -C "${REPO2}" add transcripts/session.log
R=$(run_guard "${REPO2}")
if echo "${R}" | grep -q "EXIT:1"; then
    pass "test2: transcripts/ file blocked"
else
    fail "test2: transcripts/ NOT blocked (got: ${R})"
fi

# --- TEST 3: blocks .ostk/sessions/ ---------------------------------------
REPO3="${SCRATCH}/repo3"
setup_repo "${REPO3}"
mkdir -p "${REPO3}/.ostk/sessions"
echo "session state" > "${REPO3}/.ostk/sessions/abc123.json"
git -C "${REPO3}" add .ostk/sessions/abc123.json
R=$(run_guard "${REPO3}")
if echo "${R}" | grep -q "EXIT:1"; then
    pass "test3: .ostk/sessions/ file blocked"
else
    fail "test3: .ostk/sessions/ NOT blocked (got: ${R})"
fi

# --- TEST 4: blocks .claude/plans/ ----------------------------------------
REPO4="${SCRATCH}/repo4"
setup_repo "${REPO4}"
mkdir -p "${REPO4}/.claude/plans"
echo "plan content" > "${REPO4}/.claude/plans/my-plan.md"
git -C "${REPO4}" add .claude/plans/my-plan.md
R=$(run_guard "${REPO4}")
if echo "${R}" | grep -q "EXIT:1"; then
    pass "test4: .claude/plans/ file blocked"
else
    fail "test4: .claude/plans/ NOT blocked (got: ${R})"
fi

# --- TEST 5: blocks .claude/memory/ ---------------------------------------
REPO5="${SCRATCH}/repo5"
setup_repo "${REPO5}"
mkdir -p "${REPO5}/.claude/memory"
echo "memory data" > "${REPO5}/.claude/memory/user_profile.md"
git -C "${REPO5}" add .claude/memory/user_profile.md
R=$(run_guard "${REPO5}")
if echo "${R}" | grep -q "EXIT:1"; then
    pass "test5: .claude/memory/ file blocked"
else
    fail "test5: .claude/memory/ NOT blocked (got: ${R})"
fi

# --- TEST 6: blocks docs/spec/ --------------------------------------------
REPO6="${SCRATCH}/repo6"
setup_repo "${REPO6}"
mkdir -p "${REPO6}/docs/spec"
echo "spec content" > "${REPO6}/docs/spec/feature.md"
git -C "${REPO6}" add docs/spec/feature.md
R=$(run_guard "${REPO6}")
if echo "${R}" | grep -q "EXIT:1"; then
    pass "test6: docs/spec/ file blocked"
else
    fail "test6: docs/spec/ NOT blocked (got: ${R})"
fi

# --- TEST 7: ALLOWS api/ source file --------------------------------------
REPO7="${SCRATCH}/repo7"
setup_repo "${REPO7}"
mkdir -p "${REPO7}/api"
echo "print('hello')" > "${REPO7}/api/foo.py"
git -C "${REPO7}" add api/foo.py
R=$(run_guard "${REPO7}")
if echo "${R}" | grep -q "EXIT:0"; then
    pass "test7: api/foo.py allowed"
else
    fail "test7: api/foo.py was blocked (got: ${R})"
fi

# --- TEST 8: ALLOWS .claude/settings.json ---------------------------------
REPO8="${SCRATCH}/repo8"
setup_repo "${REPO8}"
mkdir -p "${REPO8}/.claude"
echo '{}' > "${REPO8}/.claude/settings.json"
git -C "${REPO8}" add .claude/settings.json
R=$(run_guard "${REPO8}")
if echo "${R}" | grep -q "EXIT:0"; then
    pass "test8: .claude/settings.json allowed"
else
    fail "test8: .claude/settings.json was blocked (got: ${R})"
fi

# --- TEST 9: ALLOWS .claude/hooks/ file -----------------------------------
REPO9="${SCRATCH}/repo9"
setup_repo "${REPO9}"
mkdir -p "${REPO9}/.claude/hooks"
echo "#!/bin/bash" > "${REPO9}/.claude/hooks/my-hook.sh"
git -C "${REPO9}" add .claude/hooks/my-hook.sh
R=$(run_guard "${REPO9}")
if echo "${R}" | grep -q "EXIT:0"; then
    pass "test9: .claude/hooks/ file allowed"
else
    fail "test9: .claude/hooks/ file was blocked (got: ${R})"
fi

# --- TEST 10: no staged files -> passes cleanly ---------------------------
REPO10="${SCRATCH}/repo10"
setup_repo "${REPO10}"
R=$(run_guard "${REPO10}")
if echo "${R}" | grep -q "EXIT:0"; then
    pass "test10: no staged files -> allowed"
else
    fail "test10: no staged files -> unexpected block (got: ${R})"
fi

echo ""
if [ "${FAILED}" -eq 0 ]; then
    echo "PASS (10/10 tests)"
    exit 0
else
    echo "FAIL (${FAILED} test(s) failed)"
    exit 1
fi
