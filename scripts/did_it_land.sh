#!/usr/bin/env bash
# did_it_land.sh
#
# One-shot probe that answers: "did this stopped or completed agent's work land?"
#
# Usage:   scripts/did_it_land.sh <agent_name>
# Exit:    0 = LANDED (changes exist + tests green)
#          1 = NOT LANDED (no changes, or changes but tests red)
#          2 = NO-TEST-COVERAGE (changes exist but no runnable tests found)
#
# Plain language: when an agent stops, this script tells you in one glance
# whether the work actually reached disk and whether the tests still pass.

set -u

AGENT_NAME="${1:-}"
if [[ -z "${AGENT_NAME}" ]]; then
  echo "usage: $0 <agent_name>" >&2
  exit 3
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${MYOS_DID_IT_LAND_REPO:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
API_BASE="https://127.0.0.1:8000"
# Bumped from --connect-timeout 3 -m 5 to 5/10. The original budget was
# tripping false "backend unreachable" reports during cold-cache responses
# (first /api/agents call after a reload can take 6 to 8 seconds while
# routers lazily import). did_it_land is invoked when an agent stops, so
# we cannot afford a false negative right when Tori needs the answer.
CURL="curl --connect-timeout 5 -m 10 -sSk"

# --- Step 1: fetch agent metadata from the API (one silent retry on miss) ---
AGENTS_JSON="$({ ${CURL} "${API_BASE}/api/agents" 2>/tmp/did-it-land-curl.err; } || printf '')"
if [[ -z "${AGENTS_JSON}" ]]; then
    AGENTS_JSON="$({ ${CURL} "${API_BASE}/api/agents" 2>/tmp/did-it-land-curl.err; } || printf '')"
fi
META="$(
  printf '%s' "${AGENTS_JSON}" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    print('{}'); sys.exit(0)
agents = d.get('agents', d) if isinstance(d, dict) else d
for a in agents:
    if a.get('name') == '${AGENT_NAME}':
        print(json.dumps(a))
        break
else:
    print('{}')
"
)"

STATUS="$({ printf '%s' "${META}" | python3 -c "import json,sys;print(json.load(sys.stdin).get('status','unknown'))" 2>/tmp/did-it-land-py.err; } || printf unknown)"
TRANSCRIPT_PATH="$({ printf '%s' "${META}" | python3 -c "import json,sys;print(json.load(sys.stdin).get('transcript_path') or '')" 2>>/tmp/did-it-land-py.err; } || printf '')"

# Wall-clock elapsed since the agent spawned. We prefer spawned_at, fall back
# to last_heartbeat_at, then registered_at. Printed later in plain language so
# "Agent has been running Xs" is visible even when the transcript shows 0 lines.
ELAPSED_HUMAN="$(printf '%s' "${META}" | python3 -c "
import json, sys
from datetime import datetime, timezone
try:
    a = json.load(sys.stdin)
except Exception:
    print(''); sys.exit(0)

def parse_iso(v):
    if not v or not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

start = (
    parse_iso(a.get('started_at'))
    or parse_iso(a.get('spawned_at'))
    or parse_iso(a.get('last_heartbeat_at'))
    or parse_iso(a.get('registered_at'))
)
if start is None:
    print('')
    sys.exit(0)
sec = int(max(0, (datetime.now(timezone.utc) - start).total_seconds()))
if sec < 60:
    print(f'{sec}s')
elif sec < 3600:
    m, s = divmod(sec, 60)
    print(f'{m}m{s:02d}s')
else:
    h, rem = divmod(sec, 3600)
    m, _ = divmod(rem, 60)
    print(f'{h}h{m:02d}m')
" 2>>/tmp/did-it-land-py.err || printf '')"

# --- Step 2: pull the tail of the agent's task output if present ---
SUMMARY_TAIL=""
if [[ -n "${TRANSCRIPT_PATH}" && -e "${TRANSCRIPT_PATH}" ]]; then
  # JSONL format: grab last 3 assistant messages' text content, cap at 40 lines.
  SUMMARY_TAIL="$(
    tail -20 "${TRANSCRIPT_PATH}" 2>/dev/null | python3 -c "
