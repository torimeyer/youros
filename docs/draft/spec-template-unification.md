# Spec template unification (→1599)

**Status:** draft — awaiting tori review before Phase 3 implementation  
**Date:** 2026-05-22  
**Agent:** 1599-specs-template-needs-clari-40cb2e  

---

## 1. What IS a spec in myOS?

A spec is a **shared agreement between tori and the build system** about what a piece of work is and how to know it's done. It lives as a Markdown file in `~/.myos/specs/` (private) or `docs/draft/` (committed/shared). It has three jobs:

1. Tell any agent enough context to start building without asking follow-up questions.
2. Provide the "Acceptance criteria" (AC) checkboxes the Build pipeline uses to split work into tasks.
3. Record decisions and feedback so future agents don't re-litigate settled choices.

A spec is **not** a design doc, a research note, or a meeting summary. Those things can inform a spec, but they need to be converted into the spec format before the build system will touch them.

---

## 2. Canonical sections

Every spec should have these headings, in this order:

| # | Heading | Why it must be there |
|---|---------|---------------------|
| 1 | **Problem** | Anchors all decisions. Without a problem statement, the scope is unbounded. |
| 2 | **Goals** | Lists what success looks like. Agents use this to avoid over-building. |
| 3 | **Non-goals** | Equally important as goals — prevents scope creep during build. |
| 4 | **Solution** | Describes the approach. Can be short for simple work; detailed for architectural changes. |
| 5 | **Edge cases** | Lists the failure modes, unusual inputs, or race conditions the solution must handle. |
| 6 | **Acceptance criteria** | The `- [ ]` checkbox list the Build pipeline reads. Each item must be specific and testable. |
| 7 | **Verification** | How to confirm the AC are actually met (test command, manual test steps, smoke test). |
| 8 | **USER FEEDBACK** | Reserved for tori's decisions and overrides. Agents must check this before acting. |
| 9 | **DECISION** | Records the final call on any open question in the spec. |
| 10 | **References** | Links to related specs, needles (`→NNN`), files, or docs. |

"Success criteria" is an accepted alias for "Acceptance criteria" (the audit tool recognizes both). All other headings must match exactly (case-insensitive H1–H3). The audit tool at `api/services/spec_audit.py:TEMPLATE_SECTIONS` scores specs against these 10 sections.

---

## 3. The "Needs clarity" rules

The "Needs clarity" badge is driven by `api/services/gemini_ready.py:compute_spec_readiness()` (lines 288–335). It checks **5 things** — completely separate from the 10-section audit. A spec can score 0/10 on structure and still pass these checks if it has the right content.

### The 5 checks

| Check name | What it looks for | Signal quality |
|---|---|---|
| `has_ac_checkboxes` | At least one `- [ ]` line anywhere in the file | **Real.** No AC = Build pipeline has nothing to decompose. Always legitimate. |
| `no_vague_ac` | No AC line contains: `TBD`, `?`, `should we`, `TODO`, `maybe`, `discuss`, `clarify/clarification`, `figure out`, `we'll see`, `decide what` | **Real.** These tokens mean the AC isn't yet a decision — it's a question. Agents can't act on questions. |
| `has_file_paths` | At least one file path (`foo.py`, `bar.tsx`) in the body resolves to a real file on disk | **Noisy for early specs.** High-level or design specs legitimately haven't named files yet. Fires too aggressively before scope is settled. For implementation-ready specs it's a solid signal. |
| `referenced_files_exist` | At least 50% of file paths in the body resolve to real files | **Real but dependent.** When `has_file_paths` fails, this also fails — same root cause, two badges. When `has_file_paths` passes, this adds meaningful signal (catches stale paths after renames). |
| `in_repo_scope` | Title doesn't start with "upstream:" and body doesn't say "upstream ostk / external repo / different repo" | **Real but narrow.** When it fires, it's always a legitimate flag. Very rare false positives. |

**Source:** `api/services/gemini_ready.py`, lines 65–80 (regex definitions), lines 288–335 (`compute_spec_readiness`).

### What is noise

The `has_file_paths` check fires on any spec that doesn't yet name real files. This is normal for specs in early/design stage — not a signal that the spec is unclear, just that it's early. Recommendation for Phase 3: add a `stage` field to specs so the clarity check can skip `has_file_paths` for specs still in "design" stage.

---

## 4. Existing-spec audit

### Current spec inventory (as of 2026-05-22)

Spec directories: `~/.myos/specs/` and `docs/draft/`.  
Sources: file reads this session + `docs/draft/_review/2026-05-19-spec-audit-results.md`.

