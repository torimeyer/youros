# Diagnosis: user-memory v1 dormant — trigger never fires on real user turns

**Date:** 2026-05-20
**Task:** →1536
**Investigator:** agent-1536-verify-user-memory-v1-e2e-retry-001

## Symptom

`~/.myos/users/default/MEMORY.md` does not exist. The user believes the
in-chat memory feature is working, but no bullet has ever been written.
The file has never been created.

## Investigation

All four v1 files exist and the wiring is correct at the code level:

- `api/services/user_memory_store.py` — `append_bullet()` creates the file correctly
- `api/services/memory_trigger.py` — `handle()` calls `append_bullet()` on match
- `api/routers/chat.py:1249` — `_handle_memory_trigger()` is called for every user turn
- `api/services/chat_providers.py:1936` — `_user_memory_block()` injects memory into system prompt

Service-layer unit tests (`test_user_memory_store_e2e.py`) pass for exact trigger phrases:
"remember X", "I prefer X", "from now on X", "always X" — all fire correctly.

## Root Cause

The trigger patterns in `memory_trigger.py` are anchored with `^`:

```python
(re.compile(r"^remember\s*[,:]?\s*(?!when\b)(?!to\s+\w)(.+)", re.IGNORECASE | re.DOTALL), ...),
(re.compile(r"^from\s+now\s+on\s*[,:]?\s*(.+)", re.IGNORECASE | re.DOTALL), ...),
(re.compile(r"^always\s+(.+)", re.IGNORECASE | re.DOTALL), ...),
(re.compile(r"^i\s+prefer\s*[,:]?\s*(?!not\b)(.+)", re.IGNORECASE | re.DOTALL), ...),
```

Real user messages include conversational prefixes before the trigger word:

| User types | match_trigger() returns | Expected |
|------------|------------------------|----------|
| `"please remember I prefer plain language"` | None | "I prefer plain language" |
| `"can you always use plain language"` | None | "use plain language" |
| `"note that I prefer plain language"` | None | "plain language" |
| `"ok so remember I prefer concise answers"` | None | "I prefer concise answers" |
| `"remember I prefer plain language"` | "I prefer plain language" | ✓ correct |
| `"I prefer plain language"` | "plain language" | ✓ correct |

A user who never types a bare "remember X" with no prefix will never trigger the
feature. Since `~/.myos/users/` doesn't exist at all, the trigger has fired zero
times in production.

## Fix

Add a `_CONVERSATIONAL_PREFIX` regex that strips common polite/conversational
prefixes from the user message before applying the existing `^`-anchored patterns.

The existing exclusions (`^remember when`, `^remember to <verb>`) are applied
against the normalized text, so they continue to block false positives like
"please remember when we launched" and "can you remember to pick up milk".

**Changed file:** `api/services/memory_trigger.py`

- Added `_CONVERSATIONAL_PREFIX` compiled regex constant
- Modified `match_trigger()` to normalize text before pattern matching
- All existing exclusions applied against normalized text (order preserved)

## Verification

After the fix, `api/tests/test_user_memory_store_e2e.py` passes 17/17 tests,
including the new natural-phrasing tests. A manual smoke of the fix:

```python
from services.memory_trigger import match_trigger
assert match_trigger("please remember I prefer plain language") == "I prefer plain language"
assert match_trigger("can you always use plain language") == "use plain language"
assert match_trigger("please remember when we launched") is None   # false-positive guard
assert match_trigger("can you remember to pick up milk") is None   # false-positive guard
```
