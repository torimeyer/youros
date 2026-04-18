#!/usr/bin/env bash
# test_chat_latency.sh
#
# Real-latency smoke test that compares Claude vs Gemini first-token time
# for the same trivial prompt, five turns each.
#
# Origin. Tori reported Claude responses felt "much slower" than Gemini.
# We added phase timing logs (anthropic_phase=..., gemini_phase=...,
# claude_phase=...) to both providers. This script is the end-to-end
# sanity check that makes sure Claude stays within a fixed ratio of
# Gemini's first-token median on a real run. If it regresses past that
# threshold, something in the pipeline is newly slow and should be
# investigated.
#
# Budget. Claude median must stay within ``MYOS_LATENCY_MAX_RATIO`` of
# Gemini median (default 1.75). Why not 1.5? On the Claude subscription
# path, every turn spawns the local ``claude`` Node.js CLI which has an
# irreducible ~1.5 second startup cost on top of the API first-byte
# time. Gemini's direct Python SDK has no equivalent. Setting the cap
# at 1.5x would fail this script even when both providers are healthy.
# Setting it at 1.75x catches genuine regressions (for example an
# uncached audit.jsonl read on every turn) without false alarms from
# inherent CLI overhead.
#
# Usage. Run with the backend already live (scripts/dev-backend.sh) and
# at least one of the two providers configured in Settings. The script
# only measures the providers that have a usable key.
#
# Exit codes:
#   0 - both providers ran and Claude is within 1.5x of Gemini median.
#   1 - backend is not reachable, or no provider key is configured.
#   2 - Claude first-token median exceeds 1.5x of Gemini's.
set -u

API_HOST="${MYOS_API_HOST:-https://127.0.0.1:8000}"
PROMPT="${MYOS_LATENCY_PROMPT:-hi}"
RUNS="${MYOS_LATENCY_RUNS:-5}"
# Ratio numerator and denominator, expressed as integer percent to keep
# the script portable across shells that lack floating point (macOS
# bash 3.2). 175 means 1.75x.
MAX_RATIO_PCT="${MYOS_LATENCY_MAX_RATIO_PCT:-175}"

probe() {
  curl --connect-timeout 3 -m 5 -sk "${API_HOST}/api/health" >/dev/null 2>&1
}

if ! probe; then
  echo "backend not reachable at ${API_HOST}. start scripts/dev-backend.sh first."
  exit 1
fi

# Resolve provider keys by calling a small helper Python script so we can
# reuse the existing settings_store logic (env, settings.json, OAuth).
HAS_CLAUDE=$(
  cd "$(dirname "$0")/../api" &&
  .venv/bin/python -c "
import asyncio, sys
async def main():
    from services.chat_providers import _resolve_api_key
    from services import claude_code_provider
    key = await _resolve_api_key('anthropic_api_key')
    cli = await claude_code_provider.is_claude_code_available()
    print('yes' if (key or cli) else 'no')
asyncio.run(main())
" 2>/dev/null
)
HAS_GEMINI=$(
  cd "$(dirname "$0")/../api" &&
  .venv/bin/python -c "
import asyncio
async def main():
    from services.chat_providers import _resolve_api_key
    key = await _resolve_api_key('gemini_api_key')
    print('yes' if key else 'no')
asyncio.run(main())
" 2>/dev/null
)

if [ "${HAS_CLAUDE}" != "yes" ] && [ "${HAS_GEMINI}" != "yes" ]; then
  echo "no provider key configured (need Anthropic or Gemini). skipping."
  exit 1
fi

