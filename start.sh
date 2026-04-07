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

# Boot ostk kernel (best-effort, continues if ostk is not installed)
if command -v ostk &> /dev/null; then
    ostk boot 2>/dev/null || true
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

exec uvicorn main:app --host 127.0.0.1 --port 8000
