---
promoted_at: 2026-05-18T01:46:04Z
status: spec
---
# users/ Directory Migration Strategy

> Operational counterpart to the team-mode plan (commit 7a086cd, `docs/draft/team-mode-plan.md`). That plan answers *what* goes per-user vs shared. This plan answers *how* we physically move files from `~/.myos/<item>` to `~/.myos/users/<uid>/<item>` without breaking running sessions or older `ostk` clients.

## Problem

`~/.myos/` is currently a flat single-user blob. There are ~90 items at the top level today, mixing:

- secrets (OAuth tokens, session keys, TLS certs)
- per-user state (chat history, settings, journals, agents, needles, tasks)
- machine-scoped infrastructure (hooks, scripts, logs, ostk-cache)
- regenerable caches (gmail/calendar/drive)
- shared-or-ambiguous items (specs, agentfiles, prototypes)

The team-mode plan commits us to `~/.myos/users/<user_id>/` as the new home for per-user data. The first real per-user file (per-user `MEMORY.md`, →1393) already lives under `users/default/`. Every other surface still reads/writes the flat path.

If we flip every reader at once we break:
1. Running myOS sessions mid-flight
2. ostk versions that pre-date the per-user paths
3. User scripts and integrations that hard-code `~/.myos/<thing>`

We need a migration that is **incremental**, **reversible**, and **invisible to the user** until they explicitly switch to team mode.

## Goals

- Define a per-item disposition (move, copy, leave) for every current `~/.myos/` surface.
- Pick one migration mechanism per disposition class. No bespoke per-file logic.
- Land in waves so each wave is independently reversible and committed.
- Keep the existing single-user installation working at every wave boundary.
- Provide a clear backward-compat shim so older ostk binaries and user scripts keep reading the right file.

## Non-goals

- Implementing team mode itself. That is the team-mode plan (commit 7a086cd).
- Multi-tenant authentication, role enforcement, billing. Same.
- Changing the *content* or *schema* of any moved file. This is a path migration only.
- Migrating data that lives outside `~/.myos/` (claude transcripts, ostk install, the repo).
- Renaming items. `chat_history.json` stays `chat_history.json`, just under `users/<uid>/`.

## Inventory and disposition

90+ items live under `~/.myos/`. Grouped by destination:

### A. Move to `users/<uid>/` (per-user)

These contain or describe a single human's work. They move to `~/.myos/users/<uid>/<item>`.

| Item | Current path | Target | Notes |
|---|---|---|---|
| Settings | `settings.json`, `config.json`, `profile.json`, `rules.json`, `labels.json`, `task_labels.json` | `users/<uid>/<item>` | Plus the 7 `settings.json.bak*` siblings — same target. |
| Conversation | `chat_history.json`, `threads.json`, `transcript_titles.json`, `gem_chat/`, `gem_knowledge/` | `users/<uid>/` | |
| Knowledge | `journals/`, `knowledge.json`, `documents/`, `files/`, `exports/` | `users/<uid>/` | |
| Tasks and agents | `needles/`, `agents/`, `subagents/`, `nudges/`, `agent_memory/`, `agent_workspace/`, `agent_workspace.json`, `task_order.json`, `task_source.json`, `session_task_map.json`, `last_task_batch.json`, `recurring_tasks.json`, `waves.json`, `build_queue.json`, `session_task_map.json` | `users/<uid>/` | |
| Recordings, checkins | `recordings/`, `checkin-runs/`, `journals/` | `users/<uid>/` | |
| Savings | `savings_history.jsonl`, `savings_snapshot.json` | `users/<uid>/` | |
| Caches (regenerable) | `calendar_cache/`, `drive_cache/`, `gmail_cache/`, `imessage_cache/`, `meeting_prep_cache/`, `team_cache.json`, `upgrade_cache.json`, `briefing_state.json`, `probe_state.json` | `users/<uid>/cache/` | Group all caches under one subdir so we can blow them away safely. |
| State | `state/`, `notifications.json`, `blocker_explanations.json` | `users/<uid>/` | |
| Per-user enterprise | `enterprise.json`, `team.json` (single-tenant), `team_catalog/` | `users/<uid>/` until team mode | When team mode lands, these get re-homed to `orgs/<org_id>/`. |

### B. Move to `users/<uid>/secrets/` (per-user, encrypted at rest)

