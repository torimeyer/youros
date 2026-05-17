# Audit primitive

A myOS primitive (v1.0). Every state change in the system writes an audit row. The audit stream is the system of record.

## Purpose

Make every meaningful state change reversible and inspectable. File edits, agent lifecycle events, primitive calls, and notes all produce ordered, timestamped rows in a single audit stream. The stream is the "what happened, in what order" view across the whole system.

## Contract

Module: `external:ostk` · Version: v1.0 · Status: active.

Audit is provided by the ostk coordination kernel, not by myOS Python. Two surfaces:

```bash
# Append a free-text annotation
ostk note "shipped v3.18.1"

# Every file mutation through ostk fs-ops writes a gen_table row automatically
ostk fs-ops <path> --old "..." --new "..."
```

From Python (myOS-side helpers):

```python
from services.ostk import ostk
await ostk._run("note", "text to annotate")
# File mutations through fs_ops MCP tool write audit rows; nothing extra needed.
```

The stream itself lives in `.ostk/audit.log` (project-scoped) or a similar kernel-managed path.

## Events emitted

By definition: every audit-emitting call IS the event. The stream contains:

- File mutations (gen_table rows): path, old hash, new hash, actor, timestamp.
- `ostk note` annotations: free text, actor, timestamp.
- Kernel verb invocations: command, args, exit code, timestamp.

## Versioning history

- **v1.0** (2026-05-16): formalized as a myOS primitive. The underlying ostk audit stream has been present since the kernel landed; this entry pins the surface myOS callers rely on.

## Worked examples

```bash
# Annotate the audit stream with a release marker
ostk note "v3.18.1 cut, GH release URL: https://github.com/torimeyer/myos/releases/tag/v3.18.1"

# Search the audit stream for a specific actor's edits
ostk search "actor:claude-code-4135" --scope=history
```

## What this primitive is NOT

- **Not a backup.** The audit stream records changes, but it does not retain content past gen_table rotation.
- **Not transactional rollback.** Reversing an audit row is mechanical (apply the reverse diff), but it is not automatic. The future Undo primitive will own that.
- **Not a debug log.** Audit rows are state-change events, not arbitrary print statements.
- **Not a write-ahead log.** Audit rows are written after the change lands, not before.
