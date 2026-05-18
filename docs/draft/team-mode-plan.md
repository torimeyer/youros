---
status: spec
promoted_at: 2026-05-18T01:00:07Z
---
# Team Mode Plan

**Status:** draft  
**Needle:** →1433  
**Blocks:** →1434 (Cost/Permission primitive promotion)  
**Author:** plan-1433-team-mode-27ee10  
**Date:** 2026-05-17  

---

## Problem

myOS today is a single-user system. Every piece of state — needles, specs,
chat history, settings, OAuth tokens, agent runs, billing — belongs to one
person on one machine. There is no concept of "another user" anywhere in the
backend or frontend.

This creates three concrete failure modes as teams adopt myOS:

1. **Shared work lands in one person's head.** When Tori files a needle or
   promotes a spec, no one else can see or act on it without her copy of
   `~/.myos/`. Work is trapped in a personal directory tree.

2. **No accountability boundary.** An agent can spend $50 of compute, delete
   a file, or promote a spec to production — and there is no record of *who*
   authorised it or whether anyone else should have had a say.

3. **Billing is per-machine, not per-team.** The Cost router tracks what *this
   instance* spent. A team lead cannot see what their whole team consumed last
   week, or enforce a weekly cap per person.

The ask from the NR partnership CTO meeting and from Tori's own use is: let
more than one person work in the same myOS workspace, with sensible defaults
about what is shared and what stays private.

---

## Goals

**v1 (this plan):**

- [ ] Define the identity model: what is a "user", what is a "workspace", how
  do they relate.
- [ ] Define what data is shared vs private by default, and who can override
  that.
- [ ] Define a three-role permission system (Owner / Member / Viewer) and the
  enforcement points in the existing API surface.
- [ ] Define the billing model: what is metered, at what granularity, who sees
  what.
- [ ] Enumerate every existing single-user surface and decide its team-mode
  fate (shared / per-user / owner-only / needs new design).
- [ ] List the open questions that must be resolved before any code ships.

**Not in v1:**

- [ ] SSO / SAML / enterprise IdP integration.
- [ ] Fine-grained per-resource ACLs (beyond the three roles).
- [ ] Multi-workspace federation (one user, many workspaces).
- [ ] Billing integration with a payment processor (Stripe etc.).
- [ ] A member invitation UI/flow in the frontend.
- [ ] Real-time collaborative editing (shared cursors, live conflict resolution).
- [ ] Audit log export in a compliance format (SOC 2, GDPR).
- [ ] The NR-enterprise overlay (separate branch, separate plan).

---

## Multi-user model

### Concepts

| Concept | Definition |
|---------|-----------|
| **Workspace** | One installation of myOS. All state lives under `~/.myos/` on the host machine. There is exactly one workspace per host for now. |
| **User** | A human who has been added to the workspace. Identified by email. Stored in `enterprise.json` under `members`. |
| **Owner** | The user who created the workspace (or the first admin). There is always at least one. |
| **Session** | One active connection to the myOS backend, authenticated as a specific user. Today sessions are anonymous / single-user; in team mode each session carries a `user_id`. |
| **Agent** | A background process launched on behalf of a user. Inherits the launching user's identity and permission level. |

### Identity source of truth

The source of truth for team membership is `~/.myos/enterprise.json`, managed
by `api/services/enterprise_store.py`. This file already has the right shape:

```json
{
  "org": { "id": "...", "name": "Meyer", "admin_email": "..." },
  "members": [{ "id": "...", "email": "...", "role": "admin", "added_at": "..." }],
  "policies": { "max_agent_budget": 5.0, ... }
}
```

No new identity store is needed for v1. The `email` field is the stable
identifier. Sessions are matched to members by email after OAuth.

### Session model

Currently the backend has no per-request user identity. In team mode:

1. Each browser session carries a signed session cookie that encodes the
   authenticated user's email and role.
2. Backend routes that read or write user-scoped data extract the identity
   from the cookie via a FastAPI dependency (`current_user()`).
3. Agents launched by a user run with that user's identity encoded in the
   spawn payload. The agent's heartbeat and output rows are attributed to
   that user.

### Isolation levels

The existing `enterprise.json` already has an `isolation_level` field.  
v1 defines three levels:

| Level | Meaning |
|-------|---------|
| `open` | All members see all workspace data by default. Per-item overrides exist (see Shared Workspace section). This is the current de facto state and the default for small trusted teams. |
| `scoped` | Data is private by default unless explicitly shared. A member must opt in to share a needle, spec, or chat thread. Good for contractors or mixed-trust environments. |
| `strict` | Data is always private. Sharing is disabled. Effectively single-user mode even when multiple accounts exist. Useful for compliance environments. |

