#!/usr/bin/env bash
# Installs (or uninstalls) myOS git hooks via core.hooksPath.
#
# Usage:
#   scripts/install-git-hooks.sh            # install
#   scripts/install-git-hooks.sh --status   # show what's installed
#   scripts/install-git-hooks.sh --uninstall
#
# Sets git's core.hooksPath to .githooks so both hooks activate:
#   pre-commit  — runs tests on staged files (scripts/pre-commit-test-check.sh)
#   post-commit — auto-closes needles when commit message contains →NNNN
#
# If a local pre-commit already exists that is NOT ours, we back it up to
# .githooks/pre-commit.local and call it from the new hook.

set -eu

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GITHOOKS_DIR="${REPO_DIR}/.githooks"
HOOK_PATH="${GITHOOKS_DIR}/pre-commit"
LOCAL_HOOK="${GITHOOKS_DIR}/pre-commit.local"
OLD_GIT_HOOK="${REPO_DIR}/.git/hooks/pre-commit"
MARKER="# myos-pre-commit-test-check v2"
OLD_MARKER="# myos-pre-commit-test-check v1"
RUNNER="${REPO_DIR}/scripts/pre-commit-test-check.sh"

if [ ! -d "${REPO_DIR}/.git" ]; then
  echo "error: ${REPO_DIR}/.git does not exist. Is this a git repo?" >&2
  exit 1
fi

case "${1:-install}" in
  --status)
    hooks_path="$(git -C "${REPO_DIR}" config core.hooksPath 2>/dev/null || true)"
    if [ "${hooks_path}" = ".githooks" ] && [ -f "${HOOK_PATH}" ] && grep -q "${MARKER}" "${HOOK_PATH}" 2>/dev/null; then
      echo "myos git hooks are INSTALLED (core.hooksPath=.githooks)."
      echo "  pre-commit:  ${HOOK_PATH}"
      echo "  post-commit: ${GITHOOKS_DIR}/post-commit"
      if [ -x "${LOCAL_HOOK}" ]; then
        echo "  pre-existing local hook preserved at ${LOCAL_HOOK} and runs first."
      fi
    else
      echo "myos git hooks are NOT installed."
    fi
    exit 0
    ;;
  --uninstall)
    if [ -f "${HOOK_PATH}" ] && grep -q "${MARKER}" "${HOOK_PATH}" 2>/dev/null; then
      rm -f "${HOOK_PATH}"
      echo "Removed ${HOOK_PATH}."
      if [ -x "${LOCAL_HOOK}" ]; then
        mv "${LOCAL_HOOK}" "${OLD_GIT_HOOK}"
        chmod +x "${OLD_GIT_HOOK}"
        echo "Restored your previous local hook to .git/hooks/pre-commit."
      fi
    else
      echo "myos pre-commit hook is not installed, nothing to do."
    fi
    git -C "${REPO_DIR}" config --unset core.hooksPath 2>/dev/null || true
    echo "Unset core.hooksPath."
    exit 0
    ;;
  install|"")
    : # fall through to install
    ;;
  *)
    echo "usage: $0 [install|--status|--uninstall]" >&2
    exit 2
    ;;
esac

if [ ! -x "${RUNNER}" ]; then
  chmod +x "${RUNNER}" || true
fi

# Migrate from v1: remove the old .git/hooks/pre-commit we wrote.
if [ -f "${OLD_GIT_HOOK}" ] && grep -q "${OLD_MARKER}" "${OLD_GIT_HOOK}" 2>/dev/null; then
  rm -f "${OLD_GIT_HOOK}"
  echo "Removed old v1 pre-commit hook from .git/hooks/pre-commit."
fi

# If a non-myos pre-commit exists in .git/hooks/, preserve it.
if [ -f "${OLD_GIT_HOOK}" ]; then
  if [ ! -f "${LOCAL_HOOK}" ]; then
    cp "${OLD_GIT_HOOK}" "${LOCAL_HOOK}"
    chmod +x "${LOCAL_HOOK}"
    echo "Preserved your existing .git/hooks/pre-commit at ${LOCAL_HOOK}."
  else
    echo "warn: ${LOCAL_HOOK} already exists, leaving .git/hooks/pre-commit untouched." >&2
  fi
fi

# If a non-myos pre-commit already exists in .githooks/, preserve it.
if [ -f "${HOOK_PATH}" ] && ! grep -q "${MARKER}" "${HOOK_PATH}" 2>/dev/null; then
  if [ ! -f "${LOCAL_HOOK}" ]; then
    cp "${HOOK_PATH}" "${LOCAL_HOOK}"
    chmod +x "${LOCAL_HOOK}"
    echo "Preserved your existing .githooks/pre-commit at ${LOCAL_HOOK}."
  else
    echo "warn: ${LOCAL_HOOK} already exists, leaving .githooks/pre-commit untouched." >&2
  fi
fi

cat > "${HOOK_PATH}" <<HOOK
#!/usr/bin/env bash
${MARKER}
# Installed by scripts/install-git-hooks.sh. To uninstall run:
#   scripts/install-git-hooks.sh --uninstall
set -u
REPO_DIR="\$(git rev-parse --show-toplevel)"

# Run a pre-existing local hook first, if we preserved one.
if [ -x "\${REPO_DIR}/.githooks/pre-commit.local" ]; then
  "\${REPO_DIR}/.githooks/pre-commit.local" "\$@" || exit \$?
fi

exec "\${REPO_DIR}/scripts/pre-commit-test-check.sh" "\$@"
HOOK

chmod +x "${HOOK_PATH}"
git -C "${REPO_DIR}" config core.hooksPath .githooks
echo "Installed myos git hooks:"
echo "  core.hooksPath=.githooks"
echo "  pre-commit  — runs tests on staged files before each commit"
echo "  post-commit — auto-closes needles (→NNNN) after each commit"
echo ""
echo "Bypass pre-commit with 'git commit --no-verify' in a pinch."
echo "Uninstall with 'scripts/install-git-hooks.sh --uninstall'."
