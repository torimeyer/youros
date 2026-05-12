#!/usr/bin/env bash
# Smoke tests for scripts/install-ostk-hooks.sh
#
# Verifies:
#   T1: --dry-run exits 0 and prints expected summary
#   T2: missing ostk exits 1 with a clear error message
#   T3: install adds all four lifecycle hooks to settings.json
#   T4: second install is idempotent (all hooks already-wired)
#   T5: --uninstall removes installed hooks cleanly

set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="${SCRIPT_DIR}/install-ostk-hooks.sh"
PASS=0
FAIL=0

pass() { printf 'PASS: %s\n' "$1"; PASS=$((PASS+1)); }
fail() { printf 'FAIL: %s\n' "$1"; FAIL=$((FAIL+1)); }

assert_exit() {
    local label="$1" actual="$2" expected="$3"
    if [ "${actual}" = "${expected}" ]; then
        pass "${label}"
    else
        fail "${label} — expected exit ${expected}, got ${actual}"
    fi
}

assert_contains() {
    local label="$1" haystack="$2" needle="$3"
    if printf '%s' "${haystack}" | grep -qF "${needle}"; then
        pass "${label}"
    else
        fail "${label} — expected '${needle}' in output"
        printf '  actual output:\n%s\n' "${haystack}" | head -20
    fi
}

assert_not_contains() {
    local label="$1" haystack="$2" needle="$3"
    if ! printf '%s' "${haystack}" | grep -qF "${needle}"; then
        pass "${label}"
    else
        fail "${label} — did not expect '${needle}' in output"
    fi
}

# Build a minimal settings.json that looks like a real Claude Code config.
make_settings() {
    local dir="$1"
    mkdir -p "${dir}/.claude"
    printf '%s\n' '{"permissions":{},"hooks":{}}' > "${dir}/.claude/settings.json"
}

# ---- T1: --dry-run exits 0 -----------------------------------------------

T1_DIR="$(mktemp -d)"
make_settings "${T1_DIR}"

T1_OUT=$(HOME="${T1_DIR}" bash "${INSTALLER}" --dry-run --yes 2>&1) || true
T1_RC=$?

assert_exit    "T1: --dry-run exits 0"               "${T1_RC}"   "0"
assert_contains "T1: --dry-run prints SessionStart"   "${T1_OUT}"  "SessionStart"
assert_contains "T1: --dry-run prints dry-run notice" "${T1_OUT}"  "dry-run"
assert_not_contains "T1: --dry-run writes nothing"    "${T1_OUT}"  "wired"