Tokens and credentials need the same per-user split, plus stricter file-mode handling.

| Item | Current path | Target |
|---|---|---|
| OAuth tokens | `github_token.json`, `google_credentials.json`, `google_token.json`, `slack_token.json` | `users/<uid>/secrets/` |
| Push and session | `vapid_keys.json`, `session_secret`, `oauth_states.json` | `users/<uid>/secrets/` |

Mode `0600`, owner-only. Re-symlink shim writes a `chmod 0600` after every materialization.

### C. Leave at `~/.myos/` (machine-scoped or shared infra)

These do not belong to a user. They stay where they are.

| Item | Why |
|---|---|
| `localhost.crt`, `localhost.key` | TLS for the local backend, host-scoped. |
| `hooks/`, `scripts/`, `agentfiles/` | ostk infrastructure, shared across users. |
| `logs/`, `ostk-cache.log`, `ostk-cache.err` | Process-scoped logs, currently grow at the root. Worth a separate cleanup pass but not part of this plan. |
| `myos.db`, `primitives.db`, `primitives.db-shm`, `primitives.db-wal` | See class D — these need a schema migration, not a file move. |
| `private/`, `workspace/`, `shared/`, `orgs/`, `sync_repo/`, `sync_config.json` | Already designed as shared/team surfaces; team-mode plan owns them. |
| `specs/` | Specs are the canonical doc location (per project rule, "ostk doc promote routes here"). Authoring is per-user, but the artifact is shared. Stays at root; per-user spec drafts go under `users/<uid>/specs-draft/` if needed. |
| `prototypes.json`, `workflows.json`, `templates.json`, `agent_templates.json` (+ `.bak`) | Currently single-user but logically shared catalogs. Defer to team-mode plan. |
| `fleet-ia-review-workspace.md` | One-off workspace file. Probably stale; defer to a cleanup needle. |
| `shares.json` | Shared resource registry. Stays. |

### D. Database split (separate workstream)

`myos.db` and `primitives.db` are SQLite databases that today hold per-user rows commingled with shared rows (chat messages, settings rows, agents, specs, primitives). A file-move does not work here.

**Disposition**: out of scope for this plan. File a follow-up needle: "split `myos.db` into `myos.db` (shared) + `users/<uid>/myos.db` (per-user) via a row-by-row migration". Cite this plan as the trigger.

## Migration plan per item

Three mechanisms cover every per-user item. Pick one per item, never invent a fourth.

### Mechanism 1: Symlink shim (recommended default)

For items we want to move but cannot atomically replace because of long-running readers.

1. `mkdir -p ~/.myos/users/<uid>/`
2. `mv ~/.myos/<item> ~/.myos/users/<uid>/<item>`
3. `ln -s users/<uid>/<item> ~/.myos/<item>`

Old callers still resolve `~/.myos/<item>` to the new location. New callers can read the canonical path directly. The shim is removed in a later wave once every reader is updated.

Applies to: most files in class A and class C cache subdir.

### Mechanism 2: Copy-on-first-read (for items we cannot move atomically)

For things actively held open by a process (`myos.db` WAL, `recordings/` while a capture is live, `nudges/<name>.signal` files being touched by the backend).

1. Reader function `myos_path("chat_history.json")` resolves to:
   - `~/.myos/users/<uid>/chat_history.json` if it exists
   - else `~/.myos/chat_history.json` (legacy fallback)
2. On first write to the new path, copy the legacy file over so both paths agree from then on.
3. Background job (idle tick) removes the legacy file once the new path is older AND a config flag `migration.users_dir.legacy_removed=true` is set.

Applies to: `nudges/` (touched on every nudge), `state/`, anything with `.lock-` siblings.

### Mechanism 3: Secrets re-issue (for tokens)

OAuth tokens and session secrets do not survive a copy cleanly because some providers tie the token to the file path's inode + machine ID. Cleaner: re-issue.

1. Move file to `users/<uid>/secrets/` under the same name.
2. `chmod 0600`.
3. On next backend boot, revalidate each token via its `whoami` endpoint. If revalidation fails, prompt the user to reconnect (Settings page already has this flow).

Applies to: class B (tokens and session keys).

## Sequencing

Five waves. Each wave is one PR, independently reversible.

