# Memory primitive

Version: 1.0  
Status: active  
Module: `api/services/user_memory_store.py`  
Routes: `GET /memory`, `PUT /memory`

---

## Purpose

The Memory primitive is the single place that reads and writes the `MEMORY.md` file — the document that persists what you want myOS to remember across sessions. When you tell myOS to remember something, Memory is what stores it. When myOS loads a session and reads your preferences, Memory is what it reads.

Nothing else in myOS touches `MEMORY.md` directly. All reads and writes go through this primitive so the file is never left in a broken state.

---

## Contract

### Inputs and outputs

```python
from services.user_memory_store import read, append_bullet, replace_all

# Read the entire MEMORY.md file as a string.
read() -> str

# Add a bullet point under a named section heading.
# Creates the section if it does not exist.
append_bullet(section: str, text: str) -> None
# section — the heading to add under (e.g. "Rules", "Context")
# text    — the bullet text (without the leading dash)

# Overwrite the entire file with new content.
replace_all(text: str) -> None
```

### HTTP endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/memory` | Returns the raw MEMORY.md text |
| PUT | `/memory` | Overwrites MEMORY.md with the request body |

---

## Events emitted

No structured events. Every write is atomic (write to a temp file, then rename) so the file is never partially written. The ostk `gen_table` audit row is written on every `replace_all` or `append_bullet` call.

---

## Versioning history

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-03 | Shipped as part of v3.18.0. `read / append_bullet / replace_all`. Atomic writes via temp-file rename. |

---

## Worked examples

### Read current memory

```python
from services.user_memory_store import read

content = read()
print(content[:200])  # first 200 chars
```

### Add a rule

```python
from services.user_memory_store import append_bullet

append_bullet("Rules", "Never use em-dashes in responses")
# MEMORY.md now has a bullet under ## Rules
```

### Fetch via HTTP

```
GET /memory

# MEMORY.md

## Rules
- Never use em-dashes in responses
...
```

---

## What this primitive is NOT

- **Not a database.** It stores a Markdown file, not structured records. Do not use it for large datasets.
- **Not shared across users.** `MEMORY.md` lives at `~/.claude/projects/.../memory/MEMORY.md` — one file per person per project.
- **Not versioned automatically.** There is no built-in undo for memory writes. If you overwrite something important, check git history on the file.
- **Not a search index.** It returns the whole file; callers do their own filtering.
