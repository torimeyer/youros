#!/usr/bin/env bash
# backend_watchdog_test.sh
#
# Smoke-tests for backend_watchdog.sh.
#
# Covers:
#   1. HEALTH_URL defaults to /api/health (not any heavier endpoint).
#   2. MYOS_WATCHDOG_DEADLOCK_THRESHOLD defaults to 10.
#   3. Watchdog does NOT kill a pid-alive backend after < 10 consecutive
#      failed probes (the false-positive scenario that caused crash loops).
#   4. Watchdog DOES fire SIGKILL once the threshold is reached.
#
# Does NOT require a live backend. Health probes are simulated via a tiny
# local HTTP server (nc or python3) or by pointing HEALTH_URL at a URL
# that always returns a specific code.
#
# Usage:
#   bash scripts/backend_watchdog_test.sh
#
# Exit 0 = all tests passed. Exit 1 = at least one test failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WATCHDOG="$SCRIPT_DIR/backend_watchdog.sh"

PASS=0
FAIL=0
_test_failures=()

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); _test_failures+=("$1"); }

# ---------------------------------------------------------------------------
# Test 1: HEALTH_URL default is /api/health
# ---------------------------------------------------------------------------
_default_health_url=$(grep 'HEALTH_URL=.*MYOS_WATCHDOG_HEALTH_URL' "$WATCHDOG" | head -1)
if echo "$_default_health_url" | grep -q '/api/health'; then
    pass "HEALTH_URL default contains /api/health"
else
    fail "HEALTH_URL default does not point to /api/health (got: $_default_health_url)"
fi

# Confirm no other URL is probed during deadlock checks (only HEALTH_URL is used).
_probe_calls=$(grep -n 'curl.*HEALTH_URL\|curl.*api/' "$WATCHDOG" | grep -v '#' || true)
_non_health=$(echo "$_probe_calls" | grep -v 'HEALTH_URL' | grep -v '^$' || true)
if [ -z "$_non_health" ]; then
    pass "Only HEALTH_URL is probed (no hardcoded alternative endpoints)"
else
    fail "Found curl calls to non-HEALTH_URL endpoints: $_non_health"
fi

# ---------------------------------------------------------------------------
# Test 2: MYOS_WATCHDOG_DEADLOCK_THRESHOLD default is 10
# ---------------------------------------------------------------------------
_threshold_line=$(grep 'MYOS_WATCHDOG_DEADLOCK_THRESHOLD:-' "$WATCHDOG" | head -1)
if echo "$_threshold_line" | grep -q ':-10}'; then
    pass "MYOS_WATCHDOG_DEADLOCK_THRESHOLD default is 10"
else
    fail "MYOS_WATCHDOG_DEADLOCK_THRESHOLD default is NOT 10 (got: $_threshold_line)"
fi

# ---------------------------------------------------------------------------
# Test 3: Watchdog skips SIGKILL when pid is alive and misses < threshold
#
# Strategy: start a dummy long-running process (sleep) to act as a fake
# backend pid. Write that pid to a temp BACKEND_PIDFILE. Start the watchdog
# with a failing HEALTH_URL (points at a port that refuses connections) and a
# short INTERVAL. After (threshold - 1) cycles, the fake pid should still be
# alive (no SIGKILL fired). Check the log for no SIGKILL line.
# ---------------------------------------------------------------------------
_tmpdir=$(mktemp -d)
trap 'rm -rf "$_tmpdir"; jobs -p | xargs kill 2>/dev/null || true' EXIT

_wd_pidfile="$_tmpdir/watchdog.pid"
_wd_logfile="$_tmpdir/watchdog.log"
_be_port=19876   # unused port — health probes will always fail (ECONNREFUSED)
# The watchdog computes BACKEND_PIDFILE="/tmp/myos-backend-${BACKEND_PORT}.pid".
# The fake PID must go there, not into _tmpdir, or backend_pid_alive() returns
# false and the deadlock-threshold SIGKILL path is never reached.
_be_pidfile="/tmp/myos-backend-${_be_port}.pid"

