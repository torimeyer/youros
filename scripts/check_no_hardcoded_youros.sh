#!/bin/bash
# Fail if any code under api/ or scripts/ builds the data dir by hand WITHOUT
# honoring YOUROS_HOME. This keeps the single-root invariant from rotting after
# the P2 migration: a future PR can otherwise reintroduce a hardcoded ~/.youros
# and the throwaway-profile guarantee quietly breaks.
#
# Usage: check_no_hardcoded_youros.sh [ROOT]   (default: git repo root)
#
# A line is OK if it goes through the resolver (api/services/youros_paths.py) OR
# explicitly references YOUROS_HOME (the documented fallback standalone scripts
# use, since they cannot import the api package). Anything else is a bypass.
# Allowlisted: the resolver, test files, this guard's self-test, binaries.

set -u
ROOT="${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

DIRS=()
[ -d "$ROOT/api" ] && DIRS+=("$ROOT/api")
[ -d "$ROOT/scripts" ] && DIRS+=("$ROOT/scripts")
if [ "${#DIRS[@]}" -eq 0 ]; then
    echo "OK: nothing to scan under $ROOT"
    exit 0
fi

# `.` in the regex matches the surrounding quote, catching both ".youros" and
# '.youros' / "~/.youros" / '~/.youros'. -I skips binaries (stale .pyc).
hits="$(grep -rnEI --exclude-dir=__pycache__ --exclude='*.pyc' \
    -e 'Path\.home\(\)[[:space:]]*/[[:space:]]*.\.youros' \
    -e 'expanduser\(.~/\.youros' \
    "${DIRS[@]}" 2>/dev/null \
    | grep -v '/youros_paths.py:' \
    | grep -v 'YOUROS_HOME' \
    | grep -vE '/tests?/|/test_[^/]*\.py:|_test\.py:|check_no_hardcoded_youros' )"

if [ -n "$hits" ]; then
    echo "FAIL: data-dir paths that ignore YOUROS_HOME (use api/services/youros_paths.py,"
    echo "      or reference YOUROS_HOME in standalone scripts):"
    echo "$hits"
    exit 1
fi
echo "OK: no data-dir paths bypass YOUROS_HOME."
exit 0
