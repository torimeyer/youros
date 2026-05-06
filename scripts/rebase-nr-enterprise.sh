#!/usr/bin/env bash
# Rebase nr-enterprise onto main and verify parity.
# Safe to re-run; does not push anything.
set -euo pipefail

# ── Constants ──────────────────────────────────────────────────────────────────
DELTA_FILES=(
  "api/main.py"
  "api/services/claude_code_provider.py"
  "api/routers/settings.py"
  "app/src/components/OnboardingWizard.tsx"
)

USAGE="Usage: $(basename "$0") [--cleanup] [--help]

Rebases origin/nr-enterprise onto origin/main in a throwaway worktree,
then runs the test suite to verify parity. Does not push anything.

Options:
  --cleanup   Remove all .tmp-nr-rebase-* worktrees and their branches, then exit.
  --help      Print this message and exit.

The 4 delta-files (touched on both main and nr-enterprise) are:
$(printf '  %s\n' "${DELTA_FILES[@]}")

Exit codes:
  0   Rebase succeeded and all tests passed.
  1   Rebase conflict or test failure (worktree left in place for manual resolution).
"

# ── Helpers ────────────────────────────────────────────────────────────────────
log()  { echo "[rebase-nr] $*"; }
warn() { echo "[rebase-nr] WARNING: $*" >&2; }
die()  { echo "[rebase-nr] ERROR: $*" >&2; exit 1; }

cleanup_worktrees() {
  local found=0
  while IFS= read -r wt_path; do
    [[ "$wt_path" == *".tmp-nr-rebase-"* ]] || continue
    found=1
    log "Removing worktree: $wt_path"
    git worktree remove --force "$wt_path" 2>/dev/null || true
    local branch
    branch=$(basename "$wt_path")
    git branch -D "$branch" 2>/dev/null || true
  done < <(git worktree list --porcelain | awk '/^worktree / {print $2}')
  if [[ $found -eq 0 ]]; then
    log "No .tmp-nr-rebase-* worktrees found."
  else
    log "Cleanup complete."
  fi
}

# ── Argument parsing ───────────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --help)
      echo "$USAGE"
      exit 0
      ;;
    --cleanup)
      cleanup_worktrees
      exit 0
      ;;
    *)
      die "Unknown argument: $arg. Run with --help for usage."
      ;;
  esac
done

# ── Pre-flight checks ──────────────────────────────────────────────────────────
if [[ -n "$(git status --porcelain)" ]]; then
  die "Working tree is dirty. Commit or stash your changes before running this script."
fi

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "$CURRENT_BRANCH" != "main" ]]; then
  die "Must be on the 'main' branch. Currently on: $CURRENT_BRANCH"
fi

log "Fetching origin..."
git fetch origin --quiet

LOCAL_MAIN=$(git rev-parse HEAD)
REMOTE_MAIN=$(git rev-parse origin/main)
if [[ "$LOCAL_MAIN" != "$REMOTE_MAIN" ]]; then
  die "Local main is not up to date with origin/main. Run 'git pull' first."
fi

if ! git rev-parse --verify origin/nr-enterprise >/dev/null 2>&1; then
  die "origin/nr-enterprise does not exist. Cannot proceed."
fi

# ── Create throwaway worktree ──────────────────────────────────────────────────
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
WT_PATH=".tmp-nr-rebase-${TIMESTAMP}"
WT_BRANCH="tmp-nr-rebase-${TIMESTAMP}"

log "Creating throwaway worktree at ${WT_PATH}..."
git worktree add -b "$WT_BRANCH" "$WT_PATH" origin/nr-enterprise

# ── Attempt rebase ─────────────────────────────────────────────────────────────
log "Rebasing origin/nr-enterprise onto origin/main..."
REBASE_OK=0
CONFLICT_FILES=()

pushd "$WT_PATH" > /dev/null
git config user.email "ci@rebase-check.local"
git config user.name "rebase-nr-enterprise"

if git rebase origin/main; then
  REBASE_OK=1
else
  # Gather conflicting files
  mapfile -t CONFLICT_FILES < <(git diff --name-only --diff-filter=U 2>/dev/null || true)
  git rebase --abort 2>/dev/null || true
fi
popd > /dev/null

# ── Conflict report ────────────────────────────────────────────────────────────
if [[ $REBASE_OK -eq 0 ]]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  REBASE CONFLICT — manual resolution required"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "Conflicting files:"
  echo ""

  EXPECTED_CONFLICTS=()
  UNEXPECTED_CONFLICTS=()

  for f in "${CONFLICT_FILES[@]}"; do
    local_is_delta=0
    for delta in "${DELTA_FILES[@]}"; do
      if [[ "$f" == "$delta" ]]; then
        local_is_delta=1
        break
      fi
    done
    if [[ $local_is_delta -eq 1 ]]; then
      EXPECTED_CONFLICTS+=("$f")
    else
      UNEXPECTED_CONFLICTS+=("$f")
    fi
  done

  if [[ ${#EXPECTED_CONFLICTS[@]} -gt 0 ]]; then
    echo "  Expected (known delta-files):"
    printf '    - %s\n' "${EXPECTED_CONFLICTS[@]}"
    echo ""
  fi

  if [[ ${#UNEXPECTED_CONFLICTS[@]} -gt 0 ]]; then
    echo "  Unexpected (not in delta-file list — investigate):"
    printf '    - %s\n' "${UNEXPECTED_CONFLICTS[@]}"
    echo ""
  fi

  echo "Worktree left in place for manual resolution: ${WT_PATH}"
  echo "When done, run: $(basename "$0") --cleanup"
  echo ""
  exit 1
fi

# ── Run tests in the rebased worktree ─────────────────────────────────────────
log "Rebase succeeded. Running tests in rebased worktree..."
TEST_OK=0

pushd "$WT_PATH" > /dev/null

NR_TEST_RESULT=0
MAIN_TEST_RESULT=0

log "Running NR-specific tests (api/tests/test_nr_enterprise.py)..."
if python -m pytest api/tests/test_nr_enterprise.py -q 2>&1; then
  log "NR tests: PASSED"
else
  NR_TEST_RESULT=1
  warn "NR tests: FAILED"
fi

log "Running main test suite (excluding NR tests)..."
if python -m pytest api/tests/ -q --ignore=api/tests/test_nr_enterprise.py 2>&1; then
  log "Main tests: PASSED"
else
  MAIN_TEST_RESULT=1
  warn "Main tests: FAILED"
fi

popd > /dev/null

# ── Cleanup on success ────────────────────────────────────────────────────────
git worktree remove --force "$WT_PATH" 2>/dev/null || true
git branch -D "$WT_BRANCH" 2>/dev/null || true

# ── Final summary ─────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  REBASE RESULT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Rebase:     OK"
echo "  NR tests:   $([ $NR_TEST_RESULT -eq 0 ] && echo PASSED || echo FAILED)"
echo "  Main tests: $([ $MAIN_TEST_RESULT -eq 0 ] && echo PASSED || echo FAILED)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [[ $NR_TEST_RESULT -ne 0 || $MAIN_TEST_RESULT -ne 0 ]]; then
  log "One or more test suites failed. Review output above before merging."
  exit 1
fi

log "All checks passed. Review and push nr-enterprise when ready."
exit 0
