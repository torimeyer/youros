Implement →659: ostk dispatch →NNN command.

Read first:
- docs/spec/agentfile-dispatch.md
- docs/spec/spawn-primitive.md
- src/commands/run.rs
- src/agentfile/parser.rs (after →657/→658 land)
- src/main.rs Commands enum

## src/commands/dispatch.rs (new file)

pub fn run_dispatch(needle_id: &str, dry_run: bool) -> Result<(), String>

Steps:
1. Load needle from .ostk/needles/issues.jsonl (normalize →NNN or NNN)
2. Glob .ostk/Agentfile.* — parse each, skip failures with warning
3. Score by WORK tag intersection with needle tags
4. Select highest score, newest file breaks ties
5. Fall back to .ostk/Agentfile.default — error if missing with: 'run ostk init'
6. Compose prompt: needle title + '\n\n' + acceptance criteria
7. dry_run: print selection + command, exit 0
8. Otherwise: exec ostk run <agentfile> --prompt <content>

Output:
  dispatching →NNN to Agentfile.rust (score: 2/3 tags)
  model: claude-sonnet-4-6  budget: $3.00
  [dry-run] ostk run .ostk/Agentfile.rust --prompt '...'

## src/main.rs: add Dispatch subcommand

Commands::Dispatch { needle_id: String, dry_run: bool }
  --dry-run flag

## Tests
- test_dispatch_tag_matching
- test_dispatch_fallback_to_default
- test_dispatch_dry_run
- test_dispatch_needle_not_found

cargo test — all existing + new tests must pass.