import sys, json
texts = []
for line in sys.stdin:
    try:
        d = json.loads(line)
    except Exception:
        continue
    msg = d.get('message') or {}
    content = msg.get('content')
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get('type') == 'text':
                texts.append(c.get('text', ''))
    elif isinstance(content, str):
        texts.append(content)
blob = '\n\n'.join(texts[-2:])
lines = blob.splitlines()[-40:]
print('\n'.join(lines))
"
  )"
fi

# --- Step 3: git diff scoped to the repo ---
cd "${REPO_ROOT}" 2>/dev/null || { echo "repo not found: ${REPO_ROOT}" >&2; exit 3; }

GIT_STATUS="$(git status --short 2>/dev/null || true)"
GIT_DIFFSTAT="$(git diff --stat HEAD 2>/dev/null || true)"
CHANGED_FILES_COUNT="$(printf '%s\n' "${GIT_STATUS}" | awk 'NF' | wc -l | tr -d ' ')"
# Added/removed totals from --shortstat
SHORTSTAT="$(git diff --shortstat HEAD 2>/dev/null || true)"
LINES_ADDED="$(printf '%s' "${SHORTSTAT}" | grep -oE '[0-9]+ insertion' | grep -oE '[0-9]+' || echo 0)"
LINES_REMOVED="$(printf '%s' "${SHORTSTAT}" | grep -oE '[0-9]+ deletion' | grep -oE '[0-9]+' || echo 0)"
[[ -z "${LINES_ADDED}" ]] && LINES_ADDED=0
[[ -z "${LINES_REMOVED}" ]] && LINES_REMOVED=0

# --- Step 4: pick tests to run ---
# Strategy: take files in the diff, find matching tests. Prefer tests under api/tests
# whose names match the stems of changed api files. Otherwise run the full api suite.

CHANGED_PY="$(git diff --name-only HEAD 2>/dev/null | grep -E '^api/.*\.py$' || true)"

TEST_FILES=()
if [[ -n "${CHANGED_PY}" ]]; then
  while IFS= read -r f; do
    [[ -z "${f}" ]] && continue
    # If the changed file is itself a test, include it (only if it still exists).
    if [[ "${f}" == api/tests/test_*.py ]]; then
      if [[ -f "${f}" ]]; then
        TEST_FILES+=("${f}")
      fi
      continue
    fi
    # Otherwise map api/foo/bar.py -> api/tests/test_bar.py if it exists.
    stem="$(basename "${f}" .py)"
    candidate="api/tests/test_${stem}.py"
    if [[ -f "${candidate}" ]]; then
      TEST_FILES+=("${candidate}")
    fi
  done <<< "${CHANGED_PY}"
fi

