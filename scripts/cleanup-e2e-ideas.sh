#!/usr/bin/env bash
# cleanup-e2e-ideas.sh
#
# Finds every idea in the live Ideas store whose title starts with
# "e2e-idea-" and deletes it. Run this once to clean up test pollution
# left by previous smoke-test runs.
#
# Usage:
#   ./scripts/cleanup-e2e-ideas.sh
#   API_PORT=8001 ./scripts/cleanup-e2e-ideas.sh

set -euo pipefail

API_PORT="${API_PORT:-8000}"
API_BASE="https://127.0.0.1:${API_PORT}"
CURL_OPTS="-sSk --connect-timeout 3 -m 5"

echo "Scanning for e2e-idea-* entries on ${API_BASE} ..."

# Fetch active ideas with test data visible.
active_json=$(curl $CURL_OPTS "${API_BASE}/api/ideas?include_test_data=true" 2>/dev/null || true)

if [ -z "$active_json" ]; then
    echo "ERROR: Could not reach backend at ${API_BASE}. Is it running?" >&2
    exit 1
fi

# Extract all e2e-idea-* straws from unclustered and clusters.
straws=$(python3 - <<'PYEOF' "$active_json"
import sys, json, urllib.parse

raw = sys.argv[1]
try:
    data = json.loads(raw)
except Exception as e:
    print(f"PARSE_ERROR: {e}", file=sys.stderr)
    sys.exit(1)

items = list(data.get("unclustered", []))
for c in data.get("clusters", []):
    items.extend(c.get("items", []))

for item in items:
    straw = item if isinstance(item, str) else item.get("straw", "")
    if straw.startswith("e2e-idea-"):
        print(urllib.parse.quote(straw, safe=""))
PYEOF
)

if [ -z "$straws" ]; then
    echo "No e2e-idea-* entries found. Ideas page is clean."
    exit 0
fi

removed=0
failed=0
while IFS= read -r encoded; do
    [ -z "$encoded" ] && continue
    # Delete from active list.
    resp=$(curl $CURL_OPTS -X DELETE "${API_BASE}/api/ideas/${encoded}" 2>/dev/null || true)
    # Delete from converted list too (belt and suspenders).
    curl $CURL_OPTS -X DELETE "${API_BASE}/api/ideas/converted/${encoded}" > /dev/null 2>&1 || true
    if echo "$resp" | grep -qE '"result"|"deleted"'; then
        decoded=$(python3 -c "import urllib.parse,sys; print(urllib.parse.unquote(sys.argv[1]))" "$encoded" 2>/dev/null || echo "$encoded")
        echo "  deleted: $decoded"
        removed=$((removed + 1))
    else
        echo "  WARN: unexpected response for $encoded: $resp" >&2
        failed=$((failed + 1))
    fi
done <<< "$straws"

echo ""
echo "Done. Removed: ${removed}  Failures: ${failed}"
[ "$failed" -eq 0 ] && exit 0 || exit 1
