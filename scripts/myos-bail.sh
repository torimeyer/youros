#!/usr/bin/env bash
# myos-bail.sh — portable myOS setup export/import (→1313)
#
# Wraps `ostk bail pack/unpack/verify` and also bundles the extra directories
# that ostk does not manage natively: ~/.youros/, memory files, handoffs.
#
# Usage:
#   scripts/myos-bail.sh pack   <out.yourosbail>
#   scripts/myos-bail.sh unpack <in.yourosbail>
#   scripts/myos-bail.sh verify <in.yourosbail>
#
# Output format (.yourosbail = tar.gz containing):
#   ostk-state.bail    — signed ostk bail (needles, decisions, docs)
#   myos-extras.tar.gz — extra dirs archived relative to $HOME
#
# Overridable env vars (for tests):
#   MYOS_DIR     — defaults to $HOME/.youros
#   MEMORY_DIR   — defaults to $HOME/.claude/projects/-Users-$(whoami)-claude-torios/memory
#   HANDOFFS_DIR — defaults to $HOME/.claude/handoffs
#   DECISIONS_DIR — defaults to <repo>/.ostk/decisions
#   POLICY_DIR   — defaults to <repo>/.ostk/policy

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Resolve whoami-based default only when not overridden
_whoami="$(whoami)"
MYOS_DIR="${MYOS_DIR:-$HOME/.youros}"
MEMORY_DIR="${MEMORY_DIR:-$HOME/.claude/projects/-Users-${_whoami}-claude-torios/memory}"
HANDOFFS_DIR="${HANDOFFS_DIR:-$HOME/.claude/handoffs}"
DECISIONS_DIR="${DECISIONS_DIR:-$REPO_DIR/.ostk/decisions}"
POLICY_DIR="${POLICY_DIR:-$REPO_DIR/.ostk/policy}"

# Files excluded from ~/.youros/ — transient/reproducible or secrets
MYOS_EXCLUDES=(
    '*.jsonl'
    'github_token.json'
    'localhost.key'
    'localhost.crt'
    '.env'
    'exports'
    'files'
    'agent_workspace'
    'agent_workspace.json'
    'agent_memory'
    'agents'
    'calendar_cache'
    'drive_cache'
)

usage() {
    echo "Usage: $0 <pack|unpack|verify> <file.yourosbail>" >&2
    echo "" >&2
    echo "  pack   <out.yourosbail>  — export setup to a portable archive" >&2
    echo "  unpack <in.yourosbail>   — restore setup from archive (verify + apply)" >&2
    echo "  verify <in.yourosbail>   — check archive signature without applying" >&2
    exit 1
}

cmd="${1:-}"
target="${2:-}"
[[ "$cmd" =~ ^(pack|unpack|verify)$ ]] && [[ -n "$target" ]] || usage

# ── helpers ──────────────────────────────────────────────────────────────────

extract_bundle() {
    local bundle="$1" dest="$2"
    [[ -f "$bundle" ]] || { echo "error: file not found: $bundle" >&2; exit 1; }
    tar xzf "$bundle" -C "$dest"
    [[ -f "$dest/ostk-state.bail" ]] || { echo "error: not a myosbail archive (missing ostk-state.bail)" >&2; exit 1; }
}

# ── pack ─────────────────────────────────────────────────────────────────────

do_pack() {
    local out="$1"
    local tmpdir
    tmpdir="$(mktemp -d)"
    # shellcheck disable=SC2064 — intentional: capture value now, not at fire time
    trap "rm -rf '$tmpdir'" EXIT

    echo "→ packing ostk state (needles, decisions, docs)..."
    ostk bail pack --out "$tmpdir/ostk-state.bail"

    echo "→ bundling extras (~/.youros, memory, handoffs, decisions, policy)..."

    # Build exclude args for tar (macOS-compatible: --exclude patterns)
    local excl_args=()
    for pat in "${MYOS_EXCLUDES[@]}"; do
        excl_args+=(--exclude="$pat")
    done

    # Archive extras relative to HOME so they restore cleanly on unpack.
    # We cd to HOME and use relative paths so tar stores ".youros/config.json"
    # not "/Users/torimeyer/.youros/config.json".
    relpath() { python3 -c "import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))" "$1" "$HOME"; }

    local dirs_to_pack=()
    [[ -d "$MYOS_DIR" ]]     && dirs_to_pack+=("$(relpath "$MYOS_DIR")")
    [[ -d "$MEMORY_DIR" ]]   && dirs_to_pack+=("$(relpath "$MEMORY_DIR")")
    [[ -d "$HANDOFFS_DIR" ]] && dirs_to_pack+=("$(relpath "$HANDOFFS_DIR")")
    # decisions + policy are under REPO_DIR; archive relative to HOME too
    [[ -d "$DECISIONS_DIR" ]] && dirs_to_pack+=("$(relpath "$DECISIONS_DIR")")
    [[ -d "$POLICY_DIR" ]]    && dirs_to_pack+=("$(relpath "$POLICY_DIR")")

    if [[ ${#dirs_to_pack[@]} -gt 0 ]]; then
        (cd "$HOME" && tar czf "$tmpdir/myos-extras.tar.gz" \
            "${excl_args[@]}" \
            "${dirs_to_pack[@]}" 2>/dev/null) || true
    else
        # Create empty archive so bundle format stays consistent
        (cd "$HOME" && tar czf "$tmpdir/myos-extras.tar.gz" --files-from /dev/null 2>/dev/null) || \
            touch "$tmpdir/myos-extras.tar.gz"
    fi

    echo "→ sealing bundle..."
    tar czf "$out" -C "$tmpdir" ostk-state.bail myos-extras.tar.gz

    local size
    size="$(du -sh "$out" | cut -f1)"
    echo "✓ packed → $out  ($size)"
}

# ── unpack ────────────────────────────────────────────────────────────────────

do_unpack() {
    local in="$1"
    local tmpdir
    tmpdir="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '$tmpdir'" EXIT

    echo "→ extracting bundle..."
    extract_bundle "$in" "$tmpdir"

    echo "→ verifying signature..."
    ostk bail verify "$tmpdir/ostk-state.bail"

    echo "→ unpacking ostk state..."
    ostk bail unpack "$tmpdir/ostk-state.bail"

    if [[ -f "$tmpdir/myos-extras.tar.gz" ]]; then
        echo "→ restoring extras..."
        (cd "$HOME" && tar xzf "$tmpdir/myos-extras.tar.gz")
    fi

    echo "✓ unpacked"
}

# ── verify ────────────────────────────────────────────────────────────────────

do_verify() {
    local in="$1"
    local tmpdir
    tmpdir="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '$tmpdir'" EXIT

    echo "→ extracting bundle..."
    extract_bundle "$in" "$tmpdir"

    echo "→ verifying signature..."
    ostk bail verify "$tmpdir/ostk-state.bail"

    echo "✓ signature valid"
}

# ── dispatch ──────────────────────────────────────────────────────────────────

case "$cmd" in
    pack)   do_pack   "$target" ;;
    unpack) do_unpack "$target" ;;
    verify) do_verify "$target" ;;
esac
