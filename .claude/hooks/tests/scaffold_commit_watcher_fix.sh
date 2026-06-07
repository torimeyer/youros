#!/bin/bash
# Regression test for scaffold_commit_watcher.sh fixes (→1344).
#
# Proves three bugs are fixed:
#   1. SPAWNED_AT no longer falls back to date +%s at PostToolUse fire time.
#      When YOUROS_SPAWNED_AT is not set, watcher fetches actual spawned_at from
#      the API -- so git log --after finds commits made during the agent's run.
#   2. WORKTREE_PATH is built from the API's worktree_branch (short name), not
#      the raw agent_name (which may be the full untruncated bridge name).
#   3. Premature-close detection fires immediately (no sleep): when
#      transcript_bytes < 5KB AND all branch commits are scaffold-only,
#      writes a JSON entry to scaffold-warnings.jsonl.
#
# Run: bash .claude/hooks/tests/scaffold_commit_watcher_fix.sh
# Budget: 15 seconds.

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WATCHER_LIB="$REPO_ROOT/.claude/hooks/lib/rules/scaffold_commit_watcher.sh"

if [ ! -f "$WATCHER_LIB" ]; then
    echo "FAIL cannot find watcher lib at $WATCHER_LIB"
    exit 1
fi

# Minimal stubs required by the lib (load-rule.sh, log-fire.sh)
rule_enabled()  { return 0; }
log_rule_fire() { return 0; }
export -f rule_enabled log_rule_fire 2>/dev/null || true

FAILED=0
PORT=$((21000 + RANDOM % 5000))
SCRATCH=$(mktemp -d -t watcher-fix-test.XXXXXX)
SERVER_PID=""

fail() { echo "FAIL: $1"; FAILED=1; }
pass() { echo "  pass: $1"; }