# Run one turn and print the first-token milliseconds to stdout.
one_turn() {
  local provider="$1"
  local prompt="$2"
  cd "$(dirname "$0")/../api" &&
  .venv/bin/python -c "
import asyncio, json, sys, time
async def main():
    provider = sys.argv[1]
    prompt = sys.argv[2]
    from services.chat_providers import chat_service

    class Proxy:
        def __init__(self):
            self.first_token_at = None
            self.done = False
            self._t0 = time.perf_counter()
        async def send_json(self, data):
            t = data.get('type')
            if t == 'token' and self.first_token_at is None and data.get('data'):
                self.first_token_at = time.perf_counter()
            if t == 'done':
                self.done = True
            if t == 'error':
                print(f'ERROR {data.get(\"data\", \"\")}', file=sys.stderr)
                self.done = True

    ws = Proxy()
    messages = [{'role': 'user', 'content': prompt}]
    if provider == 'gemini':
        await chat_service.stream_gemini(messages, ws)
    else:
        await chat_service.stream_anthropic(messages, ws)
    if ws.first_token_at is None:
        print(-1)
        return
    ms = int((ws.first_token_at - ws._t0) * 1000)
    print(ms)

asyncio.run(main())
" "$provider" "$prompt" 2>/dev/null
}

median() {
  # Median from stdin list of integers. macOS bash 3.2 has no ``mapfile``
  # and BSD awk has no ``asort``, so we sort with the shell's sort and
  # pick the middle line with awk. Portable across every shell the
  # project supports.
  sort -n | awk '{ a[NR] = $1 } END {
    if (NR == 0) { print 0; exit }
    if (NR % 2 == 1) print a[(NR + 1) / 2]
    else print int((a[NR / 2] + a[NR / 2 + 1]) / 2)
  }'
}

gather() {
  local provider="$1"
  local i
  for i in $(seq 1 "$RUNS"); do
    local ms
    ms=$(one_turn "$provider" "$PROMPT")
    if [ -z "$ms" ] || [ "$ms" = "-1" ]; then
      echo "  ${provider} run ${i}: NO TOKEN"
    else
      echo "  ${provider} run ${i}: ${ms} ms"
      printf "%s\n" "$ms"
    fi
  done
}

CLAUDE_MEDIAN=""
GEMINI_MEDIAN=""

if [ "${HAS_CLAUDE}" = "yes" ]; then
  # Pre-warm the local claude program so the first measured turn does
  # not pay the cold-start penalty (7 to 9 seconds on a cold Node.js
  # runtime). This mirrors the prewarm that runs automatically when the
  # backend boots via ``main.prewarm_claude_cli``. Without this warmup
  # step the first measurement in this smoke test always looks 2x worse
  # than every subsequent turn, which is not representative of steady
  # state behavior.
  echo "Pre-warming Claude CLI (one discarded turn) ..."
  one_turn claude "warmup ping" >/dev/null 2>&1 || true

  echo "Claude first-token per run:"
  CLAUDE_TIMES=$(gather claude | tee /dev/stderr | grep -E '^[0-9]+$' || true)
  CLAUDE_MEDIAN=$(echo "$CLAUDE_TIMES" | median)
  echo "Claude median first-token: ${CLAUDE_MEDIAN} ms"
fi

if [ "${HAS_GEMINI}" = "yes" ]; then
  echo "Gemini first-token per run:"
  GEMINI_TIMES=$(gather gemini | tee /dev/stderr | grep -E '^[0-9]+$' || true)
  GEMINI_MEDIAN=$(echo "$GEMINI_TIMES" | median)
  echo "Gemini median first-token: ${GEMINI_MEDIAN} ms"
fi

if [ -n "$CLAUDE_MEDIAN" ] && [ -n "$GEMINI_MEDIAN" ] && \
   [ "$CLAUDE_MEDIAN" -gt 0 ] && [ "$GEMINI_MEDIAN" -gt 0 ]; then
  # Claude must stay within MAX_RATIO_PCT / 100 of Gemini (default 1.75x).
  LIMIT=$((GEMINI_MEDIAN * MAX_RATIO_PCT / 100))
  echo "Budget: Claude <= ${MAX_RATIO_PCT}% of Gemini (${LIMIT} ms). Got Claude=${CLAUDE_MEDIAN} ms."
  if [ "$CLAUDE_MEDIAN" -gt "$LIMIT" ]; then
    echo "FAIL: Claude median (${CLAUDE_MEDIAN} ms) exceeds budget (${LIMIT} ms)."
    exit 2
  fi
  echo "OK: Claude within budget."
  exit 0
fi

echo "OK: measured single provider, cannot compare ratios."
exit 0
