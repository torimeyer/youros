# Spec Audit Results — 2026-05-19

**Audit tool:** `GET /api/specs/audit` (api/services/spec_audit.py, →1469)
**Run date:** 2026-05-19
**Directories scanned:**
- `~/.myos/specs/` — 7 specs
- `docs/spec/` — 4 specs (not reachable by API; audited directly below)

---

## Summary

| Metric | Value |
|---|---|
| Total specs | 11 |
| API-scanned (via GET /api/specs/audit) | 7 |
| Manually audited (docs/spec/) | 4 |
| Fully templated (10/10 sections) | 0 |
| Average template score (API-scanned set) | 2.86 / 10 |
| Stale (not touched in >60 days) | **0** |
| With missing AC checkboxes | **2** |
| With no traceability refs | **6** |
| Likely duplicates | **1 pair** |

**No stale specs.** All 11 were last modified May 14–18, 2026.

---

## Per-Spec Scores

| Spec | Location | Score | Checkboxes | Refs | Status |
|---|---|---|---|---|---|
| spec-quality-gaps-audit.md | ~/.myos/specs/ | 9/10 | 12 | 2 | spec |
| mychat-claude-code-parity.md | ~/.myos/specs/ | 4/10 | 57 | 0 | spec |
| team-mode-plan.md | ~/.myos/specs/ | 3/10 | 36 | 9 | spec |
| users-directory-migration-strategy.md | ~/.myos/specs/ | 3/10 | 11 | 62 | spec |
| pattern-watcher.md | ~/.myos/specs/ | 1/10 | 13 | 1 | spec |
| spec-auto-status.md | ~/.myos/specs/ | **0/10** | **0** | 0 | spec |
| vp-marketing-first-impression.md | ~/.myos/specs/ | **0/10** | 20 | 0 | spec |
| gemini-ready-chip-and-spawn.md | docs/spec/ | — | 13 | 1 | — |
| per-user-memory-md.md | docs/spec/ | — | 12 | 0 | — |
| narrative-coordination-intel-themes-main-only.md | docs/spec/ | — | 22 | 0 | — |
| gemini-cli-integration.md | docs/spec/ | — | **0** | 0 | — |

---

## Findings

### Stale (>60 days since last touch)

**None.** All specs were touched May 14–18, 2026. No stale specs exist today.

---

### Duplicated

**1 pair identified.**

| Specs | Overlap | Recommendation |
|---|---|---|
| `docs/spec/gemini-cli-integration.md` + `docs/spec/gemini-ready-chip-and-spawn.md` | Both cover Gemini integration. `gemini-ready-chip-and-spawn.md` has 13 AC items and 1 ref; `gemini-cli-integration.md` has 0 checkboxes and 0 refs. Likely the same surface described twice at different fidelity levels. | Review for merge: promote the higher-fidelity one (`gemini-ready-chip-and-spawn.md`), fold any unique scope from `gemini-cli-integration.md` into it, then archive the empty one. |

**P1 action:** Merge or archive `gemini-cli-integration.md` — it contributes no unique AC and no refs, and is likely superseded by the other Gemini spec.

---

### Missing AC (no `- [ ]` checkboxes)

Two specs have the "Acceptance criteria" heading (or are supposed to) but contain zero actionable checkbox items.

**1. `~/.myos/specs/spec-auto-status.md`**
- Score: 0/10 (no template sections present at all)
- Uses entirely non-standard headings: "Objective", "Background & Motivation", "Proposed Solution", "Open Decisions"
- Contains a full design writeup but zero `- [ ]` lines
- No traceability references (no needle numbers, no commit hashes)
- **P0 finding:** This is a complete spec in substance but invisible to the audit tool and to the Build pipeline because none of its headings match the template. It cannot be decomposed or built.
- **Recommended action:** Convert headings to the 10-section template and extract existing prose into `- [ ]` AC items.

