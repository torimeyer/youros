#!/usr/bin/env bash
# test_build_frontend_atomic.sh
#
# Hermetic + end-to-end tests for scripts/build-frontend-atomic.sh (task ->3111).
#
# Scenarios tested:
#   1. Concurrent reader loop checking [ -s "$DIST_DIR/index.html" ] during a
#      slow simulated build (sleep 0.4 inside BUILD_CMD): 0 missing reads across
#      the entire build and atomic swap.
#   2. Failed build (BUILD_CMD exits 1 or fails to produce non-empty index.html):
#      existing $DIST_DIR/index.html stays intact and staging dir is cleaned up.
#   3. Prior hashed chunk retention: recent hashed asset in $DIST_DIR/assets is
#      preserved alongside newly built assets without overwriting rebuilt files,
#      while expired chunks older than CHUNK_RETENTION_MINUTES (24h default) are
#      pruned.
#   4. Real end-to-end `npm run build` in app/ succeeds (exit 0) via
#      build-frontend-atomic.sh and produces valid app/dist/index.html + assets/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_SCRIPT="$SCRIPT_DIR/build-frontend-atomic.sh"

PASS=0
FAIL=0
FAILURES=()

pass() {
  echo "[PASS] $1"
  PASS=$((PASS + 1))
}

fail() {
  echo "[FAIL] $1"
  FAIL=$((FAIL + 1))
  FAILURES+=("$1")
}

TMP_ROOT="$(mktemp -d)"
READER_PID=""

