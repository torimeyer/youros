You are implementing the llmOS concurrency model. The spec is docs/spec/llmos-concurrency.md — it is the authoritative source.

Implement in compounding order:

1. →628 thread states (§4.2): write .ostk/fleet/<alias>/state.yml on every tool call. States: init/ready/running/blocked/dying/idle/reaped. Read src/kernel/identity.rs for the alias system. Add state write to src/serve/server.rs tool dispatch path.

2. →620 dying notification (§4.5): at 90% context_pct, acquire .ostk/fleet/<alias>/dying.lock, write dying.md (needle, work_done, next_steps), fire nudge to scheduler.

3. →629 scheduler loop (§3): read .ostk/staging/ (diff + active.tack) + vault + fleet states. DECIDE: FROM auto + dying detection + reap. DISPATCH: BOOT --bail + PROMPT + execute. Add as ostk scheduler subcommand.

Run cargo check after each needle. Close each needle when its acceptance criteria from the spec pass.