| Wave | Scope | Mechanism | Reversibility | Reader update |
|---|---|---|---|---|
| **W0** | Create `~/.myos/users/default/` if missing. No moves yet. Add `myos_path()` helper that defaults to legacy path. | n/a | trivial | every later wave depends on this |
| **W1** | Move read-mostly settings: `settings.json`, `config.json`, `profile.json`, `rules.json`, `labels.json`, `task_labels.json`. | Symlink shim | `mv` symlink back | callers updated in same PR |
| **W2** | Move append-only conversation: `chat_history.json`, `threads.json`, `transcript_titles.json`, `journals/`, `gem_chat/`. | Symlink shim | symlink back | callers updated in same PR |
| **W3** | Move caches into `users/<uid>/cache/`. Easy because caches can be wiped. | Symlink shim (or just delete-and-regenerate) | regenerate | low risk |
| **W4** | Move secrets to `users/<uid>/secrets/` with `0600`. Trigger token revalidation on next boot. | Secrets re-issue | restore from backup | banner if token invalid |
| **W5** | Live-touched files (`nudges/`, `state/`, anything backend writes on every nudge). | Copy-on-first-read | leave both paths in place until flag flipped | reader uses `myos_path()` only |

After all 5 waves land, a separate cleanup PR removes the symlink shims one item at a time once metrics show no caller reads the legacy path for 7 days.

## Backward compatibility

Three callers we cannot ignore.

### 1. Running backend session

The backend writes to `~/.myos/` ~once per second (heartbeats, signal files). Symlinks let any open file descriptor keep working through the move. New writes after the symlink lands hit the new path.

Verification: after each wave, the running backend must not need a restart. Test: hold an open `tail -f` on a moved file before the migration, run the migration, confirm the `tail` continues without breaking.

### 2. Older `ostk` binaries

ostk pre-6.0.5 reads from the flat path. The symlink shim covers them transparently. Once we drop pre-6.0.5 support (separate decision), the shim can be removed.

Verification: in CI, install ostk 6.0.5 against a `~/.myos/` with the symlink layout. Confirm all `ostk *` commands behave the same as on the flat layout.

### 3. User shell scripts and aliases

`tori` aliases and shell helpers reference paths like `~/.myos/settings.json` directly. The shim keeps them working forever (until we explicitly remove it). When team mode lands and there is a second user, those scripts have no way to choose a `<uid>` — they will still resolve to the symlink, which points at the *currently active* user's data.

Mitigation: document the convention `~/.myos/users/$(myos uid)/<item>` in the team-mode docs. Do not break the symlink even after multi-user.

## Open questions

- [ ] How is `<uid>` decided pre-team-mode? Hard-code `default` until team mode lands, or generate a stable UUID at boot?
- [ ] Do the 7 `settings.json.bak*` siblings deserve a `users/<uid>/backups/` subdir, or do we drop them as part of W1?
- [ ] `recordings/` can be very large. Are we sure we want to symlink-shim it, or should it stay at root with a per-user index?
- [ ] `agentfiles/` is currently per-user authoring but logically shared template library. Leave at root (class C) or move (class A)?
- [ ] Specs draft vs promoted: draft lives under `docs/draft/`, promoted lives under `~/.myos/specs/`. Should per-user drafts go under `users/<uid>/specs-draft/`?
- [ ] `myos.db` / `primitives.db` split is gated on a separate needle. What is the trigger condition for that needle becoming P0?
- [ ] Symlink approach assumes a POSIX filesystem. Windows port (if we ever) needs a different mechanism. Do we care?
- [ ] When team mode lands and a second user is added, do existing flat-path symlinks need to point at the inviting user, or do we error out and require explicit migration?
- [ ] What metrics confirm "no caller reads the legacy path for 7 days"? File-access tracing on macOS is annoying; do we instrument `myos_path()` instead?
- [ ] Rollback story: if W4 breaks token revalidation for one user, do we have a single command that restores their tokens from the legacy path? Spec it.

## Acceptance criteria

- [ ] Every item currently under `~/.myos/` appears in exactly one of classes A/B/C/D above
- [ ] Each per-user item has one of the three mechanisms assigned to it
- [ ] Waves W0 through W5 are each independently reversible (no W2 depends on a W1 detail beyond `myos_path()`)
- [ ] Open questions all have either an answer or a follow-up needle filed
- [ ] Database split (class D) has its own filed needle, not bundled with this plan
- [ ] Old `ostk` binaries pass their full smoke against the W1 layout in CI
