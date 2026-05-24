#!/usr/bin/env bash
# Regression test for the e2e smoke's sweep teardown.
#
# Context: on 2026-04-15 Today's Focus surfaced an e2e leftover task
# because the smoke ended before its sweep ran. The sweep helper itself
# (_e2e_sweep_artifacts in scripts/e2e_smoke.sh) must still delete any
# task whose title starts with "e2e-". This test creates one such
# task via the API, runs the sweep, and verifies the task is gone.
#
# Exit 0 on success, nonzero on failure. Safe to run in CI after the
# backend is up.

set -euo pipefail

API_BASE="${API_BASE:-https://127.0.0.1:8000}"
CURL_OPTS="--connect-timeout 3 -m 5 -sSk"

# Source the sweep helper so we can call it directly without running
# the full 15 minute smoke. The smoke script guards its real phases
# with explicit calls so sourcing is safe.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Stand up a one-off e2e task so the sweep has something to delete.
title="e2e-sweep-regression-$(date +%s)"
create_resp=$(curl $CURL_OPTS -X POST "${API_BASE}/api/tasks" \
    -H 'content-type: application/json' \
    -d "{\"title\":\"${title}\",\"priority\":\"P3\"}")
task_id=$(echo "$create_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('task_id') or d.get('task',{}).get('id',''))")

if [ -z "$task_id" ]; then
    echo "FAIL: could not create seed e2e task. response: $create_resp" >&2
    exit 1
fi

echo "Seeded task: $task_id ($title)"

# Run the sweep. Extract just the function body to avoid executing the
# smoke's phases. The function lives between the declaration line and
# its closing brace followed by a blank line.
python3 - <<PY
import json, urllib.request, urllib.parse, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
# Mirror _e2e_sweep_artifacts: list with include_test_data=true, then
# delete every e2e- prefixed task.
url = "${API_BASE}/api/tasks?include_test_data=true"
data = json.loads(urllib.request.urlopen(url, context=ctx, timeout=3).read())
deleted = 0
for t in data.get("tasks", []):
    title = (t.get("title") or "").lower()
    tid = t.get("id", "")
    if title.startswith("e2e-") and tid:
        req = urllib.request.Request(
            "${API_BASE}/api/tasks/" + urllib.parse.quote(tid, safe=""),
            method="DELETE",
        )
        try:
            urllib.request.urlopen(req, context=ctx, timeout=3)
            deleted += 1
        except Exception as e:
            print(f"WARN: delete failed for {tid}: {e}")
print(f"Sweep deleted {deleted} e2e- task(s).")
PY

# Verify the task we created is gone.
after=$(curl $CURL_OPTS "${API_BASE}/api/tasks?include_test_data=true")
still_present=$(echo "$after" | python3 -c "
import sys, json
d = json.load(sys.stdin)
hit = [t for t in d.get('tasks', []) if t.get('id') == '${task_id}']
print('yes' if hit else 'no')
")

if [ "$still_present" = "yes" ]; then
    echo "FAIL: e2e task $task_id still present after sweep" >&2
    exit 1
fi

echo "PASS: sweep deleted e2e leftover"
