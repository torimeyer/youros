#!/bin/bash
# Run the yourOS MCP stdio server.
# Activates the api/.venv and starts the server module on stdin/stdout.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
API_DIR="$REPO_ROOT/api"

if [ ! -f "$API_DIR/.venv/bin/activate" ]; then
    echo "Python virtualenv not found at $API_DIR/.venv. Run install.sh first." >&2
    exit 1
fi

cd "$API_DIR"
# shellcheck source=/dev/null
source .venv/bin/activate

exec python -m mcp_server
