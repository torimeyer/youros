#!/usr/bin/env bash
# myos-handoff.sh — wrap ostk handoff with markdown fallback (→1312)
#
# Routes handoff dispatch based on MYOS_HANDOFF_USE_OSTK flag.
# When set:  delegates to `ostk handoff <target> --transition "<msg>"`
# When unset: writes markdown to ~/.claude/handoffs/YYYY-MM-DD-<slug>.md
#
# Usage:
#   scripts/myos-handoff.sh <target> [--topic <slug>] [--transition "<msg>"] [--content-file <path>]
#
# With no --content-file, content is read from stdin.
#
# Environment:
#   MYOS_HANDOFF_USE_OSTK — if non-empty, use ostk handoff (kernel-aware path)
#   HANDOFFS_DIR          — override output dir (default: ~/.claude/handoffs)
#   OSTK_CMD              — override ostk binary (for tests)

set -euo pipefail

HANDOFFS_DIR="${HANDOFFS_DIR:-$HOME/.claude/handoffs}"
OSTK_CMD="${OSTK_CMD:-ostk}"

usage() {
    echo "Usage: $0 <target> [--topic <slug>] [--transition <msg>] [--content-file <path>]" >&2
    exit 1
}

[[ $# -ge 1 ]] || usage
target="$1"
shift

topic=""
transition=""
content_file=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --topic)        topic="$2";        shift 2 ;;
        --transition)   transition="$2";   shift 2 ;;
        --content-file) content_file="$2"; shift 2 ;;
        *) echo "error: unknown option: $1" >&2; usage ;;
    esac
done

# ── ostk path ─────────────────────────────────────────────────────────────────

if [[ -n "${MYOS_HANDOFF_USE_OSTK:-}" ]]; then
    args=("$target")
    [[ -n "$transition" ]] && args+=(--transition "$transition")
    exec "$OSTK_CMD" handoff "${args[@]}"
fi

# ── markdown fallback ─────────────────────────────────────────────────────────

mkdir -p "$HANDOFFS_DIR"

date_prefix="$(date +%Y-%m-%d)"
slug="${topic:-handoff}"
out_file="$HANDOFFS_DIR/${date_prefix}-${slug}.md"

write_content() {
    if [[ -n "$content_file" ]]; then
        cat "$content_file"
    else
        cat
    fi
}

if [[ -f "$out_file" ]]; then
    { echo ""; echo "---"; write_content; } >> "$out_file"
else
    write_content > "$out_file"
fi

echo "→ handoff written: $out_file"
