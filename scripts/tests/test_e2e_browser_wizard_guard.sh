#!/usr/bin/env bash
# Test: e2e_browser.sh detects the onboarding wizard and never restores an
# empty OS name.
#
# Regression guard for →2685. Journey 5 flaked when a cold browser session
# showed the onboarding wizard over every route (first-load settings
# hydration race in app/src/stores/app.ts), and one bad run PATCHed an EMPTY
# os_name into real user settings because the capture step read empty and
# the restore wrote it back verbatim.
#
# Pins three guarantees:
#   1. Wizard guard: ensure_no_onboarding_wizard exists, queries the
#      [data-testid=onboarding-wizard] overlay, consults /api/settings for
#      the server-side onboarded flag, reloads once, names settings
#      hydration in its failure reason, and is called by Journeys 3, 4,
#      and 5 before any clicking starts.
#   2. Empty-capture restore is skipped: restore_original_os_name refuses
#      to PATCH an empty os_name (checked statically AND behaviourally with
#      a stubbed curl), and Journey 5 plus the cleanup trap both route
#      through it. Journey 5 also asserts the Preferences tab is on screen
#      before clicking, reporting what WAS on screen when it is missing.
#   3. Capture retry: capture_original_os_name tries twice before giving up.

set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$THIS_DIR/../e2e_browser.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

[ -f "$SCRIPT" ] || fail "e2e_browser.sh not found at $SCRIPT"

bash -n "$SCRIPT" || fail "e2e_browser.sh has a shell syntax error"
pass "e2e_browser.sh parses cleanly (bash -n)"

# ---------------------------------------------------------------------------
# 1. Wizard guard helper
# ---------------------------------------------------------------------------

grep -q '^ensure_no_onboarding_wizard()' "$SCRIPT" \
    || fail "ensure_no_onboarding_wizard() helper not defined"
GUARD_BODY=$(awk '/^ensure_no_onboarding_wizard\(\)/,/^\}/' "$SCRIPT")

echo "$GUARD_BODY" | grep -q 'onboarding-wizard' \
    || fail "wizard guard does not query the [data-testid=onboarding-wizard] overlay"
pass "wizard guard queries the onboarding-wizard testid"

echo "$GUARD_BODY" | grep -q '/api/settings' \
    || fail "wizard guard does not consult /api/settings for the server-side onboarded flag"
pass "wizard guard consults /api/settings"

echo "$GUARD_BODY" | grep -q 'reload' \
    || fail "wizard guard never reloads (the cold-session hydration race needs one retry)"
pass "wizard guard reloads once before giving up"

echo "$GUARD_BODY" | grep -q 'phase_fail' \
    || fail "wizard guard never phase_fails, so a stuck wizard would be misattributed again"
echo "$GUARD_BODY" | grep -qi 'hydration' \
    || fail "wizard guard failure message does not name settings hydration as the real reason"
pass "wizard guard fails loudly with the hydration reason"

# Called by every journey that clicks (3, 4, 5)
JOURNEY3=$(awk '/^header "Journey 3/,/^header "Journey 4/' "$SCRIPT")
JOURNEY4=$(awk '/^header "Journey 4/,/^header "Journey 5/' "$SCRIPT")
JOURNEY5=$(awk '/^header "Journey 5/,/^header "Browser test summary/' "$SCRIPT")
[ -n "$JOURNEY3" ] || fail "could not locate Journey 3 section"
[ -n "$JOURNEY4" ] || fail "could not locate Journey 4 section"
[ -n "$JOURNEY5" ] || fail "could not locate Journey 5 section"

echo "$JOURNEY3" | grep -q 'ensure_no_onboarding_wizard "Journey 3"' \
    || fail "Journey 3 does not call the wizard guard before clicking"
echo "$JOURNEY4" | grep -q 'ensure_no_onboarding_wizard "Journey 4"' \
    || fail "Journey 4 does not call the wizard guard before clicking"
echo "$JOURNEY5" | grep -q 'ensure_no_onboarding_wizard "Journey 5"' \
    || fail "Journey 5 does not call the wizard guard before clicking"
pass "Journeys 3, 4, and 5 all call the wizard guard"

# ---------------------------------------------------------------------------
# 2. Capture retry
# ---------------------------------------------------------------------------

grep -q '^capture_original_os_name()' "$SCRIPT" \
    || fail "capture_original_os_name() helper not defined"
