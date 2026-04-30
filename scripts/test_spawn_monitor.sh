#!/usr/bin/env bash
# test_spawn_monitor.sh
#
# Tests for scripts/spawn-monitor.sh.
#
# Cases:
#   1. Exits (DONE) when API returns status=completed for the named agent
#   2. Exits (DONE) when a commit matching the sentinel lands (agent left list)
#   3. Exits with error when called with no args and no pending-monitor-spawns.jsonl
#   4. Auto-mode: reads agent name from pending-monitor-spawns.jsonl and exits DONE

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SPAWN_MON="${SCRIPT_DIR}/spawn-monitor.sh"

if [[ ! -x "${SPAWN_MON}" ]]; then
    echo "spawn-monitor.sh not found or not executable: ${SPAWN_MON}" >&2
    exit 3
fi

TMP="$(mktemp -d /tmp/test_spawn_monitor.XXXXXX)"
trap 'rm -rf "${TMP}"' EXIT

FAIL=0
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1" >&2; FAIL=1; }

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

run_spawn_monitor() {
    local agent_name="$1" git_sentinel="$2" interval="$3" max_secs="$4"
    local git_dir="$5" fake_curl_dir="$6"
    local pending_file="${7:-}"
    local outfile="${TMP}/sm_$$_${RANDOM}.out"

    (
        export PATH="${fake_curl_dir}:${PATH}"
        export MYOS_API_BASE="http://unused"
        if [[ -n "${pending_file}" ]]; then
            export CLAUDE_PROJECT_DIR="${TMP}/proj_$$_${RANDOM}"
            mkdir -p "${CLAUDE_PROJECT_DIR}/.ostk"
            cp "${pending_file}" "${CLAUDE_PROJECT_DIR}/.ostk/pending-monitor-spawns.jsonl"
            cd "${git_dir}"
            bash "${SPAWN_MON}"
        else
            cd "${git_dir}"
            bash "${SPAWN_MON}" "${agent_name}" "${git_sentinel}" "${interval}"
        fi
    ) > "${outfile}" 2>&1 &
    local pid=$!

    (sleep "${max_secs}" && kill "${pid}" 2>/dev/null) &
    local watcher=$!

    wait "${pid}" 2>/dev/null || true
    kill "${watcher}" 2>/dev/null || true
    wait "${watcher}" 2>/dev/null || true

    cat "${outfile}" 2>/dev/null || true
    rm -f "${outfile}"
}

# ── Case 1: exits DONE when API returns status=completed ─────────────────────
D1="${TMP}/c1"; mkdir -p "${D1}/curl_dir"
cat > "${D1}/curl_dir/curl" <<'SH'
#!/usr/bin/env bash
printf '{"agents":[{"name":"my-agent","status":"completed","current_step":"","transcript_bytes":200}]}'
SH
chmod +x "${D1}/curl_dir/curl"
seed_git_repo "${D1}/repo" "initial"

out1="$(run_spawn_monitor "my-agent" "my-agent" 0 5 "${D1}/repo" "${D1}/curl_dir")"
if printf '%s\n' "${out1}" | grep -q 'DONE'; then
    pass "case1: exits DONE when API returns status=completed"
else
    fail "case1: expected DONE in output"
    printf '%s\n' "${out1}" | head -10
fi

# ── Case 2: exits DONE when commit lands + agent left running list ────────────
D2="${TMP}/c2"; mkdir -p "${D2}/curl_dir"
CALLS2="${D2}/calls"; echo 0 > "${CALLS2}"
cat > "${D2}/curl_dir/curl" <<SH
#!/usr/bin/env bash
count=\$(cat "${CALLS2}")
count=\$((count+1))
printf '%s' "\$count" > "${CALLS2}"
if [[ "\$count" -le 1 ]]; then
    printf '{"agents":[{"name":"worker","status":"running","current_step":"building","transcript_bytes":50}]}'
else
    printf '{"agents":[]}'
fi
SH
chmod +x "${D2}/curl_dir/curl"
seed_git_repo "${D2}/repo" "initial"
(cd "${D2}/repo" && echo "fix" > f.txt && git add f.txt && git commit -q -m "worker: landed the feature")

out2="$(run_spawn_monitor "worker" "worker" 0 6 "${D2}/repo" "${D2}/curl_dir")"
if printf '%s\n' "${out2}" | grep -q 'DONE'; then
    pass "case2: exits DONE when agent left running list + git commit landed"
else
    fail "case2: expected DONE"
    printf '%s\n' "${out2}" | head -10
fi

# ── Case 3: error when no args and no pending-monitor-spawns.jsonl ────────────
D3="${TMP}/c3"; mkdir -p "${D3}/curl_dir"
cat > "${D3}/curl_dir/curl" <<'SH'
#!/usr/bin/env bash
printf '{"agents":[]}'
SH
chmod +x "${D3}/curl_dir/curl"
seed_git_repo "${D3}/repo" "initial"

out3_file="${TMP}/sm3_out.txt"
(
    export PATH="${D3}/curl_dir:${PATH}"
    export CLAUDE_PROJECT_DIR="${D3}/nopending"
    mkdir -p "${D3}/nopending"
    cd "${D3}/repo"
    bash "${SPAWN_MON}"
) > "${out3_file}" 2>&1 || true
out3="$(cat "${out3_file}")"
if printf '%s\n' "${out3}" | grep -qi 'error'; then
    pass "case3: exits with error when no args and no pending spawns file"
else
    fail "case3: expected error message"
    printf '%s\n' "${out3}" | head -5
fi

# ── Case 4: auto-mode reads agent name from pending-monitor-spawns.jsonl ──────
D4="${TMP}/c4"; mkdir -p "${D4}/curl_dir"
cat > "${D4}/curl_dir/curl" <<'SH'
#!/usr/bin/env bash
printf '{"agents":[{"name":"auto-agent","status":"completed","current_step":"","transcript_bytes":300}]}'
SH
chmod +x "${D4}/curl_dir/curl"
seed_git_repo "${D4}/repo" "initial"

PENDING4="${TMP}/pending4.jsonl"
printf '{"name":"auto-agent","ts":"2026-04-30T00:00:00Z","source":"task-isolation-bridge"}\n' > "${PENDING4}"

out4="$(run_spawn_monitor "" "" 0 5 "${D4}/repo" "${D4}/curl_dir" "${PENDING4}")"
if printf '%s\n' "${out4}" | grep -q 'DONE'; then
    pass "case4: auto-mode reads agent name from pending-monitor-spawns.jsonl and exits DONE"
else
    fail "case4: expected DONE via auto-mode"
    printf '%s\n' "${out4}" | head -10
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
if [[ "${FAIL}" -eq 0 ]]; then
    echo "all spawn-monitor cases passed"
    exit 0
else
    echo "one or more spawn-monitor cases FAILED"
    exit 1
fi
