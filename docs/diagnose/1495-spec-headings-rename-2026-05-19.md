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

<!-- Filled in after renames applied -->
Score: TBD  
Sections present: TBD

## Notes

Task predicted score jump to 8+. Only 1 heading matched the given patterns exactly. The spec uses more descriptive section names than the canonical 10-section template uses. If a higher score is needed, the remaining headings would need additional renames — but none of them match the mapping strings in the task, so they were left per the "don't invent renames" rule.
