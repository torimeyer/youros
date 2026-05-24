# Fill proposal: team-mode-plan.md

## Provenance

Created 2026-05-18. Originated from needle →1433 (team mode multi-user design) and the NR partnership CTO meeting that surfaced the need for multiple people to work in the same myOS workspace. Agent `plan-1433-team-mode-27ee10` wrote the spec. Commit `112a7bd` on main: "→1433 plan: team mode — multi-user, shared workspace, roles, billing, surface migration". The spec also absorbed →1434 (Cost/Permission primitive promotion), which was closed 2026-05-17.

## What's missing

Four canonical sections:
1. **Non-goals** — content exists but is embedded inside the Goals section as "Not in v1". Needs its own heading.
2. **USER FEEDBACK** — no section exists. Design phase, so empty is correct, but the heading should be there.
3. **DECISION** — no section. Architecture decisions ARE recorded in the spec body, just not under this heading.
4. **References** — no section. Referenced needles and files exist in the body.

## Confidence: HIGH

All content is derivable from what's already in the spec. Non-goals just need to be moved. DECISION can be assembled from the architecture choices scattered through the spec. References can be extracted from inline mentions.

---

## Proposed fills (sections to add)

### Non-goals

Move "Not in v1" content out of Goals into its own section:

```markdown
## Non-goals

- SSO / SAML / enterprise IdP integration.
- Fine-grained per-resource ACLs beyond the three roles (Owner, Member, Viewer).
- Multi-workspace federation (one user, many workspaces).
- Billing integration with a payment processor (Stripe etc.).
- Member invitation UI/flow in the frontend.
- Real-time collaborative editing (shared cursors, live conflict resolution).
- Audit log export in a compliance format (SOC 2, GDPR).
- The NR-enterprise overlay (separate branch, separate plan).
- `scoped` and `strict` isolation levels — only `open` ships in v1.
- Cost/Permission primitive promotion (absorbed from →1434) — deferred until team-mode v1 ships with real callers.
```

### USER FEEDBACK

```markdown
## USER FEEDBACK

*(Reserved for tori during design and implementation. Open questions in the spec body are the pending items to resolve here.)*
```

### DECISION

```markdown
## DECISION

Core architecture decisions made 2026-05-18:

- **Identity model**: Source of truth is `~/.myos/enterprise.json` managed by `api/services/enterprise_store.py`. Email is the stable identifier. No new identity store for v1.
- **Three roles**: Owner (full control), Member (read/write shared, launch agents), Viewer (read-only). Single FastAPI dependency `require_role(minimum: Role)` enforces at router level.
- **Isolation level v1**: `open` only — all members see all workspace data by default. `scoped` and `strict` are non-goals.
- **Path strategy for per-user data**: `~/.myos/users/<user_id>/<file>` with `user_id` from enterprise.json. Flat paths remain as read-only fallbacks during deprecation, then removed.
- **Authentication v1**: Magic link (owner emails a one-time link; on click, session cookie is set). No password, no IdP.
- **Cost/Permission primitive promotion** (from →1434): deferred until team-mode v1 ships and modules have real callers. See "Cost & Permission as myOS primitives (v2)" section.
- **Open questions** (see Open questions section): magic link delivery, workspace host, conflict resolution, per-user OAuth, API key model, session expiry, Viewer role scope, →1410 users/ directory, Atlassian tokens, team.json fate — all pending before implementation.
```

### References

```markdown
## References

- Needle →1433 (team mode design)
- Needle →1434 (cost/permission primitive promotion — absorbed, deferred to v2)
- Needle →1410 (users/ directory migration strategy)
- `~/.myos/enterprise.json` — identity source of truth
- `api/services/enterprise_store.py` — member store (398 lines, no live callers as of 2026-05-17)
- NR-enterprise branch (separate; nothing NR ever in main per memory rule)
```

## Items that couldn't be recovered

None — all fills derived from spec body content. DECISION content assembled from architecture prose in the spec. Nothing needs tori to recover.
