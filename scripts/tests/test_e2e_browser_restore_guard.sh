#!/usr/bin/env bash
# Test: e2e_browser.sh save-and-restore machinery cannot corrupt real settings.
#
# Regression guard for →2779. Three guarantees:
#   (a) A name containing special characters (double-quote, backslash) produces
#       syntactically valid JSON in the restore PATCH body.
#   (b) A polluted capture ('e2e-browser-os') triggers the refuse path: the
#       function warns, clears ORIGINAL_OS_NAME, sets OS_NAME_CAPTURE_FAILED=1,
#       and a subsequent restore makes no PATCH to the API.
#   (c) After a verified successful inline restore, a second call to
#       restore_original_os_name (e.g. from the EXIT trap) is a no-op.

set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$THIS_DIR/../e2e_browser.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

[ -f "$SCRIPT" ] || fail "e2e_browser.sh not found at $SCRIPT"

bash -n "$SCRIPT" || fail "e2e_browser.sh has a shell syntax error"
pass "e2e_browser.sh parses cleanly (bash -n)"

TMP_DIR=$(mktemp -d /tmp/test-e2e-restore-guard-XXXXXX)
trap 'rm -rf "$TMP_DIR"' EXIT

# Extract the functions under test
awk '/^restore_original_os_name\(\)/,/^\}/' "$SCRIPT" > "$TMP_DIR/restore_fn.sh"
awk '/^capture_original_os_name\(\)/,/^\}/' "$SCRIPT" > "$TMP_DIR/capture_fn.sh"

# ── Static checks ─────────────────────────────────────────────────────────────

RESTORE_BODY=$(awk '/^restore_original_os_name\(\)/,/^\}/' "$SCRIPT")
CAPTURE_BODY=$(awk '/^capture_original_os_name\(\)/,/^\}/' "$SCRIPT")

# idempotency flag present
echo "$RESTORE_BODY" | grep -q '_OS_NAME_RESTORE_DONE' \
    || fail "restore_original_os_name has no _OS_NAME_RESTORE_DONE idempotency flag"
pass "restore_original_os_name has an idempotency flag"

# JSON built via python3, not raw interpolation
echo "$RESTORE_BODY" | grep -q 'python3' \
    || fail "restore_original_os_name still uses raw string interpolation for the JSON body"
echo "$RESTORE_BODY" | grep -q 'json.dumps' \
    || fail "restore_original_os_name does not use json.dumps to build the body"
pass "restore_original_os_name builds the PATCH body via python3 json.dumps"

# Verification read present
echo "$RESTORE_BODY" | grep -q 'MISMATCH\|restored' \
    || fail "restore_original_os_name never verifies the restore succeeded"
pass "restore_original_os_name verifies the restore via a follow-up read"

# Pollution guard in capture
echo "$CAPTURE_BODY" | grep -q 'e2e-browser-os' \
    || fail "capture_original_os_name has no pollution guard for the test value"
pass "capture_original_os_name guards against the test value 'e2e-browser-os'"

# ── (a) Quote + backslash in OS name produces valid JSON ──────────────────────

# Driver: stubs curl so we can capture the -d body without needing a live API.
# PATCH call: logs the body to CALLS_LOG.
# GET call: returns JSON-encoded VALUE so the verification step sees success.
cat > "$TMP_DIR/driver_a.sh" << 'DRIVEREOF'
#!/usr/bin/env bash
set -u
VALUE="$1"; CALLS_LOG="$2"; FN_FILE="$3"
YELLOW=""; RED=""; NC=""
CURL_OPTS=""
API_BASE="http://stub.invalid"
_OS_NAME_RESTORE_DONE=0
OS_NAME_CAPTURE_FAILED=0

curl() {
    local is_patch=0 saw_d=0 body=""
    for arg in "$@"; do
        [ "$arg" = "PATCH" ] && is_patch=1
        if [ "$saw_d" = "1" ]; then body="$arg"; saw_d=0; fi
        [ "$arg" = "-d" ] && saw_d=1
    done
    if [ "$is_patch" = "1" ]; then
        printf 'PATCH_BODY:%s\n' "$body" >> "$CALLS_LOG"
    else
        # GET verification: return valid JSON so _OS_NAME_RESTORE_DONE=1 is set
        python3 -c "import json,sys; print(json.dumps({'os_name': sys.argv[1]}))" "$VALUE"
    fi
}

# shellcheck disable=SC1090
source "$FN_FILE"
ORIGINAL_OS_NAME="$VALUE"
restore_original_os_name
DRIVEREOF

CALLS="$TMP_DIR/curl-a.log"
: > "$CALLS"
TRICKY_NAME='my"os\name'   # double-quote and backslash
bash "$TMP_DIR/driver_a.sh" "$TRICKY_NAME" "$CALLS" "$TMP_DIR/restore_fn.sh" 2>/dev/null

PATCH_LINE=$(grep '^PATCH_BODY:' "$CALLS" | head -1 || true)
[ -n "$PATCH_LINE" ] || fail "(a) no PATCH body found in log"
PATCH_BODY="${PATCH_LINE#PATCH_BODY:}"

EXTRACTED=$(python3 -c "import sys,json; print(json.loads(sys.argv[1]).get('os_name',''))" "$PATCH_BODY" 2>/dev/null || true)
[ "$EXTRACTED" = "$TRICKY_NAME" ] \
    || fail "(a) PATCH body JSON round-trips incorrectly: body='$PATCH_BODY', got='$EXTRACTED', want='$TRICKY_NAME'"
