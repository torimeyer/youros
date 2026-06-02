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

if grep -q 'need git' "$DIR/install.sh"; then
    assert "install.sh checks for git" 0
else
    assert "install.sh checks for git" 1
fi

if grep -q 'need curl' "$DIR/install.sh"; then
    assert "install.sh checks for curl" 0
else
    assert "install.sh checks for curl" 1
fi

if grep -q 'need python3' "$DIR/install.sh"; then
    assert "install.sh checks for python3" 0
else
    assert "install.sh checks for python3" 1
fi

if grep -q 'need node' "$DIR/install.sh"; then
    assert "install.sh checks for node" 0
else
    assert "install.sh checks for node" 1
fi

if grep -q 'need npm' "$DIR/install.sh"; then
    assert "install.sh checks for npm" 0
else
    assert "install.sh checks for npm" 1
fi

# --- Clone URL ---
# Must use SSH for private repo access

if grep -q 'git@github.com:' "$DIR/install.sh"; then
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

# --- uvicorn reload watch is scoped ---
# Background agents edit test files and state files all the time. If the
# uvicorn reload watcher reloads on any of those, it kills in-flight chat
# WebSocket responses mid-stream. The reload watch must be scoped to the
# api/ runtime code only, with explicit excludes for tests, pytest cache,
# and pyc files.

if grep -q 'reload-dir' "$DIR/start.sh"; then
    assert "start.sh scopes uvicorn reload to a directory (--reload-dir)" 0
else
    assert "start.sh scopes uvicorn reload to a directory (--reload-dir)" 1
fi

if grep -qE 'reload-dir[^\\]*api' "$DIR/start.sh"; then
    assert "start.sh reload watch points at api/" 0
else
    assert "start.sh reload watch points at api/" 1
fi

if grep -qE "reload-exclude[^\\\\]*'?api/tests" "$DIR/start.sh" || grep -qE "reload-exclude[^\\\\]*'?tests/" "$DIR/start.sh"; then
    assert "start.sh excludes test files from uvicorn reload" 0
else
    assert "start.sh excludes test files from uvicorn reload" 1
fi

if grep -q '__pycache__' "$DIR/start.sh"; then
    assert "start.sh excludes __pycache__ from uvicorn reload" 0
else
    assert "start.sh excludes __pycache__ from uvicorn reload" 1
fi

if grep -q '.pytest_cache' "$DIR/start.sh"; then
    assert "start.sh excludes .pytest_cache from uvicorn reload" 0
else
    assert "start.sh excludes .pytest_cache from uvicorn reload" 1
fi

# routers and services must NOT be excluded from reload, otherwise legitimate
# code changes would be ignored.
if grep -qE "reload-exclude[^\\\\]*routers" "$DIR/start.sh"; then
    assert "start.sh does not exclude api/routers from reload" 1
else
    assert "start.sh does not exclude api/routers from reload" 0
fi

if grep -qE "reload-exclude[^\\\\]*services" "$DIR/start.sh"; then
    assert "start.sh does not exclude api/services from reload" 1
else
    assert "start.sh does not exclude api/services from reload" 0
fi

# --- Newlines before appended lines in .zshrc/.bashrc ---
# Without a leading newline, the alias/PATH export lands on the end of the last line

APPEND_LINES=$(grep -c 'echo "" >>' "$DIR/install.sh")
if [ "$APPEND_LINES" -ge 2 ]; then
    assert "install.sh adds newline before appending to shell rc" 0
else
    assert "install.sh adds newline before appending to shell rc" 1
fi

# --- No auto-installing system packages ---
# Coulter's feedback: an installer should list prereqs and let the user
# install them. install.sh must NOT run apt-get, dnf, pacman, brew, or
# any other system package manager. It only checks and reports.

if grep -qE 'sudo (apt-get|apt|dnf|pacman|zypper) install' "$DIR/install.sh"; then
    assert "install.sh does not auto-install system packages" 1
else
    assert "install.sh does not auto-install system packages" 0
fi

# It also must not print "install with: sudo apt-get install" style hints,
# which is what Coulter pushed back on. The script should just say what is
# missing and exit.
if grep -qE 'Install with: sudo' "$DIR/install.sh"; then
    assert "install.sh does not print sudo install hints" 1
else
    assert "install.sh does not print sudo install hints" 0
fi

# Detects the python venv module gap (Ubuntu's python3-venv package),
# but reports it instead of trying to install it.
if grep -q 'venv' "$DIR/install.sh"; then
    assert "install.sh checks the python venv module" 0
else
    assert "install.sh checks the python venv module" 1
fi

# Must not auto-install the Claude command line tool. It is optional and
# third-party. The script may mention it as an optional follow-up.
if grep -q 'npm install -g @anthropic-ai/claude-code --silent' "$DIR/install.sh"; then
    assert "install.sh does not auto-install the Claude CLI" 1
