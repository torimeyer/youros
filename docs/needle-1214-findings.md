# →1214: Fleet-Active Gate Self-Filter — Findings

**Date:** 2026-05-12
**Source located:** `~/claude/torios/haystack-main/` (Cargo.toml version label: 2.3.0; installed binary: 6.0.5)

## Root Cause

The fleet-active gate in `src/serve/dispatch.rs:478–495` calls `count_fleet()` to decide
whether to block a git-state-mutation command. `count_fleet()` counts **all** rows in
`agents.jsonl` where `status == "active"` and `last_seen` is within 90 seconds — including
the operator's own Claude session row. In a solo session (no peer agents), the operator's
own row triggers `active > 0`, which fires the gate and blocks the git command.

## Exact Locations

### Gate call site
`src/serve/dispatch.rs:485`
```rust
let (active, _) = crate::commands::helpers::count_fleet(&agents_path);
if active > 0 {
    return Err(ToolError::new(...));  // blocks git op
}
```

### Counter function
`src/commands/helpers.rs:214` — `pub fn count_fleet(path: &Path) -> (usize, usize)`

Filters: `status == "active"` AND `last_seen` within `FLEET_ACTIVE_STALENESS_SECS` (90s).
No self-exclusion. Counts every active row including the calling session itself.

## Proposed Fix

The dispatch context already holds the current session alias at `self.state.agent_alias`
(a `RwLock<Option<String>>`), readable via the existing `self.get_agent_alias().await`
helper (dispatch.rs:439–440).

**Three-part change:**

1. **`src/commands/helpers.rs`** — add `self_alias: Option<&str>` parameter to `count_fleet`;
   add filter clause `&& Some(a.alias.as_str()) != self_alias` inside the active count.

2. **`src/serve/dispatch.rs:481–488`** — at the gate call site, fetch the alias and pass it:
   ```rust
   let self_alias = self.get_agent_alias().await;
   let (active, _) = crate::commands::helpers::count_fleet(
       &agents_path, self_alias.as_deref()
   );
   ```

3. **Update all callers** of `count_fleet` (status.rs, bootloader, tests) to pass `None`
   where the self-alias is not relevant.

`myos-api-*` system rows do not need special treatment: they have `status == "active"` and
should continue to block (they represent live backend processes writing state). Only the
calling session's own row should be skipped.

## Existing Bypass

`OSTK_SKIP_GIT_GUARD=1` already bypasses the gate entirely (dispatch.rs:482). This is
documented as "operator use only" but is the current workaround for solo-session git ops.
The self-filter fix eliminates the need for this workaround in normal solo use.

## What Step 1 Found

| Location checked | Result |
|---|---|
| `~/.youros/sync_repo/haystack-main/Cargo.toml` | Not present (empty file) |
| `~/Downloads/haystack-main/Cargo.toml` | v2.3.0 — contains gate source |
| `~/claude/torios/haystack-main/Cargo.toml` | v2.3.0 — contains gate source (same) |
| `which tori` | Not found |
| Installed binary: `ostk --version` | 6.0.5 |

Source version label (2.3.0) does not match installed binary (6.0.5). The gate code is
present in the local source tree and matches the installed behavior. The Cargo.toml version
is not being bumped with each release cycle.

## Next Steps (separate Task pass)

1. Implement the three-part change above in `~/claude/torios/haystack-main/`
2. Build with `cargo build --release` and install to `~/.local/bin/ostk`
3. Add a regression test in `helpers.rs` tests: solo session (1 active row matching
   self_alias) should return active=0 from the gate's perspective
4. Verify with `ostk --version` after install
