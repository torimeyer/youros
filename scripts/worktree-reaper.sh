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

usage() {
  cat <<EOF
Usage: scripts/worktree-reaper.sh [--apply] [-h|--help]

Scans .claude/worktrees/agent-* and classifies each agent worktree as
absorbed (diff against main is empty) or unique (has changes not on main).

Without --apply: dry-run. Prints a table and exits 0.
With --apply:    removes absorbed worktrees and their agent-* branches.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)
      APPLY=1
      shift
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
              if ! ahead=$(git rev-list --count "main..$wt_branch" 2>/dev/null); then
                printf '%-48s %-10s %s\n' "$wt_branch" "error" "?"
                error_count=$((error_count + 1))
              elif [ "$ahead" -gt 0 ]; then
                printf '%-48s %-10s %s\n' "$wt_branch" "unique" "$ahead"
                echo "  [reaper] REFUSING to delete $wt_branch: $ahead unmerged commit(s) ahead of main -- cherry-pick before removing" >&2
                git log --oneline "main..$wt_branch" 2>/dev/null | head -5 | while IFS= read -r oneline; do
                  echo "    commit: $oneline" >&2
                done
                unique_count=$((unique_count + 1))
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
  if [ "$error_count" -gt 0 ]; then
    exit 1
  fi
  exit 0
fi

if [ "$absorbed_count" -eq 0 ]; then
  echo "nothing to remove."
  if [ "$error_count" -gt 0 ]; then
    exit 1
  fi
  exit 0
fi

echo
echo "applying: removing $absorbed_count absorbed worktree(s)..."

# ------------------------------------------------------------------
# Active-agent guard (→947): never remove a worktree whose owning
# agent is still in a non-terminal state.
#
# Source priority:
#   1. MYOS_ACTIVE_AGENTS env var (comma-separated names; set by the
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

if [ "${MYOS_ACTIVE_AGENTS+set}" = "set" ]; then
  ACTIVE_AGENT_NAMES="${MYOS_ACTIVE_AGENTS}"
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
# MYOS_ACTIVE_AGENTS set (see needle →947, →1051).
if [ "$_ACTIVE_NAMES_LOADED" -eq 0 ]; then
  echo "  warning: could not load active agent names from MYOS_ACTIVE_AGENTS or agent_state.json" >&2
  echo "  skipping all removals to avoid deleting a running agent's worktree" >&2
  echo "  re-run with MYOS_ACTIVE_AGENTS='' (empty, not unset) to force removal" >&2
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
  else
    echo "  error: failed to remove worktree at $pa (branch $br)" >&2
    removal_fail=$((removal_fail + 1))
  fi
done

_actually_removed=$((absorbed_count - removal_fail - protected_count))
echo
echo "done. removed=$_actually_removed protected=$protected_count failed=$removal_fail"

if [ "$removal_fail" -gt 0 ] || [ "$error_count" -gt 0 ]; then
  exit 1
fi
exit 0
