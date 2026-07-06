#!/usr/bin/env bash
# worktree-reaper.sh
#
# Scan .claude/worktrees/agent-* and classify each as:
#   absorbed : branch has no unique diff against main (safe to remove)
#   unique   : branch has changes not yet on main (park, do not delete)
#   error    : diff/status could not be determined
#
# Default: dry-run (prints a table). Pass --apply to actually remove
# absorbed worktrees and their agent-prefixed branches.
#
# Safety:
#   - Never touches the main worktree itself.
#   - Only deletes branches that match worktree-agent-*.
#   - Never uses --no-verify or any hook-skip flags.
#
# Exit codes:
#   0  success (dry-run clean, or apply with no removal failures)
#   1  one or more worktree removals failed but script otherwise completed
#   2  bad arguments

set -euo pipefail

APPLY=0
BASE=main

usage() {
  cat <<EOF
Usage: scripts/worktree-reaper.sh [--apply] [--base <branch>] [-h|--help]

Scans .claude/worktrees/agent-* and classifies each agent worktree as
absorbed (diff against <branch> is empty) or unique (has changes not on <branch>).

Without --apply: dry-run. Prints a table and exits 0.
With --apply:    removes absorbed worktrees and their agent-* branches.
--base <branch>: base branch to diff against (default: main).
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)
      APPLY=1
      shift
      ;;
    --base)
      BASE="${2:-}"
      if [ -z "$BASE" ]; then
        echo "error: --base requires a branch name" >&2
        usage >&2
        exit 2
      fi
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

# Locate repo root (main worktree). We resolve against the current working
# directory so the reaper operates on whichever repo the user is in, not the
# repo where the script file happens to live (matters for test fixtures and
# for running against symlinked clones).
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ]; then
  echo "error: not inside a git repo" >&2
  exit 2
fi

# If we're inside a worktree (common_dir != git_dir), resolve to the primary.
COMMON_DIR="$(git -C "$REPO_ROOT" rev-parse --git-common-dir)"
# common-dir is absolute or relative to REPO_ROOT; normalize.
if [ "${COMMON_DIR#/}" = "$COMMON_DIR" ]; then
  COMMON_DIR="$REPO_ROOT/$COMMON_DIR"
fi
# The primary worktree contains .git as a directory (not a file).
PRIMARY="$(dirname "$COMMON_DIR")"
if [ -f "$PRIMARY/.git" ] || [ ! -d "$PRIMARY/.git" ]; then
  # fallback: use `git worktree list` first entry
  PRIMARY="$(git -C "$REPO_ROOT" worktree list --porcelain | awk '/^worktree / {print $2; exit}')"
fi

cd "$PRIMARY"

WORKTREE_BASE="$PRIMARY/.claude/worktrees"

printf '%-48s %-10s %s\n' "BRANCH" "STATUS" "UNIQUE_FILES"
printf '%-48s %-10s %s\n' "------" "------" "------------"

absorbed_branches=()
absorbed_paths=()
unique_count=0
absorbed_count=0
error_count=0
removal_fail=0

