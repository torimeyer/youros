#!/usr/bin/env bash
# Tests for scripts/install-git-hooks.sh
# Verifies that the installer activates both pre-commit and post-commit hooks
# via core.hooksPath=.githooks.

set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="${SCRIPT_DIR}/install-git-hooks.sh"
PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

assert_eq() {
  local label="$1" actual="$2" expected="$3"
  if [ "${actual}" = "${expected}" ]; then
    pass "${label}"
  else
    fail "${label} — expected '${expected}', got '${actual}'"
  fi
}

assert_file_exists() {
  local label="$1" path="$2"
  if [ -f "${path}" ]; then
    pass "${label}"
  else
    fail "${label} — ${path} does not exist"
  fi
}

assert_executable() {
  local label="$1" path="$2"
  if [ -x "${path}" ]; then
    pass "${label}"
  else
    fail "${label} — ${path} is not executable"
  fi
}

assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  if echo "${haystack}" | grep -qF "${needle}"; then
    pass "${label}"
  else
    fail "${label} — expected '${needle}' in output"
  fi
}

assert_not_contains() {
  local label="$1" haystack="$2" needle="$3"
  if ! echo "${haystack}" | grep -qF "${needle}"; then
    pass "${label}"
  else
    fail "${label} — did not expect '${needle}' in output"
  fi
}

# ---- helpers ----------------------------------------------------------------

make_test_repo() {
  local dir
  dir="$(mktemp -d)"
  git -C "${dir}" init -q
  git -C "${dir}" config user.email "test@test.com"
  git -C "${dir}" config user.name "Test"
  mkdir -p "${dir}/.githooks"
  # Copy the real post-commit hook (the one we want auto-activated)
  cp "${SCRIPT_DIR}/../.githooks/post-commit" "${dir}/.githooks/post-commit"
  chmod +x "${dir}/.githooks/post-commit"
  # Copy the installer into the test repo so REPO_DIR resolves correctly
  mkdir -p "${dir}/scripts"
  cp "${INSTALLER}" "${dir}/scripts/install-git-hooks.sh"
  chmod +x "${dir}/scripts/install-git-hooks.sh"
  # Stub pre-commit-test-check.sh so the hook is runnable without real infra
  cat > "${dir}/scripts/pre-commit-test-check.sh" <<'SH'
#!/usr/bin/env bash
exit 0
SH
  chmod +x "${dir}/scripts/pre-commit-test-check.sh"
  echo "${dir}"
}

run_installer() {
  local repo="$1"; shift
  # Run the installer from inside the test repo so REPO_DIR resolves to it
  bash "${repo}/scripts/install-git-hooks.sh" "$@" 2>&1
}

# ---- test 1: fresh install sets core.hooksPath and writes pre-commit --------

T1="$(make_test_repo)"
run_installer "${T1}" > /dev/null

hooks_path="$(git -C "${T1}" config core.hooksPath 2>/dev/null || true)"
assert_eq  "T1: core.hooksPath=.githooks"            "${hooks_path}"                 ".githooks"
assert_file_exists "T1: .githooks/pre-commit exists"  "${T1}/.githooks/pre-commit"
assert_executable  "T1: .githooks/pre-commit executable" "${T1}/.githooks/pre-commit"
assert_file_exists "T1: .githooks/post-commit exists" "${T1}/.githooks/post-commit"
assert_executable  "T1: .githooks/post-commit executable" "${T1}/.githooks/post-commit"

# pre-commit must contain our marker
marker_check="$(grep -c 'myos-pre-commit-test-check v2' "${T1}/.githooks/pre-commit" || true)"
assert_eq "T1: pre-commit has v2 marker" "${marker_check}" "1"

# ---- test 2: --status reports INSTALLED after install -----------------------

T2="$(make_test_repo)"
run_installer "${T2}" > /dev/null
status_out="$(run_installer "${T2}" --status)"
assert_contains "T2: --status says INSTALLED"         "${status_out}" "INSTALLED"
assert_contains "T2: --status mentions post-commit"   "${status_out}" "post-commit"

# ---- test 3: idempotent — second install does not break anything -----------

T3="$(make_test_repo)"
run_installer "${T3}" > /dev/null
run_installer "${T3}" > /dev/null
hooks_path3="$(git -C "${T3}" config core.hooksPath 2>/dev/null || true)"
assert_eq "T3: idempotent install keeps core.hooksPath" "${hooks_path3}" ".githooks"
assert_executable "T3: pre-commit still executable after re-install" "${T3}/.githooks/pre-commit"

# ---- test 4: --uninstall removes hook and unsets config --------------------

T4="$(make_test_repo)"
run_installer "${T4}" > /dev/null
uninstall_out="$(run_installer "${T4}" --uninstall)"
assert_contains "T4: --uninstall mentions Unset"       "${uninstall_out}" "Unset core.hooksPath"

hooks_path4="$(git -C "${T4}" config core.hooksPath 2>/dev/null || true)"
assert_eq "T4: core.hooksPath unset after uninstall"   "${hooks_path4}" ""

# pre-commit file should be gone
if [ ! -f "${T4}/.githooks/pre-commit" ]; then
  pass "T4: .githooks/pre-commit removed after uninstall"
else
  fail "T4: .githooks/pre-commit still exists after uninstall"
fi

# post-commit (shipped in repo) must still exist
assert_file_exists "T4: post-commit untouched by uninstall" "${T4}/.githooks/post-commit"

# ---- test 5: --status reports NOT installed after uninstall ----------------

T5="$(make_test_repo)"
run_installer "${T5}" > /dev/null
run_installer "${T5}" --uninstall > /dev/null
status_out5="$(run_installer "${T5}" --status)"
assert_contains "T5: --status says NOT installed after uninstall" "${status_out5}" "NOT installed"

# ---- test 6: existing .git/hooks/pre-commit gets backed up -----------------

T6="$(make_test_repo)"
mkdir -p "${T6}/.git/hooks"
cat > "${T6}/.git/hooks/pre-commit" <<'SH'
#!/usr/bin/env bash
echo "user pre-commit"
SH
chmod +x "${T6}/.git/hooks/pre-commit"

install_out6="$(run_installer "${T6}")"
assert_contains "T6: installer mentions Preserved"        "${install_out6}" "Preserved"
assert_file_exists "T6: backup at .githooks/pre-commit.local" "${T6}/.githooks/pre-commit.local"
assert_executable  "T6: backup is executable"                 "${T6}/.githooks/pre-commit.local"

# The installed pre-commit must reference .local
assert_contains "T6: installed hook calls .local" \
  "$(cat "${T6}/.githooks/pre-commit")" "pre-commit.local"

# ---- summary ----------------------------------------------------------------

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[[ ${FAIL} -eq 0 ]]
