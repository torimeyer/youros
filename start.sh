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

echo -e "${BLUE}Starting myOS...${NC}"

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