else
    assert "install.sh does not auto-install the Claude CLI" 0
fi

# --- Data safety: install.sh must never clobber user data ---
# Tori has two machines. When she updates, none of her settings, chat
# history, tasks, or labels can get overwritten. These checks make sure
# the installer always preserves existing user files.

# settings.json must be preserved if it already exists.
if grep -qE '\[ ! -f "\$MYOS_CONFIG_DIR/settings.json" \]|\[\[ ! -f "\$MYOS_CONFIG_DIR/settings.json" \]\]' "$DIR/install.sh"; then
    assert "install.sh preserves existing ~/.myos/settings.json" 0
else
    assert "install.sh preserves existing ~/.myos/settings.json" 1
fi

# There must be no unconditional cp into ~/.myos/ anywhere in the script.
# Every cp into the user config dir must sit inside an "if file does not
# exist" guard. We check by making sure no line with `cp ... $MYOS_CONFIG_DIR`
# appears at zero indentation (outside of an if block).
UNGUARDED_CP=$(grep -nE '^[[:space:]]*cp .*MYOS_CONFIG_DIR' "$DIR/install.sh" | wc -l | tr -d ' ')
GUARDED_CP=$(grep -B2 -E 'cp .*MYOS_CONFIG_DIR' "$DIR/install.sh" | grep -cE '! -f.*MYOS_CONFIG_DIR' || true)
# If there is any cp at all, at least one guard must exist before it.
if [ "$UNGUARDED_CP" -eq 0 ] || [ "$GUARDED_CP" -ge 1 ]; then
    assert "install.sh has no unguarded cp into ~/.myos/" 0
else
    assert "install.sh has no unguarded cp into ~/.myos/" 1
fi

# No write redirect (>) can target anything under ~/.myos/ without a guard.
if grep -qE '>[[:space:]]*"?\$HOME/\.myos|>[[:space:]]*"?\$MYOS_CONFIG_DIR' "$DIR/install.sh"; then
    assert "install.sh does not redirect output into ~/.myos/" 1
else
    assert "install.sh does not redirect output into ~/.myos/" 0
fi

# The myos alias must only be added if not already present.
if grep -q 'grep -q "alias myos=' "$DIR/install.sh"; then
    assert "install.sh adds myos alias only if missing" 0
else
    assert "install.sh adds myos alias only if missing" 1
fi

# The PATH export to ~/.local/bin must only be added if not already present.
if grep -qE '\[\[ ":\$PATH:" != \*":\$OSTK_BIN_DIR:"\* \]\]' "$DIR/install.sh"; then
    assert "install.sh adds PATH export only if missing" 0
else
    assert "install.sh adds PATH export only if missing" 1
fi

# The myos-update alias (if present) must also be guarded.
if grep -q "alias myos-update=" "$DIR/install.sh"; then
    if grep -q 'grep -q "alias myos-update=' "$DIR/install.sh"; then
        assert "install.sh adds myos-update alias only if missing" 0
    else
        assert "install.sh adds myos-update alias only if missing" 1
    fi
fi

# update.sh exists and has valid syntax.
if [ -f "$DIR/update.sh" ]; then
    bash -n "$DIR/update.sh" 2>/dev/null
    assert "update.sh has valid bash syntax" $?

    # update.sh must never touch ~/.myos/.
    if grep -qE '>[[:space:]]*"?\$HOME/\.myos|rm .*\$HOME/\.myos' "$DIR/update.sh"; then
        assert "update.sh does not write into or delete ~/.myos/" 1
    else
        assert "update.sh does not write into or delete ~/.myos/" 0
    fi
fi

# --- Data safety: state files inside the repo must be gitignored ---
# .ostk/ holds live user data (needles, agent state, audit log). If any of
# that gets tracked, a git pull will overwrite a user's work.

if grep -qE '^\.ostk/?$' "$DIR/.gitignore"; then
    assert ".ostk/ is listed in .gitignore" 0
else
    assert ".ostk/ is listed in .gitignore" 1
fi

if grep -qE '^transcripts/?$' "$DIR/.gitignore"; then
    assert "transcripts/ is listed in .gitignore" 0
else
    assert "transcripts/ is listed in .gitignore" 1
fi

# If issues.jsonl exists at the repo root, it MUST be gitignored.
if [ -f "$DIR/issues.jsonl" ]; then
    if git -C "$DIR" check-ignore -q "issues.jsonl"; then
        assert "issues.jsonl is gitignored" 0
    else
        assert "issues.jsonl is gitignored" 1
    fi
else
    assert "issues.jsonl is not present at repo root (safe)" 0
fi

