# Plans vs Specs vs Tasks: Data Model Research

_2026-05-17 — agent plans-vs-specs-vs-tasks-model-d155a1_

---

## 1. Plans

**Where they live:** Two places, different purposes.

- `~/.claude/plans/*.md` — personal scratchpads written by Claude during planning
  (`superpowers:writing-plans` skill). Example:
  [`~/.claude/plans/build-a-plan-for-deep-metcalfe.md`](~/.claude/plans/build-a-plan-for-deep-metcalfe.md)
  — starts with a prose `# Title`, narrative context, customer problems,
  design principles. No frontmatter. No lifecycle states.
- `transcripts/plan-<needle_id>.md` — per-task plan transcripts written by
  agents before executing a needle. Example: `transcripts/plan-1117.md`.
  These have heartbeat timestamps embedded in them. Also no frontmatter.

**Fields:** Free-form markdown. No structured frontmatter, no `status` key,
no `acceptance_criteria`, no `task_ids`.

**States:** None. A plan file has no lifecycle. It cannot be "draft", "ready",
or "building". It is a static artifact.

**How the API treats them:** `GET /specs` explicitly **excludes** plan documents:

```python
# api/routers/specs.py:304
return {"docs": [d for d in docs if d.get("status") != "plan"]}
```

`list_docs()` in `ostk.py:2221` discovers plan files in `transcripts/plan-*.md`
and tags them `status="plan"`. They surface only via `GET /specs/recent`
(the Recent Documents widget), not the main Specs page.

**The `plan_path` field on tasks:** When `GET /tasks` returns a task,
`tasks.py:_attach_plan_path` checks whether `transcripts/plan-<id>.md` exists
and attaches a `plan_path` field. This is a link from a task to its plan
transcript — not a separate "plan" object in the data model.

---

## 2. Specs

**Where they live:**

- `docs/draft/<slug>.md` — status: `draft` (frontmatter-driven)
- `docs/spec/<slug>.md` — status: `spec` (promoted), then computed

**Fields (from frontmatter + computed):**

```
status: draft | spec | building | complete | done
promoted_at: <ISO timestamp>   (set on promote)
task_ids: [list of needle IDs] (written by decompose)
acceptance_criteria: [...]     (parsed from "## Acceptance criteria" checkboxes)
```

Example header from [`docs/spec/gemini-ready-chip-and-spawn.md`](docs/spec/gemini-ready-chip-and-spawn.md):

```markdown
---
status: spec
promoted_at: 2026-05-18T04:47:12Z
---
# Gemini-ready chip and spawn

## Context
...
## Problem
...
## Goals
...
## Non-goals
...
## Acceptance criteria
- [ ] ...
```

**States (computed by `compute_spec_status` in `ostk.py:2695`):**

| State | Condition |
|---|---|
| `draft` | file is in `docs/draft/`, frontmatter says draft — never overridden |
| `plan` | file is a `transcripts/plan-*.md` — never overridden |
| `ready` | promoted to `docs/spec/`, no tasks linked yet, no active claims |
| `in-progress` | any open task OR any active build claim |
| `building` | build started but tasks not yet linked (narrow window) |
| `complete` | all linked tasks closed, no active claims |

**API routes:**

- `GET /specs` — list all (excludes `plan` status)
- `POST /specs/draft` — create draft in `docs/draft/`
- `POST /specs/promote` — move to `docs/spec/`, set `promoted_at`
- `POST /specs/{path}/decompose` — break into child needles, write `task_ids`
- `POST /specs/{path}/build` — spawn per-task agents, set status `building`
- `POST /specs/{path}/verify` — check AC checkboxes
- `GET /specs/{path}/tasks` — get linked task rows

---

## 3. Tasks / Needles

**Where they live:** `ostk` issues store — `~/.myos/` or the project's `.ostk/`
directory (the exact path is managed by the `ostk` kernel, accessed via
`ostk.list_tasks()`). Not markdown files. Stored in `issues.jsonl` or equivalent.

**Fields (from `tasks.py` enrichment):**

```
id: "→1234"
title: string
status: "open" | "in_progress" (derived) | "closed" | "shelved"
priority: P0 | P1 | P2
tags: [labels]
plan_path: "transcripts/plan-1234.md" (if exists)
blocked_by: [task IDs]
```

**States:**

- `open` — backlog. The only state you can _write_ via `POST /tasks` or
  `PATCH /tasks/:id` (`valid_statuses = {"open"}`, `tasks.py:1231`).
- `in_progress` — **derived at read time**, never stored. Overlaid when
  an active agent session is working on the task (`tasks.py:254`).
- `closed` — done.
- `shelved` — parked/deferred.

**API routes (key ones):**

