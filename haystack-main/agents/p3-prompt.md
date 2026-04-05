You are a Rust systems engineer working on ostk, an LLM agent coordination kernel.

## Your task: P3 — Agentfile TOOL capability class patterns

### Context
The approval policy chain spec is at docs/spec/approval-policy-chain.md — read it first.

P0 has already landed:
- `CapabilityClass` enum in src/cpu/mod.rs with 11 variants and labels like "file:read", "shell:write", etc.
- `detect_capability_class(tool_name, input)` function that classifies tool calls

### What to implement
Extend the Agentfile parser to accept capability class syntax in the TOOL directive:

Current syntax:
  TOOL shell
  TOOL file:read
  TOOL file:edit

New syntax (additive — old syntax still works):
  TOOL shell:read          — approve all shell:read calls
  TOOL shell:write(src/)   — approve shell:write targeting paths under src/
  TOOL file:edit            — approve all file edits (already works as tool name)
  TOOL kernel:read         — approve kernel read operations

The parser should:
1. Check if the TOOL value contains a colon — if so, parse as capability class
2. Extract optional pattern in parens: "shell:write(src/)" → class=ShellWrite, pattern=Some("src/")
3. Store parsed patterns in a new field on the Agentfile struct
4. Validate that the class label is a known CapabilityClass

The Agentfile parser is in src/agentfile/ — read it to understand the current structure.

For the approval gate integration (src/cpu/agent_loop.rs), add Step 6 logic:
- After the mode gate and session allow-list, check Agentfile patterns
- If the classified tool call matches an Agentfile pattern, ALLOW
- Privileged classes (ShellExec, ShellSecret, KernelSpawn) require a signed Agentfile (check the `signed` field or equivalent)

### Important constraints
- NEVER run cargo test without `--ignore pty` or filtering to specific tests — PTY tests hang for 5+ minutes
- Write tests for the parser changes
- Run `cargo check` frequently
- Don't break existing TOOL directive parsing — the old "TOOL shell" syntax must keep working
- Read the existing parser code before writing anything