# Enumerate via `git worktree list --porcelain` so we only look at real worktrees.
while IFS= read -r line; do
  if [ -z "$line" ]; then
    wt_path=""
    wt_branch=""
    continue
  fi
  case "$line" in
    worktree\ *)
      wt_path="${line#worktree }"
      ;;
    branch\ refs/heads/*)
      wt_branch="${line#branch refs/heads/}"
      # We have a path + branch. Decide whether to process.
      case "$wt_path" in
        "$WORKTREE_BASE"/agent-*)
          case "$wt_branch" in
            worktree-agent-*)
              # eligible
              # Count commits on the branch not yet merged into main.
              # git rev-list --count main..branch is immune to the case
              # where a commit produces content identical to main (which
              # git diff would report as empty, causing a false "absorbed"
              # classification and deleting live agent worktrees).
              if ! ahead=$(git rev-list --count "$BASE..$wt_branch" 2>/dev/null); then
                printf '%-48s %-10s %s\n' "$wt_branch" "error" "?"
                error_count=$((error_count + 1))
              elif [ "$ahead" -gt 0 ]; then
                # Commits ahead by hash lineage, but squash-merged branches have
                # new SHAs whose content is already present on main.  Content-check
                # so those are not parked forever as "unique".
                if git diff --quiet "$BASE..$wt_branch" 2>/dev/null; then
                  # Empty content diff against main. This is EITHER a real
                  # squash-merged branch (content already on main, safe to
                  # remove) OR a live agent that made an --allow-empty
                  # scaffold commit and has uncommitted work in its tree.
                  # The ahead==0 path below guards dirty trees; this path
                  # did not, so an empty scaffold commit let the reaper
                  # delete live agents mid-flight (→1665). Apply the same
                  # dirty-tree guard here before treating it as absorbed.
                  wt_dirty=0
                  if [ -d "$wt_path" ]; then
                    if ! git -C "$wt_path" diff --quiet 2>/dev/null || \
                       ! git -C "$wt_path" diff --cached --quiet 2>/dev/null; then
                      wt_dirty=1
                    fi
                    # Also check for untracked new files (→2466): mirrors spawn_isolation.py:remove_worktree.
                    # git diff only sees tracked changes; an agent report written but never staged
                    # is invisible to it and would be silently lost on deletion.
                    if [ "$wt_dirty" -eq 0 ]; then
                      _wt_untracked=$(git -C "$wt_path" ls-files --others --exclude-standard 2>/dev/null | head -1)
                      [ -n "$_wt_untracked" ] && wt_dirty=1
                    fi
                  fi
                  if [ "$wt_dirty" -eq 1 ]; then
                    printf '%-48s %-10s %s\n' "$wt_branch" "unique" "dirty (empty-diff commit)"
                    echo "  [reaper] $wt_branch has uncommitted work over an empty-diff commit, skipping (→1665)" >&2
                    unique_count=$((unique_count + 1))
                  else
                    printf '%-48s %-10s %s\n' "$wt_branch" "absorbed" "$ahead (squashed)"
                    absorbed_branches+=("$wt_branch")
                    absorbed_paths+=("$wt_path")
                    absorbed_count=$((absorbed_count + 1))
                  fi
                else
                  printf '%-48s %-10s %s\n' "$wt_branch" "unique" "$ahead"
                  echo "  [reaper] REFUSING to delete $wt_branch: $ahead unmerged commit(s) ahead of main -- cherry-pick before removing" >&2
                  git log --oneline -n 5 "$BASE..$wt_branch" 2>/dev/null | while IFS= read -r oneline; do
                    echo "    commit: $oneline" >&2
                  done
                  unique_count=$((unique_count + 1))
                fi
              else
                # No commits ahead of main. Also guard against uncommitted
                # work (staged or unstaged) left behind if an agent was
                # killed before it could commit.
                wt_dirty=0
                if [ -d "$wt_path" ]; then
                  if ! git -C "$wt_path" diff --quiet 2>/dev/null || \
                     ! git -C "$wt_path" diff --cached --quiet 2>/dev/null; then
                    wt_dirty=1
                  fi
                  # Also check for untracked new files (→2466): mirrors spawn_isolation.py:remove_worktree.
                  # git diff only sees tracked changes; an agent report written but never staged
                  # is invisible to it and would be silently lost on deletion.
                  if [ "$wt_dirty" -eq 0 ]; then
                    _wt_untracked=$(git -C "$wt_path" ls-files --others --exclude-standard 2>/dev/null | head -1)
                    [ -n "$_wt_untracked" ] && wt_dirty=1
                  fi
                fi
                if [ "$wt_dirty" -eq 1 ]; then
                  printf '%-48s %-10s %s\n' "$wt_branch" "unique" "dirty"
                  echo "  [reaper] $wt_branch has uncommitted work, skipping" >&2
                  unique_count=$((unique_count + 1))
                else
                  # Check if main repo has tracked-file modifications that might belong to this worktree.
                  # We ignore untracked files (??) — those are state/log files, not agent work.
                  if git -C "$PRIMARY" status --porcelain 2>/dev/null | grep -v '^??' | grep -q .; then
                    echo "WARN: worktree $wt_path absorbed (0 commits) but main repo is dirty — skipping deletion to avoid data loss"
                    unique_count=$((unique_count + 1))
                    continue
                  fi
                  printf '%-48s %-10s %s\n' "$wt_branch" "absorbed" "0"
                  absorbed_branches+=("$wt_branch")
                  absorbed_paths+=("$wt_path")
                  absorbed_count=$((absorbed_count + 1))
                fi
              fi
              ;;
          esac
          ;;
      esac
      ;;
  esac
done < <(git worktree list --porcelain)

echo
echo "summary: absorbed=$absorbed_count unique=$unique_count error=$error_count"

if [ "$APPLY" -eq 0 ]; then
  echo "(dry-run. pass --apply to remove absorbed worktrees.)"
fi

# Initialise before APPLY block so phase 2 can reference these in dry-run mode.
_ACTIVE_NAMES_LOADED=0
ACTIVE_AGENT_NAMES=""

if [ "$APPLY" -eq 1 ]; then
  if [ "$absorbed_count" -eq 0 ]; then
    echo "nothing to remove."
  else

    echo
    echo "applying: removing $absorbed_count absorbed worktree(s)..."

# ------------------------------------------------------------------
# Active-agent guard (→947): never remove a worktree whose owning
# agent is still in a non-terminal state.
#
# Source priority:
#   1. YOUROS_ACTIVE_AGENTS env var (comma-separated names; set by the
#      Python worktree_reaper service before calling this script).
#   2. $PRIMARY/.ostk/agent_state.json (used when called standalone,
#      e.g. via launchd / scripts/scheduled/reaper.sh).
#
# Terminal statuses (safe to remove):
#   stopped, completed, completed_timeout, failed, cancelled,
#   abandoned, terminated_stale.
# Anything else (running, pending, spawned, queued, unknown) is active.
#
# If the state file exists but cannot be parsed, we fail safe: skip
# all removals rather than risk deleting an active agent's checkout.
# ------------------------------------------------------------------
_ACTIVE_NAMES_LOADED=0
ACTIVE_AGENT_NAMES=""

if [ "${YOUROS_ACTIVE_AGENTS+set}" = "set" ]; then
  ACTIVE_AGENT_NAMES="${YOUROS_ACTIVE_AGENTS}"
  _ACTIVE_NAMES_LOADED=1
elif [ -f "$PRIMARY/.ostk/agent_state.json" ]; then
  _state_out=$(STATE_FILE="$PRIMARY/.ostk/agent_state.json" python3 -c "
import json, os, sys
TERMINAL = {
    'stopped','completed','completed_timeout','failed',
    'cancelled','abandoned','terminated_stale',
}
try:
    d = json.load(open(os.environ['STATE_FILE']))
    active = [k for k, v in d.items()
              if isinstance(v, dict) and v.get('status') not in TERMINAL]
    print('ok:' + ','.join(active))
except Exception as exc:
    print('error:' + str(exc))
" 2>/dev/null)
  case "${_state_out:-}" in
    ok:*)
      ACTIVE_AGENT_NAMES="${_state_out#ok:}"
      _ACTIVE_NAMES_LOADED=1
      ;;
    error:*)
      echo "error: could not parse $PRIMARY/.ostk/agent_state.json; skipping all removals to protect running agents (→947)" >&2
      echo "  detail: ${_state_out#error:}" >&2
      exit 1
      ;;
  esac
fi

# Fail safe: if we could not load active agent names from any source,
# skip all removals. Removing a running agent's worktree corrupts in-flight
# work; it is safer to do nothing and let the operator re-run with
# YOUROS_ACTIVE_AGENTS set (see needle →947, →1051).
if [ "$_ACTIVE_NAMES_LOADED" -eq 0 ]; then
  echo "  warning: could not load active agent names from YOUROS_ACTIVE_AGENTS or agent_state.json" >&2
  echo "  skipping all removals to avoid deleting a running agent's worktree" >&2
  echo "  re-run with YOUROS_ACTIVE_AGENTS='' (empty, not unset) to force removal" >&2
  exit 1
fi

i=0
protected_count=0
while [ "$i" -lt "${#absorbed_branches[@]}" ]; do
  br="${absorbed_branches[$i]}"
  pa="${absorbed_paths[$i]}"
  i=$((i + 1))

  # Derive agent name: worktree path ends with agent-<name>.
  _agent_name="${pa##*/agent-}"

  # Skip if the owning agent is still alive.
  # Guard (→1194): the worktree ID may be a short_worktree_id-truncated version
  # of the registered agent name (truncation kicks in at 30 chars). Compare
  # both the worktree ID itself AND the short_worktree_id of every active agent
  # name so long-named agents are not falsely unprotected.
  if [ "$_ACTIVE_NAMES_LOADED" -eq 1 ] && [ -n "$ACTIVE_AGENT_NAMES" ]; then
    _is_protected=$(python3 - "$_agent_name" "$ACTIVE_AGENT_NAMES" <<'PYEOF'
import hashlib, sys

wt_id = sys.argv[1]
active_csv = sys.argv[2]

def short_id(name, max_len=30):
    if len(name) <= max_len:
        return name
    digest = hashlib.blake2s(name.encode(), digest_size=4).hexdigest()
    prefix = name[:max_len - 9].rstrip("-_")
    return f"{prefix}-{digest}"

for name in (n for n in active_csv.split(",") if n):
    if name == wt_id or short_id(name) == wt_id:
        print("protected")
        sys.exit(0)
print("unprotected")
PYEOF
)
    if [ "$_is_protected" = "protected" ]; then
      echo "  protected (agent '$_agent_name' still active; leaving worktree in place): $br"
      protected_count=$((protected_count + 1))
      continue
    fi
  fi

  # Unlock if locked (ignore failure; it may not be locked).
  git worktree unlock "$pa" >/dev/null 2>&1 || true

  if git worktree remove --force "$pa" >/dev/null 2>&1; then
    if git branch -D "$br" >/dev/null 2>&1; then
      echo "  removed $br"
    else
      echo "  error: worktree gone but branch $br could not be deleted" >&2
      removal_fail=$((removal_fail + 1))
    fi
  elif git worktree remove --force --force "$pa" >/dev/null 2>&1; then
    # --force --force is required for worktrees that were created with --lock
    # or that git still considers locked after the unlock attempt above.
    if git branch -D "$br" >/dev/null 2>&1; then
      echo "  removed $br (locked worktree, used --force --force)"
    else
      echo "  error: worktree gone but branch $br could not be deleted" >&2
      removal_fail=$((removal_fail + 1))
    fi
  else
    # Both git worktree remove attempts failed (e.g. .ostk/ sockets or state
    # files that git won't touch). Fall back to rm -rf then prune.
    if rm -rf "$pa" 2>/dev/null; then
      git worktree prune >/dev/null 2>&1 || true
      if git branch -D "$br" >/dev/null 2>&1; then
        echo "  removed $br (via rm -rf fallback)"
      else
        echo "  error: dir removed but branch $br could not be deleted" >&2
        removal_fail=$((removal_fail + 1))
      fi
    else
      echo "  error: failed to remove worktree at $pa (branch $br)" >&2
      removal_fail=$((removal_fail + 1))
    fi
  fi