v1 ships with `open` only. `scoped` and `strict` are non-goals.

---

## Shared workspace

### Decision matrix

For each data type, the default in `open` isolation:

| Data type | File / path | Default in team mode | Rationale |
|-----------|-------------|----------------------|-----------|
| **Needles** | `~/.myos/needles/` | **Shared** | Backlog is a team artifact. Already synced via `team.json` category_defaults. |
| **Specs** | `~/.myos/specs/` | **Shared** | Specs are the contract for shared work. |
| **Agents** (registry rows) | `~/.myos/agents/` | **Shared** | All members should see running / completed agents. |
| **Agent memory** | `~/.myos/agent_memory/` | **Shared** | Agents write discovery notes; the whole team benefits. |
| **Knowledge** | `~/.myos/knowledge.json` | **Shared** | Team knowledge base. |
| **Agent templates** | `~/.myos/agent_templates.json` | **Shared** | Team-curated launch configs. |
| **Workflows** | `~/.myos/workflows.json` | **Shared** | Repeatable processes are team property. |
| **Decisions** | in needles / ostk audit | **Shared** | Decision log is a team artifact. |
| **Chat history** | `~/.myos/chat_history.json` | **Per-user (private)** | Conversations are personal; no member should read another's chat by default. |
| **Gem chat** | `~/.myos/gem_chat/` | **Per-user (private)** | Same as above. |
| **Settings** | `~/.myos/settings.json` | **Per-user (private)** | Theme, shortcuts, persona, notification prefs — personal. |
| **OAuth tokens** | `~/.myos/google_token.json`, `slack_token.json`, etc. | **Per-user (private)** | Credentials are personal. Each user completes their own OAuth flow. |
| **Journals** | `~/.myos/journals/` | **Per-user (private)** | Personal daily notes. |
| **Private dir** | `~/.myos/private/` | **Per-user (private)** | Explicitly personal by name. |
| **Org API keys** | `enterprise.json → api_keys` | **Owner-only** | High-trust: only the Owner can see/set org-level API keys. |
| **Enterprise / org settings** | `enterprise.json → policies` | **Owner-only (read for all)** | Members can read policies; only Owner can change them. |
| **Billing / cost data** | ostk audit rows | **Owner sees all; member sees own** | Cost transparency without full exposure. |
| **Recordings** | `~/.myos/recordings/` | **Per-user (private)** | Screen/audio recordings are personal. |
| **MEMORY.md** | `~/.claude/projects/.../memory/` | **Per-user (private)** | This is the user's own AI memory file. |

### Path strategy for per-user data

Per-user data moves from flat `~/.myos/<file>` to namespaced
`~/.myos/users/<user_id>/<file>`. This is the strategy flagged in →1410.

Migration path:
1. At login, if `~/.myos/users/<user_id>/` does not exist, copy the flat
   `~/.myos/<file>` there as a one-time bootstrap for the first (Owner) user.
2. New users start with empty per-user state.
3. The flat paths remain as read-only fallbacks during a deprecation window,
   then are removed.

`user_id` is the member's `id` field from `enterprise.json` (8-char UUID
prefix). **Not** email — email can change.

---

## Role / permission model

### Three roles

| Role | Who | What they can do |
|------|-----|-----------------|
| **Owner** | The person who created the org, or explicitly promoted by another Owner. There must always be at least one. | Everything. Add/remove members, set org policies, set/read org API keys, see all cost data, delete any shared artifact, change isolation level. |
| **Member** | Standard team participant. | Read and write all shared data. Launch agents up to their personal budget cap. Cannot change org settings or see other users' private data. |
| **Viewer** | Read-only participant. | Read all shared data. Cannot write needles, specs, or agents. Cannot launch agents. |

### Enforcement points

These are the API surfaces where role checks must be wired in v1:

| Endpoint / area | Check |
|-----------------|-------|
| `POST /api/enterprise/*` (org create/update/delete, member add/remove, policy change) | Owner only |
| `GET /api/enterprise/api-keys` | Owner only |
| `POST /api/enterprise/api-keys` | Owner only |
| `POST /api/tasks` (needle create), `PATCH /api/tasks/:id` | Member+ |
| `POST /api/specs/` (spec promote) | Member+ |
| `POST /api/agents/spawn`, `POST /api/agents/register` | Member+ |
| `GET /api/costs` (full org view) | Owner: full; Member: own rows only |
| `DELETE /api/tasks/:id`, `DELETE /api/specs/:slug` | Owner or original author |
| `GET /api/chat` and all chat endpoints | Per-user (own history only, always) |
| `GET /api/settings`, `POST /api/settings` | Per-user (own settings only, always) |
| All OAuth flow endpoints (`/api/auth/*`) | Per-user (own tokens) |

