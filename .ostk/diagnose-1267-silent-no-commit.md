# Diagnosis →1267: 1265 "silent no-commit" pattern

**Verdict: False alarm. All three agents committed or correctly found no work.**

## Timeline reconstruction

| Agent | Span (UTC) | Transcript | Committed |
|-------|-----------|------------|-----------|
| migrate-atlassian-search-to-sear-570c22 | 04:19–04:21 | 716 B (truncated) | ae04756 — code fix |
| atlassian-search-jql-migration-r-a360a7 | 04:22–04:25 | 70 B (resolver miss) | af3b758 — guard test |
| finish-atlassian-search-jql-code-3ca157 | 04:30–04:33 | 2139 B (complete) | none (correctly) |

Agent 3's own transcript confirms: "Both files are already showing `/rest/api/3/search/jql`...
The migration (ae04756) already landed on main. Both call sites were confirmed correct...
64/64 [atlassian tests] pass."

## Why the orchestrator thought agents 1 and 2 were silent

**Agent 1 (ae04756 committed, needle not closed):**
Transcript truncated at 716 bytes mid-sentence. The agent committed the code fix but the
transcript doesn't record it. More critically, the needle (→1265) was NOT closed by agent 1.
The orchestrator saw the needle still open and respawned.

**Agent 2 (af3b758 committed, needle not closed):**
Transcript resolver returned a 70-byte stub ("Registered. Now let me read both files...")
instead of the real JSONL. This is the transcript resolver mis-matching: step 3 found a
small file (likely a stub from a pre-run check, or an older JSONL with the same name pattern)
before finding the real one. Agent 2 committed af3b758 and ran 2+ minutes — 70 bytes cannot
represent that work. The real transcript is in a project dir the resolver missed.

Agent 3 saw the needle still open, ran full verification, closed it, and correctly did not
commit. This is the healthy behavior once the work was done.

## Root causes

**RC1 — Agents 1 and 2 didn't close the needle.**
`→NNN` in a commit message is traceability, not a close. Agents must call
`ostk work close "→1265"` explicitly before `/complete`. Neither agent 1 nor 2 did.
This is the primary driver of the re-spawn chain.

**RC2 — Transcript resolver returned a stub for agent 2.**
The resolver (step 3, `*/subagents/agent-*.jsonl` glob) matched a small file before the
real one. Most likely cause: agent 2 ran in a worktree (isolation:worktree) and its JSONL
lives under `~/.claude/projects/<worktree-label>/...`, but the resolver's step 3c requires
`worktree_path` in agent metadata. If the agent registered before the worktree path was
known, that field is missing and step 3c is skipped. Result: resolver finds a small
non-worktree file and returns it.

## What was NOT the cause

- **Lock conflict**: all 3 agents ran to completion without blocking.
- **Hook kill**: commits landed cleanly.
- **Worktree branch missing**: ae04756 and af3b758 are both on main.

## Structural fix

**Immediate (subagent brief template):** Every task-specific agent brief must include an
explicit needle-close step: `ostk work close "→NNN"` before `/complete`. The brief template
already has a scope-pin requirement; add the close step there.

**Forward-looking (P2 needle filed as →1280):** The transcript resolver should attempt the
worktree project dir scan (step 3c) even when `worktree_path` is absent from metadata,
by scanning all `~/.claude/projects/` subdirs that could match a worktree of PROJECT_ROOT.
This prevents transcript mis-match from cascading into false "did nothing" observations.

## Status

→1265 work: fully landed (ae04756 code fix + af3b758 guard test). Needle was closed by
agent 3. No re-work needed.
