---
title: Workspace and identity model
promoted_at: 2026-06-01T00:45:11Z
status: spec
created_at: 2026-05-31T00:00:00Z
Tasks: →1844, →1845, →1846
---

## Problem

yourOS has no concept of a workspace or of users. The Projects page lists
every top-level directory inside a single hardcoded repo root
(`api/routers/projects.py` line 14: `TORIOS_DIR = PROJECT_ROOT`). There is
no way to distinguish personal codebases from shared ones, no way to add
another person to a project, and no access rules at all.

As the user begins reorganising their files into a `workspaces/` directory
structure, the application needs a clear mental model for what a workspace
is, who can be part of one, and what each person can do.

## Goals

1. Define the canonical vocabulary: **workspace** and **project** (→1844).
2. Specify which data is private to the owner and which is visible to
   collaborators by default, and who may override that (→1845).
3. Define a three-role permission system (Owner, Member, Viewer) and
   enumerate exactly what each role can do (→1846).
4. Keep the model vendor-agnostic so it can be implemented without
   dependency on any specific cloud provider or identity service.

## Non-goals

- This spec does not cover authentication mechanisms (OAuth, SSO, API keys).
  Those are separate concerns.
- This spec does not cover billing or seat limits.
- This spec does not define how the migration from the current flat-directory
  model to the workspace model happens. That is a follow-on task.
- This spec does not change any existing API routes. It is a design document
  only.

---

## Identity model (→1844)

### Vocabulary

| Term | Definition |
|------|-----------|
| **User** | A human who runs yourOS. In the initial single-user deployment a user is whoever owns the machine. In a multi-user deployment a user is identified by a stable, opaque ID (not an email address or a real name, so the model stays vendor-agnostic). |
| **Workspace** | A top-level container. A workspace holds one or more projects and optionally shared cross-project assets (docs, specs, brandkit). Each workspace has exactly one Owner. A workspace maps to a directory on disk. |
| **Project** | A codebase (git repository or plain directory) that lives inside a workspace. A project belongs to exactly one workspace. The current `/projects` endpoint returns projects; the entity that contains them is the workspace. |

### How they relate

```
User 1 (Owner)
  └── Workspace A          (personal workspace, private by default)
        ├── Project: api
        ├── Project: app
        └── Project: docs   (shared asset, readable by Members)

User 1 (Owner) + User 2 (Member)
  └── Workspace B          (shared workspace)
        ├── Project: brandkit
        └── Project: shared-docs
```

A user may own more than one workspace and may be a Member or Viewer of
workspaces owned by others. Workspaces are independent: membership in
Workspace A does not imply any access to Workspace B.

### Today's state (single-user)

The current codebase has no workspace record at all. `TORIOS_DIR` (the repo
root) acts as an implicit single workspace owned by whoever runs the server.
The spec introduces workspace as a first-class concept but the initial
implementation may store workspace metadata in `~/.myos/workspaces/` rather
than in a database, consistent with how settings are stored today in
`~/.myos/settings.json`.

---

## Shared vs private (→1845)

### Default visibility rules

| Data type | Default | Who may change it |
|-----------|---------|------------------|
| Workspace (the container itself) | **Private** — only the Owner sees it | Owner only |
| Project inside a private workspace | Inherits workspace — private | Owner only |
| Project explicitly marked shared | **Shared** — readable by all Members and Viewers of the workspace | Owner or any Member |
| Cross-project assets (docs, specs, brandkit, roadmap) | Inherits workspace privacy unless individually overridden | Owner or any Member |
| Agent task list | **Private** — not shared at any role level | Owner only; not overrideable by others |
| Personal settings (`~/.myos/settings.json`) | **Private** — never shared | Owner only; never overrideable |
| AI model credentials (API keys, OAuth tokens) | **Private** — never shared | Owner only; never overrideable |

### Override mechanism

An override is an explicit visibility tag on a project or asset:

- `visibility: private` — only the Owner sees it.
- `visibility: shared` — all workspace Members and Viewers can read it.

When no tag is present, the workspace default applies. An Owner may change
the default for the whole workspace. A Member may change visibility on
individual projects they have write access to, but may not change the
workspace-level default.

### Data that is never shared regardless of overrides

- Personal AI credentials and API keys.
- Agent task list and internal agent state.
- System-level settings (theme, model preference, MCP server list).

