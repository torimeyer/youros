---
title: ".language Resolution Internalization"
status: spec
version: 1
created: 2026-04-03
needles: ["→888", "→889", "→890", "→902", "→1152"]
compounds: ["→784 kernel daemon model"]
---

# .language Resolution Internalization

> CLI commands that fork+exec the same binary collapse into direct kernel function calls.
> .language becomes the unified resolution table. INIT becomes the boot-order spec.
> HUMANFILE/ENTITYFILE declare extension points — custom bootloaders per maintainer or LLM.

## Problem

55 of 75 .language verbs resolve to `ostk <cmd>` — forking and execing the same binary.
When an agent calls `:boot`, the kernel:

1. Receives MCP tool call
2. Shells out to `ostk boot`
3. Fork+exec creates a new process
4. New process re-parses CLI args
5. New process re-reads .ostk/ from disk
6. New process computes result
7. Result returned via stdout capture

Steps 2-6 are waste. The kernel already has the code. The state is already in memory.

## Current Resolution Map

```
Tier 0 (services):  12 verbs  → resolution: internal       ← already collapsed
Tier 1 (kernel):    ~15 verbs → resolution: ostk <cmd>     ← fork+exec to self
Tier 2 (user):      ~40 verbs → resolution: ostk <cmd>     ← fork+exec to self
FCP drivers:        2 verbs   → resolution: .ostk/drivers/  ← socket IPC (correct)
LLM-inferred:       6 verbs   → resolution: [inferred/ctrl] ← no execution
```

Tier 0 proves the pattern works. `:approval`, `:digest`, `:elision`, `:embeddings`,
`:heartbeat`, `:pitchfork` — all `internal`, all direct function calls, zero process spawn.

## Design

### Resolution Types

The `.language` resolution field supports four resolution strategies:

```
internal                    — kernel function call (same process, shared state)
fork:<alias>                — supervised child process (agents, long-running tasks)
socket:<path>               — MCP IPC to external driver (fcp-rust, fcp-web)
ostk <cmd>                  — CLI fallback (human terminal entry point only)
```

The migration path: every `ostk <cmd>` that has a corresponding `pub fn run()` in
`src/commands/` becomes `internal`. The CLI binary remains as a thin shim that calls
the same functions.

### Two Entry Points, One Kernel

```
Human (terminal):  ostk boot  → main() → match args → kernel::boot::run()
Agent (MCP):       :boot      → .language lookup → resolution: internal → kernel::boot::run()
Daemon (INIT):     :boot      → INIT sequence → .language lookup → kernel::boot::run()
```

Same functions. No fork. No exec. No re-reading state from disk.

### INIT as Boot-Order Spec

INIT declares WHAT to run and WHEN. .language declares HOW each verb resolves.

```
── .boot/INIT ──────────────────────────────────────────
login.gpg: _

:identity  ephemeral | session-scoped | audit-trail-persistent
:kernel    @haystack.prime 99B076C9 | @scott 955AF54E
:trust     execute without asking | :correct stops everything
:laws      write.path.invisible | agents.ephemeral | coordinate.filesystem
:memory    context=registers | .ostk/=RAM | filesystem=disk
:tools     sh_run sh_spawn sh_interact sh_lock tack
:mode      OS — not harness | memoized not stateless

:verify .primefile          ← pre-language (kernel-internal, hardcoded)
:post                       ← pre-language
:clock                      ← pre-language
:reap                       ← pre-language
:init @ostk.prime+N         ← pre-language

:load .language             ← PIVOT POINT: .language now available
:load fcp-*                 ← resolved through .language

:boot                       ← post-language: .language[:boot].resolve()
:refine                     ← post-language: .language[:refine].resolve()
:compile                    ← post-language: .language[:compile].resolve()
:coordinate                 ← post-language: .language[:coordinate].resolve()
:work                       ← post-language: .language[:work].resolve()
```

