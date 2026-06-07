#!/usr/bin/env bash
# test_liveness_check.sh — mock-based tests for scripts/liveness-check.sh
#
# Stubs curl so no real servers are needed. Covers:
#   1. Both up → single green line, exit 0
#   2. Backend down → red line with dev-backend.sh command, exit 0
#   3. Frontend down → red line with dev-frontend.sh command, exit 0
#   4. Both down → two red lines, exit 0

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/liveness-check.sh"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; ((PASS++)); }
fail() { echo "FAIL: $1"; ((FAIL++)); echo "  got: $2"; }

assert_contains() {
    local label="$1" haystack="$2" needle="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        pass "$label"
    else
        fail "$label" "$haystack"
        echo "  expected to find: $needle"
    fi
}

assert_not_contains() {
    local label="$1" haystack="$2" needle="$3"
    if [[ "$haystack" != *"$needle"* ]]; then
        pass "$label"
    else
        fail "$label (unexpected text present)" "$haystack"
        echo "  did not want: $needle"
    fi
}

# Build an isolated stub dir for each test scenario.
make_stub_dir() {
    local backend_code="$1"
    local frontend_code="$2"
    local dir
    dir=$(mktemp -d)
    cat >"$dir/curl" <<STUB
#!/usr/bin/env bash
# Emit the right http_code based on which URL is being probed.
for arg in "\$@"; do
    case "\$arg" in
        */api/health) echo -n "$backend_code"; exit 0 ;;
        */3010/*)     echo -n "$frontend_code"; exit 0 ;;
        *3010*)       echo -n "$frontend_code"; exit 0 ;;
    esac
done
echo -n "000"
exit 0
STUB
    chmod +x "$dir/curl"
    echo "$dir"
}

run_check() {
    local stub_dir="$1"
    local backend_url="${2:-https://127.0.0.1:8000}"
    local frontend_url="${3:-https://localhost:3010}"
    PATH="$stub_dir:$PATH" \
        YOUROS_BACKEND_URL="$backend_url" \
        YOUROS_FRONTEND_URL="$frontend_url" \
        bash "$TARGET" 2>&1
}

# --- 1. Both up ---
SD=$(make_stub_dir "200" "200")
OUT=$(run_check "$SD")
assert_contains     "both up: green line"               "$OUT" "[liveness]"
assert_contains     "both up: backend ok"               "$OUT" "backend"
assert_contains     "both up: frontend ok"              "$OUT" "frontend"
assert_not_contains "both up: no DOWN word"             "$OUT" "DOWN"
assert_not_contains "both up: no dev-backend.sh"        "$OUT" "dev-backend.sh"
assert_not_contains "both up: no dev-frontend.sh"       "$OUT" "dev-frontend.sh"
rm -rf "$SD"

# --- 2. Backend down (000 = connection refused) ---
SD=$(make_stub_dir "000" "200")
OUT=$(run_check "$SD")
assert_contains     "be down: DOWN in output"           "$OUT" "DOWN"
assert_contains     "be down: dev-backend.sh hint"      "$OUT" "dev-backend.sh"
assert_not_contains "be down: frontend not mentioned"   "$OUT" "dev-frontend.sh"
rm -rf "$SD"

# --- 3. Frontend down ---
SD=$(make_stub_dir "200" "000")
OUT=$(run_check "$SD")
assert_contains     "fe down: DOWN in output"           "$OUT" "DOWN"
assert_contains     "fe down: dev-frontend.sh hint"     "$OUT" "dev-frontend.sh"
assert_not_contains "fe down: backend not mentioned"    "$OUT" "dev-backend.sh"
rm -rf "$SD"

# --- 4. Both down ---
SD=$(make_stub_dir "000" "000")
OUT=$(run_check "$SD")
assert_contains     "both down: DOWN in output"         "$OUT" "DOWN"
assert_contains     "both down: dev-backend.sh"         "$OUT" "dev-backend.sh"
assert_contains     "both down: dev-frontend.sh"        "$OUT" "dev-frontend.sh"
rm -rf "$SD"

# --- 5. Frontend 302 redirect counts as up ---
SD=$(make_stub_dir "200" "302")
OUT=$(run_check "$SD")
assert_not_contains "fe 302: not DOWN"                  "$OUT" "DOWN"
rm -rf "$SD"

# --- 6. Exit code is always 0 (advisory, not blocking) ---
SD=$(make_stub_dir "000" "000")
PATH="$SD:$PATH" YOUROS_BACKEND_URL="https://127.0.0.1:8000" YOUROS_FRONTEND_URL="https://localhost:3010" \
    bash "$TARGET" >/dev/null 2>&1
EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
    pass "both down: exit 0 (advisory)"
else
    fail "both down: exit 0 (advisory)" "exit code was $EXIT_CODE"
fi
rm -rf "$SD"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
