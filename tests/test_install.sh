#!/bin/bash
# Tests for install.sh and start.sh
# Run: ./tests/test_install.sh
# These tests verify the scripts won't embarrass you in front of friends.

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
echo "=== install.sh tests ==="
echo ""

# --- Syntax ---

bash -n "$DIR/install.sh" 2>/dev/null
assert "install.sh has valid bash syntax" $?

bash -n "$DIR/start.sh" 2>/dev/null
assert "start.sh has valid bash syntax" $?

# --- No TMPDIR clobbering ---
# TMPDIR is a POSIX system variable. If install.sh uses it, pip/npm/tsc break.

if grep -q '^[[:space:]]*TMPDIR=' "$DIR/install.sh"; then
    assert "install.sh does not clobber TMPDIR" 1
else
    assert "install.sh does not clobber TMPDIR" 0
fi

if grep -q '^[[:space:]]*TMPDIR=' "$DIR/start.sh"; then
    assert "start.sh does not clobber TMPDIR" 1
else
    assert "start.sh does not clobber TMPDIR" 0
fi

# --- Correct ostk repo ---
# Releases are at os-tack/ostk.ai, NOT os-tack/ostk

if grep -q 'os-tack/ostk.ai' "$DIR/install.sh"; then
    assert "install.sh uses correct ostk repo (os-tack/ostk.ai)" 0
else
    assert "install.sh uses correct ostk repo (os-tack/ostk.ai)" 1
fi

if grep -q 'os-tack/ostk"' "$DIR/install.sh" || grep -q "os-tack/ostk'" "$DIR/install.sh"; then
    assert "install.sh does not use wrong repo (os-tack/ostk)" 1
else
    assert "install.sh does not use wrong repo (os-tack/ostk)" 0
fi

# --- ostk download URL format ---
# Must be: ostk-VERSION-ARCH-OS.tar.gz (tarball, not raw binary)

if grep -q '\.tar\.gz' "$DIR/install.sh"; then
    assert "install.sh downloads ostk as tarball (.tar.gz)" 0
else
    assert "install.sh downloads ostk as tarball (.tar.gz)" 1
fi

if grep -q 'tar -xzf' "$DIR/install.sh" || grep -q 'tar xzf' "$DIR/install.sh"; then
    assert "install.sh extracts the tarball" 0
else
    assert "install.sh extracts the tarball" 1
fi

# --- Platform detection ---
# arm64 must map to aarch64, Darwin must map to apple-darwin

if grep -q 'arm64.*aarch64\|aarch64.*arm64' "$DIR/install.sh"; then
    assert "install.sh maps arm64 to aarch64" 0
else
    assert "install.sh maps arm64 to aarch64" 1
fi

if grep -q 'apple-darwin' "$DIR/install.sh"; then
    assert "install.sh maps Darwin to apple-darwin" 0
else
    assert "install.sh maps Darwin to apple-darwin" 1
fi

if grep -q 'unknown-linux-musl' "$DIR/install.sh"; then
    assert "install.sh maps Linux to unknown-linux-musl" 0
else
    assert "install.sh maps Linux to unknown-linux-musl" 1
fi

# --- Prerequisite checks ---

if grep -q 'check_cmd git' "$DIR/install.sh"; then
    assert "install.sh checks for git" 0
else
    assert "install.sh checks for git" 1
fi

if grep -q 'check_cmd curl' "$DIR/install.sh"; then
    assert "install.sh checks for curl" 0
else
    assert "install.sh checks for curl" 1
fi

if grep -q 'check_cmd python3' "$DIR/install.sh"; then
    assert "install.sh checks for python3" 0
else
    assert "install.sh checks for python3" 1
fi

if grep -q 'check_cmd node' "$DIR/install.sh"; then
    assert "install.sh checks for node" 0
else
    assert "install.sh checks for node" 1
fi

if grep -q 'check_cmd npm' "$DIR/install.sh"; then
    assert "install.sh checks for npm" 0
else
    assert "install.sh checks for npm" 1
fi

# --- Clone URL ---
# Must use SSH for private repo access

if grep -q 'git clone git@github.com:' "$DIR/install.sh"; then
    assert "install.sh uses SSH clone URL" 0
else
    assert "install.sh uses SSH clone URL" 1
fi

# --- README matches install.sh clone URL ---

if grep -q 'git@github.com:' "$DIR/README.md"; then
    assert "README uses SSH clone URL" 0
else
    assert "README uses SSH clone URL" 1
fi

# --- npm errors not hidden ---
# npm install should NOT have 2>/dev/null (hides all errors)