# settings.json must be unchanged (still minimal)
T1_HOOKS=$(python3 -c "
import json
d = json.load(open('${T1_DIR}/.claude/settings.json'))
print(json.dumps(d.get('hooks', {})))
" 2>/dev/null || echo "PARSE_ERR")
assert_contains "T1: settings.json untouched by dry-run" "${T1_HOOKS}" "{}"

rm -rf "${T1_DIR}"

# ---- T2: missing ostk exits 1 with clear error ---------------------------

T2_DIR="$(mktemp -d)"
make_settings "${T2_DIR}"

# Build a fake PATH that has no ostk
T2_STUB_DIR="${T2_DIR}/stubs"
mkdir -p "${T2_STUB_DIR}"

T2_OUT=$(HOME="${T2_DIR}" PATH="${T2_STUB_DIR}:/usr/bin:/bin" bash "${INSTALLER}" --yes 2>&1) || T2_RC=$?
T2_RC="${T2_RC:-0}"

assert_exit     "T2: missing ostk exits 1"            "${T2_RC}"   "1"
assert_contains "T2: error mentions ostk not found"   "${T2_OUT}"  "ostk not found"

rm -rf "${T2_DIR}"

# ---- T3: install wires all four hooks ------------------------------------

T3_DIR="$(mktemp -d)"
make_settings "${T3_DIR}"

# Stub ostk so we can install without the real binary needing to exist
T3_STUB_DIR="${T3_DIR}/stubs"
mkdir -p "${T3_STUB_DIR}"
cat >"${T3_STUB_DIR}/ostk" <<'SH'
#!/bin/bash
if [ "${1:-}" = "--version" ]; then echo "ostk 6.0.0-stub"; exit 0; fi
exit 0
SH
chmod +x "${T3_STUB_DIR}/ostk"

T3_OUT=$(HOME="${T3_DIR}" PATH="${T3_STUB_DIR}:${PATH}" bash "${INSTALLER}" --yes 2>&1)
T3_RC=$?

assert_exit     "T3: install exits 0"                    "${T3_RC}"  "0"
assert_contains "T3: reports SessionStart wired"         "${T3_OUT}" "SessionStart"
assert_contains "T3: reports SessionEnd wired"           "${T3_OUT}" "SessionEnd"
assert_contains "T3: reports PreToolUse[Agent] wired"    "${T3_OUT}" "PreToolUse[Agent]"
assert_contains "T3: reports PostToolUse[Agent] wired"   "${T3_OUT}" "PostToolUse[Agent]"

# Verify the JSON was actually written
T3_JSON=$(python3 -c "
import json
d = json.load(open('${T3_DIR}/.claude/settings.json'))
h = d.get('hooks', {})
cmds = []
for event in ('SessionStart','SessionEnd','PreToolUse','PostToolUse'):
    for entry in h.get(event, []):
        for hk in entry.get('hooks', []):
            cmds.append(hk.get('command',''))
print('\n'.join(cmds))
" 2>/dev/null)

assert_contains "T3: settings has ostk hook session-start"  "${T3_JSON}" "ostk hook session-start"
assert_contains "T3: settings has ostk hook session-end"    "${T3_JSON}" "ostk hook session-end"
assert_contains "T3: settings has ostk hook agent-start"    "${T3_JSON}" "ostk hook agent-start"
assert_contains "T3: settings has ostk hook agent-stop"     "${T3_JSON}" "ostk hook agent-stop"

# ---- T4: second install is idempotent ------------------------------------

T4_OUT=$(HOME="${T3_DIR}" PATH="${T3_STUB_DIR}:${PATH}" bash "${INSTALLER}" --yes 2>&1)
T4_RC=$?

assert_exit         "T4: idempotent install exits 0"          "${T4_RC}"  "0"
assert_not_contains "T4: second run does not say 'wired'"     "${T4_OUT}" "wired    "
assert_contains     "T4: second run says already wired"       "${T4_OUT}" "already"

# JSON should still have exactly four ostk lifecycle commands (no duplicates)
T4_COUNT=$(python3 -c "
import json
d = json.load(open('${T3_DIR}/.claude/settings.json'))
h = d.get('hooks', {})
cmds = []
for event in ('SessionStart','SessionEnd','PreToolUse','PostToolUse'):
    for entry in h.get(event, []):
        for hk in entry.get('hooks', []):
            c = hk.get('command','')
            if c.startswith('ostk hook '):
                cmds.append(c)
print(len(cmds))
" 2>/dev/null)
assert_exit "T4: exactly 4 ostk hook entries (no duplicates)" "${T4_COUNT}" "4"

# ---- T5: --uninstall removes hooks cleanly -------------------------------

T5_OUT=$(HOME="${T3_DIR}" PATH="${T3_STUB_DIR}:${PATH}" bash "${INSTALLER}" --uninstall --yes 2>&1)
T5_RC=$?

assert_exit     "T5: --uninstall exits 0"                     "${T5_RC}"  "0"
assert_contains "T5: uninstall reports removed events"         "${T5_OUT}" "removed"

# Verify no ostk lifecycle hooks remain
T5_COUNT=$(python3 -c "
import json
d = json.load(open('${T3_DIR}/.claude/settings.json'))
h = d.get('hooks', {})
count = 0
for event in ('SessionStart','SessionEnd','PreToolUse','PostToolUse'):
    for entry in h.get(event, []):
        for hk in entry.get('hooks', []):
            if hk.get('command','').startswith('ostk hook '):
                count += 1
print(count)
" 2>/dev/null)
assert_exit "T5: no ostk hook entries remain after uninstall" "${T5_COUNT}" "0"

rm -rf "${T3_DIR}"

# ---- Summary -------------------------------------------------------------

printf '\n'
printf 'Results: %d passed, %d failed\n' "${PASS}" "${FAIL}"
[ "${FAIL}" -eq 0 ]
