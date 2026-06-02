---
title: Org maturity baseline, agent gallery, and portfolio rollup
status: spec
promoted_at: 2026-06-01T19:09:52Z
created_at: 2026-06-01T19:08:39Z
---

## Problem

Organizations adopting AI assistance have three recurring needs that myOS does
not yet serve directly:

1. No way to baseline how skilled each person is with AI, or to roll that up so a
   manager can see their team.
2. No in-app way to discover, install, and share useful agents with peers. The
   template store has a marketplace bucket and a personal/custom bucket, but no
   "shared to my org" bucket and no role tagging, so peer sharing has no home.
3. No leadership view that rolls individual work up into projects and projects up
   into the organization's strategic themes, with risk indicators.

Separately, any downstream distribution of myOS (an enterprise overlay, a team
fork) currently has no clean, supported way to preload starter content (default
roles, a curated agent set, a project-to-theme mapping) without forking code.
Today that requires code changes on top of myOS, which fights the release model.

## Goals

- Ship three user-facing features on `origin/main` as generic, vendor-neutral
  capabilities: a maturity self-rating, an agent gallery with org sharing, and a
  portfolio rollup dashboard.
- Ship a small set of shared primitives (a per-org configurable list mechanism, a
  pillar tag on work items, and a data-only seed loader) that the three features
  consume.
- Keep `main` free of any single deployment's specific content. All concrete
  content (role labels, agent bundles, theme names, project mappings) arrives as
  data via the seed loader, never as code on `main`.

## Non-goals

- No deployment-specific content in this work (role lists, agent bundles, theme
  names, project mappings are all data, supplied separately, out of scope here).
- No bidirectional Jira write-back. Portfolio reads are read-only in this scope.
- No telemetry-source plugin, manager dashboard, spec-story packaging, workshop
  mode, learning-path mirror, or back-office workflow library. Those are backlog
  and may become a separate spec once these three ship and prove the foundation.

## Dependencies

The multi-user aspects of these features build on the Team Mode spec (S005,
user-scope `~/.myos/specs/team-mode-plan.md`), which defines identity, membership,
and the Owner/Member/Viewer permission model:

- Track A's manager rollup, Track B's "share to org", and Track C's team portfolio
  all assume a multi-user workspace. Until Team Mode v1 ships, every surface here
  degrades to single-user: empty `job_roles` is a single bucket, maturity is
  self-only, gallery org-share is hidden, and portfolio shows the local user's
  work.
- `job_roles` (job-function labels, this spec) and `role` (permission tier:
  Owner/Member/Viewer, stored as `members[].role` in Team Mode) are distinct
  fields on the same `enterprise_store`. Do not conflate them.

## Track 0 — Shared primitives (must ship first; the other tracks depend on it)

Three additions on `main`. Each is generic; none names any specific deployment.

### 0.1 Per-org configurable lists

Extend the existing org config store with two free-form lists, both defaulting to
empty (empty means "no job-roles / no themes", i.e. a single bucket, the current
behavior).

- Existing (reuse): `api/services/enterprise_store.py` already holds per-org
  collections via its `org_templates` CRUD block. Extend that schema.

Acceptance criteria:
- [ ] Org config supports `job_roles: list[str]` and `pillars: list[str]`, both
      (`job_roles` are job-function labels, distinct from Team Mode's permission
      `role`; see Dependencies)
      defaulting to `[]`.
- [ ] Reading config on a fresh install returns empty lists, and all existing
      behavior is unchanged when the lists are empty.
- [ ] CRUD to set/replace each list is exercised by a pytest under `api/tests/`.

### 0.2 Pillar tag on tasks and projects

- Existing (reuse): `api/routers/tasks.py`, `api/routers/projects.py`, and the
  task/project models. Edit existing `app/src/pages/Tasks.tsx` and
  `app/src/pages/Projects.tsx` (not new files) to render the chip and filter.

Acceptance criteria:
- [ ] Task and project models gain `pillar: str | null` (nullable, defaults null).
- [ ] The task and project list views render the pillar as a chip when set.
- [ ] The list views offer a filter-by-pillar control driven by the org `pillars`
      list from 0.1.
- [ ] A task/project with no pillar behaves exactly as today.

### 0.3 Seed loader (data only, reversible)

New `api/services/seed_loader.py`. On startup, read JSON files from
`~/.myos/seeds/*.json` and apply them through the existing CRUD APIs (tasks,
agent templates, org config). This is the supported way a downstream distribution
preloads content without `main` knowing that distribution exists.

Design constraints (do not violate):
- Pure data. Each seed file declares `target` (router/store name), `version`, and
  `payload` (a list of CRUD operations). No code self-registration, no DSL.
- Fail-open. A bad or unparseable seed is logged and skipped; OS startup never
  errors because of a seed.
- Reversible (this replaces the earlier "flip a profile flag" idea, which could
  not work: seeded content is copied into the user's real data, so a flag cannot
  remove it). The loader records what it applied so it can be un-applied.