done

_actually_removed=$((absorbed_count - removal_fail - protected_count))
echo
echo "done. removed=$_actually_removed protected=$protected_count failed=$removal_fail"

  fi  # end: absorbed_count > 0 branch
fi  # end: APPLY == 1 block

# ------------------------------------------------------------------
# Phase 2: disk-first orphan sweep (→1333)
#
# The main loop above iterates `git worktree list --porcelain` and is
# therefore BLIND to dirs that exist on disk but have no git registration.
# These "orphan" dirs accumulate when:
#   - The backend restarts mid-spawn (event loop dies, _drain_stderr
#     never calls remove_worktree(), later git prune drops the entry)
#   - ghost_reaper / agent_state_prune delete registry rows without
#     calling remove_worktree(), the worktree git entry later gets pruned
#   - create_worktree() pre-clean removes the git registration but
#     shutil.rmtree fails, leaving the dir behind
#
# This sweep finds those dirs and safely removes them.
# Only removes under --apply (dry-run just prints the orphan list).
# ------------------------------------------------------------------
echo
echo "phase 2: orphan sweep (dirs on disk with no git registration)..."

# Build the set of registered worktree paths (absolute, one per line).
_registered_paths=$(git worktree list --porcelain | awk '/^worktree / {print $2}')

orphan_count=0
orphan_removed=0
orphan_skipped=0
orphan_fail=0

