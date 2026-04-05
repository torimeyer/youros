You are participating in a multi-model design debate about llmOS text compression (→630).

## The problem

ostk OS state is semi-structured and highly repetitive:
- `[procs] agent-1:active:0s agent-2:stale:45s` (repeated every turn)
- `→628`, `→629`, `→620` (needle IDs, repeated constantly)
- `:compile`, `:boot`, `:delegate` (tack verbs, repeated constantly)
- `2026-03-12T18:16:40Z` (timestamps, high entropy but structured)

The LLM must both ENCODE output (write compressed OS state) and DECODE input (read compressed OS state) without fidelity loss. This is different from VT100 strip (which is lossy for display) — this must be lossless for semantics.

## The thesis to debate

**Proposed codec:**
1. **Delta encoding** — only emit changes from previous OS state. `[procs:delta] agent-2:stale` instead of full procs list each turn.
2. **Symbol table** — HUMANFILE registers shorthand: `{a1}=agent-1`, `{n628}=→628`. LLM uses symbols in output.
3. **Tack grammar IS the codec** — `:compile` is already compressed intent. Extend tack to cover OS state emissions.
4. **Digest as diff** — `[ctx] +3n -1a` means "3 needles added, 1 agent removed since last turn".

**The problem:**
- LLM cannot maintain a persistent symbol table across sessions (Law 2: agents ephemeral)
- Codec must be in HUMANFILE (human-defined) or boot.md (kernel-defined) — not in agent memory
- Gemini and Claude may have different compression preferences / training distributions

## Your role

You are the Claude side of this debate. Gemini is the other participant (simulated in this session via a second Agentfile). Your job:

1. Propose a concrete codec design that solves the encode+decode problem
2. Identify what Gemini's objections will likely be (different training, different token costs)
3. Draft a spec section for llmOS compression that both models can implement
4. Output: `docs/draft/llmOS-compression.md` — your position paper

The negotiate protocol applies: your output is an OFFER. Gemini counter-offers. The merged spec is what ships.

## Constraints

- The codec must be stateless per-session (reconstructable from HUMANFILE + boot.md)
- Must not require model-specific fine-tuning — works with base models
- Must compound with existing fcp-ostk tack grammar
- Must be negotiable — if Gemini proposes a better approach, yield