CAPTURE_BODY=$(awk '/^capture_original_os_name\(\)/,/^\}/' "$SCRIPT")
echo "$CAPTURE_BODY" | grep -q 'for attempt in 1 2' \
    || fail "capture helper does not make a second attempt on an empty read"
echo "$JOURNEY5" | grep -q 'capture_original_os_name' \
    || fail "Journey 5 does not use the retrying capture helper"
pass "capture retries once on an empty read and Journey 5 uses it"

# ---------------------------------------------------------------------------
# 3. Restore guard (static)
# ---------------------------------------------------------------------------

grep -q '^restore_original_os_name()' "$SCRIPT" \
    || fail "restore_original_os_name() helper not defined"
RESTORE_BODY=$(awk '/^restore_original_os_name\(\)/,/^\}/' "$SCRIPT")
echo "$RESTORE_BODY" | grep -q -- '-z "${ORIGINAL_OS_NAME' \
    || fail "restore helper has no empty-value guard"
echo "$JOURNEY5" | grep -q 'restore_original_os_name' \
    || fail "Journey 5 does not restore through the guarded helper"
awk '/^_browser_cleanup\(\)/,/^\}/' "$SCRIPT" | grep -q 'restore_original_os_name' \
    || fail "_browser_cleanup does not restore through the guarded helper"
if echo "$JOURNEY5" | grep -q 'X PATCH'; then
    fail "Journey 5 still PATCHes /api/settings directly (bypasses the empty-value guard)"
fi
if awk '/^_browser_cleanup\(\)/,/^\}/' "$SCRIPT" | grep -q 'X PATCH'; then
    fail "_browser_cleanup still PATCHes /api/settings directly (bypasses the empty-value guard)"
fi
pass "both restore paths route through the guarded helper, no direct PATCH left"

# ---------------------------------------------------------------------------
# 4. Journey 5 hardening: Preferences tab asserted before clicking
# ---------------------------------------------------------------------------

echo "$JOURNEY5" | grep -q 'Settings page shows no Preferences tab' \
    || fail "Journey 5 does not fail loudly when the Preferences tab is missing"
echo "$JOURNEY5" | grep -q 'innerText.slice(0, 100)' \
    || fail "Journey 5 does not report what was on screen when the Preferences tab is missing"
pass "Journey 5 asserts the Preferences tab and reports screen contents when missing"

# ---------------------------------------------------------------------------
# 5. Restore guard (behavioural, stubbed curl — no live API needed)
# ---------------------------------------------------------------------------

TMP_DIR=$(mktemp -d /tmp/test-e2e-wizard-guard-XXXXXX)
trap 'rm -rf "$TMP_DIR"' EXIT

awk '/^restore_original_os_name\(\)/,/^\}/' "$SCRIPT" > "$TMP_DIR/restore_fn.sh"
CALLS="$TMP_DIR/curl-calls.log"

cat > "$TMP_DIR/driver.sh" << 'DRIVEREOF'
#!/usr/bin/env bash
# Usage: driver.sh <os_name_value> <calls_log> <restore_fn_file>
set -u
VALUE="$1"; CALLS_LOG="$2"; FN_FILE="$3"
YELLOW=""; NC=""
CURL_OPTS=""
API_BASE="http://stub.invalid"
curl() { echo "curl $*" >> "$CALLS_LOG"; }
# shellcheck disable=SC1090
source "$FN_FILE"
ORIGINAL_OS_NAME="$VALUE"
OS_NAME_CAPTURE_FAILED=1
restore_original_os_name
DRIVEREOF

: > "$CALLS"
bash "$TMP_DIR/driver.sh" "" "$CALLS" "$TMP_DIR/restore_fn.sh" 2>/dev/null
if [ -s "$CALLS" ]; then
    fail "empty ORIGINAL_OS_NAME still produced a settings call: $(cat "$CALLS")"
fi
pass "behaviour: empty capture makes NO settings PATCH"

: > "$CALLS"
bash "$TMP_DIR/driver.sh" "my-real-os" "$CALLS" "$TMP_DIR/restore_fn.sh" 2>/dev/null
grep -q 'PATCH' "$CALLS" || fail "non-empty capture did not issue a PATCH"
grep -q 'my-real-os' "$CALLS" || fail "non-empty capture did not send the captured name back"
pass "behaviour: non-empty capture PATCHes the original name back"

echo ""
echo "All →2685 guard checks passed."
