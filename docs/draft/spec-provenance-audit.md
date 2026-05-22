# Spec Provenance Audit (→1603)

Auditing all spec files to trace origin and identify canonical 8-section gaps.
Canonical sections (8): Problem, Goals, Non-goals, Solution, Acceptance criteria, USER FEEDBACK, DECISION, References

| Spec | First commit / date | Origin | Canonical sections present | Canonical sections missing | Recommended fills | Status |
|------|--------------------|---------|-----------------------------|---------------------------|-------------------|--------|
| `~/.myos/specs/pattern-watcher-v2.md` | 2026-05-19 (promoted_at) | Plan `i-asked-gemini-to-tender-kernighan.md` | All 8 | None | — | **leave** |
| `~/.myos/specs/pattern-watcher.md` | 2026-05-14 (promoted_at) | Organic: threading meta-bug →1352 surfaced this design | All 8 | None | — | **leave** |
| `~/.myos/specs/spec-auto-status.md` | 2026-05-15 (promoted_at) | Organic: problem noticed when terminal agents didn't flip spec status | All 8 | None | — | **leave** |
| `~/.myos/specs/spec-drawer-hygiene-stage-as-state.md` | 2026-05-19 (promoted_at) | Plan `i-asked-gemini-to-tender-kernighan.md` | All 8 | None | — | **leave** |
| `~/.myos/specs/user-memory-store-improvements.md` | 2026-05-19 (promoted_at) | Plan `i-asked-gemini-to-tender-kernighan.md` | All 8 | None | — | **leave** |
| `~/.myos/specs/team-mode-plan.md` | 2026-05-18 (commit `112a7bd`) | Needle →1433, NR partnership CTO meeting prompted multi-user design | Problem, Goals, Solution, Acceptance criteria | Non-goals (embedded not headed), USER FEEDBACK, DECISION, References | Extract Non-goals from "Not in v1" list; add USER FEEDBACK (empty, design phase); DECISION covering architecture choices made; References list | **fill** |
| `~/.myos/specs/vp-marketing-first-impression.md` | 2026-05-14 (promoted_at) | Plan `my-vp-of-marketing-majestic-pony.md` (written 2026-05-01) | Problem, Goals, Non-goals, Solution, USER FEEDBACK, DECISION, References | Acceptance criteria (top-level section — ACs buried inside each Block) | Extract all `- [ ]` items from Blocks 1, 2, 3 into a single top-level AC section | **fill** |
| `docs/draft/spec-template-unification.md` | 2026-05-22 (commit `771fe4c`) | Agent 1599 research doc for →1599 spec unification | None — uses numbered sections not canonical headings | All 8 canonical headings | Convert numbered-section research doc to 8-section spec; content is rich, confidence medium | **fill** |
| `docs/draft/pattern-watcher-v2.md` | 2026-05-21 (commit `42af974`) | Husk — frontmatter only. Duplicate of `~/.myos/specs/pattern-watcher-v2.md` | None | All | Duplicate with real content at `~/.myos/specs/`; delete husk | **archive** |
| `docs/draft/user-memory-store-improvements.md` | 2026-05-21 (commit `42af974`) | Husk — placeholder AC only. Duplicate of `~/.myos/specs/user-memory-store-improvements.md` | None | All | Duplicate with real content at `~/.myos/specs/`; delete husk | **archive** |

## Summary

- **Total specs audited:** 10
- **leave:** 5 (already have all 8 canonical sections)
- **fill:** 3 (team-mode-plan, vp-marketing-first-impression, spec-template-unification)
- **archive:** 2 (both husks in docs/draft/ that duplicate live specs in ~/.myos/specs/)
- **needs-tori:** 0

## Fill confidence ranking

| Spec | Confidence | Why |
|------|-----------|-----|
| `vp-marketing-first-impression.md` | High | Single missing section; all AC items exist in blocks, just need extracting |
| `team-mode-plan.md` | High | Non-goals, DECISION content all present in spec body; DECISION and References derivable from text |
| `spec-template-unification.md` | Medium | Content is rich but this is a research doc, not a traditional feature spec — conversion requires judgment calls |

## Fill proposals

See `docs/draft/_review/spec-fills-1603/` for full proposed fills.
