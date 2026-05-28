# Task → Task Rename: Backend Surface Map (2026-05-28)

Read-only survey. No edits made. Scope: `api/` (routers, models, services, tests).
Companion to: `docs/Task-rename-survey-2026-05-28.md` (docs/hooks/memory/CLAUDE.md — created by agent-map-Task-usage-in-d-e8f488cb).

---

## Summary

| Category | File count | Approximate hit count | Must change for rename? |
|---|---|---|---|
| Public API routes | 3 files | 3 routes | **Yes** — URL is user/frontend contract |
| Pydantic schema fields | 1 file | 5 fields | **Yes** — API request/response contract |
| Agent metadata keys | 2 files | ~40 sites | **Yes** — agent spawn/register JSON shape |
| Service functions | 4 files | ~10 functions | **Yes** — internal, but rename for consistency |
| JSON response keys | 4 files | ~15 sites | **Yes** — frontend reads these keys |
| Event/trace names | 2 files | ~8 names | **Yes** — consumed by activity feed |
| `kind="Task"` string literal | 3 files | ~12 sites | **Yes** — API request field value |
| ostk path strings (.ostk/Tasks/) | 2 files | ~6 sites | **No** — ostk kernel owns this path; don't rename |
| Test files | 75 files | ~200 hits | **Partial** — rename to match renamed symbols |

Total unique `.py` files with "Task" hits: **111**.

---

## 1. Public API routes

These URLs are the frontend-facing contract. A rename here is a breaking change unless the old route stays as an alias.

| Route | File | Line | Notes |
|---|---|---|---|
| `GET /api/files/Tasks` | `api/routers/files.py` | 474 | Returns Task history from `.ostk/Tasks/issues.jsonl`. Candidate: `/api/files/tasks` |
| `GET /api/tasks/by-Task/{Task_id}` | `api/routers/tasks.py` | 832 | Looks up a task by ostk Task ID. Candidate: `/api/tasks/by-id/{task_id}` |
| `GET /api/agents/delegate?Task_id=…` | `api/routers/agents.py` | 8751 | Query param name. Candidate: `task_id` |

**Constraint:** `/api/files/Tasks` reads from `.ostk/Tasks/issues.jsonl` — that path is owned by the ostk kernel and MUST NOT change in this rename. Only the HTTP route and the response key rename.

---

## 2. Pydantic schema fields — `api/models/schemas.py`

These are the request/response shapes that every spawn and register call uses. Renaming them changes the JSON wire format — coordinate with frontend.

| Field | Model | Line | Rename to |
|---|---|---|---|
| `Task_id: Optional[str]` | `AgentSpawn` | 235 | `task_id` |
| `Task: Optional[str]` | `AgentSpawn` | 319 | `task` |
| `Task_ids: Optional[List[str]]` | `AgentSpawn` | 327 | `task_ids` |
| `kind: str = Field("Task")` | `SpecDraft` (and siblings) | 337 | `kind: str = Field("task")` |
| `"Tasks": "shared"` | `api/models/team_schemas.py` | 20 | `"tasks": "shared"` |

**Note:** `kind="Task"` is also the default value in `SpecDraft`, `SpecFromTemplate`, `SpeckitImport`, `SpecFromRoadmapLine`, and `WizardCreateRequest` — all in `api/routers/specs.py`. These share the same default string literal; a grep-and-replace is safe but must update all 5 endpoints.

---

## 3. Agent metadata keys — `api/routers/agents.py`

The metadata stored per-agent in the registry and used by the auto-close path.

| Symbol | Line | Notes |
|---|---|---|
| `get_running_Task_ids()` | 4409 | Returns set of Task IDs for live agents. Rename to `get_running_task_ids()` |
| `_infer_Task_id(...)` | 4448 | Internal helper. Rename to `_infer_task_id()` |
| `_extract_all_Task_ids(...)` | 4499 | Internal helper. Rename to `_extract_all_task_ids()` |
| `meta.get("Task_id")` | 4428 | Reads from agent metadata dict. Rename key to `"task_id"` |
| `meta.get("Task_ids")` | 4431 | Reads from agent metadata dict. Rename key to `"task_ids"` |
| `spawn_meta["Task_id"] = body.Task_id` | 5863 | Writes to agent metadata. Rename both sides |
| `existing.get("Task_id")` | 6489 | Reads from registered agent data |
| `Task_id=meta.get("Task_id")` | 1653 | In completion / auto-close path |

**High blast radius:** `get_running_Task_ids()` is imported in `api/routers/tasks.py:273` (`from routers.agents import get_running_Task_ids`). That import site must update alongside the definition.

---

## 4. Service functions — `api/services/`

| Function | File | Line | Rename to |
|---|---|---|---|
| `list_Tasks_history(limit=…)` | `api/services/ostk_files.py` | 56 | `list_tasks_history()` |
| `ostk.set_Task_in_progress(Task_id)` | `api/services/ostk.py` | 608 | Keep as-is — this calls the ostk CLI verb; rename only if ostk renames the verb |
| `ostk.work_radiate(Task_id=…)` | `api/services/ostk.py` | 1595 | Keep param name in sync with ostk CLI |
| `ostk.create_thread(Task_ids=…)` | `api/services/ostk.py` | 3083 | `task_ids` if ostk renames the param |
| `threads_store.create_thread(Task_ids=…)` | `api/services/threads_store.py` | 52 | `task_ids` |
| `_local_Tasks()` | `api/services/team_sync.py` | 87 | `_local_tasks()` |
| `open_linked_Tasks` key | `api/services/ostk.py` | 289, 318 | `open_linked_tasks` |

