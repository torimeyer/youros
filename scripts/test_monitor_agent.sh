#!/usr/bin/env bash
# test_monitor_agent.sh
#
# Tests for scripts/monitor-agent.sh.
#
# All cases use a fake curl binary (first on PATH) and throwaway git repos
# so we never touch the live backend or the real repository.
# Uses a background-process + kill pattern for timeouts (macOS has no `timeout`
# without coreutils).
#
# Cases:
#   1. Emits on every tick for the first 3 ticks even when status is unchanged
#   2. Exits with DONE when API returns status=completed
#   3. Emits WARNING after 3 consecutive empty responses (API unreachable)
#   4. Circuit-breaker fires after 5 consecutive empty + no git sentinel
#   5. DONE when agent leaves running list and git sentinel matches
#   6. DONE via git-log fallback at circuit-breaker (API always unreachable, commit landed)
#   7. Does NOT declare DONE when agent left list but sentinel is absent

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MONITOR="${SCRIPT_DIR}/monitor-agent.sh"

if [[ ! -x "${MONITOR}" ]]; then
  echo "monitor-agent.sh not found or not executable: ${MONITOR}" >&2
  exit 3
fi

TMP="$(mktemp -d /tmp/test_monitor_agent.XXXXXX)"
trap 'rm -rf "${TMP}"' EXIT

FAIL=0
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1" >&2; FAIL=1; }

# Build a minimal git repo with an initial commit
seed_git_repo() {
  local dir="$1" msg="${2:-initial}"
  mkdir -p "${dir}"
  cd "${dir}"
  git init -q
  git config user.email "t@t" && git config user.name "t"
  echo "seed" > seed.txt
  git add seed.txt
  git commit -q -m "${msg}"
  cd - > /dev/null
}

# Run monitor-agent.sh with a fake curl on PATH and a temp git repo.
# Kills the monitor after max_secs to prevent hanging.
# Prints the monitor's combined stdout+stderr.
#
# Args: agent sentinel interval max_secs git_dir fake_curl_dir
run_monitor() {
  local agent="$1" sentinel="$2" interval="$3" max_secs="$4"
  local git_dir="$5" fake_curl_dir="$6"
  local outfile="${TMP}/mon_$$_${RANDOM}.out"

  (
    export PATH="${fake_curl_dir}:${PATH}"
    export YOUROS_API_BASE="http://unused"
    cd "${git_dir}"
    bash "${MONITOR}" "${agent}" "${sentinel}" "${interval}"
  ) > "${outfile}" 2>&1 &
  local pid=$!

  # Watchdog: kill the monitor after max_secs so tests don't hang
  (sleep "${max_secs}" && kill "${pid}" 2>/dev/null) &
  local watcher=$!

  wait "${pid}" 2>/dev/null || true
  kill "${watcher}" 2>/dev/null || true
  wait "${watcher}" 2>/dev/null || true

  cat "${outfile}" 2>/dev/null || true
  rm -f "${outfile}"
}

# ── Case 1: emits on every tick for the first 3 ticks ────────────────────────
D1="${TMP}/c1"; mkdir -p "${D1}/curl_dir"
cat > "${D1}/curl_dir/curl" <<'SH'
#!/usr/bin/env bash
printf '{"agents":[{"name":"my-agent","status":"running","current_step":"step1","transcript_bytes":100}]}'
SH
chmod +x "${D1}/curl_dir/curl"
seed_git_repo "${D1}/repo" "initial"

out1="$(run_monitor "my-agent" "my-agent" 0 4 "${D1}/repo" "${D1}/curl_dir")"
tick_count="$(printf '%s\n' "${out1}" | grep -c 'monitor:my-agent:' 2>/dev/null || echo 0)"
if [[ "${tick_count}" -ge 3 ]]; then
  pass "case1: emits on every tick for first 3 ticks (got ${tick_count} lines)"
else
  fail "case1: expected >= 3 emit lines, got ${tick_count}"
  printf '%s\n' "${out1}" | head -10
fi

