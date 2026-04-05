Implement →657 EXTENDS + →658 PROMPT_ARG + ostk run --prompt flag.

Read first:
- docs/spec/agentfile-dispatch.md
- src/agentfile/parser.rs
- src/commands/run.rs
- src/main.rs (Commands enum)

## Part 1: Parser (src/agentfile/parser.rs)

Add to Agentfile struct:
  pub extends: Option<String>,
  pub prompt_arg: bool,
  pub runtime: Option<String>,

Parse:
  EXTENDS <path>   -> extends = Some(path)
  PROMPT_ARG       -> prompt_arg = true
  RUNTIME <value>  -> runtime = Some(value)

Add resolve_extends(af: &Agentfile, base_dir: &Path) -> Result<Agentfile, String>:
  - Load base from extends path
  - Merge: child FROM/BOOT/WORK wins if set, else base
  - TOOL/SKILL/LIMIT: union (child appends to base)
  - prompt_arg: inherited if base has it and child has no PROMPT
  - Max depth 3, error on cycle

## Part 2: ostk run --prompt (src/commands/run.rs)

Add optional --prompt <text> to Run subcommand.
If prompt_arg=true and --prompt provided: prepend to prompt list.
If prompt_arg=true and no --prompt: return error with usage.
If no prompt_arg: ignore --prompt (warn only).

## Part 3: Agentfile.default bootstrap

In ostk init (find the init command): write .ostk/Agentfile.default if not exists:
  FROM auto
  BOOT ostk boot --bail
  TOOL sh_run
  LIMIT budget_usd 3
  PROMPT_ARG

## Tests
- test_parse_extends
- test_parse_prompt_arg
- test_resolve_extends_merge: child FROM overrides, TOOLs union
- test_resolve_extends_max_depth: depth > 3 = error
- test_run_prompt_arg_injection
- test_run_prompt_arg_missing: error when no --prompt

cargo test — all existing + new tests must pass.
