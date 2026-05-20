# →1495 Spec Headings Rename

**Date:** 2026-05-19  
**Spec:** `~/.myos/specs/vp-marketing-first-impression.md`  
**Agent:** build-1495-rename-spec-headings-3038d2

## Original headings (verbatim)

```
## What this is for
## What is already in place (do not rebuild)
## What still needs to be built
## Out of scope (named so we do not slide into them)
## Build order
## Files to touch
## Estimate
## Smoke verification before the meeting
## References
```

## Audit score before

Score: **0/10**  
Sections present: none  
Sections missing: Problem, Goals, Non-goals, Solution, Edge cases, Success criteria, Acceptance criteria, Verification, USER FEEDBACK, DECISION

## Renames applied

Only headings that exactly matched the given mapping were renamed. The instructions say: "Don't invent renames. If a section doesn't match any of the above, leave it untouched."

| Source heading | Renamed to | Match reason |
|---|---|---|
| `## What this is for` | `## Problem` | Exact match for "What this is for" |

Headings that did NOT match any mapping pattern (left unchanged):

| Heading | Closest mapping candidate | Why not renamed |
|---|---|---|
| `## What still needs to be built` | `What needs to be built` | Has "still" — not an exact match |
| `## Out of scope (named so we do not slide into them)` | `Not-doing` / `What we're not doing` | Completely different phrasing |
| `## Smoke verification before the meeting` | `Done-when` / `How we'll know` | Completely different phrasing |
| `## Build order`, `## Files to touch`, `## Estimate`, `## What is already in place (do not rebuild)`, `## References` | — | No mapping defined |

## Audit score after

Score: **1/10**  
Sections present: Problem  
Sections missing: Goals, Non-goals, Solution, Edge cases, Success criteria, Acceptance criteria, Verification, USER FEEDBACK, DECISION

## Gap analysis

Task predicted 8+ but only 1 rename matched. Here is why each other candidate fell short:

**`## What still needs to be built`** — mapping pattern is `What needs to be built` (no "still"). Exact match rule prevents this rename. The section does contain `**Acceptance**` subsections with `- [ ]` checklists, but the heading line itself doesn't match.

**`## Out of scope (named so we do not slide into them)`** — mapping patterns are `Not-doing` and `What we're not doing`. Completely different phrasing; no rename applied.

**`## Smoke verification before the meeting`** — mapping patterns are `Done-when` and `How we'll know`. Completely different phrasing; no rename applied.

**`## Build order`, `## Files to touch`, `## Estimate`, `## What is already in place (do not rebuild)`, `## References`** — no mapping defined for any of these.

## Recommendation

To reach 8+, a follow-up task should add the remaining canonical sections directly or add extended patterns to the mapping (e.g. also match "What still needs to be built" for Acceptance-criteria, "Out of scope" for Non-goals, "Smoke verification" for Verification). That would require a deliberate decision to expand the rename mapping rather than a mechanical rename of exact strings.
