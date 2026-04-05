You are a Rust systems engineer working on ostk, an LLM agent coordination kernel.

## Your task: P1 (shell classifier) + P2 (chain reorder + pin.caps integration)

### Context
The approval policy chain spec is at docs/spec/approval-policy-chain.md — read it first.

P0 has already landed:
- `CapabilityClass` enum in src/cpu/mod.rs with 11 variants (FileRead, FileEdit, FileWrite, ShellRead, ShellWrite, ShellExec, ShellSecret, KernelRead, KernelWrite, KernelSpawn, KernelSecret)
- `detect_capability_class(tool_name, input)` stub that classifies file tools precisely but defaults all shell to ShellExec
- `runtime_allowed` moved to AgentSession as Arc<Mutex<HashSet<String>>>
- Existing `detect_destructive()` in src/kernel/destructor.rs

### P1: Implement 4-layer shell classifier
Create src/cpu/classify.rs with the real `detect_shell_class()`:

Layer 1 — Operator scan: if command contains >, >>, |, $(), backticks → at least ShellWrite
Layer 2 — Known-dangerous flags: sed -i, perl -e, python -c, ruby -e → ShellExec; git checkout/reset/clean → ShellWrite; curl/wget/nc → ShellExec; gpg/ssh-keygen → ShellSecret
Layer 3 — Command allowlist: cat, ls, head, tail, wc, find, grep, ps, env, pwd, which, file, git log, git diff, git status, git branch → ShellRead (only if layers 1-2 didn't escalate)
Layer 4 — Default: ShellExec

Then update `detect_capability_class()` in src/cpu/mod.rs to call the new classifier for Bash/shell/sh_run tools instead of defaulting to ShellExec.

Also implement Auto mode in the approval gate at src/cpu/agent_loop.rs:~918. Currently only Governed mode has logic. Auto should:
- ALLOW: FileRead, FileEdit, FileWrite, ShellRead, KernelRead
- CONTINUE (fall to approval): ShellWrite, ShellExec, ShellSecret, KernelWrite, KernelSpawn, KernelSecret

### P2: Chain reorder + pin.caps + kernel:write demotion
In the approval gate (src/cpu/agent_loop.rs:~918):
1. Destructive check must run BEFORE the runtime_allowed check (currently it's nested inside the else-if for runtime_allowed — that means AlwaysAllow can bypass destructive for non-shell tools)
2. kernel:write should fall through to the mode gate, not auto-approve

For pin.caps integration, check src/kernel/policy.rs — if it exists, wire pin.caps deny checks into the approval gate at Step 1. If it doesn't exist yet, create the wiring. Pin.caps parse errors should DENY (fail-closed), not skip.

### Important constraints
- NEVER run cargo test without `--ignore pty` or filtering to specific tests — PTY tests hang for 5+ minutes
- Write tests for the classifier in classify.rs
- Run `cargo check` frequently to catch compilation errors early
- The existing detect_destructive in src/kernel/destructor.rs is your reference for pattern-matching style
- Be conservative: the classifier should over-promote (extra prompts) but never under-promote (security holes)
