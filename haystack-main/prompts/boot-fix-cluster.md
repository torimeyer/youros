Implement →651 + →661 + →663 — the boot orientation fix cluster.

These three needles are one implementation unit. They fix the harness jail.

Read first:
- src/commands/boot.rs
- src/main.rs (--agents flag, agents guide text, detect_os_name)

## →651: Harness detection at boot

In boot.rs (or wherever boot.md is generated/printed), detect harness type:
  - TERM_PROGRAM=claude → harness: claude-code
  - HAYSTACK_SERVE=1 → harness: ostk-serve
  - CI=true or GITHUB_ACTIONS=true → harness: ci
  - Default → harness: terminal

Print at the top of ostk boot output:
  harness: claude-code
  tool pattern: ostk -c "cmd" via Bash

## →661: ostk --agents content in boot.md

When ostk boot regenerates or prints boot context, append:

  ## Agent guide
  [full output of ostk --agents]

The agents guide text is already in the binary. Find it in main.rs (search
for the --agents handler). Include it verbatim as the ## Agent guide section
in boot output.

## →663: Shim hint — Claude Code harness section in --agents

In the --agents guide text (in main.rs), add a new section:

  ## Claude Code Harness

  When running inside Claude Code (TERM_PROGRAM=claude), the correct
  tool invocation is:

    ostk -c "cmd"    via the native Bash tool

  NOT mcp__ostk__sh_run tool calls.
  NOT cat / grep / Read / Edit native tools.

  The shim intercepts transparently:
    Bash tool → ostk -c → kernel routes, audits, compresses

  Examples:
    ostk -c "boot"
    ostk -c "cat src/main.rs"
    ostk -c "needle list --open"
    ostk -c "cargo test 2>&1 | tail -5"

  This section appears only when ostk boot detects harness: claude-code.

## Tests
- test_boot_detects_harness: ostk boot output contains "harness:" line
- test_agents_guide_has_shim_hint: --agents output contains "Claude Code Harness" section
- test_boot_includes_agents_guide: boot output contains "## Agent guide"

cargo test after. All existing tests must pass.