# ── Case 2: DONE when API returns status=completed ────────────────────────────
D2="${TMP}/c2"; mkdir -p "${D2}/curl_dir"
cat > "${D2}/curl_dir/curl" <<'SH'
#!/usr/bin/env bash
printf '{"agents":[{"name":"done-agent","status":"completed","current_step":"","transcript_bytes":500}]}'
SH
chmod +x "${D2}/curl_dir/curl"
seed_git_repo "${D2}/repo" "initial"

out2="$(run_monitor "done-agent" "done-agent" 0 5 "${D2}/repo" "${D2}/curl_dir")"
if printf '%s\n' "${out2}" | grep -q 'DONE'; then
  pass "case2: emits DONE on status=completed"
else
  fail "case2: expected DONE in output"
  printf '%s\n' "${out2}" | head -10
fi

# ── Case 3: WARNING after 3 consecutive empty responses ──────────────────────
D3="${TMP}/c3"; mkdir -p "${D3}/curl_dir"
cat > "${D3}/curl_dir/curl" <<'SH'
#!/usr/bin/env bash
exit 1
SH
chmod +x "${D3}/curl_dir/curl"
seed_git_repo "${D3}/repo" "initial"

out3="$(run_monitor "any-agent" "no-sentinel-xyz" 0 6 "${D3}/repo" "${D3}/curl_dir")"
if printf '%s\n' "${out3}" | grep -q 'WARNING'; then
  pass "case3: WARNING emitted after 3 consecutive API failures"
else
  fail "case3: expected WARNING in output"
  printf '%s\n' "${out3}" | head -10
fi

# ── Case 4: circuit-breaker exits after 5 consecutive empty + no sentinel ────
D4="${TMP}/c4"; mkdir -p "${D4}/curl_dir"
cat > "${D4}/curl_dir/curl" <<'SH'
#!/usr/bin/env bash
exit 1
SH
chmod +x "${D4}/curl_dir/curl"
seed_git_repo "${D4}/repo" "initial"

out4="$(run_monitor "any-agent" "no-sentinel-xyz" 0 8 "${D4}/repo" "${D4}/curl_dir")"
if printf '%s\n' "${out4}" | grep -q 'circuit-breaker'; then
  pass "case4: circuit-breaker fires after 5 consecutive API failures"
else
  fail "case4: expected circuit-breaker message"
  printf '%s\n' "${out4}" | head -10
fi

# ── Case 5: DONE when agent leaves running list + git sentinel matches ─────────
D5="${TMP}/c5"; mkdir -p "${D5}/curl_dir"
CALLS5="${D5}/calls"; echo 0 > "${CALLS5}"
cat > "${D5}/curl_dir/curl" <<SH
#!/usr/bin/env bash
count=\$(cat "${CALLS5}")
count=\$((count+1))
printf '%s' "\$count" > "${CALLS5}"
if [[ "\$count" -le 1 ]]; then
  printf '{"agents":[{"name":"worker","status":"running","current_step":"doing","transcript_bytes":50}]}'
else
  printf '{"agents":[]}'
fi
SH
chmod +x "${D5}/curl_dir/curl"
seed_git_repo "${D5}/repo" "unrelated initial"
(cd "${D5}/repo" && echo "fix" > f.txt && git add f.txt && git commit -q -m "worker: fix something important")

out5="$(run_monitor "worker" "worker" 0 6 "${D5}/repo" "${D5}/curl_dir")"
if printf '%s\n' "${out5}" | grep -q 'DONE'; then
  pass "case5: DONE when agent left running list and git sentinel matched"
else
  fail "case5: expected DONE"
  printf '%s\n' "${out5}" | head -15
fi

# ── Case 6: git-log fallback at circuit-breaker when API always unreachable ───
D6="${TMP}/c6"; mkdir -p "${D6}/curl_dir"
cat > "${D6}/curl_dir/curl" <<'SH'
#!/usr/bin/env bash
exit 1
SH
chmod +x "${D6}/curl_dir/curl"
seed_git_repo "${D6}/repo" "initial"
(cd "${D6}/repo" && echo "x" > x.txt && git add x.txt && git commit -q -m "landed-agent: shipped the thing")

