#!/usr/bin/env bash
# Block em-dashes introduced in this commit's frontend changes.
# Rule: tori does not want em-dashes in myOS frontend strings.
#
# Default mode: only fails on NEWLY ADDED em-dash lines in staged changes
# under app/src. Existing em-dashes in unchanged files are NOT blocked so
# unrelated commits still go through. Cleanup is a separate task.
#
# --all flag: lists every existing em-dash under app/src and exits 1 if any.
# Use this to sweep before turning on stricter enforcement.
set -u
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

if [ "${1:-}" = "--all" ]; then
    HITS=$(grep -rn --include='*.ts' --include='*.tsx' \
        --exclude-dir=node_modules --exclude-dir=dist --exclude-dir=__generated__ \
        -- $'\xe2\x80\x94' app/src 2>/dev/null || true)
    if [ -z "${HITS}" ]; then
        echo "[em-dash --all] none found in frontend."
        exit 0
    fi
    echo "[em-dash --all] em-dashes currently in frontend:"
    echo "${HITS}"
    exit 1
fi

# Default mode: only NEWLY ADDED lines in this commit.
# git diff --cached --unified=0 outputs lines starting with + for added.
# We scan for em-dash in those, scoped to app/src/*.ts and *.tsx files.
HITS=$(git diff --cached --unified=0 2>/dev/null | \
    awk '
        /^\+\+\+ b\// {
            file = substr($0, 7)
            next
        }
        /^\+[^+]/ {
            if (file ~ /^app\/src\// && file ~ /\.(ts|tsx)$/ && index($0, "\xe2\x80\x94") > 0) {
                print file ": " $0
            }
        }
    ' | head -50)

if [ -n "${HITS}" ]; then
    echo "[em-dash check] em-dashes added in this commit (project rule: no em-dashes in myOS frontend):"
    echo "${HITS}"
    echo ""
    echo "Replace with a period, comma, or rewrite. Existing em-dashes are not blocked."
    echo "To list ALL existing em-dashes for cleanup: scripts/check-no-emdash-frontend.sh --all"
    exit 1
fi

exit 0
