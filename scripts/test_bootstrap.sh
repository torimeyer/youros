#!/bin/bash
# Hermetic unit tests for scripts/bootstrap.sh.
#
# bootstrap.sh is the curl-pipe-bash one-liner front door:
#   curl -fsSL https://raw.githubusercontent.com/torimeyer/youros/main/scripts/bootstrap.sh | bash
# It checks prereqs (printing the exact `brew install` line for anything
# missing), clones or updates the repo into ${YOUROS_DIR:-$HOME/youros}, then
# runs the in-repo ./install.sh (which detects it is already inside the repo
# and does NOT re-clone).
#
# These tests never touch the network, ~/.youros, or your real repo. They
# source bootstrap.sh and drive its functions with stubs.
#
# Usage: scripts/test_bootstrap.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOOTSTRAP="$SCRIPT_DIR/bootstrap.sh"

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
PASS=0; FAIL=0

ok()  { printf "  ${GREEN}PASS${NC} %s\n" "$1"; PASS=$((PASS+1)); }
bad() { printf "  ${RED}FAIL${NC} %s\n" "$1"; FAIL=$((FAIL+1)); }

assert_contains() { # haystack needle msg
    case "$1" in
        *"$2"*) ok "$3" ;;
        *) bad "$3 (missing: '$2')"; printf "      got: %s\n" "$1" ;;
    esac
}
assert_rc() { # actual expected msg
    if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (want rc $2, got $1)"; fi
}

echo "=== bootstrap.sh unit tests ==="

# --- existence / syntax ---
if [ -f "$BOOTSTRAP" ]; then ok "bootstrap.sh exists"; else bad "bootstrap.sh exists"; fi
if bash -n "$BOOTSTRAP" 2>/dev/null; then ok "bootstrap.sh parses (bash -n)"; else bad "bootstrap.sh parses (bash -n)"; fi

# Sourcing must NOT run main (source-guard present).
export BOOTSTRAP_NONINTERACTIVE=1
# shellcheck disable=SC1090
if source "$BOOTSTRAP" 2>/dev/null; then ok "bootstrap.sh is sourceable without running main"; else bad "bootstrap.sh sourceable without running main"; fi

# --- prereq detection with exact brew lines ---
# check_prereqs probes capability via bs_have and versions via
# bs_python_version / bs_node_major. We override those to simulate machines.

# git missing
bs_have() { [ "$1" = "git" ] && return 1; return 0; }
bs_python_version() { echo "3.13"; }
bs_node_major() { echo "20"; }
out="$(check_prereqs 2>&1)"; rc=$?
assert_rc "$rc" 1 "missing git -> non-zero exit"
assert_contains "$out" "brew install git" "missing git -> exact brew line"

# curl missing
bs_have() { [ "$1" = "curl" ] && return 1; return 0; }
out="$(check_prereqs 2>&1)"; rc=$?
assert_rc "$rc" 1 "missing curl -> non-zero exit"
assert_contains "$out" "brew install curl" "missing curl -> exact brew line"

# node missing
bs_have() { [ "$1" = "node" ] && return 1; return 0; }
out="$(check_prereqs 2>&1)"; rc=$?
assert_rc "$rc" 1 "missing node -> non-zero exit"
assert_contains "$out" "brew install node" "missing node -> exact brew line"

# python present but too old
bs_have() { return 0; }
bs_python_version() { echo "3.10"; }
bs_node_major() { echo "20"; }
out="$(check_prereqs 2>&1)"; rc=$?
assert_rc "$rc" 1 "old python -> non-zero exit"
assert_contains "$out" "brew install python" "old python -> brew python line"

# node present but too old
bs_python_version() { echo "3.13"; }
bs_node_major() { echo "16"; }
out="$(check_prereqs 2>&1)"; rc=$?
assert_rc "$rc" 1 "old node -> non-zero exit"

# everything present and new enough
bs_have() { return 0; }
bs_python_version() { echo "3.13"; }
bs_node_major() { echo "20"; }
out="$(check_prereqs 2>&1)"; rc=$?
assert_rc "$rc" 0 "all prereqs present -> zero exit"

# --- clone vs update ---
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
CALLS="$TMP/gitcalls"

# Stub git: record args; create the target dir on clone so later checks pass.
git() { echo "$*" >> "$CALLS"; if [ "$1" = "clone" ]; then mkdir -p "${@: -1}"; fi; return 0; }

export YOUROS_REPO="torimeyer/youros"

# fresh clone path (target absent)
export YOUROS_DIR="$TMP/clone-target"
export YOUROS_BRANCH="main"
: > "$CALLS"
clone_or_update >/dev/null 2>&1
calls="$(cat "$CALLS")"
assert_contains "$calls" "clone" "absent dir -> git clone"
assert_contains "$calls" "https://github.com/torimeyer/youros.git" "clone uses public https url"
assert_contains "$calls" "$TMP/clone-target" "clone targets YOUROS_DIR"

# existing repo -> pull
mkdir -p "$TMP/existing/.git"
export YOUROS_DIR="$TMP/existing"
: > "$CALLS"
clone_or_update >/dev/null 2>&1
calls="$(cat "$CALLS")"
assert_contains "$calls" "pull" "existing repo -> git pull"

# branch passthrough on clone
export YOUROS_DIR="$TMP/branch-target"
export YOUROS_BRANCH="some-branch"
: > "$CALLS"
clone_or_update >/dev/null 2>&1
calls="$(cat "$CALLS")"
assert_contains "$calls" "some-branch" "YOUROS_BRANCH passed to git clone"

unset -f git

# --- run_installer runs in-repo install.sh (no re-clone) ---
REPO="$TMP/repo"; mkdir -p "$REPO"
cat > "$REPO/install.sh" <<'INS'
#!/bin/bash
echo "INSTALLER RAN args:$*"
INS
chmod +x "$REPO/install.sh"
export YOUROS_DIR="$REPO"
out="$(run_installer --without-claude-hooks 2>&1)"; rc=$?
assert_rc "$rc" 0 "run_installer succeeds"
assert_contains "$out" "INSTALLER RAN" "run_installer executes the in-repo install.sh"
assert_contains "$out" "--without-claude-hooks" "run_installer forwards args to install.sh"

echo ""
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
