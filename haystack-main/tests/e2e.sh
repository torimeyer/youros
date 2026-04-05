#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HAYSTACK_BIN="${HAYSTACK_BIN:-$SCRIPT_DIR/../target/debug/haystack}"
HAYSTACK_BIN="$(cd "$(dirname "$HAYSTACK_BIN")" && pwd)/$(basename "$HAYSTACK_BIN")"
PASS=0
FAIL=0

# Colors
if [ -t 1 ]; then
  GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
else
  GREEN=''; RED=''; NC=''
fi

check() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo -e "  ${GREEN}PASS${NC} $desc"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}FAIL${NC} $desc"
    FAIL=$((FAIL + 1))
  fi
}

check_output() {
  local desc="$1"; local pattern="$2"; shift 2
  local out
  out=$("$@" 2>&1) || true
  if echo "$out" | grep -q "$pattern"; then
    echo -e "  ${GREEN}PASS${NC} $desc"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}FAIL${NC} $desc (expected '$pattern', got: $out)"
    FAIL=$((FAIL + 1))
  fi
}

check_fail() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo -e "  ${RED}FAIL${NC} $desc (should have failed)"
    FAIL=$((FAIL + 1))
  else
    echo -e "  ${GREEN}PASS${NC} $desc"
    PASS=$((PASS + 1))
  fi
}

# Setup temp dir
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT
cd "$TMPDIR"
git init -q .
mkdir -p .haystack/beads .haystack/wip docs/draft docs/spec
echo "0" > .haystack/beads/counter
touch .haystack/beads/issues.jsonl .haystack/audit.jsonl

echo "=== Haystack CLI e2e test ==="
echo "Binary: $HAYSTACK_BIN"
echo "Workdir: $TMPDIR"
echo ""

# 1. Draft
echo "--- draft ---"
OUT=$($HAYSTACK_BIN draft "e2e test feature" 2>&1)
check "draft creates file" test -f docs/draft/e2e-test-feature.md
check_output "draft output shows path" "e2e-test-feature.md" echo "$OUT"
check "frontmatter has status:draft" grep -q "status: draft" docs/draft/e2e-test-feature.md

# 2. Promote (should fail without acceptance criteria)
echo "--- promote (error cases) ---"
check_fail "promote without criteria fails" $HAYSTACK_BIN promote docs/draft/e2e-test-feature.md

# Add acceptance criteria
printf "\n## Acceptance\n- [ ] First criterion\n- [ ] Second criterion\n" >> docs/draft/e2e-test-feature.md

echo "--- promote ---"
$HAYSTACK_BIN promote docs/draft/e2e-test-feature.md >/dev/null 2>&1
check "promote creates spec" test -f docs/spec/e2e-test-feature.md
check "promote removes draft" test ! -f docs/draft/e2e-test-feature.md
check "spec has status:spec" grep -q "status: spec" docs/spec/e2e-test-feature.md

# 3. Decompose
echo "--- decompose ---"
OUT=$($HAYSTACK_BIN decompose docs/spec/e2e-test-feature.md 2>&1)
check "decompose creates beads" test -s .haystack/beads/issues.jsonl
check_output "decompose outputs bd-001" "bd-001" echo "$OUT"
check_output "decompose outputs bd-002" "bd-002" echo "$OUT"

echo "--- decompose (error cases) ---"
check_fail "decompose same spec twice fails" $HAYSTACK_BIN decompose docs/spec/e2e-test-feature.md

# 4. Trace
echo "--- trace ---"
check_output "trace bd-001 shows bead" "bd-001" $HAYSTACK_BIN trace bd-001
check_output "trace bd-001 shows spec ref" "e2e-test-feature" $HAYSTACK_BIN trace bd-001

# 5. Issue/work queue
echo "--- issue add ---"
check_output "issue add works" "bd-003" $HAYSTACK_BIN issue add "test task" --priority P0

echo "--- work next ---"
check_output "work next shows P0 first" "P0" $HAYSTACK_BIN work next

echo "--- work list ---"
check_output "work list shows items" "bd-003" $HAYSTACK_BIN work list

# 6. Amend
echo "--- amend ---"
check_output "amend shows affected beads" "bd-001" $HAYSTACK_BIN amend docs/spec/e2e-test-feature.md --severity minor

# 7. Shelve / unshelve
echo "--- shelve ---"
$HAYSTACK_BIN shelve bd-001 >/dev/null 2>&1
check "shelve creates snapshot" ls .haystack/wip/bd-001-*.md >/dev/null 2>&1
check_output "shelved bead status" "shelved" grep "bd-001" .haystack/beads/issues.jsonl

echo "--- shelve (error cases) ---"
check_fail "shelve already-shelved fails" $HAYSTACK_BIN shelve bd-001

echo "--- unshelve ---"
$HAYSTACK_BIN unshelve bd-001 >/dev/null 2>&1
check_output "unshelved bead status" "open" grep "bd-001" .haystack/beads/issues.jsonl

echo "--- unshelve (error cases) ---"
check_fail "unshelve non-shelved fails" $HAYSTACK_BIN unshelve bd-001

# 8. Close
echo "--- close ---"
check_output "work close works" "closed" $HAYSTACK_BIN work close bd-001 --reason "e2e tested"

# 9. Audit chain verification
echo ""
echo "=== Audit Chain Verification ==="
AUDIT=$(cat .haystack/audit.jsonl)

check_audit() {
  local desc="$1"; local pattern="$2"
  if echo "$AUDIT" | grep -q "$pattern"; then
    echo -e "  ${GREEN}PASS${NC} $desc"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}FAIL${NC} $desc (missing: $pattern)"
    FAIL=$((FAIL + 1))
  fi
}

check_audit "audit has draft.created" "draft.created"
check_audit "audit has spec.promoted" "spec.promoted"
check_audit "audit spec.promoted has from/to" "from.*to"
check_audit "audit has beads.created" "beads.created"
check_audit "audit beads.created has spec" "spec.*e2e-test-feature"
check_audit "audit has task.added" "task.added"
check_audit "audit has spec.amended" "spec.amended"
check_audit "audit has bead.shelved" "bead.shelved"
check_audit "audit has bead.unshelved" "bead.unshelved"
check_audit "audit has task.closed" "task.closed"
check_audit "all events have timestamps" "timestamp"

# Verify order
EVENTS=$(cat .haystack/audit.jsonl | grep -o '"event":"[^"]*"' | sed 's/"event":"//;s/"//')
EXPECTED="draft.created
spec.promoted
beads.created
task.added
spec.amended
bead.shelved
bead.unshelved
task.closed"
if [ "$EVENTS" = "$EXPECTED" ]; then
  echo -e "  ${GREEN}PASS${NC} audit events in correct order"
  PASS=$((PASS + 1))
else
  echo -e "  ${RED}FAIL${NC} audit events out of order"
  echo "    expected: $(echo "$EXPECTED" | tr '\n' ' ')"
  echo "    got:      $(echo "$EVENTS" | tr '\n' ' ')"
  FAIL=$((FAIL + 1))
fi

# Summary
echo ""
TOTAL=$((PASS + FAIL))
echo "=== $PASS/$TOTAL tests passed ==="
if [ "$FAIL" -gt 0 ]; then
  echo -e "${RED}$FAIL tests failed${NC}"
  exit 1
else
  echo -e "${GREEN}All tests passed${NC}"
  exit 0
fi
