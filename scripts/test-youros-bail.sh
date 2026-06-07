#!/usr/bin/env bash
# test-youros-bail.sh — regression tests for scripts/youros-bail.sh (→1313)
#
# Creates a fake directory tree, stubs out `ostk`, runs pack/verify/unpack,
# and asserts included files are present and excluded files are absent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BAIL_SCRIPT="$SCRIPT_DIR/youros-bail.sh"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

TESTROOT="$(mktemp -d)"
trap 'rm -rf "$TESTROOT"' EXIT

# ── fake ostk binary ──────────────────────────────────────────────────────────

FAKE_BIN="$TESTROOT/bin"
mkdir -p "$FAKE_BIN"

cat > "$FAKE_BIN/ostk" <<'OSTK_STUB'
#!/usr/bin/env bash
shift  # drop 'bail'
sub="$1"; shift
case "$sub" in
  pack)
    OUT=""
    while [[ $# -gt 0 ]]; do
        [[ "$1" == "--out" ]] && { OUT="$2"; shift 2; } || shift
    done
    [[ -n "$OUT" ]] || { echo "ostk bail pack: missing --out" >&2; exit 1; }
    echo "mock-ostk-bail-content" > "$OUT"
    ;;
  verify)
    echo "signature valid (mock)"
    ;;
  unpack)
    echo "unpacked (mock)"
    ;;
  *)
    echo "unknown sub: $sub" >&2; exit 1
    ;;
esac
OSTK_STUB
chmod +x "$FAKE_BIN/ostk"
export PATH="$FAKE_BIN:$PATH"

# ── fake HOME tree ────────────────────────────────────────────────────────────

FAKE_HOME="$TESTROOT/home"

# ~/.youros/ — mix of included and excluded files
mkdir -p "$FAKE_HOME/.youros"
echo "config"   > "$FAKE_HOME/.youros/config.json"
echo "token"    > "$FAKE_HOME/.youros/github_token.json"   # SECRET — must exclude
echo "audit"    > "$FAKE_HOME/.youros/audit.jsonl"          # transient — must exclude
echo "agents"   > "$FAKE_HOME/.youros/agents.jsonl"         # transient — must exclude
echo "registry" > "$FAKE_HOME/.youros/registry.jsonl"       # transient — must exclude
echo "briefing" > "$FAKE_HOME/.youros/briefing_state.json" # included

# ~/.claude/handoffs/
mkdir -p "$FAKE_HOME/.claude/handoffs"
echo "handoff content" > "$FAKE_HOME/.claude/handoffs/2026-01-01-test.md"

# memory dir (flat list of .md files)
FAKE_MEMORY="$FAKE_HOME/.claude/projects/-test/memory"
mkdir -p "$FAKE_MEMORY"
echo "memory entry" > "$FAKE_MEMORY/feedback_test.md"
echo "MEMORY index" > "$FAKE_MEMORY/MEMORY.md"

# .ostk/decisions/ and .ostk/policy/ — repo must be inside FAKE_HOME so
# relative paths don't contain '..' (macOS tar refuses those on extract)
FAKE_REPO="$FAKE_HOME/claude/torios"
mkdir -p "$FAKE_REPO/.ostk/decisions"
mkdir -p "$FAKE_REPO/.ostk/policy"
echo "decision content" > "$FAKE_REPO/.ostk/decisions/2026-01-01.md"
echo "policy rule"      > "$FAKE_REPO/.ostk/policy/core.md"

OUT_BAIL="$TESTROOT/test-export.yourosbail"

# ── pack ──────────────────────────────────────────────────────────────────────

HOME="$FAKE_HOME" \
YOUROS_DIR="$FAKE_HOME/.youros" \
MEMORY_DIR="$FAKE_MEMORY" \
HANDOFFS_DIR="$FAKE_HOME/.claude/handoffs" \
DECISIONS_DIR="$FAKE_REPO/.ostk/decisions" \
POLICY_DIR="$FAKE_REPO/.ostk/policy" \
  "$BAIL_SCRIPT" pack "$OUT_BAIL"

# bundle file created
[[ -f "$OUT_BAIL" ]] && pass "pack creates output file" || fail "pack creates output file"

# ── inspect bundle structure ──────────────────────────────────────────────────

INSPECT="$TESTROOT/inspect"
mkdir -p "$INSPECT"
tar xzf "$OUT_BAIL" -C "$INSPECT"

[[ -f "$INSPECT/ostk-state.bail" ]]    && pass "bundle contains ostk-state.bail"    || fail "bundle contains ostk-state.bail"
[[ -f "$INSPECT/youros-extras.tar.gz" ]] && pass "bundle contains youros-extras.tar.gz" || fail "bundle contains youros-extras.tar.gz"

# ── inspect extras archive contents (tar list, not extract to /) ──────────────

EXTRAS_LIST="$(tar tzf "$INSPECT/youros-extras.tar.gz" 2>/dev/null)"

echo_extras_check() {
    local label="$1" pattern="$2" want_present="$3"
    if echo "$EXTRAS_LIST" | grep -q "$pattern"; then
        [[ "$want_present" == "yes" ]] && pass "$label included" || fail "$label should be excluded"
    else
        [[ "$want_present" == "no"  ]] && pass "$label excluded" || fail "$label missing from extras"
    fi
}

echo_extras_check "config.json"          "config\.json"          yes
echo_extras_check "briefing_state.json"  "briefing_state\.json"  yes
echo_extras_check "handoff .md"          "2026-01-01-test\.md"   yes
echo_extras_check "memory feedback file" "feedback_test\.md"     yes
echo_extras_check "MEMORY.md"            "MEMORY\.md"            yes
echo_extras_check "decision file"        "2026-01-01\.md"        yes
echo_extras_check "policy file"          "core\.md"              yes

echo_extras_check "audit.jsonl"          "audit\.jsonl"          no
echo_extras_check "agents.jsonl"         "agents\.jsonl"         no
echo_extras_check "registry.jsonl"       "registry\.jsonl"       no
echo_extras_check "github_token.json"    "github_token\.json"    no

# ── verify ────────────────────────────────────────────────────────────────────

HOME="$FAKE_HOME" \
  "$BAIL_SCRIPT" verify "$OUT_BAIL" && pass "verify exits 0" || fail "verify exits 0"

# ── unpack (smoke: must not crash; mock ostk, extras stay in TESTROOT) ────────

HOME="$FAKE_HOME" \
  "$BAIL_SCRIPT" unpack "$OUT_BAIL" && pass "unpack exits 0" || fail "unpack exits 0"

# ── summary ───────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [[ $FAIL -eq 0 ]]; then
    echo "ALL TESTS PASSED"
    exit 0
else
    echo "TESTS FAILED"
    exit 1
fi
