Fix failing tests and add missing coverage for v1.3 additions.

## Step 1: Fix 6 failing TUI pane cycle tests

Run `cargo test 2>&1 | grep FAILED` to see the list.

The cycle changed from `Identity→Fleet` to `Identity→Bench→Fleet` (→502 bench pane added).
All assertions expecting `Identity.next() == Fleet` must become `Identity.next() == Bench`.
All assertions expecting 8 tabs to reach Fleet must add one more tab (through Bench).

Files to fix: `src/tui/mod.rs` — search for `Pane::Fleet` in test assertions near Identity.

## Step 2: Add missing coverage for new functionality

### BOOT directive (src/agentfile/parser.rs)
- test: `BOOT ostk boot --bail` parses → `af.boot_cmd == Some("ostk boot --bail")`
- test: Agentfile without BOOT → `af.boot_cmd == None`
- test: BOOT with empty arg → ParseError::MissingArgument

### context_pct in ServerState (src/serve/state.rs)
- test: `set_context_pct(90.0)` → `get_context_pct() == 90.0`
- test: `set_context_pct(0.0)` → `get_context_pct() == 0.0`
- test: default is 0.0

### Bench pane renders (src/tui/mod.rs)
- test: `app.focused_pane = Pane::Bench` → draw doesn't panic (headless TestBackend)
- test: `app.bench_results` is empty by default (no bench/results/ dir in temp project)

### Scheduler orient (src/commands/scheduler.rs) — already has 7 tests, add:
- test: `orient` with `.? query` → `SchedulerIntent::Query`
- test: `orient` with empty string → `SchedulerIntent::Idle`

## Step 3: Verify

Run `cargo test 2>&1 | tail -5` — must show 0 failures.
Run `cargo install --path . --root ~/.local 2>&1 | tail -2`.