Acceptance criteria:
- [ ] Loader reads every `~/.myos/seeds/*.json`, validates `target`/`version`/
      `payload`, and applies valid ones via existing CRUD APIs.
- [ ] Applied seeds are tracked in `~/.myos/seeds/.applied` keyed on
      `(target, version)`, so re-running does not duplicate, and a changed
      `version` re-applies as an update.
- [ ] The loader records the IDs of every item it created, and exposes an
      `unapply(target, version)` path that deletes exactly those items.
- [ ] A malformed seed file is logged and skipped; startup still completes.
- [ ] Covered by pytest: apply, idempotent re-apply, version bump re-apply, and
      un-apply round-trip (apply then un-apply leaves the store as it began).

Size guard: Track 0 is ~300 LOC of net change, roughly one sprint. If it stretches
past two weeks, stop and rescope; A/B/C depend on Track 0 staying small.

## Track A — AI maturity baseline

- New: `api/routers/maturity.py`, `api/services/maturity_store.py`,
  `app/src/pages/MaturityPage.tsx`.
- Existing (reuse): org role lookup from `enterprise_store.py` (landed in 0.1).

Acceptance criteria:
- [ ] A person can set a self-rating of Beginner / Intermediate / Proficient /
      Master, stored per user.
- [ ] A manager can view their team's ratings as an aggregate (rollup), gated by
      the role lookup from org config.
- [ ] The job-roles offered come from the org `job_roles` list; on an empty list
      the page still works (single implicit job-role).
- [ ] pytest covers the endpoint; vitest covers the page.

## Track B — Agent gallery / peer skills sharing

- Existing (reuse): `api/services/agent_templates_store.py` (already has
  marketplace + custom buckets; add a third `org_shared` source and a `job_roles`
  tag). Extend `api/routers/agents.py` with gallery endpoints. Edit existing
  `app/src/pages/Agents.tsx` as needed.
- New: `app/src/pages/AgentGallery.tsx`, `app/src/components/PeerFollowButton.tsx`.

Acceptance criteria:
- [ ] Template schema gains an `org_shared` source bucket and a `job_roles: list[str]`
      tag.
- [ ] Gallery page supports browse, install, "share to org", and peer follow.
- [ ] Gallery can sort to surface templates authored by peers at adjacent maturity
      levels first (consumes Track A data).
- [ ] Installing a gallery template produces a usable agent.
- [ ] pytest covers the new endpoints; vitest covers the gallery page.

## Track C — Portfolio rollup + pillar tagging

- New: `api/routers/portfolio.py`, `api/services/pillar_config.py`,
  `app/src/pages/PortfolioPage.tsx`.
- Existing (reuse): the `pillars` org list and the pillar tag from Track 0; Jira
  reads from `services/atlassian` (see open question below).

Acceptance criteria:
- [ ] Dashboard rolls tasks up into projects and projects up into pillars, using
      the pillar tag from Track 0.
- [ ] Each rollup row shows a risk indicator (overdue / blocked / no recent
      update).
- [ ] Any Jira integration is read-only in this scope.
- [ ] pytest covers the rollup endpoint; vitest covers the page.

Open question / sizing risk (resolve before estimating Track C):
- The existing Jira reader is `atlassian.list_assigned_issues()` in
  `services/atlassian` (NOT `atlassian_sync.py:157`, which is only the 5-minute
  poll loop that turns *the current user's assigned* tickets into notifications).
  `list_assigned_issues()` returns only the current user's assigned issues. A
  portfolio rollup across a team/org needs a broader query that does not exist
  yet. Decide the required scope (my tickets / a project / a board / the whole
  org) and confirm whether `services/atlassian` can fetch it. If not, Track C
  includes a new query function and the estimate grows beyond a straight reuse.

## Verification

- `scripts/e2e_smoke.sh` exercises the new endpoints.
- `scripts/run-vitest.sh` for the new/edited pages; `tsc -b` clean.
- A pytest file under `api/tests/` per new router (maturity, portfolio) and for
  the seed loader (apply / idempotent / version-bump / un-apply round-trip).
- Empty-state check: with empty `job_roles`/`pillars` and no seeds present, every
  new surface degrades to current behavior (no job-roles, single bucket, no pillar
  chips).

## Guardrails (keep `main` content-free)

- CI grep on `origin/main` fails the build on any deployment-specific string
  (the actual deny-list is maintained by the downstream distribution, not here).
- The `projects/` directory stays untracked; CI fails the build if any path under
  `projects/` is staged.
- This spec, and `main`, hold zero deployment-specific content. All concrete
  content ships as seed JSON from the downstream distribution only.

## Sequencing

1. Track 0 — one sprint, `main` only. Hard gate for the rest.
2. Track A — after Track 0 lands.
3. Track B.
4. Track C — larger if the broader Jira query is new work (see open question).

Stop after Track C and reassess against measured outcomes before starting any
backlog item.
