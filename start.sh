#!/bin/bash
# Start myOS
# Usage: ./start.sh

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

YELLOW='\033[1;33m'

echo -e "${BLUE}Starting myOS...${NC}"

# Check for updates
echo "Checking for updates..."
CURRENT=$(git rev-parse HEAD 2>/dev/null)
git fetch --quiet origin main 2>/dev/null || true
LATEST=$(git rev-parse origin/main 2>/dev/null || echo "$CURRENT")

if [ "$CURRENT" != "$LATEST" ]; then
    echo -e "${YELLOW}Update available. Updating...${NC}"
    git pull --ff-only 2>/dev/null && {
        # Reinstall backend deps if requirements changed
        cd "$DIR/api"
        source .venv/bin/activate
        pip install -q --upgrade pip
        pip install -q -r requirements.txt
        deactivate 2>/dev/null || true
        cd "$DIR"

        # Rebuild frontend
        cd "$DIR/app"
        npm install --silent
        npm run build
        cd "$DIR"

        echo -e "${GREEN}Updated to latest version.${NC}"
    } || echo -e "${YELLOW}Could not auto-update. Continuing with current version.${NC}"
else
    echo -e "${GREEN}Already up to date.${NC}"
fi

# Update ostk if a newer version is available (best-effort, 5s timeout)
if command -v ostk &> /dev/null; then
    echo "Checking ostk..."
    OSTK_CURRENT=$(ostk --version 2>/dev/null | head -1 || echo "unknown")
    OSTK_REPO="os-tack/ostk.ai"
    OSTK_LATEST=$(timeout 5 curl -fsSL "https://api.github.com/repos/${OSTK_REPO}/releases/latest" 2>/dev/null \
        | grep '"tag_name"' | head -1 | cut -d'"' -f4 || echo "")
    if [ -n "$OSTK_LATEST" ] && [ "$OSTK_LATEST" != "$OSTK_CURRENT" ]; then
        echo -e "${YELLOW}Updating ostk to ${OSTK_LATEST}...${NC}"
        ARCH=$(uname -m); case "$ARCH" in arm64) ARCH="aarch64" ;; esac
        OS_TAG=$(uname -s); case "$OS_TAG" in Linux) OS_TAG="unknown-linux-musl" ;; Darwin) OS_TAG="apple-darwin" ;; esac
        OSTK_TMP=$(mktemp -d)
        TARBALL="ostk-${OSTK_LATEST}-${ARCH}-${OS_TAG}.tar.gz"
        if timeout 15 curl -fsSL "https://github.com/${OSTK_REPO}/releases/download/${OSTK_LATEST}/${TARBALL}" \
            -o "$OSTK_TMP/$TARBALL" 2>/dev/null; then
            tar -xzf "$OSTK_TMP/$TARBALL" -C "$OSTK_TMP"
            [ -f "$OSTK_TMP/ostk" ] && install -m 755 "$OSTK_TMP/ostk" "$(which ostk)" 2>/dev/null \
                && echo -e "${GREEN}ostk updated to ${OSTK_LATEST}.${NC}" \
                || echo -e "${YELLOW}Could not update ostk binary. Continuing.${NC}"
        else
            echo -e "${YELLOW}Could not download ostk update. Continuing.${NC}"
        fi
        rm -rf "$OSTK_TMP"
    else
        echo -e "${GREEN}ostk is up to date.${NC}"
    fi
    ostk boot 2>/dev/null || true

    # Migrate needles to ~/.myos/needles so tasks survive git pull.
    # If needles is a real directory (not already a symlink), move it
    # outside the repo and replace with a symlink.
    NEEDLES_DIR="$DIR/.ostk/needles"
    SAFE_NEEDLES="$HOME/.myos/needles"
    if [ -d "$NEEDLES_DIR" ] && [ ! -L "$NEEDLES_DIR" ]; then
        mkdir -p "$HOME/.myos"
        if [ -d "$SAFE_NEEDLES" ]; then
            # Merge: copy any files not already in the safe location
            cp -n "$NEEDLES_DIR"/* "$SAFE_NEEDLES/" 2>/dev/null || true
        else
            cp -a "$NEEDLES_DIR" "$SAFE_NEEDLES"
        fi
        rm -rf "$NEEDLES_DIR"
        ln -s "$SAFE_NEEDLES" "$NEEDLES_DIR"
        echo -e "${GREEN}Migrated tasks to ~/.myos/needles (safe from git pull).${NC}"
    elif [ ! -e "$NEEDLES_DIR" ] && [ ! -L "$NEEDLES_DIR" ]; then
        # Fresh install: create the safe dir and symlink
        mkdir -p "$SAFE_NEEDLES"
        ln -s "$SAFE_NEEDLES" "$NEEDLES_DIR"
    fi
else
    echo "ostk not installed. Skipping."
fi

# Update Claude Code if a newer version is available (best-effort, 15s timeout)
if command -v claude &> /dev/null; then
    echo "Checking Claude Code..."
    timeout 15 npm update -g @anthropic-ai/claude-code --silent 2>/dev/null \
        && echo -e "${GREEN}Claude Code is up to date.${NC}" \
        || echo -e "${YELLOW}Could not check Claude Code updates. Continuing.${NC}"
else
    echo "Claude Code not installed. Skipping."
fi

# Check if frontend is built
if [ ! -d "$DIR/app/dist" ]; then
    echo "Building the frontend (first time only)..."
    cd "$DIR/app"
    npm run build
    cd "$DIR"
fi

# Start the API server (serves both API and frontend)
cd "$DIR/api"
if [ ! -f .venv/bin/activate ]; then
    echo "Python environment not found. Run install.sh first."
    exit 1
fi
source .venv/bin/activate

echo -e "${GREEN}myOS is starting at http://localhost:8000${NC}"
echo "Keep this window open while using myOS. Press Ctrl+C to stop."

# Open the browser after a brief delay
if [[ "$(uname)" == "Darwin" ]]; then
    (sleep 2 && open http://localhost:8000) &
else
    (sleep 2 && xdg-open http://localhost:8000 2>/dev/null) &
fi

# Reload watch is scoped to the api/ runtime code only.
# Test files, pytest cache, and pyc files must NOT trigger a reload, because
# background agents edit them frequently and a reload mid-stream kills any
# in-flight chat WebSocket. Routers and services SHOULD still reload so real
# code changes are picked up. Reload-delay batches back-to-back writes into
# a single reload instead of many.
#
# Note: cwd here is $DIR/api, so --reload-dir api uses the absolute path to
# keep the intent obvious in the command line.
exec uvicorn main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --reload \
    --reload-dir "$DIR/api" \
    --reload-exclude 'api/tests/*' \
    --reload-exclude 'api/tests/**/*' \
    --reload-exclude 'tests/*' \
    --reload-exclude 'tests/**/*' \
    --reload-exclude '**/.pytest_cache/*' \
    --reload-exclude '**/__pycache__/*' \
    --reload-exclude '**/*.pyc' \
    --reload-delay 1.0 \
    --no-access-log