| Spec | Location | AC items | Template score | Likely failing checks | Badge real? |
|---|---|---|---|---|---|
| `pattern-watcher-v2.md` | `~/.myos/specs/` | 1 (placeholder) | ~7/10 | `has_file_paths` (refs `api/services/pattern_watcher.py` which doesn't exist yet) | **Noisy** — spec is intentionally deferred; the flag is technically correct but the spec is frozen by design |
| `spec-auto-status.md` | `~/.myos/specs/` | **0** | 0/10 | `has_ac_checkboxes` | **Real.** No AC = can't build. Non-standard headings: "Objective", "Background & Motivation", "Proposed Solution", "Open Decisions" — audit tool sees nothing. Full design exists, just needs AC extraction. |
| `vp-marketing-first-impression.md` | `~/.myos/specs/` | 20 | 0/10 | likely `referenced_files_exist` (references future files like `app/src/pages/Library.tsx`) | **Partially real** — file paths reference things not yet built, which is expected for a design spec |
| `user-memory-store-improvements.md` | `~/.myos/specs/` | many | ~3/10 | `has_file_paths` passes (refs `memory_trigger.py`, `user_memory_store.py`, `MemoryToast.tsx` — all exist from v1) | **Clean** — likely passes all 5 checks |
| `spec-drawer-hygiene-stage-as-state.md` | `~/.myos/specs/` | many | ~3/10 | `has_file_paths` passes (refs `api/services/spec_audit.py`, `api/routers/specs.py` — both exist) | **Clean** |
| `team-mode-plan.md` | `~/.myos/specs/` | 36 | 3/10 | `in_repo_scope` unlikely; AC lines are mostly concrete | **Clean** |
| `pattern-watcher.md` | `~/.myos/specs/` | 13 | 1/10 | `has_file_paths` may fail (no `api/services/pattern_watcher.py` yet) | **Noisy** — same situation as v2 |
| `docs/draft/pattern-watcher-v2.md` | `docs/draft/` | **0** | 0/10 | `has_ac_checkboxes`, `has_file_paths`, `referenced_files_exist` | **Real** — this file is a husk (frontmatter only, 5 lines). Husk detector in `spec_audit.py:compute_husk_status` catches it. |
| `docs/draft/user-memory-store-improvements.md` | `docs/draft/` | **0** | 0/10 | same as above | **Real** — also a husk. |

**Key structural finding:** The badge fires on 5 checks that are independent of the 10-section template. Specs that score 0/10 on structure (like `spec-auto-status.md`) can still pass the badge checks if they have AC — and conversely, well-structured specs can fail the badge due to missing file paths. These two systems are not connected. The root confusion is that tori sees a "Needs clarity" badge but the badge doesn't tell her which canonical sections are missing.

**Source quotes:**
- `spec_audit.py:57-66` — `TEMPLATE_SECTIONS = ["Problem", "Goals", "Non-goals", "Solution", "Edge cases", "Success criteria", "Acceptance criteria", "Verification", "USER FEEDBACK", "DECISION"]`
- `gemini_ready.py:92-96` — `_SPEC_CHECK_NAMES = ["has_ac_checkboxes", "no_vague_ac", "has_file_paths", "referenced_files_exist", "in_repo_scope"]`

---

## 5. Plan to fix the AI suggest button

### Root cause (confirmed from source)

The bug is in `app/src/components/NeedsClarityChip.tsx`, in the `handleSuggest` function. There are two failure modes:

**Mode 1 — Error silently swallowed.** The catch block on lines 73–76 does nothing:
```javascript
} catch {
  // user can type manually
}
```
When the API call fails (e.g., no Anthropic API key configured, or network error), the error is caught and discarded. The button shows "Thinking…" and then returns to "AI suggest" with no explanation. The user has no idea whether the call was made, failed, or was skipped.

**Mode 2 — Early return with no feedback.** If `specPath` is falsy and `mode` is `'spec'`, the code hits the `else { return }` branch (line 69). The `finally` block executes immediately, resetting `suggesting` to null. Same symptom: button flashes "Thinking…", goes back to "AI suggest", textarea stays empty.

The backend endpoint at `api/routers/specs.py:1070` is correctly implemented and returns `{ proposed_fix, rationale }`. The `clarity_suggest.py` service has prompt templates for all 6 check types. When there's no API key, the backend returns a `400` with the message `"No Anthropic API key configured. Add one in Settings to use AI suggestions."` — which is useful, but the frontend never shows it.

### What the fix looks like (Phase 3)

1. Add a per-check error state: `const [suggestErrors, setSuggestErrors] = useState<Record<string, string>>({})`.
2. In the catch block, extract the error message and store it: `setSuggestErrors(e => ({ ...e, [checkName]: errorText }))`.
3. Render the error below the "AI suggest" button when `suggestErrors[check.name]` is set.
4. Clear the error when the user types in the textarea.
5. For the "no API key" case, the error message should link to Settings.

**The API key error path:** `clarity_suggest.py:191-193` raises `ValueError("No Anthropic API key configured. Add one in Settings to use AI suggestions.")` which becomes `HTTPException(400, detail=...)`. The frontend `api.post()` rejects on non-2xx; the catch block needs to read `err.message` or `err.detail` from the response.

---

## 6. Plan for placeholder copy

Current placeholder for every check: `"Provide the missing information…"` — completely generic.

The `clarity_suggest.py` service already has 6 distinct AI prompt templates, one per check. The textarea should show a matching hint so tori knows what shape of answer to provide, whether or not she uses AI suggest.

### Proposed hint text by check

| Check | Hint text |
|---|---|
| `has_ac_checkboxes` | `Add acceptance criteria — one per line: - [ ] When X, the result is Y` |
| `no_vague_ac` | `Rewrite vague lines to be specific and testable. Remove: TBD, ?, TODO, maybe, discuss` |
| `has_file_paths` | `List the files this spec will touch: api/routers/foo.py, app/src/components/Bar.tsx` |
| `referenced_files_exist` | `Fix broken file paths — check spelling or recent renames` |
| `in_repo_scope` | `Clarify how this work lives in the current repo (not upstream or an external system)` |
| `outcome_concrete` | `State exactly what will be built or changed — no TBD, no vague qualifiers` |

### Implementation (Phase 3)

Add a `CHECK_PLACEHOLDER` map to `NeedsClarityChip.tsx` (parallel to the existing `CHECK_LABEL` map at line 26) and wire it to the `placeholder` prop of the textarea. Two lines of code.

---

## 7. "Use template" scaffolding — recommendation

**Recommendation: Yes — prefill canonical headings when creating or promoting a spec.**

### Justification

The audit data shows the structural problem is at creation time. `spec-auto-status.md` used "Objective" / "Background & Motivation" / "Proposed Solution" — reasonable names, but invisible to the audit tool and to the Build pipeline. `vp-marketing-first-impression.md` used numbered "Build blocks" instead of "Acceptance criteria". These weren't mistakes; the authors just wrote what felt natural. Without seeing a template, every spec author invents their own structure.

Prefilling the 10 canonical headings (with placeholder text under each) at creation time costs zero ongoing maintenance: it's a static string. It prevents structural drift before it starts. Tori deletes sections she doesn't need; she doesn't have to remember what sections exist.

### Where to apply it

- `POST /api/specs/draft` (creates a new draft) — return a body that starts with the 10 sections pre-populated.
- `POST /api/specs/{path}/promote` (promotes a draft to spec) — if the body is missing any canonical headings, append them at the end with a placeholder.
- `api/services/spec_templates.py` already exists (`ls api/services/` confirms it). Read this file before implementing — it may already have template content.

### Template content

```markdown
## Problem

<!-- What is broken or missing? Who is affected? -->

## Goals

<!-- What does success look like? -->

## Non-goals

<!-- What are we explicitly NOT doing? -->

## Solution

<!-- How will we solve it? -->

## Edge cases

<!-- What failure modes, unusual inputs, or race conditions matter? -->

## Acceptance criteria

- [ ] 

## Verification

<!-- How do we confirm the AC are met? (test command, smoke test, manual steps) -->

## USER FEEDBACK

*(Reserved for tori's overrides and decisions.)*

## DECISION

*(Final calls on open questions will be recorded here.)*

## References

<!-- Related specs, needles (→NNN), or files -->
```

---

## Executive summary

1. **AI suggest button is broken by a silent catch.** The endpoint works, the service works, the AI prompts are good. The frontend swallows all errors with `// user can type manually`. Fix is: add per-check error state and render the error message so tori knows why nothing happened.

2. **The textarea placeholder is useless.** "Provide the missing information…" gives zero guidance. Six check types need six distinct hints telling tori what shape of answer to provide. Fix is: add a `CHECK_PLACEHOLDER` map (parallel to `CHECK_LABEL`) and wire it to the textarea's `placeholder` prop.

3. **Two clarity systems exist independently and don't talk to each other.** The "Needs clarity" badge checks 5 things (AC, vague tokens, file paths, repo scope). The audit tool checks 10 template sections. A spec can score 0/10 on structure and still pass the badge. This is the root cause of structural inconsistency — specs that look wrong are never flagged as wrong.

4. **`has_file_paths` fires too aggressively on early-stage specs.** It correctly signals "no real files referenced" but fires on design specs where that's expected. Not urgent but noisy. Fix requires a spec lifecycle stage field (Phase 3+).

5. **Template scaffolding at creation time is the durable fix.** Prefilling the 10 canonical section headings when a spec is drafted prevents structural drift before it starts. `api/services/spec_templates.py` already exists — read it before implementing to avoid duplication.

---

## Phase 3 work items (gated on tori approval)

Once tori approves this proposal, the three problems can be fixed in this order (each is independent):

**P1 — Fix AI suggest button** (1 file, ~15 lines):
- `app/src/components/NeedsClarityChip.tsx`: add `suggestErrors` state, populate it in the catch block, render per-check error below the button.

**P2 — Fix textarea placeholder copy** (1 file, ~10 lines):
- `app/src/components/NeedsClarityChip.tsx`: add `CHECK_PLACEHOLDER` map (6 entries), wire to `placeholder` prop on the textarea.

**P3 — Template scaffolding at creation time** (2–3 files):
- Read `api/services/spec_templates.py` first — it already exists and may have template content.
- `POST /api/specs/draft` and `POST /api/specs/{path}/promote` — inject the 10-section template when body is missing headings.
- Optional: add a "what does this spec produce?" step to the wizard (from `vp-marketing-first-impression.md` spec).

---

**READY FOR TORI REVIEW**