cleanup() {
  if [ -n "$READER_PID" ] && kill -0 "$READER_PID" 2>/dev/null; then
    kill "$READER_PID" 2>/dev/null || true
    wait "$READER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT INT TERM

assert_no_swap_leftovers() {
  local parent_dir="$1"
  local base_name="$2"
  local label="$3"
  local leftovers
  leftovers="$(find "$parent_dir" -maxdepth 1 \( -name "${base_name}.next.*" -o -name "${base_name}.prev.*" -o -name "${base_name}.swap.lock" \) 2>/dev/null || true)"
  if [ -z "$leftovers" ]; then
    pass "$label: no staging/prev/lock leftovers"
  else
    fail "$label: unexpected leftovers found: $leftovers"
  fi
}

echo "=== Test 0: Script existence and permissions ==="
if [ -x "$BUILD_SCRIPT" ]; then
  pass "scripts/build-frontend-atomic.sh exists and is executable"
else
  fail "scripts/build-frontend-atomic.sh is missing or not executable"
fi

# ---------------------------------------------------------------------------
# Scenario 1: Zero missing reads during slow build + atomic swap
# ---------------------------------------------------------------------------
echo "=== Scenario 1: Concurrent reader loop during slow simulated build ==="
CASE1_DIR="$TMP_ROOT/case1"
CASE1_DIST="$CASE1_DIR/dist"
mkdir -p "$CASE1_DIST/assets"
printf '<html>initial-v1</html>\n' > "$CASE1_DIST/index.html"
printf 'console.log("v1");\n' > "$CASE1_DIST/assets/Init-111.js"

STOP_FILE="$CASE1_DIR/stop_reader"
READY_FILE="$CASE1_DIR/reader_ready"
STATS_FILE="$CASE1_DIR/reader_stats"

(
  reads=0
  missing=0
  touch "$READY_FILE"
  while [ ! -f "$STOP_FILE" ]; do
    reads=$((reads + 1))
    if [ ! -s "$CASE1_DIST/index.html" ]; then
      missing=$((missing + 1))
    fi
  done
  printf '%s %s\n' "$reads" "$missing" > "$STATS_FILE"
) &
READER_PID=$!

while [ ! -f "$READY_FILE" ]; do
  sleep 0.01
done
sleep 0.05

set +e
DIST_DIR="$CASE1_DIST" \
  BUILD_CMD='sleep 0.4 && mkdir -p "$STAGE_DIR/assets" && printf "<html>swapped-v2</html>\n" > "$STAGE_DIR/index.html" && printf "console.log(\"v2\");\n" > "$STAGE_DIR/assets/Next-222.js"' \
  bash "$BUILD_SCRIPT"
CASE1_RC=$?
set -e

sleep 0.05
touch "$STOP_FILE"
wait "$READER_PID"
READER_PID=""

read -r TOTAL_READS MISSING_READS < "$STATS_FILE"

if [ "$CASE1_RC" -eq 0 ]; then
  pass "Scenario 1: slow simulated build exited 0"
else
  fail "Scenario 1: slow simulated build exited $CASE1_RC"
fi

if [ "$TOTAL_READS" -gt 50 ] && [ "$MISSING_READS" -eq 0 ]; then
  pass "Scenario 1: 0 missing reads across $TOTAL_READS concurrent checks"
else
  fail "Scenario 1: expected >50 reads and 0 missing reads, got reads=$TOTAL_READS missing=$MISSING_READS"
fi

if [ -f "$CASE1_DIST/index.html" ] && grep -q "swapped-v2" "$CASE1_DIST/index.html"; then
  pass "Scenario 1: $CASE1_DIST/index.html atomically updated to v2"
else
  fail "Scenario 1: $CASE1_DIST/index.html was not updated to v2"
fi

assert_no_swap_leftovers "$CASE1_DIR" "dist" "Scenario 1"

# ---------------------------------------------------------------------------
# Scenario 2: Failed build leaves existing dist intact and cleans staging dir
# ---------------------------------------------------------------------------
echo "=== Scenario 2: Failed build rollback and cleanup ==="
CASE2_DIR="$TMP_ROOT/case2"
CASE2_DIST="$CASE2_DIR/dist"
mkdir -p "$CASE2_DIST/assets"
printf '<html>known-good-prod</html>\n' > "$CASE2_DIST/index.html"
printf 'console.log("good");\n' > "$CASE2_DIST/assets/Good-aaa.js"

set +e
DIST_DIR="$CASE2_DIST" \
  BUILD_CMD='mkdir -p "$STAGE_DIR/assets" && printf "broken" > "$STAGE_DIR/assets/Broken-zzz.js" && exit 1' \
  bash "$BUILD_SCRIPT" >/dev/null 2>&1
CASE2A_RC=$?
set -e

if [ "$CASE2A_RC" -ne 0 ] && [ -x "$BUILD_SCRIPT" ]; then
  pass "Scenario 2a: failing BUILD_CMD returns non-zero ($CASE2A_RC)"
else
  fail "Scenario 2a: expected executable build script to return non-zero on BUILD_CMD failure (rc=$CASE2A_RC)"
fi

if grep -q "known-good-prod" "$CASE2_DIST/index.html" && [ -f "$CASE2_DIST/assets/Good-aaa.js" ] && [ ! -e "$CASE2_DIST/assets/Broken-zzz.js" ]; then
  pass "Scenario 2a: existing dist stayed intact after non-zero BUILD_CMD"
else
  fail "Scenario 2a: existing dist was modified on failed build"
fi

assert_no_swap_leftovers "$CASE2_DIR" "dist" "Scenario 2a"

set +e
DIST_DIR="$CASE2_DIST" \
  BUILD_CMD='mkdir -p "$STAGE_DIR/assets" && : > "$STAGE_DIR/index.html"' \
  bash "$BUILD_SCRIPT" >/dev/null 2>&1
CASE2B_RC=$?
set -e

if [ "$CASE2B_RC" -ne 0 ] && [ -x "$BUILD_SCRIPT" ]; then
  pass "Scenario 2b: empty index.html rejected before swap (rc=$CASE2B_RC)"
else
  fail "Scenario 2b: expected empty staged index.html to fail validation (rc=$CASE2B_RC)"
fi

if grep -q "known-good-prod" "$CASE2_DIST/index.html"; then
  pass "Scenario 2b: existing dist stayed intact when index.html was empty"
else
  fail "Scenario 2b: existing dist was corrupted when staged index.html was empty"
fi

assert_no_swap_leftovers "$CASE2_DIR" "dist" "Scenario 2b"

# ---------------------------------------------------------------------------
# Scenario 3: Prior hashed chunk retention and >24h pruning
# ---------------------------------------------------------------------------
echo "=== Scenario 3: Prior chunk retention and expired chunk pruning ==="
CASE3_DIR="$TMP_ROOT/case3"
CASE3_DIST="$CASE3_DIR/dist"
mkdir -p "$CASE3_DIST/assets"
printf '<html>old-bundle</html>\n' > "$CASE3_DIST/index.html"
printf 'console.log("old-route");\n' > "$CASE3_DIST/assets/OldRoute-abc123.js"
printf 'console.log("shared-v1");\n' > "$CASE3_DIST/assets/Shared-777.js"
printf 'console.log("ancient-route");\n' > "$CASE3_DIST/assets/AncientRoute-000.js"
# Mark AncientRoute-000.js well past the 24h (1440m) retention window.
touch -t 202001010000 "$CASE3_DIST/assets/AncientRoute-000.js"

set +e
DIST_DIR="$CASE3_DIST" \
  BUILD_CMD='mkdir -p "$STAGE_DIR/assets" && printf "<html>new-bundle</html>\n" > "$STAGE_DIR/index.html" && printf "console.log(\"new-route\");\n" > "$STAGE_DIR/assets/NewRoute-def456.js" && printf "console.log(\"shared-v2\");\n" > "$STAGE_DIR/assets/Shared-777.js"' \
  bash "$BUILD_SCRIPT"
CASE3_RC=$?
set -e

if [ "$CASE3_RC" -eq 0 ]; then
  pass "Scenario 3: retention build exited 0"
else
  fail "Scenario 3: retention build exited $CASE3_RC"
fi

if [ -f "$CASE3_DIST/assets/OldRoute-abc123.js" ] && grep -q 'old-route' "$CASE3_DIST/assets/OldRoute-abc123.js"; then
  pass "Scenario 3: recent prior chunk OldRoute-abc123.js retained"
else
  fail "Scenario 3: recent prior chunk OldRoute-abc123.js missing after swap"
fi

if [ -f "$CASE3_DIST/assets/NewRoute-def456.js" ] && grep -q 'new-route' "$CASE3_DIST/assets/NewRoute-def456.js"; then
  pass "Scenario 3: newly built chunk NewRoute-def456.js present"
else
  fail "Scenario 3: newly built chunk NewRoute-def456.js missing after swap"
fi

if [ -f "$CASE3_DIST/assets/Shared-777.js" ] && grep -q 'shared-v2' "$CASE3_DIST/assets/Shared-777.js"; then
  pass "Scenario 3: newly built file Shared-777.js was not overwritten by prior copy"
else
  fail "Scenario 3: Shared-777.js did not keep newly built contents"
fi

if [ ! -e "$CASE3_DIST/assets/AncientRoute-000.js" ]; then
  pass "Scenario 3: expired chunk AncientRoute-000.js (>24h old) pruned"
else
  fail "Scenario 3: expired chunk AncientRoute-000.js was NOT pruned"
fi

assert_no_swap_leftovers "$CASE3_DIR" "dist" "Scenario 3"

set +e
FORCE_POSIX_SWAP=1 DIST_DIR="$CASE3_DIST" \
  BUILD_CMD='mkdir -p "$STAGE_DIR/assets" && printf "<html>posix-bundle</html>\n" > "$STAGE_DIR/index.html" && printf "console.log(\"third-route\");\n" > "$STAGE_DIR/assets/ThirdRoute-999.js"' \
  bash "$BUILD_SCRIPT"
CASE3B_RC=$?
set -e

if [ "$CASE3B_RC" -eq 0 ] && grep -q "posix-bundle" "$CASE3_DIST/index.html" && [ -f "$CASE3_DIST/assets/OldRoute-abc123.js" ] && [ -f "$CASE3_DIST/assets/NewRoute-def456.js" ] && [ -f "$CASE3_DIST/assets/ThirdRoute-999.js" ]; then
  pass "Scenario 3b: POSIX mv fallback swap retains prior chunks cleanly"
else
  fail "Scenario 3b: POSIX mv fallback swap failed (rc=$CASE3B_RC)"
fi

assert_no_swap_leftovers "$CASE3_DIR" "dist" "Scenario 3b"

# ---------------------------------------------------------------------------
# Scenario 4: Real end-to-end `npm run build` in app/
# ---------------------------------------------------------------------------
echo "=== Scenario 4: Real end-to-end npm run build in app/ ==="
if grep -q 'build-frontend-atomic\.sh' "$REPO_ROOT/app/package.json"; then
  pass "Scenario 4: app/package.json wires build script to build-frontend-atomic.sh"
  set +e
  (cd "$REPO_ROOT/app" && npm run build)
  CASE4_RC=$?
  set -e
  if [ "$CASE4_RC" -eq 0 ]; then
    pass "Scenario 4: npm run build exited 0"
  else
    fail "Scenario 4: npm run build failed with exit code $CASE4_RC"
  fi

  if [ -s "$REPO_ROOT/app/dist/index.html" ]; then
    pass "Scenario 4: app/dist/index.html exists and is non-empty"
  else
    fail "Scenario 4: app/dist/index.html missing or empty"
  fi

  ASSET_COUNT="$(find "$REPO_ROOT/app/dist/assets" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')"
  if [ "${ASSET_COUNT:-0}" -gt 0 ]; then
    pass "Scenario 4: app/dist/assets contains $ASSET_COUNT built asset(s)"
  else
    fail "Scenario 4: app/dist/assets has no built files"
  fi

  assert_no_swap_leftovers "$REPO_ROOT/app" "dist" "Scenario 4"
else
  fail "Scenario 4: app/package.json build script does not invoke build-frontend-atomic.sh"
fi

echo ""
echo "Summary: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed checks:"
  for item in "${FAILURES[@]}"; do
    echo "  - $item"
  done
  exit 1
fi
exit 0
