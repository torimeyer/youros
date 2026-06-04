#!/usr/bin/env bash
# Forbidden-path guard for git pre-commit.
#
# Blocks any commit that stages user/session working-state that must never
# land in the repository. These paths contain transcripts, agent state,
# memory files, drafts, and specs that belong to the user's local
# environment — not to the codebase history.
#
# Usage:
#   scripts/forbidden-path-guard.sh              # check current git index
# Called from scripts/pre-commit-test-check.sh before any test checks.
#
# Exit codes:
#   0  clean — no forbidden paths staged
#   1  blocked — one or more forbidden paths detected (prints details to stderr)
#
# Override: MYOS_SKIP_HOOK=1 git commit  (bypass all pre-commit checks)

set -u

REPO_DIR="${REPO_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"

if [ -z "${REPO_DIR}" ]; then
    exit 0
fi

FORBIDDEN_PREFIXES=(
    "transcripts/"
    ".ostk/sessions/"
    ".ostk/memory/"
    ".ostk/needles/"
    "docs/spec/"
    "docs/draft/"
    "docs/superpowers/"
    "docs/projects/"
    "projects/iam/"
    ".claude/agents/"
    ".claude/custom-agents/"
    ".claude/handoffs/"
    ".claude/plans/"
    ".claude/memory/"
)

# These .claude/ paths are yourOS source code — always allowed.
ALLOWED_EXACT=(
    ".claude/settings.json"
    ".claude/settings.local.json"
)
ALLOWED_PREFIXES=(
    ".claude/hooks/"
    ".claude/skills/"
)

STAGED="$(git -C "${REPO_DIR}" diff --cached --name-only --diff-filter=ACMRT 2>/dev/null)"

if [ -z "${STAGED}" ]; then
    exit 0
fi

is_allowed() {
    local path="$1"
    for a in "${ALLOWED_EXACT[@]}"; do
        [ "${path}" = "${a}" ] && return 0
    done
    for p in "${ALLOWED_PREFIXES[@]}"; do
        case "${path}" in
            "${p}"*) return 0 ;;
        esac
    done
    return 1
}

BLOCKED=0

while IFS= read -r filepath; do
    [ -z "${filepath}" ] && continue
    is_allowed "${filepath}" && continue
    for prefix in "${FORBIDDEN_PREFIXES[@]}"; do
        case "${filepath}" in
            "${prefix}"*)
                printf '[forbidden-path-guard] BLOCKED: %s\n' "${filepath}" >&2
                printf '  Reason: "%s" paths contain session or working state\n' "${prefix}" >&2
                printf '  that must not be committed. Remove from staging:\n' >&2
                printf '    git reset HEAD "%s"\n' "${filepath}" >&2
                BLOCKED=1
                break
                ;;
        esac
    done
done <<< "${STAGED}"

if [ "${BLOCKED}" -eq 1 ]; then
    printf '\n[forbidden-path-guard] Commit blocked.\n' >&2
    printf 'These files belong to your local environment, not the repo.\n' >&2
    printf 'Fix: git reset HEAD <file>  then retry.\n' >&2
    printf 'Override (use only if you know what you are doing):\n' >&2
    printf '  MYOS_SKIP_HOOK=1 git commit\n' >&2
    exit 1
fi

exit 0
