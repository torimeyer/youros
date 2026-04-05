Write docs/spec/agentfile-dispatch.md — the Agentfile-as-process-descriptor spec.

Read first:
- docs/spec/spawn-primitive.md
- docs/spec/unix-to-ostk.md (Agentfile = Executable row)
- docs/spec/llmos-concurrency.md (Agentfile as TCB)
- docs/spec/smp-architecture.md
- docs/spec/ostk-compile.md

Spec must cover:

## 1. Agentfile as process descriptor
Unix analog: executable + TCB. Agentfile defines the execution environment.
PROMPT is the entry point (main()). Agentfile.default is init — always present.

## 2. EXTENDS directive (→657)
EXTENDS Agentfile.base — inherit FROM/BOOT/TOOL/LIMIT from base.
Child overrides any field. Resolution: child > parent > defaults.
No circular extends. Max depth: 3.

## 3. PROMPT_ARG directive (→658)
Marks runtime injection point.
ostk run Agentfile.default --prompt 'text' fills it.
Without --prompt and no fallback PROMPT: error with usage message.
Only one PROMPT_ARG per chain.

## 4. RUNTIME directive (→656)
RUNTIME native|docker|firecracker
native: in-process (default, v1.4)
docker: containerized (v1.5)
firecracker: microVM, hardware isolation (bench/CI)

## 5. ostk dispatch (→659)
ostk dispatch →NNN:
1. Read needle content
2. Load all .ostk/Agentfile.* and parse
3. Match by WORK tag intersection, score, select best
4. Fall back to Agentfile.default if no match
5. Inject needle as PROMPT_ARG, spawn

## 6. Agentfile.default (→660)
Created by ostk init. Always present.
FROM auto / BOOT ostk boot --bail / TOOL sh_run / LIMIT budget_usd 3 / PROMPT_ARG

## 7. Unix mapping additions
Add to docs/spec/unix-to-ostk.md:
- EXTENDS = .so inheritance / shared library
- PROMPT_ARG = argv[]
- RUNTIME = exec environment (bare metal / container / VM)
- ostk dispatch = exec() + scheduler

## Acceptance criteria
- [ ] EXTENDS resolves max 3 deep
- [ ] PROMPT_ARG injection via ostk run --prompt
- [ ] Missing PROMPT_ARG + no --prompt = clear error
- [ ] ostk dispatch falls back to Agentfile.default
- [ ] Agentfile.default created by ostk init

Spec only — no code changes. No cargo test needed.