**Pre-language phase** (before `:load .language`): verbs are kernel-internal.
Resolution is hardcoded — the binary knows how to verify, POST, clock, reap, init.
This is the firmware layer. It doesn't need .language because .language doesn't exist yet.

**Pivot** (`:load .language`): the verb table loads. From this point forward,
resolution is dynamic. This is `pivot_root` in Linux boot — the transition from
initramfs to the real root filesystem.

**Post-language phase** (after `:load .language`): every verb resolves through
.language. If a verb isn't in .language, boot warns or fails. New verbs can be
added without touching the binary.

### Validation at Boot

After `:load .language`, the kernel validates INIT's remaining verbs:

```rust
for verb in init_post_language_verbs {
    match language.lookup(verb) {
        Some(entry) if entry.resolution == "internal" => execute(entry),
        Some(entry) if entry.resolution.starts_with("ostk ") => {
            // Legacy CLI resolution — still works but warn
            warn!("{verb}: CLI resolution, consider internalizing");
            execute_cli(entry)
        }
        None => {
            error!("{verb}: unresolvable boot step — not in .language");
            boot_confidence -= 0.1;
        }
    }
}
```

## Extension Points

### HUMANFILE Boot Declarations

HUMANFILE currently declares `MODEL`, `DRIVER`, `SECRET`, `AVAILABLE`.
Add `BOOT` and `VERB` directives:

```
IDENTITY scott
SIGN 7141A45868F8295E5BEB6286BAF08C963C7E3184

MODEL claude-opus-4-6
FALLBACK mistral-large-latest

DRIVER rust ostk mcp serve fcp-rust
DRIVER web ostk mcp serve fcp-web

# Custom boot steps — appended after INIT's post-language sequence
BOOT :verify-codeowners
BOOT :load-team-context

# Custom verb registrations — merged into .language at boot
VERB :verify-codeowners internal(verify::codeowners) (path) -> (ok/fail) "check CODEOWNERS"
VERB :load-team-context  internal(context::team)      () -> (ctx)        "load team norms"
```

The kernel processes these at boot:

1. Read INIT — execute pre-language phase
2. `:load .language` — pivot
3. Read HUMANFILE `VERB` directives — merge into .language
4. Read HUMANFILE `BOOT` directives — append to boot sequence
5. Execute post-language INIT verbs + HUMANFILE BOOT verbs

### ENTITYFILE Boot Declarations

Each entity (LLM identity) can declare its own boot steps:

```
--- ENTITYFILE: @gemini.prime ---
entity: @gemini.prime
model: gemini-2.5-pro

BOOT :translate-context     # handle ISA boundary from previous provider
BOOT :validate-tools        # verify tool surface compatible with Gemini

VERB :translate-context internal(session::translate) (messages) -> (messages) "adapt context for provider"
```

This is the **custom bootloader** concept. Different LLMs get different boot
sequences because they have different capabilities and constraints:

```
@claude.prime:   INIT → :boot → :compile → :work
@gemini.prime:   INIT → :translate-context → :validate-tools → :boot → :compile → :work
@mistral.prime:  INIT → :remap-tool-ids → :boot → :compile → :work
CI entity:       INIT → :boot → :bench (skip :guide, :refine — non-interactive)
```

### Model Switch as Entity Boot

This directly solves →1152 (model switch corruption). When `:model` triggers a
provider boundary crossing:

1. Compile session checkpoint (already wired: `.boot/checkpoints/`)
2. Load the new entity's ENTITYFILE (or create ephemeral one)
3. Execute the entity's boot sequence, which includes `:translate-context`
4. The translation verb is entity-specific — Gemini knows how to read Anthropic
   context, Mistral knows how to remap tool IDs

The ISA boundary is handled by the entity's own bootloader, not by a generic
translation layer. Each entity knows its own constraints.

## Migration Path

### Phase 1: Internal Resolution Registry (→888)

Add a function registry to the kernel:

