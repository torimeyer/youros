#!/usr/bin/env bash
# Regression test: tori() must NOT launch Claude or run `claude update`
# when the user has not opted in via TORI_LAUNCH_AGENT=claude.
#
# Rationale: myOS is generic by default. Not every user is on Claude
# (Codex, Cursor, plain shell, or no agent at all are all valid). The
# Claude-specific hooks (auto-update + final CLI launch) must be opt-in.
#
# Invariants enforced statically:
#   1. tori() must reference TORI_LAUNCH_AGENT (the opt-in gate).
#   2. Every `command claude --dangerously-skip-permissions` call in
#      tori() must be preceded (in-body) by a TORI_LAUNCH_AGENT check.
#   3. Every `command claude update` call must also be preceded by a
#      TORI_LAUNCH_AGENT check.
#
# Runtime check: shadow the `claude` binary with a logging stub, stub
# the dev-backend/dev-frontend scripts so tori does not bind real
# ports, invoke `tori` under zsh with NO TORI_LAUNCH_AGENT set, and
# confirm that neither `claude update` nor `claude --dangerously-skip-
# permissions` appears in the stub's call log.
#
# Run:
#   bash scripts/test_tori_no_claude_by_default.sh

set -u

TORI_ZSH="${TORI_ZSH:-${ZSHRC:-$(cd "$(dirname "$0")" && pwd)/tori.zsh}}"
ZSHRC="$TORI_ZSH"

if [[ ! -f "$ZSHRC" ]]; then
  echo "FAIL: $ZSHRC not found" >&2
  exit 1
fi

tori_body=$(awk '
  /^tori\(\) \{/ { inside = 1 }
  inside { print }
  inside && /^\}$/ { exit }
' "$ZSHRC")

if [[ -z "$tori_body" ]]; then
  echo "FAIL: could not locate tori() function in $ZSHRC" >&2
  exit 1
fi

fail=0

# --- Static invariants ---

# Invariant 1: TORI_LAUNCH_AGENT must appear as the opt-in gate.
if ! grep -qF 'TORI_LAUNCH_AGENT' <<<"$tori_body"; then
  echo "FAIL: tori() does not reference TORI_LAUNCH_AGENT." >&2
  echo "      The Claude launch must be opt-in; TORI_LAUNCH_AGENT=claude" >&2
  echo "      is the documented gate." >&2
  fail=1
fi

# Invariant 2: every `command claude --dangerously-skip-permissions`
# must be preceded by an `if .*TORI_LAUNCH_AGENT` line in the body.
check_guarded() {
  local pattern="$1"
  local label="$2"
  local lines
  lines=$(grep -nE "$pattern" <<<"$tori_body" || true)
  if [[ -z "$lines" ]]; then
    return 0
  fi
  while IFS=: read -r lineno _; do
    local before
    before=$(awk -v n="$lineno" 'NR < n' <<<"$tori_body")
    if ! grep -qE 'if .*TORI_LAUNCH_AGENT' <<<"$before"; then
      echo "FAIL: '$label' on line $lineno of tori() body is NOT guarded" >&2
      echo "      by any preceding TORI_LAUNCH_AGENT check." >&2
      fail=1
    fi
  done <<<"$lines"
}

check_guarded 'command claude --dangerously-skip-permissions' 'command claude --dangerously-skip-permissions'
check_guarded 'command claude update'                         'command claude update'

# --- Runtime check ---
# Shadow `claude` with a logger, stub the dev scripts, run tori() under
# zsh with no opt-in env var, and assert the two gated call shapes
# never hit the stub.
if command -v zsh >/dev/null 2>&1; then
  workdir=$(mktemp -d)
  stub="$workdir/claude"
  log="$workdir/claude.log"
  cat >"$stub" <<EOF
#!/usr/bin/env bash
echo "STUB_CALLED: \$*" >>"$log"
exit 0
EOF
  chmod +x "$stub"

  cat >"$workdir/dev-backend.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  cat >"$workdir/dev-frontend.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$workdir/dev-backend.sh" "$workdir/dev-frontend.sh"
  : >"$log"

  # Put stub versions of dev-backend/dev-frontend at the path tori()
  # actually calls (it invokes ~/claude/torios/scripts/dev-*.sh by
  # absolute path). We do NOT want to clobber the real repo copies
  # used in development, so skip the runtime check entirely if the
  # user is already running against the real tracked repo and
  # dev-*.sh diverge from our no-op stubs. Detect by checking whether
  # $HOME/claude/torios is a git repo and current cwd points there.
  real_be="$HOME/claude/torios/scripts/dev-backend.sh"
  real_fe="$HOME/claude/torios/scripts/dev-frontend.sh"
  safe_to_stub_dev_scripts=1
  if [[ -f "$real_be" ]] || [[ -f "$real_fe" ]]; then
    safe_to_stub_dev_scripts=0
  fi

  script="$workdir/run.zsh"
  cat >"$script" <<EOF
#!/usr/bin/env zsh
# Prepend the stub dir so \`command claude\` resolves to our logger.
export PATH="$workdir:\$PATH"
# Neutralize heavy side effects.
ostk() { return 0; }
git() { return 0; }
curl() { return 0; }
# If the user's ~/claude/torios does not exist, tori()'s cd will fail
# and return 1; create a safe stand-in that has no dev scripts.
if [[ ! -d \$HOME/claude/torios ]]; then
  mkdir -p \$HOME/claude/torios/scripts
  cp "$workdir/dev-backend.sh" \$HOME/claude/torios/scripts/
  cp "$workdir/dev-frontend.sh" \$HOME/claude/torios/scripts/
fi
# Source tori.zsh under test.
source "$ZSHRC"
tori >/dev/null 2>&1
exit 0
EOF
  chmod +x "$script"

  (
    unset TORI_LAUNCH_AGENT
    zsh "$script"
  ) >/dev/null 2>&1 &
  rpid=$!
  waited=0
  while kill -0 "$rpid" 2>/dev/null; do
    if [[ $waited -ge 30 ]]; then
      kill -9 "$rpid" 2>/dev/null
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$rpid" 2>/dev/null || true

  # The two argv shapes that MUST NOT appear on the default path:
  #   1. `update` (from the `command claude update` background call)
  #   2. `--dangerously-skip-permissions` (from the final launch)
  #
  # Other stub calls (e.g. `auth status` made by backend prewarm) are
  # NOT regressions for this test; they come from subprocesses the
  # backend server spawns, not from tori.zsh itself.
  if grep -qE '^STUB_CALLED:( |.* )update( |$)' "$log" 2>/dev/null; then
    echo "FAIL: 'claude update' was invoked on the default tori() path:" >&2
    grep -E '^STUB_CALLED:.*update' "$log" >&2
    fail=1
  fi
  if grep -qE '^STUB_CALLED:.*--dangerously-skip-permissions' "$log" 2>/dev/null; then
    echo "FAIL: 'claude --dangerously-skip-permissions' was invoked on default path:" >&2
    grep -E '^STUB_CALLED:.*--dangerously-skip-permissions' "$log" >&2
    fail=1
  fi

  rm -rf "$workdir"
else
  echo "SKIP: zsh not available; relying on static invariants only." >&2
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "PASS: default tori() does not invoke 'claude update' or 'claude --dangerously-skip-permissions'."
exit 0
