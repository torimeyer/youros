#!/usr/bin/env bash
# test-youros-handoff.sh — regression tests for youros-handoff.sh (→1312)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER="$SCRIPT_DIR/youros-handoff.sh"
PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1" >&2; FAIL=$((FAIL + 1)); }

# ── setup ─────────────────────────────────────────────────────────────────────
tmpdir="$(mktemp -d)"
trap "rm -rf '$tmpdir'" EXIT

mock_ostk="$tmpdir/ostk"
cat > "$mock_ostk" << 'EOF'
#!/usr/bin/env bash
echo "ostk_called: $*"
EOF
chmod +x "$mock_ostk"

# ── test: markdown fallback creates file from stdin ───────────────────────────
hdir1="$tmpdir/h1"
echo "# Handoff content" | HANDOFFS_DIR="$hdir1" bash "$WRAPPER" myagent --topic test-slug >/dev/null
expected="$hdir1/$(date +%Y-%m-%d)-test-slug.md"
if [[ -f "$expected" ]] && grep -q "# Handoff content" "$expected"; then
    pass "markdown fallback: creates file from stdin"
else
    fail "markdown fallback: file missing or wrong content ($expected)"
fi

# ── test: markdown fallback appends on second run ─────────────────────────────
echo "# Second section" | HANDOFFS_DIR="$hdir1" bash "$WRAPPER" myagent --topic test-slug >/dev/null
line_count="$(wc -l < "$expected")"
if [[ "$line_count" -gt 2 ]]; then
    pass "markdown fallback: appends on second run ($line_count lines)"
else
    fail "markdown fallback: should have appended (lines: $line_count)"
fi

# ── test: default slug when no --topic given ──────────────────────────────────
hdir2="$tmpdir/h2"
echo "content" | HANDOFFS_DIR="$hdir2" bash "$WRAPPER" myagent >/dev/null
default_file="$hdir2/$(date +%Y-%m-%d)-handoff.md"
if [[ -f "$default_file" ]]; then
    pass "markdown fallback: default slug 'handoff' used when no --topic"
else
    fail "markdown fallback: expected default file $default_file"
fi

# ── test: --content-file arg ──────────────────────────────────────────────────
hdir3="$tmpdir/h3"
content_file="$tmpdir/content.md"
echo "# From file" > "$content_file"
HANDOFFS_DIR="$hdir3" bash "$WRAPPER" myagent --topic from-file --content-file "$content_file" >/dev/null
out="$hdir3/$(date +%Y-%m-%d)-from-file.md"
if [[ -f "$out" ]] && grep -q "# From file" "$out"; then
    pass "markdown fallback: --content-file writes file content"
else
    fail "markdown fallback: --content-file failed ($out)"
fi

# ── test: ostk path dispatches to ostk handoff ────────────────────────────────
output="$(YOUROS_HANDOFF_USE_OSTK=1 OSTK_CMD="$mock_ostk" bash "$WRAPPER" claude-opus-4-7 --transition "switching for context" 2>&1)"
if echo "$output" | grep -q "ostk_called: handoff claude-opus-4-7 --transition switching for context"; then
    pass "ostk path: calls ostk handoff with target + --transition"
else
    fail "ostk path: unexpected output: $output"
fi

# ── test: ostk path omits --transition when not given ─────────────────────────
output2="$(YOUROS_HANDOFF_USE_OSTK=1 OSTK_CMD="$mock_ostk" bash "$WRAPPER" claude-opus-4-7 2>&1)"
if echo "$output2" | grep -qE "^ostk_called: handoff claude-opus-4-7$"; then
    pass "ostk path: omits --transition flag when not provided"
else
    fail "ostk path: unexpected output without transition: $output2"
fi

# ── test: missing target exits non-zero ───────────────────────────────────────
if ! bash "$WRAPPER" 2>/dev/null; then
    pass "error handling: exits non-zero with no target"
else
    fail "error handling: should exit non-zero with no arguments"
fi

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