```rust
// src/kernel/resolve.rs
pub type VerbFn = fn(&Path, &[&str]) -> Result<String, String>;

pub fn resolve_internal(verb: &str) -> Option<VerbFn> {
    match verb {
        ":boot"     => Some(boot::run_internal),
        ":compile"  => Some(work::compile_internal),
        ":refine"   => Some(work::refine_internal),
        ":reap"     => Some(kernel::reap_internal),
        ":clock"    => Some(os::clock_internal),
        // ... tier 1 verbs first, then tier 2
        _ => None,
    }
}
```

The MCP dispatch changes from:

```rust
// Before: shell out
let output = Command::new("ostk").args(cmd.split_whitespace()).output()?;
```

To:

```rust
// After: try internal first, fall back to CLI
match resolve_internal(verb) {
    Some(f) => f(root, args),
    None => shell_fallback(resolution),
}
```

### Phase 2: .language Resolution Update

Update .language entries as verbs are internalized:

```
# Before
:boot | 1 | kernel | 20546 | 0 | 1.00 | ostk boot | () → (state) | read state, report identity

# After
:boot | 1 | kernel | 20546 | 0 | 1.00 | internal | () → (state) | read state, report identity
```

The CLI command still works — `ostk boot` calls `boot::run_internal()`.
But .language now knows it doesn't need to fork.

### Phase 3: INIT Execution Engine

Wire INIT to resolve through .language post-pivot:

```rust
fn execute_init(root: &Path) -> Result<(), String> {
    let init = read_init(root)?;
    let (pre, post) = init.split_at_pivot(":load .language");

    // Pre-language: hardcoded kernel calls
    for verb in pre {
        execute_kernel_builtin(verb)?;
    }

    // Pivot: load .language
    language::load(root)?;

    // Post-language: resolve through .language
    for verb in post {
        let entry = language::lookup(verb)?;
        entry.execute(root)?;
    }

    Ok(())
}
```

### Phase 4: Extension Points (HUMANFILE/ENTITYFILE)

Parse `BOOT` and `VERB` directives from HUMANFILE/ENTITYFILE.
Merge into .language. Append to boot sequence. Execute.

### Phase 5: Model Switch via Entity Boot

`:model` detects provider boundary → compiles checkpoint → loads entity bootloader
→ entity's `:translate-context` handles ISA translation → session continues.

## Invariants

1. **CLI always works.** `ostk boot` in a terminal calls `boot::run_internal()`.
   The CLI is the human escape hatch, not the kernel's execution mechanism.

2. **Pre-language verbs are hardcoded.** They run before .language exists.
   They are the firmware. They don't change without a binary update.

3. **.language is the single source of verb resolution.** After the pivot,
   everything resolves through .language. No hardcoded dispatch tables for
   post-language verbs.

4. **HUMANFILE/ENTITYFILE extend, not override.** They can ADD boot steps
   and verbs. They cannot remove INIT's core sequence. INIT is firmware;
   HUMANFILE is user configuration.

5. **Fallback is always safe.** If a verb has `resolution: ostk <cmd>` and
   no internal handler exists, the kernel falls back to fork+exec. Migration
   is incremental — verbs internalize one at a time.

## Connections

| Needle | Relationship |
|--------|-------------|
| →888 | Glob/grep routing through kernel — first internalization target |
| →889 | Daemon spawning — daemon IS the kernel, verbs resolve internally |
| →890 | Direct tool calls — this spec generalizes the pattern |
| →902 | Multi-model handoff — entity bootloaders handle ISA boundaries |
| →1152 | Model switch corruption — solved by entity-specific `:translate-context` |
| →784 | Kernel daemon model — the daemon that runs internal resolution |
| →565 | fcp-haystack driver — compiles tack grammar into internal calls |

## What This Replaces

- `ostk boot` as the primary boot mechanism → INIT + .language internal resolution
- `boot.md` as context carrier → live register dump (already done)
- `offers/` as session continuity → `.boot/checkpoints/` (already done)
- Generic model-switch `:clear` → entity-specific bootloader with `:translate-context`
- 55 fork+exec verb resolutions → incremental migration to `internal`