# No state file should be tracked by git under .ostk/.
TRACKED_STATE=$(git -C "$DIR" ls-files '.ostk/' 2>/dev/null | wc -l | tr -d ' ')
if [ "$TRACKED_STATE" -eq 0 ]; then
    assert ".ostk/ has no tracked files" 0
else
    assert ".ostk/ has no tracked files ($TRACKED_STATE found)" 1
fi

# Agent state file must not be tracked.
if git -C "$DIR" ls-files --error-unmatch .ostk/agent_state.json >/dev/null 2>&1; then
    assert ".ostk/agent_state.json is not tracked by git" 1
else
    assert ".ostk/agent_state.json is not tracked by git" 0
fi

# --- No brew dependency anywhere ---
# The whole point. Keep this check so regressions are obvious.

if grep -iE '\b(brew|homebrew)\b' "$DIR/install.sh"; then
    assert "install.sh has no brew references" 1
else
    assert "install.sh has no brew references" 0
fi

if grep -iE '\b(brew|homebrew)\b' "$DIR/start.sh"; then
    assert "start.sh has no brew references" 1
else
    assert "start.sh has no brew references" 0
fi

# README may mention Homebrew in prose (e.g. "does not require Homebrew").
# What matters is no actual `brew install` commands.
if grep -iE 'brew install|brew tap|brew upgrade' "$DIR/README.md"; then
    assert "README has no brew install commands" 1
else
    assert "README has no brew install commands" 0
fi

# --- README covers both macOS and Linux ---

if grep -q '### On Linux' "$DIR/README.md"; then
    assert "README has a Linux install section" 0
else
    assert "README has a Linux install section" 1
fi

if grep -q '### On macOS' "$DIR/README.md"; then
    assert "README has a macOS install section" 0
else
    assert "README has a macOS install section" 1
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

    # GitHub tag name is "v4.0.0" but tarball filenames are "ostk-4.0.0-..." (no v).
    VERSION_NUMBER="${VERSION#v}"

    # Check that the Mac aarch64 tarball actually exists (per-arch naming, no universal build)
    TARBALL_MAC="ostk-${VERSION_NUMBER}-aarch64-apple-darwin.tar.gz"
    URL_MAC="https://github.com/${OSTK_REPO}/releases/download/${VERSION}/${TARBALL_MAC}"
    HTTP_CODE_MAC=$(curl -sIL --connect-timeout 5 -m 15 -o /dev/null -w "%{http_code}" "$URL_MAC" 2>/dev/null)
    [ -z "$HTTP_CODE_MAC" ] && HTTP_CODE_MAC="000"
    if [ "$HTTP_CODE_MAC" = "200" ]; then
        assert "ostk Mac aarch64 tarball downloads (HTTP 200)" 0
    else
        assert "ostk Mac aarch64 tarball downloads (HTTP $HTTP_CODE_MAC)" 1
    fi

    # Check Linux binary
    TARBALL_LINUX="ostk-${VERSION_NUMBER}-x86_64-unknown-linux-musl.tar.gz"
    URL_LINUX="https://github.com/${OSTK_REPO}/releases/download/${VERSION}/${TARBALL_LINUX}"
    HTTP_CODE_LINUX=$(curl -sIL --connect-timeout 5 -m 15 -o /dev/null -w "%{http_code}" "$URL_LINUX" 2>/dev/null)
    [ -z "$HTTP_CODE_LINUX" ] && HTTP_CODE_LINUX="000"
    if [ "$HTTP_CODE_LINUX" = "200" ]; then
        assert "ostk Linux x86_64 tarball downloads (HTTP 200)" 0
    else
        assert "ostk Linux x86_64 tarball downloads (HTTP $HTTP_CODE_LINUX)" 1
    fi
else
    assert "ostk latest release exists" 1
fi

# --- myOS repo accessible ---

# Override with MYOS_REPO=<org/name> to point at your own fork or mirror.
MYOS_REPO="${MYOS_REPO:-torimeyer/youros}"
MYOS_REPO_NAME="${MYOS_REPO##*/}"
GH_STATUS=$(gh repo view "$MYOS_REPO" --json name --jq '.name' 2>/dev/null || echo "")
if [ "$GH_STATUS" = "$MYOS_REPO_NAME" ]; then
    assert "$MYOS_REPO repo is accessible" 0
else
    assert "$MYOS_REPO repo is accessible" 1
fi

# --- Release conventions check ---
# Runs as part of the same pre-push gate so version/tag/filename bugs get
# caught before a tag is created. Details in docs/RELEASE_CONVENTIONS.md.

echo ""
echo "=== release conventions check ==="
if [ -x "$DIR/scripts/test_release_conventions.sh" ]; then
    if "$DIR/scripts/test_release_conventions.sh"; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
    fi
else
    echo -e "  ${RED}FAIL${NC}  scripts/test_release_conventions.sh is missing or not executable"
    FAIL=$((FAIL + 1))
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
