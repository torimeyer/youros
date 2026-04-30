#!/bin/bash
# Tests for WSL compatibility in start.sh
# Run: ./scripts/test_wsl_compat.sh

set -e

PASS=0
FAIL=0
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

assert() {
    local name="$1"
    local result="$2"
    if [ "$result" -eq 0 ]; then
        echo -e "  ${GREEN}PASS${NC}  $name"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}FAIL${NC}  $name"
        FAIL=$((FAIL + 1))
    fi
}

DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo ""
echo "=== WSL compatibility tests ==="
echo ""

# start.sh must detect WSL via $WSL_DISTRO_NAME or /proc/sys/kernel/osrelease
if grep -qE 'WSL_DISTRO_NAME|microsoft' "$DIR/start.sh"; then
    assert "start.sh contains WSL detection (WSL_DISTRO_NAME or microsoft)" 0
else
    assert "start.sh contains WSL detection (WSL_DISTRO_NAME or microsoft)" 1
fi

# start.sh must have a WSL browser-open fallback
if grep -qE 'wslview|powershell\.exe' "$DIR/start.sh"; then
    assert "start.sh has WSL browser-open fallback (wslview or powershell.exe)" 0
else
    assert "start.sh has WSL browser-open fallback (wslview or powershell.exe)" 1
fi

# start.sh syntax must still be valid after the WSL changes
bash -n "$DIR/start.sh" 2>/dev/null
assert "start.sh still has valid bash syntax after WSL changes" $?

# README must mention WSL
if grep -qi 'WSL\|Windows Subsystem' "$DIR/README.md"; then
    assert "README mentions WSL" 0
else
    assert "README mentions WSL" 1
fi

echo ""
TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}All $TOTAL WSL compat tests passed.${NC}"
    exit 0
else
    echo -e "${RED}$FAIL of $TOTAL WSL compat tests failed.${NC}"
    exit 1
fi
echo ""
