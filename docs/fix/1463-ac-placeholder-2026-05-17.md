# Fix →1463: Write Placeholder AC When No API Key (Subscription Auth)

**Date:** 2026-05-17
**Needle:** →1463
**Status:** In progress

## Problem

When running on subscription auth (no `ANTHROPIC_API_KEY`), the `create_draft` and
`_finish_ac_and_promote` functions in `api/routers/specs.py` silently swallow
exceptions and leave drafts with no AC block. The spinner never clears because
the draft stays in a limbo state with no acceptance criteria.

## Fix

- In `create_draft`: after AC generation fails (no API key), write a placeholder
  AC block with three `- [ ]` checkboxes and log a warning.
- In `_finish_ac_and_promote`: same treatment if `api_key` is empty.
- Replace bare `except Exception: pass` blocks with `logger.warning(..., exc_info=True)`.
- Repair 3 stuck drafts in place by appending the placeholder.

## Commits

(to be updated)
