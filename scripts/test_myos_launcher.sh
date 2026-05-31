#!/usr/bin/env bash
# Regression test for the generic myos() launcher (scripts/myos.zsh).
#
# Consolidates the invariants previously spread across scripts/test_tori_*.sh,
# adapted for the name-free launcher. Guards against known regressions:
#   parallel updates, pty-isolated claude update, ostk + ostk-recall updaters,
#   readiness probes that trust HTTP not PID, no zsh `local` output leak,
#   visible boot, config-driven name (no hardcoded personal identity), and
#   machine-agnostic paths.
set -u

MYOS_ZSH="${MYOS_ZSH:-$(cd "$(dirname "$0")" && pwd)/myos.zsh}"
if [[ ! -f "$MYOS_ZSH" ]]; then
  echo "FAIL: $MYOS_ZSH not found" >&2
  exit 1
fi

# Extract the myos() function body (start at "myos() {", stop at first "}" col 0).
body=$(awk '
  /^myos\(\) \{/ { inside = 1 }
  inside { print }
  inside && /^\}$/ { exit }
' "$MYOS_ZSH")

if [[ -z "$body" ]]; then
  echo "FAIL: could not locate myos() function in $MYOS_ZSH" >&2
  exit 1
fi

fail=0
check() { # check "desc" <0-if-pass>
  if [[ "$2" -ne 0 ]]; then echo "FAIL: $1" >&2; fail=1; fi
}

# --- Parallelism: ostk, ostk-recall, git all backgrounded, then waited on ---
grep -qE 'local _ostk_pid=\$!' <<<"$body";   check "ostk check not backgrounded" $?
grep -qE 'local _recall_pid=\$!' <<<"$body"; check "ostk-recall check not backgrounded" $?
grep -qE 'local _git_pid=\$!' <<<"$body";    check "git check not backgrounded" $?
grep -qE 'wait \$_ostk_pid \$_recall_pid \$_git_pid' <<<"$body"; check "updates not waited together" $?

# --- claude update: backgrounded, pty-isolated, opt-in only ---
grep -qE 'claude update.*&' <<<"$body";        check "claude update not backgrounded" $?
grep -qF '/usr/bin/script' <<<"$body";          check "claude update missing script(1) pty isolation" $?
grep -qF 'MYOS_LAUNCH_AGENT' <<<"$body";        check "no MYOS_LAUNCH_AGENT opt-in gate" $?

# --- ostk updater: tries fuse asset, surfaces curl errors (-fSL not -fsL) ---
grep -qF 'aarch64-apple-darwin-fuse' <<<"$body"; check "ostk updater missing fuse asset name" $?
grep -qF 'curl -fsL' <<<"$body" && { echo "FAIL: uses curl -fsL (silences errors); use -fSL" >&2; fail=1; }

# --- ostk-recall updater present (separate binary / release stream) ---
grep -qF 'os-tack/ostk-recall' <<<"$body";   check "no ostk-recall updater" $?
grep -qF 'ostk-recall --version' <<<"$body"; check "ostk-recall version not read" $?

# --- Name comes from settings, not hardcoded personal identity ---
grep -qF 'settings.json' <<<"$body";  check "os name not read from settings.json" $?
# The personal launcher's hallmarks must NOT survive into the generic one:
# the tori() function name, TORI_ env vars, or the TORIOS ascii banner.
# (The repo dir name "torios" in the default path is allowed; it's the
# conventional checkout dir used across main, not a personal identity.)
if grep -qE 'TORI_|^tori\(\)|TORIOS' <<<"$body"; then
  echo "FAIL: launcher retains a personal marker (tori() / TORI_ env / TORIOS art)" >&2; fail=1
fi

# --- Machine-agnostic repo path: MYOS_HOME drives the location ---
grep -qF 'MYOS_HOME' <<<"$body"; check "repo path not driven by MYOS_HOME" $?
# Every repo reference must go through the $_home var, not a bare ~/torios.
if grep -qE 'git -C (~|\$HOME)/torios|cd (~|\$HOME)/torios' <<<"$body"; then
  echo "FAIL: a repo op uses a bare ~/torios path instead of \$_home" >&2; fail=1
fi

# --- Readiness probes trust HTTP, not PID; vite via localhost ---
grep -qF '/api/health' <<<"$body"; check "backend probe missing /api/health" $?
# Ignore comments (which legitimately mention the anti-pattern); flag only real code.
code_only=$(grep -vE '^[[:space:]]*#' <<<"$body")
if grep -qE 'kill -0 \$be_pid' <<<"$code_only"; then
  echo "FAIL: backend readiness gates on kill -0 \$be_pid instead of the health probe" >&2; fail=1
fi

# --- No zsh `local` output leak: loop vars declared once above the loop ---
if grep -qE '^\s+local _response$|^\s+local _http_code$' <<<"$(grep -A40 'while \[ \$_attempt' <<<"$body")"; then
  echo "FAIL: _response/_http_code re-declared inside the probe loop (zsh leak)" >&2; fail=1
fi

# --- ostk boot runs visibly (logged), needle counts use safe fallback ---
grep -qF 'ostk boot' <<<"$body"; check "ostk boot not invoked" $?
# The broken pattern is specifically `grep -c ... || echo 0` (grep -c double-prints
# on zero matches). A plain `git rev-list --count || echo 0` is safe and allowed.
if grep -qE 'grep -c[^)]*\|\| echo 0' <<<"$body"; then
  echo "FAIL: needle count uses broken 'grep -c ... || echo 0' pattern; use var=\$(...) || var=0" >&2; fail=1
fi

if [[ "$fail" -ne 0 ]]; then exit 1; fi
echo "PASS: myos() launcher invariants hold (parallel ostk+recall+git updates, pty-isolated opt-in claude update, -fSL, HTTP probes, no leak, visible boot, config-driven name, MYOS_HOME paths)."
exit 0