# Fake backend: a long-running sleep.
sleep 3600 &
_fake_pid=$!
echo "$_fake_pid" > "$_be_pidfile"

# Run the watchdog for a limited time with:
#   THRESHOLD=4 (so we can complete in reasonable time)
#   INTERVAL=1  (fast cycling)
#   HEALTH_URL pointing at a port that never answers
#   RESTART_WAIT=1 (so restart path is quick if it somehow fires)
#   PYSPY_TIMEOUT=1
_threshold_under_test=4
# Each watchdog cycle takes ~21s (INTERVAL=1 + 3×sleep5 + sleep5-at-end) with
# PROBE_RETRY_SLEEP=0. Budget (threshold-1) full cycles plus margin, staying
# strictly below threshold cycles so SIGKILL cannot fire before we check.
_run_for=$(((_threshold_under_test - 1) * 25 + 5))
MYOS_WATCHDOG_PIDFILE="$_wd_pidfile" \
MYOS_WATCHDOG_LOGFILE="$_wd_logfile" \
MYOS_WATCHDOG_BACKEND_PORT="$_be_port" \
MYOS_WATCHDOG_INTERVAL=1 \
MYOS_WATCHDOG_DEADLOCK_THRESHOLD="$_threshold_under_test" \
MYOS_WATCHDOG_HEALTH_URL="https://127.0.0.1:${_be_port}/api/health" \
MYOS_WATCHDOG_RESTART_WAIT=1 \
MYOS_WATCHDOG_PYSPY_TIMEOUT=1 \
MYOS_WATCHDOG_PROBE_RETRY_SLEEP=0 \
bash "$WATCHDOG" &
_wd_pid=$!

# Let it run for (threshold - 1) probe cycles. Each probe waits up to 15s
# (3 retries × 5s sleep), so we budget generously.
sleep "$_run_for"

# The fake backend process should still be alive — watchdog should not have
# killed it before reaching the threshold.
if kill -0 "$_fake_pid" 2>/dev/null; then
    pass "Fake backend pid survived < threshold consecutive misses (no premature SIGKILL)"
else
    fail "Fake backend pid was killed before threshold was reached"
fi

# The log should not yet show a SIGKILL line.
if [ -f "$_wd_logfile" ] && grep -q "SIGKILL" "$_wd_logfile"; then
    fail "Watchdog logged a SIGKILL before threshold was reached"
else
    pass "No premature SIGKILL logged"
fi

# Clean up watchdog and fake backend.
kill "$_wd_pid" 2>/dev/null || true
kill "$_fake_pid" 2>/dev/null || true
wait "$_wd_pid" 2>/dev/null || true
wait "$_fake_pid" 2>/dev/null || true
rm -f "$_be_pidfile" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Test 4: Watchdog fires SIGKILL once threshold IS reached
#
# Same setup, but now run long enough for threshold cycles to elapse.
# ---------------------------------------------------------------------------
_tmpdir2=$(mktemp -d)
_wd_pidfile2="$_tmpdir2/watchdog.pid"
_wd_logfile2="$_tmpdir2/watchdog.log"
# Must write to the path the watchdog reads: /tmp/myos-backend-${BACKEND_PORT}.pid
_be_pidfile2="/tmp/myos-backend-${_be_port}.pid"

sleep 3600 &
_fake_pid2=$!
echo "$_fake_pid2" > "$_be_pidfile2"

_threshold2=3
# Enough time for threshold2 full probe cycles to complete.
# PROBE_RETRY_SLEEP=0 makes inner retries instant; each outer cycle is:
#   INTERVAL(1s) + 4×probe_once(~0s) + 3×outer-sleep(5s) + 5s = ~21s.
# Budget 25s per cycle plus margin.
_run_for2=$((_threshold2 * 25 + 5))

