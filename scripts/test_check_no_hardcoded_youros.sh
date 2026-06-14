#!/bin/bash
# Self-test for check_no_hardcoded_youros.sh, run against temp fixture roots so
# it never depends on the real repo's current migration state.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GUARD="$SCRIPT_DIR/check_no_hardcoded_youros.sh"
GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
PASS=0; FAIL=0
ok(){ printf "  ${GREEN}PASS${NC} %s\n" "$1"; PASS=$((PASS+1)); }
bad(){ printf "  ${RED}FAIL${NC} %s\n" "$1"; FAIL=$((FAIL+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# Case A: an offending file -> exit 1, names the file
mkdir -p "$TMP/a/api/services" "$TMP/a/scripts"
printf 'P = Path.home() / ".youros" / "x.json"\n' > "$TMP/a/api/services/foo.py"
out="$(bash "$GUARD" "$TMP/a" 2>&1)"; rc=$?
[ "$rc" = "1" ] && ok "offender -> exit 1" || bad "offender -> exit 1 (got $rc)"
case "$out" in *"foo.py"*) ok "offender named in output" ;; *) bad "offender named" ;; esac

# Case B: only resolver + a test file mention it -> exit 0 (allowlisted)
mkdir -p "$TMP/b/api/services" "$TMP/b/api/tests"
printf 'BASE = Path.home() / ".youros"\n' > "$TMP/b/api/services/youros_paths.py"
printf 'p = Path.home() / ".youros" / "t"\n' > "$TMP/b/api/tests/test_x.py"
out="$(bash "$GUARD" "$TMP/b" 2>&1)"; rc=$?
[ "$rc" = "0" ] && ok "resolver+test allowlisted -> exit 0" || { bad "allowlist -> exit 0 (got $rc)"; echo "$out"; }

# Case C: clean tree -> exit 0
mkdir -p "$TMP/c/api/services"
printf 'from services.youros_paths import youros_home\np = youros_home() / "x"\n' > "$TMP/c/api/services/bar.py"
out="$(bash "$GUARD" "$TMP/c" 2>&1)"; rc=$?
[ "$rc" = "0" ] && ok "clean tree -> exit 0" || { bad "clean -> exit 0 (got $rc)"; echo "$out"; }

echo ""; echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
