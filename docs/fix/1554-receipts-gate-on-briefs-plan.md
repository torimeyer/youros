# →1554 N10: Receipts-gate coverage extends to subagent briefs

## What

Extend `check_receipts` from `receipts_gate.py` to cover spawn briefs.
When `POST /api/agents/spawn` receives a prompt containing a trigger word
("done", "fixed", etc.) without inline evidence, log a warning and add
`brief_warning` to the spawn response.

## Changes

- `api/services/receipts_gate.py`: add `check_brief_receipts(brief_text)` alias
- `api/routers/agents.py`: call `check_brief_receipts` early in `spawn_agent`,
  feature-gated by `chat_receipts_gate_enabled`; add `brief_warning` to all
  return dicts
- `api/tests/test_receipts_gate.py`: two new tests for `check_brief_receipts`
- `api/tests/test_agents.py`: two new spawn integration tests
