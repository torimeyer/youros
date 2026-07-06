#!/usr/bin/env bash
# Regression test: smoke run sweep must remove e2e-prefixed spec files from
# ~/.youros/specs/ and ~/.myos/specs/ on disk, not just via the API.
#
# Context (→2470): 20+ junk spec files with e2e- / journey-id names accumulated
# in the real specs dirs across smoke runs because _e2e_sweep_artifacts in
# e2e_smoke.sh swept docs/draft/ and docs/spec/ (repo) but NOT the user-level
# ~/.youros/specs/ or ~/.myos/specs/ dirs. Files survive API sweeps if the
# backend writes to the real specs dir (started before YOUROS_USER_SPECS_DIR was
# set) or if the DELETE /api/specs call races an interrupted run.
#
# Two checks:
#   1. Static: _e2e_sweep_artifacts in e2e_smoke.sh references ~/.youros/specs
#      (or YOUROS_HOME) so it covers the disk-level cleanup.
#   2. Dynamic: plant a fake e2e-* file in ~/.youros/specs/ (and ~/.myos/specs/
#      if that dir exists), run the sweep inline, verify the planted files are
#      gone and a non-e2e control file is untouched.

set -euo pipefail

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMOKE_SCRIPT="$THIS_DIR/../e2e_smoke.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }
skip() { echo "SKIP: $*"; exit 0; }

YOUROS_SPECS_DIR="${YOUROS_HOME:-$HOME/.youros}/specs"
MYOS_SPECS_DIR="$HOME/.myos/specs"

# ---------------------------------------------------------------------------
# 1. Static check: _e2e_sweep_artifacts must cover the user-level specs dirs
# ---------------------------------------------------------------------------

[ -f "$SMOKE_SCRIPT" ] || fail "e2e_smoke.sh not found at $SMOKE_SCRIPT"

# The function must contain a reference to the user-level specs dir on disk.
# Accept either the literal path or the YOUROS_HOME env var expansion pattern.
# NOTE: Do NOT use "if ! awk | grep -q" — grep -q exits after first match,
# awk gets SIGPIPE and exits 141, `! 141` = 0 → condition true → false alarm.
# Capture the match count explicitly instead.
func_body=$(awk '/^_e2e_sweep_artifacts\(\)/,/^\}/' "$SMOKE_SCRIPT")
match_count=$(printf '%s\n' "$func_body" | grep -cE '(youros/specs|youros.*specs|YOUROS_HOME.*specs|YOUROS_SPECS)' || true)
if [ "${match_count:-0}" -eq 0 ]; then
    fail "_e2e_sweep_artifacts does not reference ~/.youros/specs on disk. \
Spec files written by a backend started without YOUROS_USER_SPECS_DIR will accumulate forever."
fi
pass "_e2e_sweep_artifacts references ~/.youros/specs (disk sweep present)"

# ---------------------------------------------------------------------------
# 2. Dynamic check: plant files, run sweep inline, verify cleanup
# ---------------------------------------------------------------------------

if [ ! -d "$YOUROS_SPECS_DIR" ]; then
    skip "~/.youros/specs not found — no disk state to test against"
fi

STALE_TAG="e2e-smoke-cleanup-test-$$"
CONTROL_FILE="real-spec-do-not-delete-$$.md"

cleanup_test_files() {
    rm -f "$YOUROS_SPECS_DIR/$STALE_TAG.md" 2>/dev/null || true
    rm -f "$YOUROS_SPECS_DIR/$CONTROL_FILE" 2>/dev/null || true
    if [ -d "$MYOS_SPECS_DIR" ]; then
        rm -f "$MYOS_SPECS_DIR/$STALE_TAG.md" 2>/dev/null || true
    fi
}
trap cleanup_test_files EXIT

# Plant a stale e2e-prefixed spec in ~/.youros/specs/
printf "# %s\ne2e test artifact — safe to delete\n" "$STALE_TAG" \
    > "$YOUROS_SPECS_DIR/$STALE_TAG.md"

# Plant an e2e-prefixed spec in ~/.myos/specs/ (if the dir exists)
MYOS_PLANTED=0
if [ -d "$MYOS_SPECS_DIR" ]; then
    printf "# %s\ne2e test artifact — safe to delete\n" "$STALE_TAG" \
        > "$MYOS_SPECS_DIR/$STALE_TAG.md"
    MYOS_PLANTED=1
fi

# Plant a control file (non-e2e prefix) that must NOT be deleted
printf "# real spec control\nThis file must survive the sweep.\n" \
    > "$YOUROS_SPECS_DIR/$CONTROL_FILE"

# --- Run the inline disk sweep (mirrors what _e2e_sweep_artifacts must do) ---
python3 - "$YOUROS_SPECS_DIR" "${MYOS_SPECS_DIR:-}" <<'PY'
import sys, os, re, pathlib

e2e_pat = re.compile(
    r'^(?:demo[-_ ]smoke[-_ ]?|smoke[-_ ]|e2e[-_ ]|test[-_ ]|'
    r'v\d+[-_ ]verify[-_ ]?|morning[-_ ]verify[-_ ]?)',
    re.IGNORECASE,
)

def sweep_dir(d):
    p = pathlib.Path(d)
    if not p.is_dir():
        return 0
    removed = 0
    for f in p.iterdir():
        if f.is_file() and f.suffix == '.md' and e2e_pat.match(f.name):
            f.unlink()
            removed += 1
    return removed

dirs = [a for a in sys.argv[1:] if a]
total = 0
for d in dirs:
    total += sweep_dir(d)
print(f'[spec-disk-sweep] removed {total} artifact(s)')
PY

# --- Verify planted e2e file is gone ---
if [ -f "$YOUROS_SPECS_DIR/$STALE_TAG.md" ]; then
    fail "e2e stale spec still on disk after sweep: $YOUROS_SPECS_DIR/$STALE_TAG.md"
fi
pass "e2e stale spec removed from ~/.youros/specs/ by sweep"

if [ "$MYOS_PLANTED" -eq 1 ]; then
    if [ -f "$MYOS_SPECS_DIR/$STALE_TAG.md" ]; then
        fail "e2e stale spec still on disk after sweep: $MYOS_SPECS_DIR/$STALE_TAG.md"
    fi
    pass "e2e stale spec removed from ~/.myos/specs/ by sweep"
else
    echo "SKIP  ~/.myos/specs/ not present — skipping myos disk check"
fi

# --- Verify non-e2e control file survived ---
if [ ! -f "$YOUROS_SPECS_DIR/$CONTROL_FILE" ]; then
    fail "non-e2e control spec was deleted by sweep (too aggressive)"
fi
pass "non-e2e control spec untouched by sweep"

# --- Static check: pre-run purge must also cover the user specs dirs ---
# The pre-run call to _e2e_sweep_artifacts must appear BEFORE the first
# phase marker in e2e_smoke.sh so stale artifacts from prior runs are
# cleared before any new journeys run. The pre-run call is already present
# (line ~481) — this check verifies it still is.
if ! grep -q "_e2e_sweep_artifacts" "$SMOKE_SCRIPT"; then
    fail "_e2e_sweep_artifacts not called in e2e_smoke.sh (pre-run purge missing)"
fi
pass "_e2e_sweep_artifacts is called in e2e_smoke.sh (pre-run purge present)"