for _orphan_dir in "$WORKTREE_BASE"/agent-*/; do
  # Strip trailing slash for consistent comparison.
  _orphan_dir="${_orphan_dir%/}"
  [ -d "$_orphan_dir" ] || continue

  # Skip if this dir IS in git worktree list (handled by phase 1).
  _is_registered=0
  while IFS= read -r _rp; do
    if [ "$_rp" = "$_orphan_dir" ]; then
      _is_registered=1
      break
    fi
  done <<< "$_registered_paths"
  [ "$_is_registered" -eq 1 ] && continue

  orphan_count=$((orphan_count + 1))
  _orphan_agent_name="${_orphan_dir##*/agent-}"

  if [ "$APPLY" -eq 0 ]; then
    echo "  orphan (dry-run): $_orphan_dir"
    continue
  fi

  # Active-agent guard: never remove an orphan whose agent is still running.
  if [ "$_ACTIVE_NAMES_LOADED" -eq 1 ] && [ -n "$ACTIVE_AGENT_NAMES" ]; then
    _orphan_is_protected=$(python3 - "$_orphan_agent_name" "$ACTIVE_AGENT_NAMES" <<'PYEOF'
import hashlib, sys

wt_id = sys.argv[1]
active_csv = sys.argv[2]

def short_id(name, max_len=30):
    if len(name) <= max_len:
        return name
    digest = hashlib.blake2s(name.encode(), digest_size=4).hexdigest()
    prefix = name[:max_len - 9].rstrip("-_")
    return f"{prefix}-{digest}"

for name in (n for n in active_csv.split(",") if n):
    if name == wt_id or short_id(name) == wt_id:
        print("protected")
        sys.exit(0)
print("unprotected")
PYEOF
)
    if [ "${_orphan_is_protected:-}" = "protected" ]; then
      echo "  orphan protected (agent '$_orphan_agent_name' still active): $_orphan_dir"
      orphan_skipped=$((orphan_skipped + 1))
      continue
    fi
  fi

  # Safety: skip orphans that have uncommitted tracked changes.
  # Orphan dirs have no git registration, so we cannot use `git diff`
  # from the repo root. Instead check whether the dir contains a
  # .git file (worktree gitdir pointer) and if so whether there are
  # staged or unstaged tracked changes.
  if [ -f "$_orphan_dir/.git" ]; then
    _orphan_unstaged=0
    _orphan_staged=0
    git -C "$_orphan_dir" diff --quiet 2>/dev/null || _orphan_unstaged=1
    git -C "$_orphan_dir" diff --cached --quiet 2>/dev/null || _orphan_staged=1
    if [ "$_orphan_unstaged" -eq 1 ] || [ "$_orphan_staged" -eq 1 ]; then
      echo "  orphan skipped (has uncommitted tracked changes): $_orphan_dir" >&2
      orphan_skipped=$((orphan_skipped + 1))
      continue
    fi
  fi

  # Remove the orphan dir. No git worktree remove needed (it has no registration).
  if rm -rf "$_orphan_dir" 2>/dev/null; then
    echo "  orphan removed: $_orphan_dir"
    orphan_removed=$((orphan_removed + 1))
  else
    echo "  error: failed to remove orphan dir $_orphan_dir" >&2
    orphan_fail=$((orphan_fail + 1))
  fi
done

if [ "$APPLY" -eq 0 ]; then
  echo "  found $orphan_count orphan dir(s) (dry-run, not removed)"
else
  echo "  orphans: found=$orphan_count removed=$orphan_removed skipped=$orphan_skipped failed=$orphan_fail"
fi

# ------------------------------------------------------------------
# Phase 3: git worktree prune
#
# Clean up the reverse case: git registrations that point to dirs that
# no longer exist on disk. These accumulate when rm-rf succeeds but git
# worktree remove was never called (e.g., the _drain_stderr rmtree
# fallback path). `git worktree prune` is idempotent and fast.
# ------------------------------------------------------------------
git worktree prune >/dev/null 2>&1 || true

_total_fail=$((removal_fail + orphan_fail))
if [ "$_total_fail" -gt 0 ] || [ "$error_count" -gt 0 ]; then
  exit 1
fi
exit 0
