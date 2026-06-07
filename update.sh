#!/bin/bash
# myOS safe updater.
# Usage: ./update.sh (from inside the repo)
#
# This script updates myOS without ever touching your settings, chat
# history, labels, or tasks. Your data lives in ~/.youros/ and the update
# process never writes there. See the project README for the full story.

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${BLUE}=== myOS Safe Update ===${NC}"
echo ""

# --- Make sure we are in a myOS repo ---

if [ ! -f "$SCRIPT_DIR/install.sh" ] || [ ! -d "$SCRIPT_DIR/api" ] || [ ! -d "$SCRIPT_DIR/app" ]; then
    echo -e "${RED}This does not look like a myOS folder.${NC}"
    echo "Run ./update.sh from inside your myOS folder (usually ~/myos)."
    exit 1
fi

# --- Show what the user already has in ~/.youros/ so they can compare ---

if [ -d "$HOME/.youros" ]; then
    echo "Your personal data lives here and will NOT be touched:"
    echo ""
    ls -la "$HOME/.youros/" 2>/dev/null | tail -n +2 | sed 's/^/  /'
    echo ""
fi

# --- Check the current state of the repo ---

echo "Checking for new updates..."
git fetch 2>/dev/null || {
    echo -e "${YELLOW}Could not reach GitHub. Check your internet connection and try again.${NC}"
    exit 1
}

LOCAL=$(git rev-parse HEAD 2>/dev/null)
REMOTE=$(git rev-parse @{u} 2>/dev/null || echo "")

if [ -z "$REMOTE" ]; then
    echo -e "${YELLOW}Could not compare with the remote. Skipping the update step.${NC}"
    echo "You can still re-run the installer if you want:"
    echo "  ./install.sh"
    exit 0
fi

if [ "$LOCAL" = "$REMOTE" ]; then
    echo -e "${GREEN}Already up to date.${NC}"
    NEED_PULL=0
else
    # Show what is new.
    echo ""
    echo -e "${BLUE}New changes since your last update:${NC}"
    echo ""
    git log --oneline --no-decorate "$LOCAL..$REMOTE" | head -20 | sed 's/^/  /'
    echo ""
    NEED_PULL=1
fi

# --- Handle any uncommitted changes on this computer ---

STASHED=0
if ! git diff-index --quiet HEAD -- 2>/dev/null; then
    echo -e "${YELLOW}You have unsaved code changes on this computer.${NC}"
    echo ""
    echo "The updater will tuck them into a safe pocket so the update can"
    echo "run cleanly. You can bring them back any time with:"
    echo "  git stash pop"
    echo ""
    read -p "Is that OK? [y/N] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Update cancelled. Your files are unchanged."
        exit 0
    fi
    STASH_MSG="myos safe-update auto stash $(date +%Y-%m-%d_%H:%M:%S)"
    git stash push -u -m "$STASH_MSG" >/dev/null
    STASHED=1
    echo -e "${GREEN}Your changes are safely tucked away.${NC}"
    echo "To bring them back later, run: git stash pop"
    echo ""
fi

# --- Pull the latest version ---

if [ "$NEED_PULL" -eq 1 ]; then
    read -p "Pull these changes now? [Y/n] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        echo "Update cancelled. Your files are unchanged."
        if [ "$STASHED" -eq 1 ]; then
            echo "Restoring your tucked-away changes..."
            git stash pop >/dev/null
        fi
        exit 0
    fi

    echo ""
    echo "Pulling..."
    if ! git pull --ff-only 2>&1; then
        echo -e "${RED}Could not pull cleanly.${NC}"
        echo "See the project README for what to do next."
        if [ "$STASHED" -eq 1 ]; then
            echo "Your unsaved changes are safe. Get them back with: git stash pop"
        fi
        exit 1
    fi
    echo -e "${GREEN}Latest version downloaded.${NC}"
    echo ""
fi

# --- Upgrade ostk binary if a newer version is available ---

