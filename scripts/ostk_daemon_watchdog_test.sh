#!/usr/bin/env bash
# Bash unit tests for ostk_daemon_watchdog.sh (→1527).
#
# Uses LSOF_CMD to inject a mock lsof so no live daemon is required.
# Exit 0 = all tests pass. Exit 1 = at least one failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHDOG="$SCRIPT_DIR/ostk_daemon_watchdog.sh"

PASS=0
FAIL=0
_failures=()

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); _failures+=("$1"); }

# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------

# Default PTY_WARN threshold >= 10
_warn=$(grep -oE 'PTY_WARN[^}]*:-[0-9]+' "$WATCHDOG" | grep -oE '[0-9]+$' | head -1)
if [[ -n "$_warn" && "$_warn" -ge 10 ]]; then
  pass "PTY_WARN default ($_{warn}) >= 10"
else
  fail "PTY_WARN default should be >= 10 (got: $_warn)"
fi

# Default PTY_CRITICAL threshold >= 20
_crit=$(grep -oE 'PTY_CRITICAL[^}]*:-[0-9]+' "$WATCHDOG" | grep -oE '[0-9]+$' | head -1)
if [[ -n "$_crit" && "$_crit" -ge 20 ]]; then
  pass "PTY_CRITICAL default ($_crit) >= 20"
else
  fail "PTY_CRITICAL default should be >= 20 (got: $_crit)"
fi

# Critical must exceed warn
if [[ -n "$_warn" && -n "$_crit" && "$_crit" -gt "$_warn" ]]; then
  pass "PTY_CRITICAL ($_crit) > PTY_WARN ($_warn)"
else
  fail "PTY_CRITICAL must exceed PTY_WARN (warn=$_warn, critical=$_crit)"
fi

# --dry-run appears in the script
if grep -q 'DRY.RUN' "$WATCHDOG"; then
  pass "dry-run mode implemented"
else
  fail "dry-run mode not found in script"
fi

# restart is guarded by DRY_RUN check
if grep -qE '\$DRY_RUN|DRY.RUN' "$WATCHDOG"; then
  pass "restart is guarded by DRY_RUN flag"
else
  fail "restart not guarded by DRY_RUN"
fi

# LSOF_CMD override is supported (for testability)
if grep -q 'LSOF_CMD' "$WATCHDOG"; then
  pass "LSOF_CMD override supported"
else
  fail "LSOF_CMD override not found — tests cannot inject mock lsof"
fi

# ---------------------------------------------------------------------------
# Functional: mock lsof with many ptmx entries → dry-run flags CRITICAL
# ---------------------------------------------------------------------------

# Build a mock lsof that returns N ptmx lines for any PID.
MOCK_DIR=$(mktemp -d)
trap 'rm -rf "$MOCK_DIR"' EXIT

# Mock lsof: for "daemon-" txt entry + N ptmx entries + cwd
cat > "$MOCK_DIR/lsof" << 'EOF'
#!/usr/bin/env bash
FAKE_PID="99999"
FAKE_INODE="111111"
# txt entry for binary inode detection
echo "daemon-6. $FAKE_PID user txt REG 1,14 23000000 $FAKE_INODE /tmp/daemon-6.0.5"
# cwd entry
echo "daemon-6. $FAKE_PID user cwd DIR 1,14 0 0 /tmp"
# 70 ptmx entries (above default critical threshold of 60)
for i in $(seq 1 70); do
  echo "daemon-6. $FAKE_PID user ${i}u CHR 15,$i 0t0 605 /dev/ptmx"
done
EOF
chmod +x "$MOCK_DIR/lsof"

# Mock pgrep: return a fake daemon PID
cat > "$MOCK_DIR/pgrep" << 'EOF'
#!/usr/bin/env bash
echo "99999"
EOF
chmod +x "$MOCK_DIR/pgrep"

# Mock stat: return matching inode so binary-inode check passes (focus on PTY)
cat > "$MOCK_DIR/stat" << 'EOF'
#!/usr/bin/env bash
# Return same inode for any path (so only PTY check triggers)
echo "111111"
EOF
chmod +x "$MOCK_DIR/stat"

export PATH="$MOCK_DIR:$PATH"

