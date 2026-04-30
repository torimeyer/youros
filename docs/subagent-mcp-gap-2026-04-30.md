# Subagent partial MCP toolkit — diagnosis and fix (2026-04-30)

## Symptom

Subagents spawned via `POST /api/agents/spawn` with `isolation: "worktree"` reported
that only a subset of ostk MCP tools were available: `context/search/nudge` present,
but `fs_read`, `fs_write`, `shell`, `bash`, and `fs_ops` absent from the deferred list
entirely. With no way to write files and the `ostk-first.sh` hook blocking native
Write/Edit/Bash fallbacks, agents exited with 0-byte transcripts or stub messages.

## Root cause

### Step 1: ostk kernel serve spawns a new daemon when no socket exists

`ostk kernel serve` looks for a running daemon socket at `.ostk/ostk.sock` in the
current working directory. If it finds the socket, it bridges to the existing daemon
and exposes the full tool set (all ~15 tools). If not, it tries to spawn a fresh daemon.

When a git worktree is created (via `spawn_isolation.create_worktree`), the spawn code
creates an empty `.ostk/` directory inside it to "anchor" the ostk root:

```python
# api/routers/agents.py line ~3863
(_wt_path / ".ostk").mkdir(parents=True, exist_ok=True)
```

The socket file is NOT created there. So `ostk kernel serve`, starting with `cwd` =
the worktree, finds an empty `.ostk/` but no socket, and tries to spawn a new daemon:

```
[kernel] spawning detached daemon: /Users/torimeyer/.local/bin/ostk daemon
[kernel] daemon not responding after 2s — attempting reap
[kernel] spawning detached daemon: /Users/torimeyer/.local/bin/ostk daemon
```

The daemon never fully initializes (probably because it cannot share state with the
already-running main daemon), so the MCP server stays in a partial state and only
registers tools that do not require daemon communication.

### Step 2: hook finds the main socket and blocks native fallbacks

`ostk-first.sh` determines the project root via `CLAUDE_PROJECT_DIR`, which is
inherited from the parent Claude Code session and points to the main project root
(`/Users/torimeyer/claude/torios`). The main daemon's socket IS present there, so the
hook concludes ostk is "up" and blocks all native Bash/Read/Edit/Write calls.

With the MCP fs tools missing AND native tools blocked, the subagent can do nothing.

### Why intermittent

- Agents spawned with `isolation: "none"` (research verbs, no code-edit signal): run
  in the main project CWD, find the real socket, bridge to the daemon, get full tools.
- Agents spawned with `isolation: "worktree"` (edit/fix/build verbs): worktree created,
  empty `.ostk/` anchors the root, no socket bridge, partial tools only.

This explains why wave-1 (backend research), wave-2a, and wave-3a-retry succeeded
(either `isolation: "none"` or running against a main-tree checkout), while wave-3a,
wave-3b initial, and wave-A failed (edit tasks, all got `isolation: "worktree"`).

## Fix

### Fix A — spawn-time socket symlink (landed, `api/routers/agents.py`)

After creating the empty `.ostk/` directory, create a symlink pointing to the main
daemon's socket. `ostk kernel serve` finds the symlink, bridges to the running daemon,
and the full tool set is available:

```python
_main_sock = PROJECT_ROOT / ".ostk" / "ostk.sock"
_wt_sock = _wt_path / ".ostk" / "ostk.sock"
if _main_sock.exists() and not _wt_sock.exists():
    try:
        os.symlink(str(_main_sock), str(_wt_sock))
        logger.info("spawn.ostk_sock_symlink.created name=%s target=%s", ...)
    except Exception as _sym_exc:
        logger.warning("spawn.ostk_sock_symlink.failed name=%s err=%s", ...)
```

The symlink preserves the anchoring behavior (`.ostk/` still exists in the worktree so
the kernel roots bash calls there) while letting it bridge to the live daemon.

### Fix B — hook escape hatch (pending user approval, `.claude/hooks/ostk-first.sh`)

A secondary guard for the window before the symlink exists (e.g., the main daemon was
restarted and the symlink target disappeared):

```bash
_GIT_LOCAL_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -n "$_GIT_LOCAL_ROOT" ] && [ "$_GIT_LOCAL_ROOT" != "$PROJ_DIR" ]; then
  _LOCAL_SOCK="${_GIT_LOCAL_ROOT}/.ostk/ostk.sock"
  if [ ! -S "$_LOCAL_SOCK" ]; then
    trace "worktree-no-local-sock-allowed" "$TOOL"
    echo "ostk socket absent in worktree root $_GIT_LOCAL_ROOT, native fallback allowed" >&2
    exit 0
  fi
fi
```

Note: the `.claude/hooks/ostk-first.sh` file requires explicit user permission to edit
(blocked as sensitive). The spawn-time fix (Fix A) is sufficient for all new spawns.
Fix B was written and awaits a settings permission grant before it can be applied.

## Files changed

- `api/routers/agents.py`: Added socket symlink creation after `.ostk/` mkdir (~line 3863)
- `docs/subagent-mcp-gap-2026-04-30.md`: This file
- `.claude/hooks/ostk-first.sh`: Escape hatch written but BLOCKED on permissions

## Verification

```
git diff --stat  # shows api/routers/agents.py modified
bash -n .claude/hooks/ostk-first.sh && echo SYNTAX-OK  # no change yet
python3 -m pytest api/tests/ -x -q  # quality gate
```
