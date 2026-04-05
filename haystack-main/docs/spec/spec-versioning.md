---
title: spec versioning
promoted_at: 2026-03-08T03:50:50Z
author: orchestrator
created_at: 2026-03-08T03:50:14Z
status: spec
implements: []
---

# Spec Versioning

> Specs evolve. The evolution must be explicit, traceable, and non-destructive.

## The Problem

Specs contradict each other as the project evolves. Today we resolve contradictions
in discussion, update the spec in place, and the old version is lost. There's no
record of WHY a design changed, what it replaced, or which newer specs reinforced
the change.

## The Rule

Every spec gets a version number. When a contradiction arises:
1. The existing spec is preserved as `spec/foo-v1.md`
2. A new draft is created referencing the old version
3. The draft documents: what changed, why, which newer specs reinforce the change
4. After discussion/promotion, the new version becomes `spec/foo.md` (current)
5. The audit trail captures the version transition

## Frontmatter

```yaml
---
status: spec
version: 2
previous_version: spec/foo-v1.md
supersedes_reason: "Pull model replaces push-based agent communication per pull-model.md"
reinforced_by: [layer-boundary.md, ostk-mvp.md]
author: round-table
created: 2026-03-08
---
```

## File Convention

- `docs/spec/foo.md` -- always the CURRENT version
- `docs/spec/foo-v1.md` -- first version (preserved when v2 created)
- `docs/spec/foo-v2.md` -- second version (preserved when v3 created)
- Current version has no version suffix in the filename

## CLI Support

`ostk amend` already handles spec amendments with severity levels. Extend it:
- `ostk amend <spec> --severity breaking --new-version` creates the version archive
  and a new draft for the replacement spec
- `ostk trace <spec>` shows version history alongside bead/commit chain

## What Triggers a Version Bump

| Trigger | Action |
|---------|--------|
| Round table produces conflicting design | New version via discussion |
| Implementation reveals spec gap | Amend with severity, new version if breaking |
| New spec contradicts old spec | Old spec versioned, new spec references it |
| Retro identifies wrong decision | New version with retro as evidence |

## What Does NOT Trigger a Version Bump

- Typo fixes, formatting, clarification of existing intent
- Adding acceptance criteria to existing sections
- Filling in the implements: field after shipping

## Acceptance Criteria

- [ ] Every spec has a version: field in frontmatter (default 1)
- [ ] Breaking amendments archive the old version as spec/foo-vN.md
- [ ] New version references previous_version and supersedes_reason
- [ ] reinforced_by lists newer specs that support the change
- [ ] ostk trace shows version history
- [ ] Old versions are never deleted, only archived
- [ ] Version transitions appear in audit.jsonl