if grep -q 'npm install.*2>/dev/null' "$DIR/install.sh"; then
    assert "install.sh does not hide npm errors" 1
else
    assert "install.sh does not hide npm errors" 0
fi

# --- pip upgrade ---

if grep -q 'upgrade pip' "$DIR/install.sh"; then
    assert "install.sh upgrades pip before installing packages" 0
else
    assert "install.sh upgrades pip before installing packages" 1
fi

# --- Repo detection ---
# Installer should detect when run from inside existing repo

if grep -q 'SCRIPT_DIR' "$DIR/install.sh"; then
    assert "install.sh detects existing repo (skips re-clone)" 0
else
    assert "install.sh detects existing repo (skips re-clone)" 1
fi

# --- start.sh guards ---

if grep -q 'venv/bin/activate' "$DIR/start.sh" && grep -q 'Run install.sh first' "$DIR/start.sh"; then
    assert "start.sh checks for missing venv before starting" 0
else
    assert "start.sh checks for missing venv before starting" 1
fi

# --- Browser open uses platform detection ---

if grep -q 'uname.*Darwin' "$DIR/start.sh"; then
    assert "start.sh uses platform detection for browser open" 0
else
    assert "start.sh uses platform detection for browser open" 1
fi

# --- Newlines before appended lines in .zshrc/.bashrc ---
# Without a leading newline, the alias/PATH export lands on the end of the last line

APPEND_LINES=$(grep -c 'echo "" >>' "$DIR/install.sh")
if [ "$APPEND_LINES" -ge 2 ]; then
    assert "install.sh adds newline before appending to shell rc" 0
else
    assert "install.sh adds newline before appending to shell rc" 1
fi

# --- ostk release URL is live ---

echo ""
echo "=== Live checks ==="
echo ""

OSTK_REPO="os-tack/ostk.ai"
VERSION=$(curl -fsSL "https://api.github.com/repos/${OSTK_REPO}/releases/latest" 2>/dev/null \
    | grep '"tag_name"' | head -1 | cut -d'"' -f4)

if [ -n "$VERSION" ]; then
    assert "ostk latest release exists ($VERSION)" 0

    # Check that the Mac ARM binary actually exists
    TARBALL="ostk-${VERSION}-aarch64-apple-darwin.tar.gz"
    URL="https://github.com/${OSTK_REPO}/releases/download/${VERSION}/${TARBALL}"
    HTTP_CODE=$(curl -fsSL -o /dev/null -w "%{http_code}" "$URL" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        assert "ostk Mac ARM tarball downloads (HTTP 200)" 0
    else
        assert "ostk Mac ARM tarball downloads (HTTP $HTTP_CODE)" 1
    fi

    # Check Mac Intel binary
    TARBALL_INTEL="ostk-${VERSION}-x86_64-apple-darwin.tar.gz"
    URL_INTEL="https://github.com/${OSTK_REPO}/releases/download/${VERSION}/${TARBALL_INTEL}"
    HTTP_CODE_INTEL=$(curl -fsSL -o /dev/null -w "%{http_code}" "$URL_INTEL" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE_INTEL" = "200" ]; then
        assert "ostk Mac Intel tarball downloads (HTTP 200)" 0
    else
        assert "ostk Mac Intel tarball downloads (HTTP $HTTP_CODE_INTEL)" 1
    fi

    # Check Linux binary
    TARBALL_LINUX="ostk-${VERSION}-x86_64-unknown-linux-musl.tar.gz"
    URL_LINUX="https://github.com/${OSTK_REPO}/releases/download/${VERSION}/${TARBALL_LINUX}"
    HTTP_CODE_LINUX=$(curl -fsSL -o /dev/null -w "%{http_code}" "$URL_LINUX" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE_LINUX" = "200" ]; then
        assert "ostk Linux x86_64 tarball downloads (HTTP 200)" 0
    else
        assert "ostk Linux x86_64 tarball downloads (HTTP $HTTP_CODE_LINUX)" 1
    fi
else
    assert "ostk latest release exists" 1
fi

# --- myOS repo accessible ---

GH_STATUS=$(gh repo view torimeyer/myos --json name --jq '.name' 2>/dev/null || echo "")
if [ "$GH_STATUS" = "myos" ]; then
    assert "torimeyer/myos repo is accessible" 0
else
    assert "torimeyer/myos repo is accessible" 1
fi

# --- Summary ---

echo ""
TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}All $TOTAL tests passed.${NC}"
else
    echo -e "${RED}$FAIL of $TOTAL tests failed.${NC}"
    exit 1
fi
echo ""
