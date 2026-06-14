#!/bin/bash
# yourOS bootstrap — the one-command install front door.
#
#   curl -fsSL https://raw.githubusercontent.com/torimeyer/youros/main/scripts/bootstrap.sh | bash
#
# What it does, in order:
#   1. Checks prerequisites (git, curl, Python >= 3.11, Node >= 18) and, for
#      anything missing, prints the EXACT `brew install` line and exits non-zero.
#   2. Clones yourOS into ${YOUROS_DIR:-$HOME/youros} (or `git pull` if already
#      there). Honors ${YOUROS_BRANCH:-main} — a plain git ref, nothing more.
#   3. Runs the in-repo ./install.sh, which detects it is already inside the
#      repo and does the heavy lifting (venv, frontend build, aliases, etc.)
#      without re-cloning.
#   4. Offers to start yourOS.
#
# This script is intentionally thin: install.sh owns the real install logic.
# It is vendor-neutral by design — it knows nothing about any specific
# deployment or enterprise. Point it at a different branch with YOUROS_BRANCH.
#
# Overrides (all optional):
#   YOUROS_DIR     where to install            (default: $HOME/youros)
#   YOUROS_REPO    GitHub <org>/<name>         (default: torimeyer/youros)
#   YOUROS_BRANCH  git ref to check out        (default: main)

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'

# --- capability + version probes (overridable in tests) ---

bs_have() { command -v "$1" >/dev/null 2>&1; }

bs_python_version() {
    python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null
}

bs_node_major() {
    node -v 2>/dev/null | sed 's/v//' | cut -d. -f1
}

# --- prereq check -----------------------------------------------------------
# Appends "human label|exact brew line" for each problem, then prints them.
# Returns 1 if anything is missing or too old, 0 otherwise.
check_prereqs() {
    local missing=()

    bs_have git  || missing+=("git|brew install git")
    bs_have curl || missing+=("curl|brew install curl")
    bs_have npm  || missing+=("npm (ships with Node)|brew install node")

    if bs_have python3; then
        local pv maj min
        pv="$(bs_python_version)"
        maj="${pv%%.*}"; min="${pv#*.}"
        if [ "${maj:-0}" -lt 3 ] || { [ "${maj:-0}" -eq 3 ] && [ "${min:-0}" -lt 11 ]; }; then
            missing+=("Python 3.11 or newer (found ${pv:-none})|brew install python@3.13")
        fi
    else
        missing+=("Python 3.11 or newer|brew install python@3.13")
    fi

    if bs_have node; then
        local nm
        nm="$(bs_node_major)"
        if [ "${nm:-0}" -lt 18 ]; then
            missing+=("Node 18 or newer (found v${nm:-none})|brew install node")
        fi
    else
        missing+=("Node 18 or newer|brew install node")
    fi

    if [ "${#missing[@]}" -ne 0 ]; then
        echo "" >&2
        echo -e "${RED}Missing prerequisites:${NC}" >&2
        local m
        for m in "${missing[@]}"; do
            echo "  - ${m%%|*}" >&2
            echo -e "      install it with:  ${YELLOW}${m#*|}${NC}" >&2
        done
        echo "" >&2
        echo "Install the items above, then re-run this command." >&2
        return 1
    fi
    return 0
}

# --- clone or update --------------------------------------------------------
clone_or_update() {
    local dir repo branch
    dir="${YOUROS_DIR:-$HOME/youros}"
    repo="${YOUROS_REPO:-torimeyer/youros}"
    branch="${YOUROS_BRANCH:-main}"

    if [ -d "$dir/.git" ]; then
        echo -e "${BLUE}Updating existing yourOS at $dir ...${NC}"
        git -C "$dir" pull --ff-only || echo -e "${YELLOW}Could not fast-forward; keeping existing files.${NC}"
    else
        echo -e "${BLUE}Downloading yourOS to $dir ...${NC}"
        git clone --branch "$branch" "https://github.com/${repo}.git" "$dir"
    fi
}

# --- run the in-repo installer ---------------------------------------------
# Runs from inside the cloned repo so install.sh's "running from existing repo"
# branch wins and it does not clone a second time. Forwards any args through.
run_installer() {
    local dir installer
    dir="${YOUROS_DIR:-$HOME/youros}"
    installer="${BOOTSTRAP_INSTALLER:-$dir/install.sh}"
    ( cd "$dir" && bash "$installer" "$@" )
}

# --- offer to start ---------------------------------------------------------
offer_start() {
    local dir
    dir="${YOUROS_DIR:-$HOME/youros}"
    echo ""
    echo -e "${GREEN}yourOS is installed.${NC} Start it with:  ${dir}/start.sh   (or type: youros in a new terminal)"
    # Never block in non-interactive / piped (curl | bash) contexts.
    [ "${BOOTSTRAP_NONINTERACTIVE:-0}" = "1" ] && return 0
    [ -t 0 ] || return 0
    printf "Start yourOS now? [y/N] "
    read -r ans || true
    case "$ans" in
        y|Y|yes|YES) exec "$dir/start.sh" ;;
    esac
}

main() {
    echo ""
    echo -e "${BLUE}=== yourOS bootstrap ===${NC}"
    check_prereqs || exit 1
    clone_or_update
    run_installer "$@"
    offer_start
}

# Source-guard: run main when executed or piped (curl | bash), but NOT when
# sourced for testing. Sourced => BASH_SOURCE[0] is set and differs from $0.
if [ -n "${BASH_SOURCE[0]:-}" ] && [ "${BASH_SOURCE[0]:-}" != "$0" ]; then
    :  # sourced for tests; expose functions, do not run
else
    set -e
    main "$@"
fi
