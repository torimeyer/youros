---
status: draft
Task: "→1525"
fr: FR-014
---

# ostk doc promote: docs/spec/ blocked as source (→1525, FR-014)

## What shipped

`ostk doc promote` now explicitly rejects any source path inside `docs/spec/`.

Previously the command had a single guard (`must contain docs/draft/`), which
already blocked `docs/spec/` paths in practice, but gave a generic error and
had no test coverage for this specific case.

## Change

- Added `validate_promote_source(path_str)` helper in
  `haystack-main/src/commands/promote.rs` (commit `1477e49`).
- The helper returns a distinct, actionable error when the source is under
  `docs/spec/`:
  `Cannot promote from docs/spec/ — source must be a draft inside docs/draft/`
- Added 4 unit tests covering: `docs/spec/` rejection, absolute path rejection,
  `docs/draft/` acceptance, and unrelated path rejection.

## Verification

```
cargo test commands::promote   # 14 passed; 0 failed
```

Manual smoke test:
```
ostk doc promote docs/spec/foo.md
# error: Cannot promote from docs/spec/ — source must be a draft inside docs/draft/
```
