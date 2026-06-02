#!/bin/bash
# lib/deny.sh — shared deny helper for PreToolUse hooks.
# Source this file near the top of each hook; do NOT execute it directly.
# After sourcing, call init_deny_traps to install EXIT/ERR crash guards.

__deny_called=0
__crash_logged=0
_TRAP_EXIT=0
_TRAP_LINE=0
_TRAP_CMD=""

# deny <reason>
# Prints Blocked + Hook to stderr, appends JSON to hook-denies.log, exits 2.
deny() {
    local reason="${1:-}"
    if [ -z "$reason" ]; then
        printf 'assert: deny called with empty reason from %s\n' "$0" >&2
        exit 2
    fi
    __deny_called=1
    printf 'Blocked: %s\nHook: %s\n' "$reason" "$(basename "$0")" >&2
    mkdir -p "${HOME}/.claude/logs"
    local ts hook tool safe_reason
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    hook="$(basename "$0")"
    tool="${CLAUDE_TOOL_NAME:-}"
    safe_reason="${reason//\"/\\\"}"
    printf '{"ts":"%s","hook":"%s","tool":"%s","reason":"%s"}\n' \
        "$ts" "$hook" "$tool" "$safe_reason" \
        >> "${HOME}/.claude/logs/hook-denies.log"
    exit 2
}

# advise <reason>
# Prints Advice + Hook to stderr, appends JSON to rule-fires.jsonl, returns 0.
# Does NOT block the tool call. Use for guidance-only rules (torios informs, never blocks).
advise() {
    local reason="${1:-}"
    if [ -z "$reason" ]; then
        printf 'assert: advise called with empty reason from %s\n' "$0" >&2
        return 0
    fi
    printf 'Advice: %s\nHook: %s\n' "$reason" "$(basename "$0")" >&2
    mkdir -p "${HOME}/.claude/logs"
    local ts hook tool safe_reason
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    hook="$(basename "$0")"
    tool="${CLAUDE_TOOL_NAME:-}"
    safe_reason="${reason//\"/\\\"}"
    printf '{"ts":"%s","hook":"%s","tool":"%s","reason":"%s","decision":"advise"}\n' \
        "$ts" "$hook" "$tool" "$safe_reason" \
        >> "${HOME}/.claude/logs/rule-fires.jsonl"
    return 0
}

# log_unexpected_exit — called by the EXIT/ERR traps installed by init_deny_traps.
# Logs a crash entry when the hook exits nonzero without going through deny().
log_unexpected_exit() {
    local exit_code="${_TRAP_EXIT:-0}"
    [ "$exit_code" -eq 0 ] && return
    [ "$__deny_called" -eq 1 ] && return
    [ "$__crash_logged" -eq 1 ] && return
    __crash_logged=1
    mkdir -p "${HOME}/.claude/logs"
    local ts hook safe_cmd
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    hook="$(basename "$0")"
    safe_cmd="${_TRAP_CMD//\"/\\\"}"
    printf '{"ts":"%s","hook":"%s","mode":"crash","line":%d,"exit":%d,"last_cmd":"%s"}\n' \
        "$ts" "$hook" "${_TRAP_LINE:-0}" "$exit_code" "$safe_cmd" \
        >> "${HOME}/.claude/logs/hook-denies.log"
}

# init_deny_traps — call once after sourcing to install EXIT and ERR guards.
init_deny_traps() {
    trap '_TRAP_EXIT=$?; _TRAP_LINE=$LINENO; _TRAP_CMD=$BASH_COMMAND; log_unexpected_exit' EXIT
    trap '_TRAP_EXIT=$?; _TRAP_LINE=$LINENO; _TRAP_CMD=$BASH_COMMAND; log_unexpected_exit' ERR
}

export -f deny advise log_unexpected_exit init_deny_traps