pass "(a) quote+backslash in OS name produces valid JSON in the PATCH body"

# ── (b) Polluted capture refuses to restore ───────────────────────────────────

# Driver for capture: stubs curl to return the polluted value.
cat > "$TMP_DIR/driver_b_cap.sh" << 'DRIVEREOF'
#!/usr/bin/env bash
set -u
CALLS_LOG="$1"; FN_FILE="$2"
YELLOW=""; RED=""; NC=""
CURL_OPTS=""
API_BASE="http://stub.invalid"
ORIGINAL_OS_NAME=""
OS_NAME_CAPTURE_FAILED=0

curl() { echo '{"os_name": "e2e-browser-os"}'; }

# shellcheck disable=SC1090
source "$FN_FILE"
capture_original_os_name 2>"$CALLS_LOG"
printf '%s\n' "OS_NAME=$ORIGINAL_OS_NAME"
printf '%s\n' "FAILED=$OS_NAME_CAPTURE_FAILED"
DRIVEREOF

OUT="$TMP_DIR/cap_b.out"
bash "$TMP_DIR/driver_b_cap.sh" "$TMP_DIR/cap_b.warn" "$TMP_DIR/capture_fn.sh" >"$OUT" 2>/dev/null || true

# The function must have warned on stderr
grep -qi 'e2e-browser-os\|pollut\|leaked\|test value' "$TMP_DIR/cap_b.warn" 2>/dev/null \
    || grep -qi 'WARN' "$TMP_DIR/cap_b.warn" 2>/dev/null \
    || fail "(b) capture did not emit a warning when it saw the test value"
pass "(b) capture warns when it reads 'e2e-browser-os'"

grep -q 'OS_NAME=$' "$OUT" \
    || fail "(b) capture did not clear ORIGINAL_OS_NAME; got: $(grep OS_NAME= "$OUT")"
pass "(b) capture clears ORIGINAL_OS_NAME after pollution detection"

grep -q 'FAILED=1' "$OUT" \
    || fail "(b) capture did not set OS_NAME_CAPTURE_FAILED=1"
pass "(b) capture sets OS_NAME_CAPTURE_FAILED=1 on pollution"

# Now verify restore makes NO PATCH when called with the poisoned state
cat > "$TMP_DIR/driver_b_res.sh" << 'DRIVEREOF'
#!/usr/bin/env bash
set -u
CALLS_LOG="$1"; FN_FILE="$2"
YELLOW=""; RED=""; NC=""
CURL_OPTS=""
API_BASE="http://stub.invalid"
_OS_NAME_RESTORE_DONE=0
ORIGINAL_OS_NAME=""
OS_NAME_CAPTURE_FAILED=1

curl() { printf 'curl %s\n' "$*" >> "$CALLS_LOG"; }

# shellcheck disable=SC1090
source "$FN_FILE"
restore_original_os_name
DRIVEREOF

CALLS="$TMP_DIR/curl-b.log"
: > "$CALLS"
bash "$TMP_DIR/driver_b_res.sh" "$CALLS" "$TMP_DIR/restore_fn.sh" 2>/dev/null

if [ -s "$CALLS" ]; then
    fail "(b) restore made a curl call after polluted capture; log: $(cat "$CALLS")"
fi
pass "(b) restore makes no PATCH after polluted capture"

# ── (c) Second call after verified restore is a no-op ─────────────────────────

# Driver: call restore twice. The stub makes the first GET return matching JSON
# so _OS_NAME_RESTORE_DONE=1. The second call must make no additional PATCH.
cat > "$TMP_DIR/driver_c.sh" << 'DRIVEREOF'
#!/usr/bin/env bash
set -u
VALUE="$1"; CALLS_LOG="$2"; FN_FILE="$3"
YELLOW=""; RED=""; NC=""
CURL_OPTS=""
API_BASE="http://stub.invalid"
_OS_NAME_RESTORE_DONE=0
OS_NAME_CAPTURE_FAILED=0
PATCH_COUNT=0

curl() {
    local is_patch=0
    for arg in "$@"; do [ "$arg" = "PATCH" ] && is_patch=1; done
    if [ "$is_patch" = "1" ]; then
        PATCH_COUNT=$((PATCH_COUNT + 1))
        printf 'PATCH:%d\n' "$PATCH_COUNT" >> "$CALLS_LOG"
    else
        python3 -c "import json,sys; print(json.dumps({'os_name': sys.argv[1]}))" "$VALUE"
    fi
}

# shellcheck disable=SC1090
source "$FN_FILE"
ORIGINAL_OS_NAME="$VALUE"

restore_original_os_name  # first call — should PATCH and verify
restore_original_os_name  # second call — must be a no-op
printf 'DONE\n'
DRIVEREOF

CALLS="$TMP_DIR/curl-c.log"
: > "$CALLS"
bash "$TMP_DIR/driver_c.sh" "tori-real-os" "$CALLS" "$TMP_DIR/restore_fn.sh" 2>/dev/null

PATCH_COUNT=$(grep -c '^PATCH:' "$CALLS" 2>/dev/null || echo 0)
[ "$PATCH_COUNT" -eq 1 ] \
    || fail "(c) expected exactly 1 PATCH from two restore calls, got $PATCH_COUNT"
pass "(c) second restore call after a verified restore makes no additional PATCH"

echo ""
echo "All →2779 restore guard checks passed."