### Enforcement implementation

A single FastAPI dependency `require_role(minimum: Role)` placed on each
router. It reads `current_user()` from the session cookie, looks up the
member record in `enterprise_store`, and raises HTTP 403 if the role is
insufficient.

In single-user mode (no `enterprise.json`), `current_user()` returns a
synthetic Owner record so nothing breaks.

---

## Billing model

### What is metered

| Metric | Who accumulates it | Where stored |
|--------|--------------------|-------------|
| **Token spend (API key mode)** | Per agent spawn, attributed to the launching user | ostk audit rows, already have a `model` field |
| **Subscription session hours** | Per chat session | Not yet tracked; needs a session-start / session-end event |
| **Agent budget consumed** | Per agent run, bounded by `max_agent_budget` in policies | Already enforced in spawn logic |

### Budget caps

`enterprise.json → policies.max_agent_budget` is already an org-level cap per
agent run. In team mode this becomes a *per-user per-run* cap enforced at
spawn time. An Owner can set a tighter cap for a specific member by adding a
`member_budget_override` field to their member record.

A weekly aggregate cap (`weekly_budget_limit`) per member is a v2 feature.

### What each role sees

| Role | Cost visibility |
|------|----------------|
| Owner | Full cost dashboard: all members, all agents, org total, daily/weekly trends |
| Member | Own cost only: my agents, my chat sessions, my token spend this week |
| Viewer | No cost data |

The existing `GET /api/costs` endpoint returns org-aggregate data today.
In team mode it accepts an optional `?user_id=<id>` filter. The backend
enforces: non-Owner requests must have `user_id == current_user.id`.

### Subscription vs API key

The existing `costs.py` already detects subscription vs API key sources
via `_SUBSCRIPTION_MODEL_PREFIXES`. In team mode this logic is unchanged.
Subscription seats are not individually tracked yet — that is a v2 concern
tied to Anthropic's billing API.

---

## Migration: single-user surface by surface

### Backend files

| File / path | Action |
|-------------|--------|
| `~/.myos/settings.json` | Move to `~/.myos/users/<owner_id>/settings.json`. Existing flat path is the Owner's settings. |
| `~/.myos/chat_history.json` | Move to `~/.myos/users/<owner_id>/chat_history.json`. |
| `~/.myos/gem_chat/` | Move to `~/.myos/users/<owner_id>/gem_chat/`. |
| `~/.myos/google_token.json` | Move to `~/.myos/users/<owner_id>/google_token.json`. |
| `~/.myos/slack_token.json` | Move to `~/.myos/users/<owner_id>/slack_token.json`. |
| `~/.myos/journals/` | Move to `~/.myos/users/<owner_id>/journals/`. |
| `~/.myos/private/` | Move to `~/.myos/users/<owner_id>/private/`. |
| `~/.myos/recordings/` | Move to `~/.myos/users/<owner_id>/recordings/`. |
| `~/.myos/needles/` | Stays at top level. Shared. |
| `~/.myos/specs/` | Stays at top level. Shared. |
| `~/.myos/agents/` | Stays at top level. Add `user_id` field to each agent row. |
| `~/.myos/agent_memory/` | Stays at top level. Shared. |
| `~/.myos/knowledge.json` | Stays at top level. Add `user_id` to each entry for attribution. |
| `~/.myos/enterprise.json` | Already correct shape. No move. |
| `~/.myos/team.json` | Superseded by enterprise.json in team mode. Keep for single-user compat. |
| `~/.myos/profile.json` | Move to `~/.myos/users/<owner_id>/profile.json`. |
| `~/.myos/briefing_state.json` | Per-user. Move to `~/.myos/users/<owner_id>/briefing_state.json`. |
| `~/.myos/notifications.json` | Per-user. Move to `~/.myos/users/<owner_id>/notifications.json`. |

### Frontend pages

