# Agentfile Validation Report — 2026-05-13

> Needle →1300 | `ostk agentfile validate` (ostk 6.0.5) against all 52 files in `agents/` and `agents/marketplace/`

## Summary

**52/52 pass. 0 failures. No child needles required.**

- Exit code histogram: 52× exit 0
- Status histogram: 52× `ok`
- Warnings: 12 files have `FROM-PROFILE-MISSING` (warn, not error) — profile `coder` not found at runtime path; resolves at fleet-seed time

---

## Validation Table

| File | Exit Code | Status | Diagnostics |
|------|-----------|--------|-------------|
| `agents/brainstorm.agent` | 0 | ok | WARN: FROM-PROFILE-MISSING (`coder`) |
| `agents/builder.agent` | 0 | ok | WARN: FROM-PROFILE-MISSING (`coder`) |
| `agents/diagnose.agent` | 0 | ok | WARN: FROM-PROFILE-MISSING (`coder`) |
| `agents/explain-plain.agent` | 0 | ok | WARN: FROM-PROFILE-MISSING (`coder`) |
| `agents/fleet-build-website-backend-developer.agent` | 0 | ok | WARN: FROM-PROFILE-MISSING (`coder`) |
| `agents/fleet-build-website-frontend-developer.agent` | 0 | ok | WARN: FROM-PROFILE-MISSING (`coder`) |
| `agents/fleet-build-website-product-manager.agent` | 0 | ok | WARN: FROM-PROFILE-MISSING (`coder`) |
| `agents/fleet-build-website-security-engineer.agent` | 0 | ok | WARN: FROM-PROFILE-MISSING (`coder`) |
| `agents/research.agent` | 0 | ok | WARN: FROM-PROFILE-MISSING (`coder`) |
| `agents/review.agent` | 0 | ok | WARN: FROM-PROFILE-MISSING (`coder`) |
| `agents/self-review.agent` | 0 | ok | WARN: FROM-PROFILE-MISSING (`coder`) |
| `agents/test.agent` | 0 | ok | WARN: FROM-PROFILE-MISSING (`coder`) |
| `agents/marketplace/blog-post.agent` | 0 | ok | — |
| `agents/marketplace/budget-builder.agent` | 0 | ok | — |
| `agents/marketplace/bug-finder.agent` | 0 | ok | — |
| `agents/marketplace/call-prep.agent` | 0 | ok | — |
| `agents/marketplace/campaign-brief.agent` | 0 | ok | — |
| `agents/marketplace/citation-helper.agent` | 0 | ok | — |
| `agents/marketplace/cold-outreach-draft.agent` | 0 | ok | — |
| `agents/marketplace/competitive-scan.agent` | 0 | ok | — |
| `agents/marketplace/concept-explainer.agent` | 0 | ok | — |
| `agents/marketplace/customer-interview-notes.agent` | 0 | ok | — |
| `agents/marketplace/customer-reply.agent` | 0 | ok | — |
| `agents/marketplace/debug-helper.agent` | 0 | ok | — |
| `agents/marketplace/debugger.agent` | 0 | ok | — |
| `agents/marketplace/design-critique.agent` | 0 | ok | — |
| `agents/marketplace/essay-outline.agent` | 0 | ok | — |
| `agents/marketplace/flash-cards.agent` | 0 | ok | — |
| `agents/marketplace/follow-up.agent` | 0 | ok | — |
| `agents/marketplace/gift-finder.agent` | 0 | ok | — |
| `agents/marketplace/headline-generator.agent` | 0 | ok | — |
| `agents/marketplace/homework-helper.agent` | 0 | ok | — |
| `agents/marketplace/interactive-debug.agent` | 0 | ok | — |
| `agents/marketplace/investor-update.agent` | 0 | ok | — |
| `agents/marketplace/launch-checklist.agent` | 0 | ok | — |
| `agents/marketplace/meal-planner.agent` | 0 | ok | — |
| `agents/marketplace/name-generator.agent` | 0 | ok | — |
| `agents/marketplace/objection-handling.agent` | 0 | ok | — |
| `agents/marketplace/prd.agent` | 0 | ok | — |
| `agents/marketplace/proofreader.agent` | 0 | ok | — |
| `agents/marketplace/prospect-research.agent` | 0 | ok | — |
| `agents/marketplace/refactor-plan.agent` | 0 | ok | — |
| `agents/marketplace/research.agent` | 0 | ok | — |
| `agents/marketplace/review.agent` | 0 | ok | — |
| `agents/marketplace/roadmap.agent` | 0 | ok | — |
| `agents/marketplace/social-post.agent` | 0 | ok | — |
| `agents/marketplace/stakeholder-update.agent` | 0 | ok | — |
| `agents/marketplace/study-guide.agent` | 0 | ok | — |
| `agents/marketplace/test-engineer.agent` | 0 | ok | — |
| `agents/marketplace/test.agent` | 0 | ok | — |
| `agents/marketplace/trip-planner.agent` | 0 | ok | — |
| `agents/marketplace/write-tests.agent` | 0 | ok | — |

---

## Notes

- **Verb form confirmed**: `ostk agentfile validate <path>` accepts relative paths (`agents/foo.agent`). Passing just the basename (`builder`) fails with "agentfile not found". Plan note about this was accurate; full path used for all runs.
- **FROM-PROFILE-MISSING** (warn, not error): All 12 `agents/*.agent` files reference `FROM-PROFILE coder`. The `.ostk/profiles/coder.Agentfile` does not exist in this worktree. The validator treats this as a warning, not a failure — resolution happens at runtime via fleet seed. No fix needed unless the profile should be committed to the repo.
- **All marketplace agents** (40 files): parse clean, no diagnostics.
- **No child needles filed**: 0 failures.