# Dedupe and rebuild array with only unique entries.
# macOS ships bash 3.2 so no associative arrays. We walk the array manually.
UNIQ_TESTS=""
UNIQ_TEST_FILES=()
if [[ ${#TEST_FILES[@]} -gt 0 ]]; then
  # Use a newline-separated string as the dedup memo, safe on bash 3.2.
  _seen=""
  for t in "${TEST_FILES[@]}"; do
    if ! printf '%s\n' "${_seen}" | grep -Fxq "${t}"; then
      UNIQ_TEST_FILES+=("${t}")
      _seen="${_seen}${t}"$'\n'
    fi
  done
  if [[ ${#UNIQ_TEST_FILES[@]} -gt 0 ]]; then
    UNIQ_TESTS="$(printf '%s\n' "${UNIQ_TEST_FILES[@]}")"
  fi
fi

TEST_OUTCOME="SKIP"
TEST_SUMMARY="no test run"
PYTEST_BIN="${REPO_ROOT}/api/.venv/bin/pytest"

if [[ "${CHANGED_FILES_COUNT}" -eq 0 ]]; then
  TEST_OUTCOME="SKIP"
  TEST_SUMMARY="no changes on disk, skipping tests"
elif [[ ! -x "${PYTEST_BIN}" ]]; then
  TEST_OUTCOME="NO-COVERAGE"
  TEST_SUMMARY="pytest binary missing at ${PYTEST_BIN}"
elif [[ ${#UNIQ_TEST_FILES[@]} -gt 0 ]]; then
  # Run just the targeted test files. Capture full output then search for the
  # final pytest status line (e.g. "12 passed", "3 failed, 9 passed").
  RAW_OUT="$(cd "${REPO_ROOT}" && "${PYTEST_BIN}" --tb=short -q "${UNIQ_TEST_FILES[@]}" 2>&1)"
  # The final summary line is like "====== 1286 passed, 27 warnings in 55s ======"
  # or "== 2 failed, 100 passed ==" or "!!! no tests ran !!!".
  STATUS_LINE="$(printf '%s' "${RAW_OUT}" | grep -E '^=+ .*(passed|failed|error|no tests ran).*=+$' | tail -1)"
  if [[ -z "${STATUS_LINE}" ]]; then
    # Fall back to any line with passed/failed.
    STATUS_LINE="$(printf '%s' "${RAW_OUT}" | grep -E '(passed|failed|error|no tests ran)' | tail -1)"
  fi
  if printf '%s' "${STATUS_LINE}" | grep -qE 'failed|error'; then
    TEST_OUTCOME="FAIL"
  elif printf '%s' "${STATUS_LINE}" | grep -qE '[0-9]+ passed'; then
    TEST_OUTCOME="PASS"
  elif printf '%s' "${STATUS_LINE}" | grep -qE 'no tests ran'; then
    TEST_OUTCOME="NO-COVERAGE"
  else
    TEST_OUTCOME="FAIL"
  fi
  TEST_SUMMARY="${STATUS_LINE:-no status line parsed}"
else
  # No targeted tests found for changed files. Do NOT run the full suite
  # (too slow for a probe). Flag as no-coverage.
  TEST_OUTCOME="NO-COVERAGE"
  TEST_SUMMARY="changes exist but no matching test files found"
fi

# --- Step 5: decide overall outcome ---
OVERALL="NOT LANDED"
EXIT_CODE=1
if [[ "${CHANGED_FILES_COUNT}" -eq 0 ]]; then
  OVERALL="NOT LANDED (no changes)"
  EXIT_CODE=1
elif [[ "${TEST_OUTCOME}" == "PASS" ]]; then
  OVERALL="LANDED"
  EXIT_CODE=0
elif [[ "${TEST_OUTCOME}" == "NO-COVERAGE" ]]; then
  OVERALL="NO-TEST-COVERAGE"
  EXIT_CODE=2
elif [[ "${TEST_OUTCOME}" == "SKIP" ]]; then
  OVERALL="NOT LANDED (no changes)"
  EXIT_CODE=1
else
  OVERALL="NOT LANDED (tests red)"
  EXIT_CODE=1
fi

# --- Step 6: print structured summary ---
echo "=== did_it_land: ${AGENT_NAME} ==="
echo "status: ${STATUS}"
if [[ -n "${ELAPSED_HUMAN}" ]]; then
  echo "Agent has been running ${ELAPSED_HUMAN}"
fi
echo "changes: ${CHANGED_FILES_COUNT} files, +${LINES_ADDED} / -${LINES_REMOVED}"
echo "test result: ${TEST_OUTCOME}"
echo "outcome: ${OVERALL}"
echo ""

if [[ -n "${SUMMARY_TAIL}" ]]; then
  echo "--- agent output tail (last 40 lines) ---"
  echo "${SUMMARY_TAIL}"
  echo ""
fi

if [[ "${CHANGED_FILES_COUNT}" -gt 0 ]]; then
  echo "--- git diff --stat HEAD ---"
  echo "${GIT_DIFFSTAT}" | tail -20
  echo ""
fi

if [[ "${TEST_OUTCOME}" != "SKIP" ]]; then
  echo "--- test summary ---"
  echo "${TEST_SUMMARY}"
fi

exit "${EXIT_CODE}"