| Page | Team-mode change |
|------|-----------------|
| **Dashboard** | Stays personal (user's own widgets). Org-level summary widget added for Owners. |
| **Agents** | Shows all agents (all users), with a "Launched by" column. Filter by user. |
| **Tasks (Needles)** | Shows all needles. Assignee field becomes meaningful (tied to a member). |
| **Specs** | Shows all specs. Author attribution added. |
| **Costs** | Owner sees org total + per-member breakdown. Member sees own costs only. |
| **Settings** | Personal settings only. Org settings moved to a new **Org Settings** page (Owner-only). |
| **Activity** | Shows all team activity. Filter by user. |
| **Onboarding** | Needs a "Join an existing workspace" path alongside "Set up my own". |
| **Chat / mychat** | Always per-user. No change in what is visible; but server routes check user identity. |
| **Calendar / Gmail / Drive** | Always per-user (each user connects their own Google account). |
| **Adoption** | Could become an org-level metric for Owner view. Per-user otherwise. |
| **Enterprise / Org Settings** | New page (Owner-only): manage members, policies, API keys, billing overview. |

### Authentication flow

Today: single anonymous session. OAuth is used only for Google/Slack
integrations, not for myOS login itself.

In team mode: a lightweight "who are you?" gate is added. Options:

1. **Magic link** (recommended for v1): Owner enters a member's email;
   backend emails a one-time link; clicking it sets a session cookie with
   that user's identity. No password, no IdP dependency.
2. **Google OAuth as identity** (v2): use the Google OAuth flow already wired
   in `auth.py` to assert identity, not just to get Drive/Gmail tokens.
3. **SSO/SAML** (non-goal for v1).

The magic-link approach requires SMTP config. If SMTP is not configured, the
Owner can copy-paste the magic link from the server log — acceptable for v1
small-team use.

### MEMORY.md

MEMORY.md lives in `~/.claude/projects/.../memory/MEMORY.md` and is written
by Claude Code's memory system, not myOS. It is personal to the user running
that Claude Code session. It is **not shared** and does not need migration.
Each team member's Claude Code session maintains its own memory file.

### ostk audit rows

The ostk audit already has an `actor` field (e.g., `claude-code-4197`,
`myos-api-4198`). In team mode the actor for human-initiated actions is
`user:<user_id>`. No schema change to the audit DB is needed; the actor field
is already freeform text.

---

## Open questions

- [ ] **Magic link delivery**: does myOS need an SMTP integration, or is
  copy-paste from logs acceptable for v1? If SMTP, which provider (SendGrid,
  SES, postmark)?
- [ ] **Workspace host**: who runs the server? For personal use it is Tori's
  Mac. For a team, someone's machine must be always-on. Does team mode require
  a deployment story (Docker, hosted), or do we assume a shared Mac that is
  always on?
- [ ] **Conflict resolution for shared files**: if two members write to
  `knowledge.json` concurrently, who wins? The current `atomic_write_json`
  uses file-level atomic replace but has no merge logic. Locking or append-only
  strategies?
- [ ] **Per-user OAuth token storage**: each member's Google/Slack tokens
  live under `~/.myos/users/<id>/`. On a shared server, does each user need a
  separate OAuth app registration, or do they all share the same OAuth client
  credentials?
- [ ] **Who pays for the Anthropic API?**: is there one org API key shared by
  all members (managed by Owner in `enterprise.json → api_keys`), or does each
  member bring their own key? The current `enterprise_store.get_org_api_key()`
  supports the shared-key model; the per-member model needs a new field.
- [ ] **Session expiry and re-auth**: magic link sessions should expire. What
  TTL? 7 days? 30 days? Browser close?
- [ ] **Viewer role demand**: is Viewer a real v1 need, or can we simplify to
  just Owner and Member for the first release?
- [ ] **→1410 users/ directory**: →1410 is still open. Should team mode wait
  for that decision, or can we make the decision here and close →1410 as part
  of this spec?
- [ ] **Atlassian/Jira tokens**: currently stored in the Atlassian MCP
  connection config. Are these per-user or org-level? For NR-enterprise the
  answer is org-level (one org Jira). For personal use it is per-user.
- [ ] **`team.json` fate**: `team.json` currently holds the `team_repo` path
  for ostk sync. Does this concept survive into team mode, or is it replaced
  entirely by `enterprise.json`?

---

## Acceptance criteria

- [ ] This spec is promoted to `~/.myos/specs/team-mode-plan.md` via
  `ostk doc promote`.
- [ ] Needle →1433 is closed with a link to the promoted spec.
- [ ] Needle →1434 (Cost/Permission primitive promotion) is unblocked: the
  spec defines the permission model clearly enough for →1434 to proceed.
- [ ] Every open question above has a tracking needle or an answer in a
  subsequent spec revision before any implementation begins.
