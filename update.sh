#!/bin/bash
# myOS safe updater.
# Usage: ./update.sh (from inside the repo)
#
# This script updates myOS without ever touching your settings, chat
# history, labels, or tasks. Your data lives in ~/.myos/ and the update
# process never writes there. See docs/SAFE_UPDATE.md for the full story.

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

# --- Show what the user already has in ~/.myos/ so they can compare ---

if [ -d "$HOME/.myos" ]; then
    echo "Your personal data lives here and will NOT be touched:"
    echo ""
    ls -la "$HOME/.myos/" 2>/dev/null | tail -n +2 | sed 's/^/  /'
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
        echo "See docs/SAFE_UPDATE.md for what to do next."
        if [ "$STASHED" -eq 1 ]; then
            echo "Your unsaved changes are safe. Get them back with: git stash pop"
        fi
        exit 1
    fi
    echo -e "${GREEN}Latest version downloaded.${NC}"
    echo ""
fi

# --- Re-run the installer to refresh dependencies ---

echo "Refreshing the program (this will not touch your settings)..."
echo ""
if bash "$SCRIPT_DIR/install.sh"; then
    echo -e "${GREEN}=== Update complete! ===${NC}"
else
    echo -e "${RED}The installer reported an error. Your data is still safe in ~/.myos/.${NC}"
    if [ "$STASHED" -eq 1 ]; then
        echo "Your unsaved changes are still in the stash. Get them back with: git stash pop"
    fi
    exit 1
fi

# --- Remind the user about the stash ---

if [ "$STASHED" -eq 1 ]; then
    echo ""
    echo -e "${YELLOW}Reminder: you had unsaved changes before the update.${NC}"
    echo "They are safe. Bring them back with:"
    echo "  git stash pop"
fi

# --- Confirm the user's data is still there ---

echo ""
if [ -d "$HOME/.myos" ]; then
    echo "Your personal data is still here (unchanged):"
    echo ""
    ls -la "$HOME/.myos/" 2>/dev/null | tail -n +2 | sed 's/^/  /'
    echo ""
fi

echo "Open a new Terminal window and type 'myos' to start."
echo ""