- `GET /tasks` — list with `in_progress` overlay
- `POST /tasks` — create (status forced to `open`)
- `PATCH /tasks/{id}` — update title/tags/priority
- `POST /tasks/{id}/close` — close
- `POST /tasks/pull` — claim next available task (pull model for agents)

---

## 4. Backlog UI

`app/src/pages/Backlog.tsx` calls two endpoints:

```ts
api.get('/specs')  // → specs[] with status draft/ready/in-progress/complete
api.get('/tasks')  // → tasks[] with status open/in_progress/closed
```

It renders:

- **"PLANS IN FLIGHT"** section = specs array (label comes from `displayStatus()`:
  `'ready' | 'spec'` → `"Ready"`, others pass through). The "Draft / Ready / Build"
  labels you see in the UI map to spec statuses `draft`, `ready`/`spec`,
  `in-progress`/`building`.
- **"STANDALONE TASKS"** section = tasks that have no `id` in any spec's `task_ids`
  set. Statuses shown: `in_progress`, `open`.

---

## 5. Are Plans and Specs the Same Thing?

**No. They are structurally different and have different lifecycles.**

| | Plans (`~/.claude/plans/`) | Specs (`docs/spec/`) |
|---|---|---|
| Format | Free-form prose, no frontmatter | Frontmatter + structured sections |
| Lifecycle states | None | draft → ready → in-progress → complete |
| Acceptance criteria | No | Yes (`- [ ]` checkboxes) |
| Linked tasks | No | Yes (`task_ids` in frontmatter) |
| Visible in Backlog UI | No | Yes (as "PLANS IN FLIGHT") |
| Verified by API | No | Yes (`/verify` endpoint) |
| Created by | Claude during planning, agent skill | `ostk doc draft` + `ostk doc promote` |

Plans are **pre-work thinking artifacts** — they inform a spec or needle but
are not themselves trackable work items. Specs are **trackable work items with
AC, lifecycle, and linked tasks**.

The label "PLANS IN FLIGHT" in the Backlog UI is misleading — it actually
renders **specs**, not plan files. Plan files never appear there.

---

## 6. Memory Rules (verbatim)

From [`feedback_specs_use_myos_pipeline.md`](/Users/torimeyer/.claude/projects/-Users-torimeyer-claude-torios/memory/feedback_specs_use_myos_pipeline.md):

> myOS has its own specs pipeline. Do NOT follow the generic
> brainstorming-skill default of `docs/superpowers/specs/YYYY-MM-DD-*.md`.
>
> The myOS flow:
> 1. `ostk doc draft "<Title>"` — Creates `docs/draft/<slug>.md`
> 2. Fill the body. Acceptance criteria MUST be `- [ ]` checkbox bullets
>    under a `## Acceptance criteria` heading.
> 3. `ostk doc promote docs/draft/<slug>.md` — Moves to `docs/spec/<slug>.md`,
>    flips computed status to `ready`.
> 4. After implementation, `ostk doc decompose docs/spec/<slug>.md` can break
>    it into sub-needles.

From [`feedback_needle_or_spec.md`](/Users/torimeyer/.claude/projects/-Users-torimeyer-claude-torios/memory/feedback_needle_or_spec.md):

> **Needle is the default.** One-line title, optional description, optional AC.
> Use for: bug fixes, refactors, small features (1-2 files), tooling tweaks,
> anything that's a single subagent wave.
>
> **Spec is the exception.** Use when ALL of: 3+ files touched, distinct
> user-facing behavior change, needs Problem/Goals/Non-goals to keep scope
> honest, or will decompose into 3+ child needles.
>
> **Plans in `~/.claude/plans/` are personal scratchpads.** They do NOT
> automatically become specs anymore. After plan approval, file a needle by
> default; only promote to spec if the criteria above are met.

---

## Summary

Three distinct things, all called "plan" somewhere:

1. **Plans** (`~/.claude/plans/`, `transcripts/plan-*.md`) — prose scratchpads.
   No lifecycle. Not visible in Backlog.
2. **Specs** (`docs/draft/`, `docs/spec/`) — structured documents with AC,
   task links, and a full lifecycle (draft → ready → in-progress → complete).
   Shown in Backlog as "PLANS IN FLIGHT" (confusing label).
3. **Tasks/Needles** — items in the ostk work queue with states
   `open` / `in_progress` (derived) / `closed`. Shown in Backlog as
   "STANDALONE TASKS" when not linked to any spec.

**Can plans and specs be collapsed?** They already are separate by design and
code. The question is whether the Backlog label "PLANS IN FLIGHT" should be
renamed to "SPECS" to reduce confusion — that's a UI change, not a data model
change.
