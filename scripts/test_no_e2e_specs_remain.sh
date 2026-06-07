#!/usr/bin/env bash
# Regression test: the /api/specs list must contain zero e2e- entries.
#
# Context: on 2026-04-15 Tori noticed 3 e2e demo specs accumulating in
# docs/draft/ because the smoke test's _e2e_sweep_artifacts helper used
# urllib.request.urlopen on an https self-signed API without an SSL
# context, so every sweep silently failed. This test asserts the
# post-smoke state is clean so that regression cannot return.
#
# Usage:
#   scripts/test_no_e2e_specs_remain.sh
#   API_BASE=https://127.0.0.1:8000 scripts/test_no_e2e_specs_remain.sh
#
# Exit 0 when zero e2e specs are present, 1 otherwise.

set -u

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

API_PORT="${API_PORT:-8000}"
SSL_CERT="$HOME/.youros/localhost.crt"
if [ -f "$SSL_CERT" ]; then
    API_BASE="${API_BASE:-https://127.0.0.1:${API_PORT}}"
    CURL_OPTS="-k"
else
    API_BASE="${API_BASE:-http://localhost:${API_PORT}}"
    CURL_OPTS=""
fi

resp=$(curl -sS $CURL_OPTS --connect-timeout 3 -m 5 "${API_BASE}/api/specs" 2>/tmp/no-e2e-specs.err || printf "")
if [ -z "$resp" ]; then
    echo -e "${RED}FAIL${NC}  could not reach ${API_BASE}/api/specs"
    exit 1
fi

count=$(echo "$resp" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print(-1); sys.exit(0)
hits = []
for x in d.get('docs', []):
    p = (x.get('path') or '').lower()
    t = (x.get('title') or '').lower()
    if 'e2e-' in p or 'e2e-' in t:
        hits.append(x.get('path'))
# Print newline-separated paths so the caller can show which specs linger.
for h in hits:
    print(h)
# Signal count via exit-visible last line.
print('COUNT=' + str(len(hits)))
" 2>/dev/null)

n=$(echo "$resp" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print(-1); sys.exit(0)
n = 0
for x in d.get('docs', []):
    p = (x.get('path') or '').lower()
    t = (x.get('title') or '').lower()
    if 'e2e-' in p or 'e2e-' in t:
        n += 1
print(n)
" 2>/dev/null)

if [ "$n" = "0" ]; then
    echo -e "${GREEN}PASS${NC}  no e2e specs remain on /api/specs"
    exit 0
fi

echo -e "${RED}FAIL${NC}  ${n} e2e spec(s) still present:"
echo "$count" | grep -v '^COUNT=' | sed 's/^/  - /'
exit 1
