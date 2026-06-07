# Memory primitive

A yourOS primitive (v1.0). Per-user free-text memory injected into every chat turn's system prompt.

## Purpose

Give the user a single text file the AI reads before every reply, so personal context (preferences, recurring decisions, rules of thumb) persists across sessions without being passed in every message.

## Contract

Module: `services.user_memory_store` · Version: v1.0 · Status: active.

```python
def read() -> str
def append_bullet(section: str, text: str) -> None
def replace_all(text: str) -> None
```

HTTP surface:

- `GET /api/memory` → `{"content": str}`
- `PUT /api/memory` → replaces the file body

Storage: `~/.youros/users/default/MEMORY.md` (per-user, outside the repo).

## Events emitted

Writes go through `services.atomic_io.atomic_write_text`. A backup of the prior content is kept at `MEMORY.md.bak` for one rotation. Reads are cached in-memory and invalidated when the file mtime changes.

## Versioning history

- **v1.0** (2026-05-16, →1393): initial release. Bullet-section append helper, full-file replace, mtime-based cache. System-prompt injection lives in `chat_providers.py` across all 3 backends.

## Worked examples

```python
from services import user_memory_store as memory

# Read the whole file
text = memory.read()

# Append a one-liner under a section heading (creates the section if absent)
memory.append_bullet("Preferences", "Sonnet is the default model")

# Replace the whole file (from the Settings → Memory editor)
memory.replace_all("# My Memory\n\n## Preferences\n- ...")
```

## What this primitive is NOT

- **Not Anthropic's memory tool.** This is a plain file the system prompt injects, not a context-window-managing tool.
- **Not multi-user.** Today only `users/default/` exists. Multi-user is deferred to the team-mode plan (→1433).
- **Not searchable history.** It's a single file. Search across user history belongs to a different primitive.
- **Not auto-edited by the AI.** The AI can append on explicit ask via slash command; it does not edit memory unprompted.
