#!/usr/bin/env bash
# api-probe.sh: scheme-aware helper to talk to the yourOS backend on :8000.
#
# WHY THIS EXISTS (→457 pattern, recurring):
#   dev-backend.sh serves HTTPS whenever ~/.youros/localhost.{key,crt} exist.
#   A plain-HTTP curl to https-uvicorn gets exit 52 "Empty reply from
#   server" because the TLS layer sees a non-TLS ClientHello and closes
#   the socket without writing an HTTP response. The symptom looks
#   identical to a middleware crash. It is not. It is a protocol
#   mismatch.
#
#   Every few weeks a diagnose misreads this as "backend middleware is
#   broken" and burns cycles chasing a ghost. This helper is the
#   canonical way to probe :8000 from a shell script or a hook so the
#   scheme is always right. Use it instead of hand-rolling curl.
#
# USAGE:
#   # 1. Print env-export form (source-friendly)
#   eval "$(scripts/api-probe.sh --env)"
#   curl $CURL_OPTS "$API_BASE/api/health"
#
#   # 2. One-shot: run curl with the right scheme + flags
#   scripts/api-probe.sh /api/health
#   scripts/api-probe.sh -X POST /api/agents/register -d '{...}'
#
#   # 3. Just print the base URL
#   scripts/api-probe.sh --base
#   # → https://127.0.0.1:8000  (or http://127.0.0.1:8000)
#
# ENV OVERRIDES:
#   API_PORT   default 8000
#   API_BASE   override the computed base entirely
#   CURL_OPTS  override the computed curl flags

set -u

API_PORT="${API_PORT:-8000}"

SSL_KEY="$HOME/.youros/localhost.key"
SSL_CERT="$HOME/.youros/localhost.crt"
if [ -f "$SSL_KEY" ] && [ -f "$SSL_CERT" ]; then
    _default_base="https://127.0.0.1:${API_PORT}"
    _default_opts="-sSk --connect-timeout 3 -m 5"
else
    _default_base="http://127.0.0.1:${API_PORT}"
    _default_opts="-sS --connect-timeout 3 -m 5"
fi

API_BASE="${API_BASE:-$_default_base}"
CURL_OPTS="${CURL_OPTS:-$_default_opts}"

case "${1:-}" in
    --env)
        # Source-friendly: eval "$(scripts/api-probe.sh --env)"
        printf 'export API_BASE=%q\n' "$API_BASE"
        printf 'export CURL_OPTS=%q\n' "$CURL_OPTS"
        exit 0
        ;;
    --base)
        printf '%s\n' "$API_BASE"
        exit 0
        ;;
    -h|--help)
        sed -n '2,30p' "$0"
        exit 0
        ;;
    "")
        # No arg: print a short status line so this is useful by itself.
        printf 'API_BASE=%s\nCURL_OPTS=%s\n' "$API_BASE" "$CURL_OPTS"
        exit 0
        ;;
esac

# Otherwise: treat all args as a curl invocation. The LAST arg (or any
# arg starting with /) is treated as a path and prefixed with API_BASE.
# Everything else is passed through to curl. This is intentionally
# simple; complex calls should eval --env and curl directly.
args=()
for a in "$@"; do
    case "$a" in
        /*)
            args+=("${API_BASE}${a}")
            ;;
        *)
            args+=("$a")
            ;;
    esac
done

# shellcheck disable=SC2086  # $CURL_OPTS must word-split
exec curl $CURL_OPTS "${args[@]}"