upgrade_ostk() {
    if ! command -v ostk &> /dev/null; then
        echo "ostk is not installed. Run ./install.sh to set it up."
        return 0
    fi

    CURRENT_VERSION=$(ostk --version 2>/dev/null | awk '{print $2}' | sed 's/^v//')
    if [ -z "$CURRENT_VERSION" ]; then
        echo -e "${YELLOW}Could not read installed ostk version. Skipping upgrade check.${NC}"
        return 0
    fi

    echo "Checking for ostk upgrades (installed: $CURRENT_VERSION)..."

    LATEST_TAG=$(curl -fsSL "https://api.github.com/repos/os-tack/ostk.ai/releases/latest" \
        2>/dev/null | grep '"tag_name"' | head -1 | cut -d'"' -f4)

    if [ -z "$LATEST_TAG" ]; then
        echo -e "${YELLOW}Could not reach GitHub to check for ostk updates. Skipping.${NC}"
        return 0
    fi

    LATEST_VERSION=$(echo "$LATEST_TAG" | sed 's/^v//')

    if [ "$CURRENT_VERSION" = "$LATEST_VERSION" ]; then
        echo -e "${GREEN}ostk is already on the latest version ($CURRENT_VERSION).${NC}"
        return 0
    fi

    # Simple semver comparison: split into major.minor.patch and compare numerically.
    compare_versions() {
        local v1="$1" v2="$2"
        local IFS=.
        local -a a1=($v1) a2=($v2)
        for i in 0 1 2; do
            local n1="${a1[$i]:-0}" n2="${a2[$i]:-0}"
            if [ "$n1" -lt "$n2" ]; then echo "lt"; return; fi
            if [ "$n1" -gt "$n2" ]; then echo "gt"; return; fi
        done
        echo "eq"
    }

    RESULT=$(compare_versions "$CURRENT_VERSION" "$LATEST_VERSION")
    if [ "$RESULT" != "lt" ]; then
        echo -e "${GREEN}ostk is up to date ($CURRENT_VERSION).${NC}"
        return 0
    fi

    echo -e "${BLUE}Upgrading ostk from $CURRENT_VERSION to $LATEST_VERSION...${NC}"

    OSTK_BIN_DIR="$HOME/.local/bin"
    mkdir -p "$OSTK_BIN_DIR"

    ARCH=$(uname -m)
    case "$ARCH" in
        arm64) ARCH="aarch64" ;;
    esac

    OS_TAG=$(uname -s)
    case "$OS_TAG" in
        Linux)  OS_TAG="unknown-linux-musl" ;;
        Darwin) OS_TAG="apple-darwin" ;;
        *)
            echo -e "${YELLOW}Unsupported OS for ostk upgrade. Skipping.${NC}"
            return 0
            ;;
    esac

    PLATFORM="${ARCH}-${OS_TAG}"
    OSTK_REPO="os-tack/ostk.ai"
    TARBALL="ostk-${LATEST_TAG}-${PLATFORM}.tar.gz"
    OSTK_URL="https://github.com/${OSTK_REPO}/releases/download/${LATEST_TAG}/${TARBALL}"
    OSTK_TMP=$(mktemp -d)

    if curl -fsSL "$OSTK_URL" -o "$OSTK_TMP/$TARBALL" 2>/dev/null; then
        tar -xzf "$OSTK_TMP/$TARBALL" -C "$OSTK_TMP"
        if [ -f "$OSTK_TMP/ostk" ]; then
            OSTK_CURRENT_PATH=$(command -v ostk)
            install -m 755 "$OSTK_TMP/ostk" "$OSTK_CURRENT_PATH"
            echo -e "${GREEN}ostk upgraded to $LATEST_VERSION.${NC}"
        else
            echo -e "${YELLOW}Could not find ostk binary in the downloaded archive. Skipping upgrade.${NC}"
        fi
    else
        echo -e "${YELLOW}Could not download the new ostk release. Skipping upgrade.${NC}"
    fi

    rm -rf "$OSTK_TMP"
}

upgrade_ostk
echo ""

# --- Upgrade Gemini CLI ---

upgrade_gemini() {
    if ! command -v gemini &> /dev/null; then
        echo "Gemini CLI is not installed. Skipping upgrade check."
        return 0
    fi

    echo "Checking for Gemini CLI upgrades..."
    if npm update -g @google/gemini-cli --silent 2>/dev/null; then
        echo -e "${GREEN}Gemini CLI updated to latest version.${NC}"
    else
        echo -e "${YELLOW}Could not update Gemini CLI. Continuing.${NC}"
    fi
}

upgrade_gemini
echo ""

# --- Re-run the installer to refresh dependencies ---

# Capture the commit we started from so we can roll back on smoke failure.
LOCAL_BEFORE=$(git rev-parse HEAD 2>/dev/null || echo "")

echo "Refreshing the program (this will not touch your settings)..."
echo ""
if bash "$SCRIPT_DIR/install.sh"; then
    echo -e "${GREEN}Install step complete.${NC}"
else
    echo -e "${RED}The installer reported an error. Your data is still safe in ~/.youros/.${NC}"
    if [ "$STASHED" -eq 1 ]; then
        echo "Your unsaved changes are still in the stash. Get them back with: git stash pop"
    fi
    exit 1
fi

# --- Run smoke test (skipped unless RELEASE_MODE=1 or e2e_smoke.sh exists) ---

if [ "${RELEASE_MODE:-0}" = "1" ] && [ -x "$SCRIPT_DIR/scripts/e2e_smoke.sh" ]; then
    echo ""
    echo "Running smoke test..."
    if RELEASE_MODE=1 bash "$SCRIPT_DIR/scripts/e2e_smoke.sh"; then
        echo -e "${GREEN}Smoke test passed.${NC}"
    else
        echo -e "${RED}Smoke test failed. Rolling back to $LOCAL_BEFORE...${NC}"
        if [ -n "$LOCAL_BEFORE" ]; then
            git reset --hard "$LOCAL_BEFORE" 2>/dev/null || true
            echo "Reinstalling previous version..."
            bash "$SCRIPT_DIR/install.sh" 2>/dev/null || true
            # Restart launchd agents so they pick up the rolled-back code.
            if [ "$(uname)" = "Darwin" ]; then
                _uid=$(id -u)
                for label in com.youros.backend com.youros.watchdog; do
                    launchctl kickstart -k "gui/${_uid}/${label}" 2>/dev/null || true
                done
            fi
        fi
        echo -e "${RED}Update rolled back. Run 'myos' to start the previous version.${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}=== Update complete! ===${NC}"

# --- Remind the user about the stash ---

if [ "$STASHED" -eq 1 ]; then
    echo ""
    echo -e "${YELLOW}Reminder: you had unsaved changes before the update.${NC}"
    echo "They are safe. Bring them back with:"
    echo "  git stash pop"
fi

# --- Confirm the user's data is still there ---

echo ""
if [ -d "$HOME/.youros" ]; then
    echo "Your personal data is still here (unchanged):"
    echo ""
    ls -la "$HOME/.youros/" 2>/dev/null | tail -n +2 | sed 's/^/  /'
    echo ""
fi

echo "Open a new Terminal window and type 'myos' to start."
echo ""
