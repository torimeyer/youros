//! `ostk --agents` — usage guide for LLM agents.

pub const AGENT_GUIDE: &str = r#"# ostk — agent guide

YOU ARE RUNNING ON AN OPERATING SYSTEM.

ostk is a coordination kernel. Output is compressed to signal, files have
generation counters, conflicts resolve at write time. You don't adopt it —
you're already on it.

## Boot Protocol

Every session starts the same way — boot is implicit:

  :boot     — read state, report identity (kernel resolves internally)
  :refine   — detect drift since shutdown
  :compile  — hay → needles (unstructured thinking → executable work)
  :work     — begin

Boot state is live — computed from .ostk/ on every turn. No stale files.

## Tools

All tools route through the kernel. Use MCP tools directly:

  shell(cmd="cargo test")            — run command, compressed output
  spawn(alias="bg", cmd="make")      — background process
  interact(alias="bg", ...)          — read/send to background process
  lock(action="create", name=".")    — coordination lock
  session(action="list")             — list active sessions
  tack(input=":compile")             — resolve tack grammar to kernel calls
  help()                             — show available tools

shell compresses output automatically:
  - ANSI stripped, repeated lines collapsed, signal preserved
  - 26 tool grammars (cargo, git, npm, docker, ...)

## Demand-Paged Tools

Tools are demand-paged from .language. High-momentum verbs are resident
(loaded at boot). Low-momentum verbs are available via ostk_verbs(query).

  ostk_verbs(query)          — discover tools (page fault handler)
  ostk_pitchfork(query)      — search all kernel state by keyword
  ostk_context_search(query) — search session history
  log_decision(key, value)   — record a key decision to working state

Most verbs resolve internally — no process spawn. The kernel calls
Rust functions directly via the resolve registry.

## Work

  :needle "description"    — file executable work
  :close ID                — close a needle with reason
  :ls                      — list needles with filters
  :next                    — show next available task
  :compile                 — triage hay into needles
  :hay "thought"           — capture raw uncompiled thinking
  :draft "title"           — create a draft document
  :promote                 — promote draft to spec
  :trace ID                — attribution chain (needle → commit → spec)

## Kernel Signals

Every tool response includes a kernel digest:

  [304]                      — read elision: file unchanged (~5 tokens)
  [stale] src/lib.rs:g2→g4  — file changed since you last read it
  [nudge] "check output"    — human/orchestrator injected a message
  [procs] bg:running(45s)   — background processes
  [ctx]                      — kernel registers (identity, needles, fleet)

## Secrets

Secrets are kernel-mediated. Values are masked in all tool output
(structure-preserving: "sk-proj-●●●● (164 chars)").

  ostk secret set KEY        — store in platform keychain (human only)
  ostk secret list           — show key names (never values)
  ostk embeddings download   — fetch semantic search model

## Model Switching

:model switches the LLM provider. Cross-provider switches build a
handoff lookup table — hot files, recent decisions, intent thread.
The new model boots with a thin index and pages in what it needs.

Same-provider switches (sonnet→opus) carry messages forward unchanged.

## HUMANFILE Extension Points

HUMANFILE declares custom boot steps and verbs:

  BOOT :verify-codeowners     — appended after kernel boot
  VERB :deploy internal (path) deploy to production

## Agentfile

Agents are defined by Agentfiles:

  FROM claude-sonnet-4-5
  PROMPT "You are a Rust expert"
  TOOL shell
  LIMIT tokens 100000
  WORK needle add "implement X"

## Vocabulary

  straw    — one thought (replaces "user story")
  hay      — uncompiled thinking (replaces "backlog")
  needle   — executable work: verb + target + test (replaces "task")
  compile  — hay → needles (replaces "sprint planning")
  trace    — attribution chain (replaces "done = proved")

## What NOT To Do

  DON'T bypass the kernel — retry through ostk tools
  DON'T call ostk secret get — secrets are kernel-mediated, never in context
  DON'T skip boot context — the kernel digest IS your orientation
"#;
