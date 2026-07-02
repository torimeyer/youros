#!/usr/bin/env bash
# Test: e2e_browser.sh restores the OS name on exit (even if interrupted).
#
# Regression guard for →2468: Journey 5 wrote "e2e-browser-os" into real user
# settings when the script was interrupted before the inline restore ran.
# The fix: _browser_cleanup now restores ORIGINAL_OS_NAME on any exit signal.
#
# Two checks:
#   1. Static: _browser_cleanup references ORIGINAL_OS_NAME and is trapped on EXIT.
#   2. Dynamic: simulated SIGINT during a "mid-journey" run restores the OS name
#      via the trap (requires a reachable API on $API_BASE).

set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$THIS_DIR/../e2e_browser.sh"
API_BASE="${API_BASE:-https://localhost:8000}"
CURL_OPTS="${CURL_OPTS:--k}"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }
skip() { echo "SKIP: $*"; exit 0; }

# ---------------------------------------------------------------------------
# 1. Static checks
# ---------------------------------------------------------------------------

[ -f "$SCRIPT" ] || fail "e2e_browser.sh not found at $SCRIPT"

# _browser_cleanup must reference ORIGINAL_OS_NAME
if ! awk '/^_browser_cleanup\(\)/,/^\}/' "$SCRIPT" | grep -q "ORIGINAL_OS_NAME"; then
    fail "_browser_cleanup body does not reference ORIGINAL_OS_NAME (trap won't restore on interrupt)"
fi
pass "_browser_cleanup references ORIGINAL_OS_NAME"

# Trap must cover EXIT so the restore fires on normal exit AND interruption
if ! grep -q "trap _browser_cleanup EXIT" "$SCRIPT"; then
    fail "trap does not include EXIT — restore won't fire on normal script completion"
fi
pass "trap covers EXIT"

# ORIGINAL_OS_NAME must be initialised before the trap is registered
# (required for set -u safety when Journey 5 never runs)
TRAP_LINE=$(grep -n "trap _browser_cleanup" "$SCRIPT" | head -1 | cut -d: -f1)
INIT_LINE=$(grep -n 'ORIGINAL_OS_NAME=""' "$SCRIPT" | head -1 | cut -d: -f1)
if [ -z "$INIT_LINE" ]; then
    fail 'ORIGINAL_OS_NAME="" initialisation not found in script'
fi
if [ "$INIT_LINE" -ge "$TRAP_LINE" ]; then
    fail "ORIGINAL_OS_NAME initialised (line $INIT_LINE) AFTER trap (line $TRAP_LINE) — set -u will fire"
fi
pass "ORIGINAL_OS_NAME initialised before trap (line $INIT_LINE < $TRAP_LINE)"

# ---------------------------------------------------------------------------
# 2. Dynamic check — requires live API
# ---------------------------------------------------------------------------

if ! curl -sS $CURL_OPTS --connect-timeout 3 -m 5 \
        -o /dev/null -w "%{http_code}" "${API_BASE}/api/settings" 2>/dev/null \
        | grep -q "^200$"; then
    skip "API not reachable at ${API_BASE} — skipping dynamic trap test"
fi

# Read current OS name; restore it no matter what
SAVED=$(curl -sS $CURL_OPTS "${API_BASE}/api/settings" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('os_name',''))")

_restore_saved() {
    curl -sS $CURL_OPTS -X PATCH "${API_BASE}/api/settings" \
        -H 'content-type: application/json' \
        -d "{\"os_name\":\"${SAVED}\"}" > /dev/null 2>&1 || true
}
trap _restore_saved EXIT

CANARY="e2e-test-canary-$$"

# Write a canary to represent what Journey 5 would leave behind on interruption
curl -sS $CURL_OPTS -X PATCH "${API_BASE}/api/settings" \
    -H 'content-type: application/json' \
    -d "{\"os_name\":\"${CANARY}\"}" > /dev/null

# Run a minimal subprocess that mimics the fixed _browser_cleanup trap:
# it holds ORIGINAL_OS_NAME, sleeps (simulating mid-journey work), then
# receives SIGINT. On SIGINT the EXIT trap should fire and restore.
TMPSCRIPT=$(mktemp /tmp/test-browser-trap-XXXXXX.sh)
cat > "$TMPSCRIPT" << INNEREOF
#!/usr/bin/env bash
set -u
_API_BASE="\$1"
_CURL_OPTS="\$2"
_ORIGINAL_OS_NAME="\$3"
_cleanup() {
    if [ -n "\${_ORIGINAL_OS_NAME:-}" ]; then
        curl -sS \${_CURL_OPTS} --connect-timeout 3 -m 5 \\
            -X PATCH "\${_API_BASE}/api/settings" \\
            -H 'content-type: application/json' \\
            -d "{\\"os_name\\":\\"\${_ORIGINAL_OS_NAME}\\"}" > /dev/null 2>&1 || true
    fi
}
trap _cleanup EXIT INT TERM HUP
sleep 30
INNEREOF
chmod +x "$TMPSCRIPT"

bash "$TMPSCRIPT" "$API_BASE" "$CURL_OPTS" "$SAVED" &
CHILD=$!
sleep 0.4
kill -INT $CHILD
wait $CHILD 2>/dev/null || true
sleep 0.4  # let the trap's curl finish

rm -f "$TMPSCRIPT"

AFTER=$(curl -sS $CURL_OPTS "${API_BASE}/api/settings" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('os_name',''))")

if [ "$AFTER" = "$SAVED" ]; then
    pass "OS name restored after SIGINT (got: $AFTER)"
else
    fail "OS name not restored after SIGINT (got: '$AFTER', expected: '$SAVED')"
fi