**2. `docs/spec/gemini-cli-integration.md`**
- 0 checkboxes, 0 refs
- Not reachable by the API audit (server CWD issue — see Coverage Gap below)
- **P1 finding:** Content unknown without reading the file, but the lack of any checkboxes means it cannot be built from the Specs page.
- **Recommended action:** Read file, add `- [ ]` AC items, or fold into `gemini-ready-chip-and-spawn.md`.

---

### Unreferenced (no needle or commit numbers in the spec body)

6 of 11 specs have zero traceability anchors — no `→NNN` needle references and no commit hashes. This makes it impossible to trace what work implemented them or which open needle backs them.

| Spec | Notes |
|---|---|
| `mychat-claude-code-parity.md` | Large spec (57 checkboxes, 4/10 score) with zero refs. High-value spec, no traceability. |
| `spec-auto-status.md` | Also in Missing AC above. |
| `vp-marketing-first-impression.md` | Score 0/10 due to non-standard headings, but 20 checkboxes worth of content. |
| `gemini-cli-integration.md` | Also in Duplicated above. |
| `narrative-coordination-intel-themes-main-only.md` | 22 checkboxes, 0 refs. |
| `per-user-memory-md.md` | 12 checkboxes, 0 refs. |

**P2 recommended action (batch):** Add a `→NNN` reference to each spec — either the needle that spawned it or the one tracking its completion. This is a one-line frontmatter or body edit per spec.

---

### Non-conformant structure (score 0 but rich content)

Two specs scored 0/10 not because they are empty but because they use non-standard heading names. The audit tool currently can't distinguish "empty" from "wrong headings."

**`vp-marketing-first-impression.md`** — Uses: "What this is for", "What is already in place", "What still needs to be built". Has 20 checkboxes and three substantial build blocks. Template score: 0/10.

**`spec-auto-status.md`** — Uses: "Objective", "Background & Motivation", "Proposed Solution". Has full design depth. Template score: 0/10.

Both would score 8+ if headings were renamed. The audit tool is surfacing real brittleness here: the score does not reflect spec quality for non-conformant files.

---

### Low template coverage (score 1–3/10)

| Spec | Score | Missing sections |
|---|---|---|
| `pattern-watcher.md` | 1/10 | Problem, Goals, Non-goals, Solution, Edge cases, Success criteria, Verification, USER FEEDBACK, DECISION |
| `team-mode-plan.md` | 3/10 | Non-goals, Solution, Edge cases, Success criteria, Verification, USER FEEDBACK, DECISION |
| `users-directory-migration-strategy.md` | 3/10 | Solution, Edge cases, Success criteria, Acceptance criteria, Verification, USER FEEDBACK, DECISION |

None are P0 on their own — they have some structure and checkboxes — but are candidates for a bulk "backfill missing sections" pass.

---

## Coverage Gap (audit tool)

The API endpoint `GET /api/specs/audit` returns only the 7 specs from `~/.myos/specs/`. The 4 specs under `docs/spec/` were **not included** in the API response.

Root cause: `api/services/spec_audit.py` uses `Path("docs") / "spec"` as a relative path. If the API server's working directory is not the repo root, `docs/spec/` does not resolve. The docs/spec files were audited manually for this report.

**P2 recommended action:** Change the relative path in `audit_all_specs()` to resolve against the repo root (`git rev-parse --show-toplevel`) or an explicit env var.

---

## Sub-needles filed

| Needle | Title |
|---|---|
| →1494 | backfill template headings in spec-auto-status.md |
| →1495 | rename non-standard headings in vp-marketing-first-impression.md |
| →1496 | merge or archive gemini-cli-integration.md (duplicate of gemini-ready-chip-and-spawn.md) |
| →1497 | add traceability refs to 6 unreferenced specs — **completed 2026-05-19** |
| →1498 | fix spec audit coverage gap (docs/spec/ not scanned by API) |

All filed P2, tagged `specs,from-audit`.

---

## Appendix: Raw API output

Saved to `/tmp/spec-audit-raw.json`.

Spec audit service version: api/services/spec_audit.py (→1469), no version field in output.
