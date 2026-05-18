# →1472: promote backfiller patch

**Source**: `~/Downloads/haystack-main/src/commands/promote.rs`
**Haystack commit**: `823d839` (→1472: scaffold promote backfiller)

## What changed

Extended `ostk doc promote` (`run_verb` in `promote.rs`) to satisfy FR-007:

1. **Auto-backfill missing sections** — `backfill_sections()` checks the 10 required sections
   (Problem, Goals, Non-goals, Solution, Edge cases, Success criteria, Acceptance criteria,
   Verification, USER FEEDBACK, DECISION) and appends any that are absent with a one-line
   italic placeholder. No warning, no refusal.

2. **Warn on empty DECISION** — after backfill, if DECISION still contains only placeholder
   text, `eprintln!` warns before promoting. Does not block the promote.

3. **Warn on docs/spec/ path** — warns that the spec is landing in `docs/spec/` and suggests
   `~/.myos/specs/` (→1411). Does not block.

## Tests

10 unit tests inside `promote.rs` (`#[cfg(test)] mod tests`):

- `has_section_*` — exact match, case-insensitive, absent
- `is_section_empty_*` — placeholder content, real content, absent section
- `backfill_zero_section_draft_gets_all_ten` — 0-section draft → all 10 sections present
- `backfill_full_draft_is_noop` — full draft untouched, each header appears exactly once
- `backfill_partial_draft_adds_only_missing` — only the 8 missing sections added
- `backfill_preserves_original_content` — existing content not mangled

**Result**: `test result: ok. 10 passed; 0 failed` (via `cargo test commands::promote::tests`)
