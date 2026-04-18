#!/usr/bin/env bash
# Regression contract: scripts/e2e_smoke.sh creates a task titled
# "e2e-crud-<timestamp>" and then lists tasks to verify it exists.
# The tasks router hides any title starting with "e2e-" unless the
# caller passes ?include_test_data=true, so without that flag the
# smoke script filters its own test task out of its own verification
# and the CRUD check fails silently.
#
# This script greps the smoke script for that specific list-verify
# curl and fails clearly if the include_test_data=true flag is missing.
#
# Usage:
#   scripts/test_smoke_list_uses_test_data_flag.sh
#
# Exit 0 = the flag is present. Exit 1 = the flag is missing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SMOKE="$SCRIPT_DIR/e2e_smoke.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

if [ ! -f "$SMOKE" ]; then
    echo -e "${RED}FAIL${NC} e2e_smoke.sh not found at $SMOKE"
    exit 1
fi

# Look for the task CRUD list-verify line. It greps the GET /api/tasks
# response for the crud_title we just created. The line must also
# contain include_test_data=true so the router does not filter our
# own test task out before we see it.
#
# Match criteria:
#   - a curl to ${API_BASE}/api/tasks
#   - with include_test_data=true in the query string
#   - piped to grep for the crud_title the smoke just created
if grep -E '/api/tasks\?[^"]*include_test_data=true' "$SMOKE" \
        | grep -q 'crud_title'; then
    echo -e "${GREEN}PASS${NC} smoke list-verify passes include_test_data=true"
    exit 0
fi

echo -e "${RED}FAIL${NC} missing include_test_data=true on smoke CRUD list-verify"
echo ""
echo "scripts/e2e_smoke.sh must pass ?include_test_data=true when verifying"
echo "its own task CRUD, otherwise the router filters the test task from"
echo "its own verification and the smoke fails."
echo ""
echo "Expected a line in $SMOKE that looks like:"
echo "  curl ... \"\${API_BASE}/api/tasks?include_test_data=true\" ... grep -qi \"\$crud_title\""
exit 1