MYOS_WATCHDOG_PIDFILE="$_wd_pidfile2" \
MYOS_WATCHDOG_LOGFILE="$_wd_logfile2" \
MYOS_WATCHDOG_BACKEND_PORT="$_be_port" \
MYOS_WATCHDOG_INTERVAL=1 \
MYOS_WATCHDOG_DEADLOCK_THRESHOLD="$_threshold2" \
MYOS_WATCHDOG_HEALTH_URL="https://127.0.0.1:${_be_port}/api/health" \
MYOS_WATCHDOG_RESTART_WAIT=1 \
MYOS_WATCHDOG_PYSPY_TIMEOUT=1 \
MYOS_WATCHDOG_PROBE_RETRY_SLEEP=0 \
bash "$WATCHDOG" &
_wd_pid2=$!

sleep "$_run_for2"

if [ -f "$_wd_logfile2" ] && grep -q "SIGKILL" "$_wd_logfile2"; then
    pass "Watchdog fired SIGKILL after threshold ($_threshold2) was reached"
else
    fail "Watchdog did NOT fire SIGKILL after threshold ($_threshold2) cycles"
fi

kill "$_wd_pid2" 2>/dev/null || true
kill "$_fake_pid2" 2>/dev/null || true
wait "$_wd_pid2" 2>/dev/null || true
wait "$_fake_pid2" 2>/dev/null || true
rm -f "$_be_pidfile2" 2>/dev/null || true
rm -rf "$_tmpdir2"

# ---------------------------------------------------------------------------
# Test 5: probe_once retries when curl fails (regression for || echo "000" bug)
#
# Root cause: curl -w "%{http_code}" writes "000" to stdout when no HTTP
# response is received. The old code also ran `|| echo "000"`, appending a
# second "000" and producing "000000" (6 chars). Since "000000" != "000",
# the retry-loop break condition fired immediately — zero retries. The fix
# replaces `|| echo "000"` with `|| true`, leaving code="000" (3 chars) so
# the loop correctly retries.
#
# Strategy: install a mock curl that fails for the first 2 calls then
# succeeds on the 3rd. Run probe_once (sourced from the watchdog) with
# PROBE_RETRY_SLEEP=0. Verify it returns 0 (success reached via retry) and
# that curl was called exactly 3 times (not 1).
# ---------------------------------------------------------------------------
_p5_dir=$(mktemp -d)
_p5_calls="$_p5_dir/calls"
printf '0' > "$_p5_calls"

# Mock curl: outputs "000"+exits 7 for first 2 calls; outputs "200"+exits 0
# on the 3rd. Uses CURL_CALLS_FILE env var (set in the subshell below) so the
# path doesn't need to be baked into the script text.
cat > "$_p5_dir/curl" << 'MOCK_CURL_EOF'
#!/usr/bin/env bash
n=$(( $(cat "$CURL_CALLS_FILE") + 1 ))
printf '%s' "$n" > "$CURL_CALLS_FILE"
if [ "$n" -le 2 ]; then printf "000"; exit 7; else printf "200"; exit 0; fi
MOCK_CURL_EOF
chmod +x "$_p5_dir/curl"

# Extract and run probe_once from the real watchdog in an isolated subshell.
# We set PROBE_RETRY_SLEEP=0 to make sleeps instant, and override PATH so
# our mock curl is found first.
_p5_result=0
HEALTH_URL="https://127.0.0.1:19999/api/health" \
PROBE_RETRY_SLEEP=0 \
CURL_CALLS_FILE="$_p5_calls" \
PATH="$_p5_dir:$PATH" \
bash -c "
$(sed -n '/^probe_once()/,/^}/p' "$WATCHDOG")
probe_once
" || _p5_result=$?

_p5_calls_actual=$(cat "$_p5_calls")

if [ "$_p5_result" -eq 0 ]; then
    pass "probe_once returns 0 when 3rd retry succeeds"
else
    fail "probe_once returned $_p5_result — retry loop broken (expected 0)"
fi

if [ "$_p5_calls_actual" -eq 3 ]; then
    pass "probe_once calls curl 3 times (inner retries execute on failure)"
else
    fail "probe_once called curl $_p5_calls_actual times (expected 3 — retry loop broke early due to || echo '000' bug)"
fi

rm -rf "$_p5_dir"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
    echo "Failed tests:"
    for _t in "${_test_failures[@]}"; do
        echo "  - $_t"
    done
    exit 1
fi
exit 0