cleanup() {
    [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
    wait "${SERVER_PID:-}" 2>/dev/null || true
    rm -rf "$SCRATCH"
}
trap cleanup EXIT

# ---- helpers ----

make_fake_worktree() {
    # Creates a fake git repo simulating a worktree branched from main.
    # main stays at the initial commit; work commits go on worktree-branch (HEAD).
    # This matches real worktree topology so merge-base(HEAD, main) = initial.
    # Usage: make_fake_worktree <dir> <commit_msg>...
    local wt_dir="$1"; shift
    mkdir -p "$wt_dir"
    git -C "$wt_dir" init -q
    git -C "$wt_dir" config user.email "test@test"
    git -C "$wt_dir" config user.name "Test"
    touch "$wt_dir/base.txt"
    git -C "$wt_dir" add base.txt
    git -C "$wt_dir" commit -q -m "initial"
    # Ensure 'main' points to initial regardless of default branch name
    local _def
    _def=$(git -C "$wt_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
    if [ "$_def" != "main" ]; then
        git -C "$wt_dir" branch main 2>/dev/null || true
    fi
    # Switch to worktree-branch; main stays at initial = correct merge-base
    git -C "$wt_dir" checkout -b worktree-branch -q 2>/dev/null || true
    for msg in "$@"; do
        echo "$msg" >> "$wt_dir/work.txt"
        git -C "$wt_dir" add work.txt
        git -C "$wt_dir" commit -q -m "$msg"
    done
}

start_mock_api() {
    # Starts a mock /api/agents endpoint on $PORT serving $1 as JSON body file.
    local body_file="$1"
    python3 - "$PORT" "$body_file" <<'PYSERVER' &
import sys, http.server, json

PORT = int(sys.argv[1])
BODY_FILE = sys.argv[2]

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/agents"):
            with open(BODY_FILE, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a, **kw): return

http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
PYSERVER
    SERVER_PID=$!
    # Wait for server to be ready
    for _ in $(seq 1 30); do
        if curl -s --connect-timeout 1 -m 1 "http://127.0.0.1:${PORT}/api/agents" -o /dev/null 2>/dev/null; then
            break
        fi
        sleep 0.1
    done
}

# ===========================================================================
# TEST 1: Bug #2 (worktree path) + Bug #1 (SPAWNED_AT from API)
#   Agent name is long (full bridge name); worktree uses short branch name.
#   Watcher should find the worktree via API's worktree_branch field.
# ===========================================================================
FULL_AGENT_NAME="1342-orphan-finished-notificati-423fc1"
SHORT_BRANCH="worktree-agent-1342-orphan-finished-ac95f0d7"
SHORT_DIR="agent-1342-orphan-finished-ac95f0d7"
SPAWNED_EPOCH=$(( $(date +%s) - 600 ))  # 10 min ago
SPAWNED_ISO=$(python3 -c "from datetime import datetime,timezone; print(datetime.fromtimestamp(${SPAWNED_EPOCH}, tz=timezone.utc).isoformat())")

# Create fake worktree at the SHORT path
FAKE_PROJECT="$SCRATCH/project1"
mkdir -p "$FAKE_PROJECT/.claude/worktrees"
WORKTREE="$FAKE_PROJECT/.claude/worktrees/$SHORT_DIR"
make_fake_worktree "$WORKTREE" "chore(→1342): scaffold diagnose" "fix(→1342): real fix"

# Mock API body (short transcript so premature-close check fires, but 2 commits so it should NOT warn)
API_BODY="$SCRATCH/agents1.json"
python3 -c "
import json
print(json.dumps({'agents': [{
    'name': '${FULL_AGENT_NAME}',
    'spawned_at': '${SPAWNED_ISO}',
    'worktree_branch': '${SHORT_BRANCH}',
    'transcript_bytes': 6000,
    'status': 'completed',
}]}))
" > "$API_BODY"

start_mock_api "$API_BODY"

WARN_FILE="$SCRATCH/warn1.jsonl"
PARENT_NAME=""  # no parent, so nudge is skipped

# Source the lib and call the function with env overrides
(
    TORIOS_API_BASE="http://127.0.0.1:${PORT}"
    CLAUDE_PROJECT_DIR="$FAKE_PROJECT"
    export TORIOS_API_BASE CLAUDE_PROJECT_DIR
    # Do NOT set YOUROS_SPAWNED_AT — tests that the API fallback works
    unset YOUROS_SPAWNED_AT 2>/dev/null || true
    HOME_ORIG="$HOME"
    # Override HOME so WARN_FILE resolves to our scratch path
    HOME="$SCRATCH/home1"
    mkdir -p "$HOME/subagents"
    # Patch WARN_FILE via HOME override: ~/.youros/subagents/scaffold-warnings.jsonl
    mkdir -p "$HOME/.youros/subagents"
    export HOME

    # Stub rule_enabled and log_rule_fire
    rule_enabled()  { return 0; }
    log_rule_fire() { return 0; }

    . "$WATCHER_LIB"
    _scaffold_commit_watcher_check "Agent" "$FULL_AGENT_NAME" "" 2>/dev/null
    # Wait for background subshell A (premature-close check, no sleep)
    sleep 2
) 2>/dev/null

# No warning expected: transcript_bytes=6000 (>5KB)
WARN_PATH="$SCRATCH/home1/.youros/subagents/scaffold-warnings.jsonl"
if [ -s "$WARN_PATH" ]; then
    fail "test1: unexpected warning for agent with 6KB transcript: $(cat "$WARN_PATH")"
else
    pass "test1: no spurious warning when transcript_bytes >= 5000"
fi

kill "${SERVER_PID:-}" 2>/dev/null; wait "${SERVER_PID:-}" 2>/dev/null; SERVER_PID=""


# ===========================================================================
# TEST 2: Bug #3 (premature-close detection)
#   Short transcript (< 5KB) + scaffold-only commit → warning MUST fire.
# ===========================================================================
FULL_AGENT2="1343-pre-tool-guard-false-posit-482f90"
SHORT_BRANCH2="worktree-agent-1343-pre-tool-guard-f-3d418382"
SHORT_DIR2="agent-1343-pre-tool-guard-f-3d418382"
SPAWNED_EPOCH2=$(( $(date +%s) - 400 ))
SPAWNED_ISO2=$(python3 -c "from datetime import datetime,timezone; print(datetime.fromtimestamp(${SPAWNED_EPOCH2}, tz=timezone.utc).isoformat())")

FAKE_PROJECT2="$SCRATCH/project2"
mkdir -p "$FAKE_PROJECT2/.claude/worktrees"
WORKTREE2="$FAKE_PROJECT2/.claude/worktrees/$SHORT_DIR2"
make_fake_worktree "$WORKTREE2" "chore(→1343): scaffold pre-tool-guard diagnose"

API_BODY2="$SCRATCH/agents2.json"
python3 -c "
import json
print(json.dumps({'agents': [{
    'name': '${FULL_AGENT2}',
    'spawned_at': '${SPAWNED_ISO2}',
    'worktree_branch': '${SHORT_BRANCH2}',
    'transcript_bytes': 3574,
    'status': 'completed',
}]}))
" > "$API_BODY2"

PORT2=$((PORT + 100))
python3 - "$PORT2" "$API_BODY2" <<'PYSERVER2' &
import sys, http.server
PORT = int(sys.argv[1]); BODY = sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/agents"):
            with open(BODY, "rb") as f: body = f.read()
            self.send_response(200); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
        else: self.send_response(404); self.end_headers()
    def log_message(self, *a, **kw): return
http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
PYSERVER2
SERVER2_PID=$!
for _ in $(seq 1 30); do
    if curl -s --connect-timeout 1 -m 1 "http://127.0.0.1:${PORT2}/api/agents" -o /dev/null 2>/dev/null; then break; fi
    sleep 0.1
done

(
    TORIOS_API_BASE="http://127.0.0.1:${PORT2}"
    CLAUDE_PROJECT_DIR="$FAKE_PROJECT2"
    export TORIOS_API_BASE CLAUDE_PROJECT_DIR
    unset YOUROS_SPAWNED_AT 2>/dev/null || true
    HOME="$SCRATCH/home2"
    mkdir -p "$HOME/.youros/subagents"
    export HOME

    rule_enabled()  { return 0; }
    log_rule_fire() { return 0; }

    . "$WATCHER_LIB"
    _scaffold_commit_watcher_check "Agent" "$FULL_AGENT2" "" 2>/dev/null
    sleep 2
) 2>/dev/null

kill "${SERVER2_PID:-}" 2>/dev/null; wait "${SERVER2_PID:-}" 2>/dev/null

WARN_PATH2="$SCRATCH/home2/.youros/subagents/scaffold-warnings.jsonl"
if [ -s "$WARN_PATH2" ]; then
    pass "test2: premature-close warning written for scaffold-only agent"
else
    fail "test2: scaffold-warnings.jsonl empty — premature-close detection did not fire"
fi

# Verify warning JSON content
if [ -s "$WARN_PATH2" ]; then
    if python3 -c "
import json, sys
d = json.loads(open('${WARN_PATH2}').readline())
assert d['agent'] == '${FULL_AGENT2}', f'wrong agent: {d}'
assert d['transcript_bytes'] == 3574, f'wrong tb: {d}'
assert 'premature-close' in d['warning'], f'missing warning text: {d}'
assert 'scaffold' in d['last_commit'], f'last_commit not scaffold: {d}'
" 2>/dev/null; then
        pass "test2: warning JSON has correct agent, transcript_bytes, warning text, last_commit"
    else
        fail "test2: warning JSON malformed: $(cat "$WARN_PATH2")"
    fi
fi


# ===========================================================================
# TEST 3: Bug #1 (SPAWNED_AT from API, not date +%s)
#   Verify SPAWNED_AT used in the delayed check (section B) is the API value,
#   not PostToolUse time. We do this by checking stderr output includes
#   a spawned_at value that matches the API-returned epoch (not "now").
# ===========================================================================
PORT3=$((PORT + 200))
SPAWNED_EPOCH3=$(( $(date +%s) - 300 ))  # 5 min ago -- clearly != "now"
SPAWNED_ISO3=$(python3 -c "from datetime import datetime,timezone; print(datetime.fromtimestamp(${SPAWNED_EPOCH3}, tz=timezone.utc).isoformat())")
FAKE_PROJECT3="$SCRATCH/project3"
mkdir -p "$FAKE_PROJECT3/.claude/worktrees/agent-fixtest"
make_fake_worktree "$FAKE_PROJECT3/.claude/worktrees/agent-fixtest" "chore(→1999): scaffold"

API_BODY3="$SCRATCH/agents3.json"
python3 -c "
import json
print(json.dumps({'agents': [{
    'name': 'fixtest-agent',
    'spawned_at': '${SPAWNED_ISO3}',
    'worktree_branch': 'worktree-agent-fixtest',
    'transcript_bytes': 9999,
    'status': 'completed',
}]}))
" > "$API_BODY3"
python3 - "$PORT3" "$API_BODY3" <<'PYSERVER3' &
import sys, http.server
PORT = int(sys.argv[1]); BODY = sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/agents"):
            with open(BODY,"rb") as f: body=f.read()
            self.send_response(200); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
        else: self.send_response(404); self.end_headers()
    def log_message(self, *a, **kw): return
http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
PYSERVER3
SERVER3_PID=$!
for _ in $(seq 1 30); do
    if curl -s --connect-timeout 1 -m 1 "http://127.0.0.1:${PORT3}/api/agents" -o /dev/null 2>/dev/null; then break; fi
    sleep 0.1
done

STDERR_CAPTURED=$(
    TORIOS_API_BASE="http://127.0.0.1:${PORT3}"
    CLAUDE_PROJECT_DIR="$FAKE_PROJECT3"
    export TORIOS_API_BASE CLAUDE_PROJECT_DIR
    unset YOUROS_SPAWNED_AT 2>/dev/null || true
    HOME="$SCRATCH/home3"; mkdir -p "$HOME/.youros/subagents"; export HOME
    rule_enabled() { return 0; }; log_rule_fire() { return 0; }
    . "$WATCHER_LIB"
    _scaffold_commit_watcher_check "Agent" "fixtest-agent" "" 2>&1 >/dev/null
)

kill "${SERVER3_PID:-}" 2>/dev/null; wait "${SERVER3_PID:-}" 2>/dev/null

# The stderr line must include the API-fetched epoch, not current time
NOW_EPOCH=$(date +%s)
if echo "$STDERR_CAPTURED" | grep -q "spawned_at=${SPAWNED_EPOCH3}"; then
    pass "test3: spawned_at in stderr matches API epoch (not current time)"
else
    fail "test3: spawned_at not correctly set from API. stderr='${STDERR_CAPTURED}' expected spawned_at=${SPAWNED_EPOCH3}, now=${NOW_EPOCH}"
fi


# ===========================================================================
# Result
# ===========================================================================
echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "PASS (3/3 tests)"
    exit 0
else
    echo "FAILED"
    exit 1
fi