# API always fails. At 5 consecutive failures the circuit-breaker checks git.
# Since "landed-agent" is in git log, it should exit 0 with DONE.
out6="$(run_monitor "landed-agent" "landed-agent" 0 10 "${D6}/repo" "${D6}/curl_dir")"
if printf '%s\n' "${out6}" | grep -q 'DONE'; then
  pass "case6: circuit-breaker git fallback fires DONE (API always down, commit in git)"
else
  fail "case6: expected DONE from git-log fallback at circuit-breaker"
  printf '%s\n' "${out6}" | head -15
fi

# ── Case 7: does NOT declare DONE when agent left list but sentinel absent ────
D7="${TMP}/c7"; mkdir -p "${D7}/curl_dir"
CALLS7="${D7}/calls"; echo 0 > "${CALLS7}"
cat > "${D7}/curl_dir/curl" <<SH
#!/usr/bin/env bash
count=\$(cat "${CALLS7}")
count=\$((count+1))
printf '%s' "\$count" > "${CALLS7}"
if [[ "\$count" -le 1 ]]; then
  printf '{"agents":[{"name":"ghost","status":"running","current_step":"","transcript_bytes":10}]}'
else
  printf '{"agents":[]}'
fi
SH
chmod +x "${D7}/curl_dir/curl"
seed_git_repo "${D7}/repo" "initial — no relevant commits here"

out7="$(run_monitor "ghost" "ghost-sentinel-NOTPRESENT" 0 4 "${D7}/repo" "${D7}/curl_dir")"
if printf '%s\n' "${out7}" | grep -q 'DONE — agent left running list'; then
  fail "case7: declared DONE but git sentinel was absent"
  printf '%s\n' "${out7}" | head -10
else
  pass "case7: does NOT declare DONE when git sentinel is absent"
fi

# ── Case 8: git_has_sentinel passes --all to git log ─────────────────────────
# Verify that git_has_sentinel() invokes "git log --all ..." so that commits
# on worktree-agent-* branches (invisible without --all) are found.
D8="${TMP}/c8"; mkdir -p "${D8}/curl_dir"
CALLS8="${D8}/calls"; echo 0 > "${CALLS8}"
cat > "${D8}/curl_dir/curl" <<SH
#!/usr/bin/env bash
count=\$(cat "${CALLS8}")
count=\$((count+1))
printf '%s' "\$count" > "${CALLS8}"
if [[ "\$count" -le 1 ]]; then
  printf '{"agents":[{"name":"gitflag-agent","status":"running","current_step":"","transcript_bytes":10}]}'
else
  printf '{"agents":[]}'
fi
SH
chmod +x "${D8}/curl_dir/curl"
seed_git_repo "${D8}/repo" "initial"
(cd "${D8}/repo" && echo "fix" > fix.txt && git add fix.txt && git commit -q -m "gitflag-agent: landed")
GITLOG8="${D8}/git_calls.log"
# Fake git: records every invocation's arguments, then delegates to real git
cat > "${D8}/curl_dir/git" <<SH
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "${GITLOG8}"
exec /usr/bin/git "\$@"
SH
chmod +x "${D8}/curl_dir/git"

run_monitor "gitflag-agent" "gitflag-agent" 0 6 "${D8}/repo" "${D8}/curl_dir" > /dev/null
if [[ -f "${GITLOG8}" ]] && grep -q -- '--all' "${GITLOG8}"; then
  pass "case8: git_has_sentinel passes --all to git log"
else
  fail "case8: git_has_sentinel did not invoke git with --all"
  echo "git calls recorded:" >&2
  cat "${GITLOG8}" 2>/dev/null | head -5 >&2
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
if [[ "${FAIL}" -eq 0 ]]; then
  echo "all monitor-agent cases passed"
  exit 0
else
  echo "one or more monitor-agent cases FAILED"
  exit 1
fi
