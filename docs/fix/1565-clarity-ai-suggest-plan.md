# →1565 Clarity-2: AI-suggest service + endpoints

Backend-only implementation of the AI-suggest clarification flow.

## What ships in this needle

- `api/services/clarity_suggest.py` — `suggest_clarification(check_name, context)` using Claude
- `POST /api/tasks/{task_id}/clarify/suggest` — returns `{proposed_fix, rationale}`, no persist
- `POST /api/tasks/{task_id}/clarify/apply` — appends fix to task description, re-runs readiness
- `POST /api/specs/{path}/clarity/suggest` — same suggest flow for specs

## Prompt templates

One Python constant per check name (`outcome_concrete`, `in_repo_scope`, `is_unblocked`,
`has_ac_checkboxes`, `no_vague_ac`, `has_file_paths`, `referenced_files_exist`).

## Tests

- `api/tests/test_clarity_suggest.py` (new): unit tests mocking `client.messages.create`
- `api/tests/test_tasks.py`: two new endpoint tests (suggest + apply)
- `api/tests/test_specs.py`: one new endpoint test (suggest)
