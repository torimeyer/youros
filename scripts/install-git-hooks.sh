#!/usr/bin/env bash
# Installs (or uninstalls) the myOS pre-commit test hook.
#
# Usage:
#   scripts/install-git-hooks.sh            # install
#   scripts/install-git-hooks.sh --status   # show what's installed
#   scripts/install-git-hooks.sh --uninstall
#
# The installer is safe to run more than once. If a local pre-commit
# already exists that is NOT ours, we back it up to pre-commit.local
# and call it from the new hook, so your existing hook keeps running.

set -eu

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_DIR="${REPO_DIR}/.git/hooks"
HOOK_PATH="${HOOK_DIR}/pre-commit"
LOCAL_HOOK="${HOOK_DIR}/pre-commit.local"
MARKER="# myos-pre-commit-test-check v1"
RUNNER="${REPO_DIR}/scripts/pre-commit-test-check.sh"

if [ ! -d "${HOOK_DIR}" ]; then
  echo "error: ${HOOK_DIR} does not exist. Is this a git repo?" >&2
  exit 1
fi

case "${1:-install}" in
  --status)
    if [ -f "${HOOK_PATH}" ] && grep -q "${MARKER}" "${HOOK_PATH}" 2>/dev/null; then
      echo "myos pre-commit hook is INSTALLED at ${HOOK_PATH}."
      if [ -x "${LOCAL_HOOK}" ]; then
        echo "a pre-existing local hook is preserved at ${LOCAL_HOOK} and will run first."
      fi
    else
      echo "myos pre-commit hook is NOT installed."
    fi
    exit 0
    ;;
  --uninstall)
    if [ -f "${HOOK_PATH}" ] && grep -q "${MARKER}" "${HOOK_PATH}" 2>/dev/null; then
      rm -f "${HOOK_PATH}"
      echo "Removed ${HOOK_PATH}."
      if [ -x "${LOCAL_HOOK}" ]; then
        mv "${LOCAL_HOOK}" "${HOOK_PATH}"
        echo "Restored your previous local hook to ${HOOK_PATH}."
      fi
    else
      echo "myos pre-commit hook is not installed, nothing to do."
    fi
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

# If a different hook already exists, preserve it as pre-commit.local.
if [ -f "${HOOK_PATH}" ] && ! grep -q "${MARKER}" "${HOOK_PATH}" 2>/dev/null; then
  if [ ! -f "${LOCAL_HOOK}" ]; then
    cp "${HOOK_PATH}" "${LOCAL_HOOK}"
    chmod +x "${LOCAL_HOOK}"
    echo "Preserved your existing pre-commit hook at ${LOCAL_HOOK}."
  else
    echo "warn: ${LOCAL_HOOK} already exists, leaving it untouched." >&2
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
if [ -x "\${REPO_DIR}/.git/hooks/pre-commit.local" ]; then
  "\${REPO_DIR}/.git/hooks/pre-commit.local" "\$@" || exit \$?
fi

exec "\${REPO_DIR}/scripts/pre-commit-test-check.sh" "\$@"
HOOK

chmod +x "${HOOK_PATH}"
echo "Installed myos pre-commit hook at ${HOOK_PATH}."
echo ""
echo "What it does: before each commit, it runs pytest/vitest/tsc only on the"
echo "files you staged. Typical budget is under 30 seconds. Bypass with"
echo "'git commit --no-verify' in a pinch. Uninstall with"
echo "'scripts/install-git-hooks.sh --uninstall'."