**Constraint:** `set_Task_in_progress`, `work_radiate`, and `create_thread` wrap ostk CLI commands. The Python-side variable names can change independently of the CLI flag names. Only change the CLI param names if `ostk` itself renames them.

---

## 5. JSON response keys returned to frontend

These keys are read by the frontend (app/) and must be renamed in sync with app/ changes.

| Key | Endpoint | File | Notes |
|---|---|---|---|
| `{"kind": "Task", ...}` | `POST /api/specs/draft` (and 4 siblings) | `specs.py:108` | Frontend reads `kind` to detect Task vs spec creation |
| `{"Tasks": [...]}` | `GET /api/files/Tasks` | `files.py:479` | Frontend reads this array |
| `{"Task": "→NNN", ...}` | `GET /api/tasks/by-Task/{id}` | `tasks.py:875` | Frontend reads `Task` key |
| `{"Tasks": [...]}` | `GET /api/tasks/waves` | `tasks.py:720` | Wave objects contain a `Tasks` array |
| `"type": "Task"` | `GET /api/tasks/waves` | `tasks.py:673, 716` | Items tagged as Task vs spec |

---

## 6. Event and trace names

| Name | File | Line | Notes |
|---|---|---|---|
| `"Task_created"` | `specs.py` | 573 | `trace_event` call. Rename to `"task_created"` |
| `"Task_closed"` | `tasks.py` | 1745 | Published to `_notifications_events_bus`. Rename to `"task_closed"` |
| `"Task.linked"` | `activity.py` | 19 | Display label dict key. Rename key + value |
| `"Task.activated"` | `activity.py` | 20, 90 | Display label dict key |
| `"Task.refined"` | `activity.py` | 30 | Display label dict key |
| `"Task"` (tool name in sessions) | `tests/test_sessions.py` | 42, 112 | Test uses tool="Task" — if ostk renames the tool, tests must follow |

---

## 7. `kind="Task"` string literal — `api/routers/specs.py`

Five creation endpoints share the same `kind` field pattern. All must change together:

| Function | Line |
|---|---|
| `create_draft` | 560 (docstring + `body.kind == "Task"` check) |
| `create_from_template` | 708 (docstring + check) |
| `import_spec` | 817 (docstring + check) |
| `create_spec_from_roadmap_line` | 1370 (docstring + check) |
| `wizard_create` | 2738 (docstring + check) |

Also: `_create_Task()` helper function at line 94 — rename to `_create_task()`.

---

## 8. ostk filesystem paths — DO NOT RENAME

These strings point to the ostk kernel's own storage layout. The rename is of the *concept* not the kernel's storage paths.

| Path string | File | Notes |
|---|---|---|
| `.ostk/Tasks/issues.jsonl` | `agents.py:4474`, `main.py:678` | ostk owns this path; leave as-is |
| `.ostk/Tasks/` dir | `agents.py:5284-5285` | Same — ostk kernel layout |
| `list_Tasks_history` reads this path | `ostk_files.py:56` | Function renames; path string stays |

---

## 9. Test files — `api/tests/`

75 test files contain "Task". Key files with concept-level hits (not just string-matching helpers):

| File | What to rename |
|---|---|
| `test_Task_task_sync.py` | Entire file tests `GET /api/tasks/by-Task/{id}`. Rename file + test names if route renames |
| `test_delegation.py` | `test_delegate_endpoint_with_Task_id()` — rename to `task_id` param |
| `test_ghost_detection.py` | `"Task_id": "940"` in fixture — rename key |
| `test_1330_groups_deprecated.py` | `Task_ids` in thread fixture — rename key |
| `test_task_suggestions.py` | `"Task.activated"` event name in fixture |
| `conftest.py` | `Task_id = obj.get("id")` — rename local var |

Remaining ~70 test files use "Task" in comments/strings that reference the term conceptually. Those are lower priority — update in a doc-sweep pass.

---

## Rename sequencing recommendation

1. **Models first** (`api/models/schemas.py`): field renames cascade to every caller; do this in one commit with `grep -rn "Task_id\|Task_ids"` sweep across `api/`.
2. **Routes** (`files.py`, `tasks.py`, `agents.py`): rename URL paths with `@router.get` aliases for backward compat during transition.
3. **Response keys** in `specs.py`, `tasks.py`, `files.py`: coordinate with frontend PR.
4. **Event names** (`activity.py`, trace calls): rename after frontend event consumers updated.
5. **Service functions** (`ostk_files.py`, `team_sync.py`, `threads_store.py`): internal; rename after API layer done.
6. **Tests**: update to match renamed symbols; `test_Task_task_sync.py` rename is cosmetic only.

---

## What MUST NOT change in this sweep

- `.ostk/Tasks/` filesystem paths — ostk kernel owns these
- `ostk work` CLI commands — ostk owns the CLI surface
- `mcp__ostk__Task*` tool names — these are kernel-registered; the hooks that trigger on them cannot change without an ostk release
- Arrow-prefixed IDs (`→NNN`) — these are the canonical work-item ID format regardless of term

---

## References

- Companion survey (docs/hooks/memory): `docs/Task-rename-survey-2026-05-28.md`
- Task →1789: backend surface map task (this document)
- Related: →1788 frontend surface map