These are owned by the running user and are outside the visibility model
entirely. They do not appear in any shared view.

---

## Permission matrix (→1846)

### Roles

| Role | How it is assigned | Cardinality |
|------|--------------------|-------------|
| **Owner** | The user who created the workspace. | Exactly one per workspace. Cannot be removed; only transferred. |
| **Member** | Invited by the Owner. Has read/write on shared projects. | Zero or more. |
| **Viewer** | Invited by the Owner or any Member. Read-only on all shared content. | Zero or more. |

### What each role can do

| Action | Owner | Member | Viewer |
|--------|:-----:|:------:|:------:|
| See the workspace exists | Yes | Yes | Yes |
| Read shared projects | Yes | Yes | Yes |
| Read private projects | Yes | No | No |
| Write to shared projects | Yes | Yes | No |
| Write to private projects | Yes | No | No |
| Create a project inside the workspace | Yes | Yes | No |
| Delete a project | Yes | No | No |
| Change a project's visibility tag | Yes | Yes (shared only) | No |
| Invite a new Viewer | Yes | Yes | No |
| Invite a new Member | Yes | No | No |
| Revoke a Member or Viewer | Yes | No | No |
| Change the workspace-level default visibility | Yes | No | No |
| Delete the workspace | Yes | No | No |
| Transfer ownership | Yes (to any Member) | No | No |
| View agent task list | Yes (own tasks) | No | No |
| View personal settings | Yes (own settings) | No | No |

### Notes on the matrix

- "Write" means create, edit, rename, and delete files inside a project.
- A Member can invite a Viewer but cannot promote a Viewer to Member; only
  the Owner can do that.
- A Viewer has no write access anywhere. If a Viewer needs write access, the
  Owner must promote them to Member.
- The Owner always retains full access. There is no action that reduces Owner
  access below what is listed above.

---

## Acceptance criteria

- [ ] →1844: A `Workspace` type is defined in code with at minimum: `id`, `name`,
      `owner_id`, `path` (on-disk root), `default_visibility`, and `created_at`.
- [ ] →1844: A `Project` type carries a `workspace_id` foreign key linking it to its
      parent workspace.
- [ ] →1844: `GET /workspaces` returns all workspaces the calling user owns or is a
      Member/Viewer of.
- [ ] →1844: `GET /workspaces/{id}/projects` returns projects in that workspace,
      filtered by the caller's role (Owner sees all; Member/Viewer see only shared).
- [ ] →1845: A project or asset with no explicit visibility tag inherits the workspace
      default.
- [ ] →1845: An explicit `visibility: shared` tag on a project makes it readable by
      Members and Viewers of that workspace regardless of workspace default.
- [ ] →1845: Personal settings, AI credentials, and agent task state are never returned
      by any endpoint to a non-Owner caller.
- [ ] →1846: `POST /workspaces/{id}/members` is restricted to Owner callers.
- [ ] →1846: `DELETE /workspaces/{id}/members/{user_id}` is restricted to Owner callers.
- [ ] →1846: A Member caller can `POST /workspaces/{id}/viewers` (invite a Viewer).
- [ ] →1846: A Viewer caller receives `403` for any write or invite endpoint.
- [ ] →1846: `DELETE /workspaces/{id}` is restricted to the Owner.
- [ ] →1846: `POST /workspaces/{id}/transfer` changes the Owner field and demotes the
      former Owner to Member.

## Verified against the codebase

- `api/routers/projects.py:14` — `TORIOS_DIR = PROJECT_ROOT` is the current
  implicit workspace root. The workspace concept does not exist in the
  codebase today; this spec introduces it.
- `api/routers/projects.py:26-52` — `/projects` endpoint iterates `TORIOS_DIR`
  returning every top-level directory as a project. After this spec is
  implemented, these will be projects belonging to the default workspace.
- `app/src/pages/Projects.tsx:17` — the `Project` interface has no
  `workspace_id` field. The spec adds it.
- `~/.myos/settings.json` — personal settings live outside the repo. The spec
  explicitly excludes them from the sharing model, consistent with the
  existing pattern.
- No existing file in `api/` or `app/src/` defines a `Workspace` type or
  `workspace` route. This is a net-new concept with no collision risk.
