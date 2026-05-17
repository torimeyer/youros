# Audit primitive

Version: 1.0  
Status: active  
Module: `external:ostk`  
Routes: (none — ostk kernel primitive, not a myOS HTTP route)

---

## Purpose

The Audit primitive records what happened. Every time a file is written, a shell command runs, or a task changes state, the ostk kernel writes an audit row. You can read these rows later to understand exactly what myOS did and when.

Audit is the foundation for undo, for debugging, and for trust. "What did myOS actually change?" — Audit has the answer.

---

## Contract

Audit is implemented inside the ostk coordination kernel, not as a Python module. myOS code does not call Audit directly. Instead, it flows automatically through the kernel tools:

| ostk tool | What triggers an audit row |
|---|---|
| `fs_ops` (file write / edit) | Every file mutation |
| `bash` / `shell.run` | Every shell command |
| `gen_table` | File generation tracking |
| `note` | Explicit annotations by agents |

All rows are written to ostk's internal store (`.ostk/` directory). They include: timestamp, actor (which agent or session), action type, file path or command, before/after content hash.

### Reading audit rows

```bash
ostk gen list            # list files with generation history
ostk gen show <path>     # show history for a specific file
```

### From Python (indirect)

To leave an audit note from a Python service:

```python
import subprocess
subprocess.run(["ostk", "note", "your message here"])
```

---

## Events emitted

Every row in the ostk audit store is itself an event. There is no separate event stream — the store is the log.

---

## Versioning history

| Version | Date | Change |
|---|---|---|
| 1.0 | 2025-Q4 | ostk kernel audit primitive, shipped as part of ostk core. |

---

## Worked examples

### See what changed in the last session

```bash
ostk gen list --since 1h
```

### Check who wrote a specific file

```bash
ostk gen show api/services/time_primitive.py
# Output: gen=3, last writer: claude-code-4135, last changed: 2026-05-16T...
```

### Leave a note from an agent

```bash
curl -sSk -X POST https://127.0.0.1:8000/api/agents/my-agent/note \
  -H 'Content-Type: application/json' \
  -d '{"content": "Decided to use rolling median instead of EWMA for ETA"}'
```

---

## What this primitive is NOT

- **Not a Python import.** You cannot `import audit` — it is a kernel primitive, not a Python module.
- **Not a user-visible log.** Audit rows are low-level and primarily for debugging and undo. They are not formatted for reading in chat.
- **Not unlimited storage.** Old audit rows are pruned. Do not rely on Audit for long-term archival.
- **Not real-time streaming.** You poll the ostk store; there is no push channel.
