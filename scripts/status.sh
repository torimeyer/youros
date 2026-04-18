#!/usr/bin/env bash
# myos_status: list the running user-spawned agents the way the Agents page shows them.
#
# Why: ad-hoc `curl /api/agents | python3 ...` loops over-count because they
# never apply the same filter as the Agents page (chat sessions, audit rows,
# hook rows, and subscription rows are excluded). This helper hits the
# canonical server-side filter so the CLI count always matches the UI.
#
# Output columns (tab-separated):
#     <status>\t<lines>\t<elapsed>\t<lines_or_elapsed>\t<name>
#
# Why elapsed: during the ~30s Claude CLI cold start the transcript has 0
# lines even though the agent process is alive. Without a wall-clock field
# the orchestrator reads "0 lines" and reports "just started" even after
# the agent has been running for a minute. The elapsed column makes cold
# start visible (e.g. "(elapsed:48s cold-start)") so the report matches
# reality.
#
# Usage:
#     scripts/status.sh                 # one line per running user-spawned agent
#     scripts/status.sh --count         # just the count
#     API_HOST=... scripts/status.sh    # override host (default https://127.0.0.1:8000)
#     MYOS_STATUS_FIXTURE=path.json     # read agents from a local JSON fixture (for tests)
#
# Exits 0 even when the list is empty. Errors on network / parse failure.

set -u

API_HOST="${API_HOST:-https://127.0.0.1:8000}"
URL="${API_HOST}/api/agents?user_spawned_only=true"

count_only=0
if [[ "${1:-}" == "--count" ]]; then
    count_only=1
fi

if [[ -n "${MYOS_STATUS_FIXTURE:-}" ]]; then
    if [[ ! -f "${MYOS_STATUS_FIXTURE}" ]]; then
        echo "status: fixture not found at ${MYOS_STATUS_FIXTURE}" >&2
        exit 1
    fi
    body="$(cat "${MYOS_STATUS_FIXTURE}")"
else
    # Bumped from --connect-timeout 3 -m 5 to 5/10 with one silent retry.
    # The 3/5 budget was tripping false "backend unreachable" reports during
    # cold-cache responses (first /api/agents call after a uvicorn reload
    # can take 6 to 8 seconds while routes lazily import). With a single
    # retry on timeout, transient slowness no longer pages Tori.
    fetch_status() {
        curl -sk --connect-timeout 5 -m 10 "$URL"
    }
    body="$(fetch_status)" || body=""
    if [[ -z "$body" ]]; then
        body="$(fetch_status)" || body=""
    fi
    if [[ -z "$body" ]]; then
        echo "status: could not reach $URL after 2 tries (is the backend running? try: scripts/health.sh --fix)" >&2
        exit 1
    fi
fi

if [[ -z "$body" ]]; then
    echo "status: empty response from $URL" >&2
    exit 1
fi

python3 - "$body" "$count_only" <<'PY'
import json
import sys
from datetime import datetime, timezone

body = sys.argv[1]
count_only = sys.argv[2] == "1"

try:
    data = json.loads(body)
except json.JSONDecodeError as exc:
    print(f"status: could not parse JSON ({exc})", file=sys.stderr)
    sys.exit(1)

agents = data.get("agents", []) or []
running = [a for a in agents if a.get("status") == "running"]

if count_only:
    print(len(running))
    sys.exit(0)


def parse_iso(value):
    """Parse an ISO8601 timestamp into an aware datetime, or return None."""
    if not value or not isinstance(value, str):
        return None
    # Python 3.9 fromisoformat does not accept trailing "Z"; normalize.
    cleaned = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_elapsed(seconds):
    if seconds is None:
        return "?"
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


# Allow tests to pin "now" for deterministic elapsed math.
# Format: ISO8601, same shape as the API timestamps.
import os
now_override = os.environ.get("MYOS_STATUS_NOW")
if now_override:
    parsed = parse_iso(now_override)
    now = parsed if parsed is not None else datetime.now(timezone.utc)
else:
    now = datetime.now(timezone.utc)

for a in running:
    name = a.get("name", "?")
    lines = a.get("transcript_lines", 0) or 0
    status = a.get("status", "?")

    # Prefer spawned_at. Fall back to last_heartbeat_at, then registered_at.
    # The API does not currently expose started_at; if it appears later we
    # pick it up automatically.
    start_ts = (
        parse_iso(a.get("started_at"))
        or parse_iso(a.get("spawned_at"))
        or parse_iso(a.get("last_heartbeat_at"))
        or parse_iso(a.get("registered_at"))
    )
    if start_ts is None:
        elapsed_seconds = None
        elapsed = "?"
    else:
        elapsed_seconds = (now - start_ts).total_seconds()
        elapsed = format_elapsed(elapsed_seconds)

    # Smart field: if lines > 0 show the line count; if lines == 0 but
    # elapsed > 15s call it out as a cold start so the orchestrator cannot
    # confuse a warming agent with a brand-new one.
    if lines > 0:
        smart = f"lines:{lines}"
    elif elapsed_seconds is not None and elapsed_seconds > 15:
        smart = f"(elapsed:{elapsed} cold-start)"
    else:
        smart = f"elapsed:{elapsed}"

    print(f"{status}\t{lines}\t{elapsed}\t{smart}\t{name}")
PY
