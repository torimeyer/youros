---
title: Spec template unification (→1599)
status: draft
created_at: 2026-05-22
promoted_at: 2026-05-22
needle: →1599
---

# Spec template unification (→1599)

## Problem

The "Needs clarity" badge and the 10-section template audit tool are two independent systems that don't talk to each other. A spec can score 0/10 on structure and still pass the badge (if it has AC items), or it can have perfect template structure and fail the badge (because file paths reference unbuilt files). Users see a "Needs clarity" badge with no explanation of which canonical sections are missing.

On top of that, the AI suggest button in `NeedsClarityChip.tsx` silently swallows all errors — when the Anthropic API key isn't configured, the button flashes "Thinking…" and goes back to "AI suggest" with no explanation. And every textarea in the chip shows the same generic placeholder ("Provide the missing information…") regardless of which check is failing.

## Goals

- Fix the AI suggest button so errors are visible rather than swallowed.
- Replace the generic textarea placeholder with per-check hint text.
- Prefill the 8 canonical section headings when a new spec is created, preventing structural drift before it starts.
- Stage-aware clarity checks: skip `has_file_paths` for specs in design/draft stage so early specs don't get noisy false positives.

## Non-goals

- Merging the 5-check badge and 10-section audit into a single system. They serve different purposes.
- Removing or changing the 5 clarity checks themselves.
- Semantic search across specs.
- Changes to ostk-recall or upstream ostk.

## Solution

Three independent fixes, each in one or two files:

**Fix 1 — AI suggest error state** (`app/src/components/NeedsClarityChip.tsx`): Add `suggestErrors` state (Record<string, string>). In the catch block, extract error message and store it per check name. Render the error below the "AI suggest" button. Clear on user input. For the "no API key" case, link to Settings.

**Fix 2 — Per-check placeholder copy** (`app/src/components/NeedsClarityChip.tsx`): Add a `CHECK_PLACEHOLDER` map (6 entries, parallel to existing `CHECK_LABEL` map) and wire it to the textarea's `placeholder` prop. Hint text per check:
- `has_ac_checkboxes`: "Add acceptance criteria — one per line: - [ ] When X, the result is Y"
- `no_vague_ac`: "Rewrite vague lines to be specific and testable. Remove: TBD, ?, TODO, maybe, discuss"
- `has_file_paths`: "List the files this spec will touch: api/routers/foo.py, app/src/components/Bar.tsx"
- `referenced_files_exist`: "Fix broken file paths — check spelling or recent renames"
- `in_repo_scope`: "Clarify how this work lives in the current repo (not upstream or an external system)"
- `outcome_concrete`: "State exactly what will be built or changed — no TBD, no vague qualifiers"

**Fix 3 — Template scaffolding at creation time** (`api/routers/specs.py`, `api/services/spec_templates.py`): Read `spec_templates.py` first (it already exists). Make `POST /api/specs/draft` return a body pre-populated with the 8 canonical headings and placeholder content under each. Make `POST /api/specs/{path}/promote` append missing canonical headings at the end if they're absent.

## Acceptance criteria

- [ ] `NeedsClarityChip.tsx`: `suggestErrors` state exists; catch block stores error per check name; error renders below the "AI suggest" button; cleared on user input.
- [ ] `NeedsClarityChip.tsx`: "No Anthropic API key" error includes a link to Settings (matches the 400 message from `clarity_suggest.py:191-193`).
- [ ] `NeedsClarityChip.tsx`: `CHECK_PLACEHOLDER` map exists with 6 entries; wired to textarea `placeholder` prop.
- [ ] `POST /api/specs/draft` response body includes all 8 canonical section headings with placeholder content.
- [ ] `POST /api/specs/{path}/promote` appends any missing canonical headings at the end of the file body.
- [ ] `api/services/spec_templates.py` is read and reused before adding new template content (no duplication).
- [ ] Frontend tests cover: error rendering on 400 response, placeholder text per check type, scaffolded headings in new draft.

## USER FEEDBACK

*(Reserved for tori. This proposal was written 2026-05-22 and is awaiting review.)*

## DECISION

TBD (needs tori) — proposal not yet reviewed. Open: (1) whether stage-aware clarity checks are in scope for Phase 3 or a separate needle; (2) whether the spec_templates.py file already covers the scaffolding use case.

## References

- Needle →1599
- Agent 1599 research (this spec converted from the original numbered research doc)
- `app/src/components/NeedsClarityChip.tsx` — primary file for Fixes 1 and 2
- `api/services/clarity_suggest.py` — backend AI suggest endpoint (working correctly)
- `api/routers/specs.py` — `/clarity-suggest` endpoint (working)
- `api/services/spec_templates.py` — existing templates service (read before implementing)
- `api/services/spec_audit.py:TEMPLATE_SECTIONS` — 10-section audit tool
- `api/services/gemini_ready.py:compute_spec_readiness` — 5-check clarity badge logic