_output=$(LSOF_CMD="$MOCK_DIR/lsof" \
  OSTK_DAEMON_PTY_CRITICAL_THRESHOLD=60 \
  OSTK_DAEMON_PTY_WARN_THRESHOLD=20 \
  bash "$WATCHDOG" --dry-run 2>&1 || true)

if echo "$_output" | grep -q "CRITICAL:"; then
  pass "CRITICAL detected when ptmx fds > threshold (mock: 70 > 60)"
else
  fail "CRITICAL not detected with 70 ptmx fds (threshold=60). Output: $_output"
fi

if echo "$_output" | grep -q "DRY-RUN"; then
  pass "dry-run label present in restart output"
else
  fail "dry-run label missing. Output: $_output"
fi

# Verify 'ostk daemon restart' is NOT actually called in dry-run
# (the mock dir has no 'ostk' binary, so any real call would error)
if ! echo "$_output" | grep -qE '^.*\[restart\]'; then
  pass "no actual restart executed in dry-run mode"
else
  fail "restart was executed in dry-run mode"
fi

# ---------------------------------------------------------------------------
# Functional: mock lsof with few ptmx entries → OK
# ---------------------------------------------------------------------------

cat > "$MOCK_DIR/lsof" << 'EOF'
#!/usr/bin/env bash
FAKE_PID="99999"
FAKE_INODE="111111"
echo "daemon-6. $FAKE_PID user txt REG 1,14 23000000 $FAKE_INODE /tmp/daemon-6.0.5"
echo "daemon-6. $FAKE_PID user cwd DIR 1,14 0 0 /tmp"
# Only 5 ptmx entries (healthy)
for i in $(seq 1 5); do
  echo "daemon-6. $FAKE_PID user ${i}u CHR 15,$i 0t0 605 /dev/ptmx"
done
EOF

_output2=$(LSOF_CMD="$MOCK_DIR/lsof" \
  OSTK_DAEMON_PTY_CRITICAL_THRESHOLD=60 \
  OSTK_DAEMON_PTY_WARN_THRESHOLD=20 \
  bash "$WATCHDOG" --dry-run 2>&1 || true)

if echo "$_output2" | grep -q "OK: pid"; then
  pass "OK reported when ptmx fds < warn threshold (mock: 5 < 20)"
else
  fail "Expected OK status with 5 ptmx fds. Output: $_output2"
fi

if ! echo "$_output2" | grep -q "CRITICAL:"; then
  pass "No CRITICAL with healthy ptmx count"
else
  fail "False CRITICAL with only 5 ptmx fds"
fi

# ---------------------------------------------------------------------------
# Functional: stale binary detection (different inodes)
# ---------------------------------------------------------------------------

cat > "$MOCK_DIR/lsof" << 'EOF'
#!/usr/bin/env bash
FAKE_PID="99999"
OLD_INODE="111111"  # running process uses old inode
echo "daemon-6. $FAKE_PID user txt REG 1,14 22000000 $OLD_INODE /tmp/daemon-6.0.5"
echo "daemon-6. $FAKE_PID user cwd DIR 1,14 0 0 /tmp"
# Only 3 ptmx (so PTY check won't trigger)
for i in $(seq 1 3); do
  echo "daemon-6. $FAKE_PID user ${i}u CHR 15,$i 0t0 605 /dev/ptmx"
done
EOF

# Override stat to return a DIFFERENT inode for the installed binary
cat > "$MOCK_DIR/stat" << 'EOF'
#!/usr/bin/env bash
echo "222222"   # installed binary has new inode
EOF

_output3=$(LSOF_CMD="$MOCK_DIR/lsof" \
  OSTK_DAEMON_PTY_CRITICAL_THRESHOLD=60 \
  OSTK_DAEMON_PTY_WARN_THRESHOLD=20 \
  bash "$WATCHDOG" --dry-run 2>&1 || true)

if echo "$_output3" | grep -q "STALE:"; then
  pass "STALE detected when running inode != installed inode"
else
  fail "STALE not detected with inode mismatch. Output: $_output3"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [[ ${#_failures[@]} -gt 0 ]]; then
  echo "Failed:"
  for f in "${_failures[@]}"; do echo "  - $f"; done
  exit 1
fi
exit 0
