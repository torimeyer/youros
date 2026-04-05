Implement HSCP v0.1 — the llmOS text compression codec.

The spec is at docs/draft/llmos-compression.md. Read it first.

**Design principle (binding):** Intent over terseness. Parse safety over compression ratio.
Every encoding must be readable without a lookup table.

Implement in this order:

1. **G1 — Needle sigil** (already done, confirm): `→628` used everywhere, `needle:` prefix dropped.
   Verify in src/kernel/digest.rs and audit output.

2. **G2 — Agent tuples in digest** (src/kernel/digest.rs):
   Change `[procs]` output from `agent-1:active:0s` to `a1:active:0s:12%`
   Format: `a{N}:{status}:{age}:{ctx}%` where N is the numeric part of the alias.
   Find where digest.rs builds the [procs] line and apply G2.

3. **G4 — Intra-session delta** (src/kernel/digest.rs):
   Add delta tracking to DigestState (last procs emission).
   On turns 2-4: emit `[procs:Δ]` with only changed agents.
   On turn 5 and every 5 turns: emit full `[procs]` (flush).
   On session start: always full.

4. **boot.md — codec block** (src/commands/boot.rs or shutdown.rs):
   Append `[hscp:v0.1]` block to boot.md output:
   ```
   [hscp:v0.1]
   rules: G1 G2 G4
   session-date: <today>
   delta-scope: intra-session
   delta-flush: 5
   ```

5. **Tests** (add to src/kernel/digest.rs tests):
   - G2: agent tuple format is `a1:active:0s:12%` not `agent-1:active:0s`
   - G4: second emission of same procs is delta, fifth is full
   - Intent principle: no encoding requires a separate lookup table to decode

Run cargo check after each step. Run cargo test --lib after all steps.
Close →630 when acceptance criteria in the spec pass.
