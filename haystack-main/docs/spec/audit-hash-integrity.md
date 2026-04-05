---
implements: []
---
status: spec
author: round-table
created: 2026-03-08
discussion: transcripts/discussions/git-rewrite-audit/
participants: [git-expert, audit-architect, ostk-integrator]
rounds: 3
---

# Audit Hash Integrity — Commit Remap & Orphan Tracking

> When git rewrites history, the audit trail must record the rewrite, not pretend it didn't happen.

## Problem Statement

ostk's audit log (`audit.jsonl`) records `bead.committed` events that reference git commit hashes. Git operations -- rebase, amend, squash merge, force-push -- rewrite those hashes. After rewriting, the audit log contains "phantom hashes" that point to commits that no longer exist in the repository. This breaks `ostk trace` (cannot follow the chain from bead to commit), `ostk audit check` (reports false integrity failures), and any compliance query against the log.

The naive fix -- rewriting `audit.jsonl` in place to update hashes -- violates the append-only invariant that makes the log trustworthy. A rewritten log cannot prove it hasn't been tampered with.

The system needs a way to record hash changes as new events, preserve the original records unchanged, and resolve remap chains at read time.

## Design Decisions

### Settled: Append-Only Is Non-Negotiable

All three participants converged independently in Round 1. The audit log is never mutated. Corrections are recorded as new events that reference old ones. This matches the pattern used in financial systems (trade ledgers, SOX-compliant audit trails): the original record is preserved byte-for-byte; a mapping event documents the change.

Rationale against in-place rewrite:
- Loses the record of what the hash was at the time of the original event.
- Cannot detect partial rewrites if the update process crashes mid-operation.
- Breaks external systems that cached or indexed log lines.
- Read-modify-write races under concurrent agent access; append is safe (O_APPEND + writes under PIPE_BUF).

### Settled: Bead ID Is the Stable Anchor

Commit hashes are ephemeral pointers. The bead ID (`bd-XXX`) embedded in the commit message is the stable identifier that survives rewriting. Remap events connect the ephemeral pointers; the bead ID is the invariant that lets backfill recover when hooks miss an event.

### Settled: Belt-and-Suspenders (Hooks + Backfill)

Round 1 produced a genuine disagreement: D1 advocated hooks as the primary mechanism, D3 advocated backfill-only and rejected hooks entirely, D2 focused on the append-only invariant without prescribing a capture mechanism.

Resolution came in Round 2:
- **D1** demonstrated that `git gc` prunes unreachable objects, making backfill's `git cat-file -t` verification unreliable after gc. Hooks capture the mapping before gc destroys evidence.
- **D3 conceded** (D3-R2): "The git gc problem is real and I didn't address it. Hooks capture the mapping before gc can destroy evidence. That's a genuine advantage I missed." D3 revised their position to support hooks as primary capture with backfill as secondary recovery.
- **D2** endorsed bounded staleness: remap events must be recorded in a timely manner, not left to manual backfill runs days later.

Final consensus: `post-rewrite` hook as primary real-time capture. `audit backfill --fix-rewrites` as secondary recovery for cases hooks cannot cover (squash merges, missing hook installation, cross-machine force-pushes).

### Settled: Squash Merges Are a One-Way Door

`git merge --squash` fires `post-commit`, not `post-rewrite`. Git provides no old-to-new mapping. The original branch commits become unreachable. No hook can automatically capture squash remap data.

Resolution: Squash merges require explicit declaration via `ostk merge --squash --bead bd-XXX`, which emits the remap events manually. Alternatively, `backfill --fix-rewrites` can recover by matching bead IDs in the squash commit message against phantom hashes in the audit log.

### Deferred: `pre-rebase` Drop Detection

D1 proposed a `pre-rebase` hook to record tracked hashes before rebase, enabling detection of dropped commits (commits removed from an interactive rebase todo list, which `post-rewrite` silently omits). D3 argued this is an edge case of an edge case and adds complexity.

Resolution: Defer to post-MVP. Backfill already detects orphaned hashes (a hash with no git object and no remap event). The `pre-rebase` hook provides more precise drop detection but is not required for correctness.

## Solution

### Primary Mechanism: `post-rewrite` Git Hook

ostk installs a `post-rewrite` hook during `ostk init`. The hook reads old/new hash pairs from stdin (provided atomically by git after rebase or amend completes) and appends `commit.remapped` events to `audit.jsonl`.

**Installation:**

```sh
ostk init
```

This sets `core.hooksPath` to a ostk-managed hooks directory, ensuring all worktrees in the repository share the same hooks. If hooks already exist at the target path, ostk chains them (runs the existing hook first, then appends its own logic).

**Hook behavior:**

1. Reads stdin. Each line contains `<old-hash> <new-hash>` (and an optional third field reserved by git).
2. For each pair, appends a `commit.remapped` event to `audit.jsonl` via atomic `O_APPEND` write.
3. The `cause` field is set based on the `$1` argument git passes to `post-rewrite`: either `rebase` or `amend`.

**Concurrency:** Each event is a single JSONL line. On POSIX, writes via `O_APPEND` are atomic for sizes under `PIPE_BUF` (4096 bytes). A single remap event is well under this limit. No locking is needed. Multiple worktrees rebasing simultaneously append safely.

### Secondary Mechanism: `audit backfill --fix-rewrites`

For cases the hook cannot cover (squash merges, repositories where the hook was not installed, force-pushes from another machine, shallow clones):

1. Collect all commit hashes referenced in `audit.jsonl` events.
2. For each hash, run `git cat-file -e <hash>` to check if the object exists.
3. If missing (phantom hash), extract the bead ID from the original event and search `git log --all --grep=<bead-id>` for a reachable commit referencing that bead.
4. If a new commit is found: append `commit.remapped` with `cause: "backfill"`.
5. If no match is found: append `commit.orphaned`.

This runs automatically as part of `ostk audit check` to provide bounded staleness rather than relying on manual invocation.

## Event Schema

### `commit.remapped`

Appended when a commit hash is rewritten and the new hash is known.

```json
{
  "event": "commit.remapped",
  "old_commit": "06c5a81b3f...",
  "new_commit": "a3f9e21c7a...",
  "cause": "rebase|amend|backfill|squash",
  "agent": "orchestrator",
  "timestamp": "2026-03-08T14:32:01Z"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | string | yes | Always `"commit.remapped"` |
| `old_commit` | string | yes | Full SHA-1 of the original commit |
| `new_commit` | string | yes | Full SHA-1 of the replacement commit |
| `cause` | string | yes | What triggered the rewrite: `rebase`, `amend`, `backfill`, `squash` |
| `agent` | string | no | Agent or user identity that performed the rewrite |
| `timestamp` | string | yes | ISO 8601 UTC timestamp |

### `commit.orphaned`

Appended when a commit hash is missing and no replacement can be found. The original commit was obliterated (squash without bead reference, gc after force-push, etc.).

```json
{
  "event": "commit.orphaned",
  "commit": "06c5a81b3f...",
  "original_event": "bead.committed",
  "bead": "bd-042",
  "reason": "no_reachable_replacement",
  "timestamp": "2026-03-08T14:32:01Z"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | string | yes | Always `"commit.orphaned"` |
| `commit` | string | yes | Full SHA-1 of the missing commit |
| `original_event` | string | yes | The event type that referenced this commit |
| `bead` | string | no | Bead ID from the original event, if known |
| `reason` | string | yes | Why no remap could be established |
| `timestamp` | string | yes | ISO 8601 UTC timestamp |

## Remap Chain Resolution

`ostk trace` and `ostk audit check` resolve remap chains at read time:

1. Scan `audit.jsonl` for all `commit.remapped` events.
2. Build a `HashMap<old_commit, new_commit>`.
3. For any commit hash referenced in a `bead.committed` (or other commit-referencing) event, resolve transitively: if A -> B and B -> C, then A resolves to C.
4. Cache the resolved map for the duration of the command.

A commit hash is in one of three states:
- **Live:** exists in the git object store. No remap needed.
- **Remapped:** has a forward pointer via `commit.remapped`. Follow the chain to the current hash.
- **Orphaned:** has a `commit.orphaned` event. The commit is gone and no replacement exists. The gap itself is the audit record.

## Failure Modes

### Garbage Collection (`git gc`)

After rebase, old commits become unreachable. `git gc` prunes unreachable objects (default: after reflog expiry, typically 90 days; server-side gc may be immediate). Once pruned, `git cat-file -e <old-hash>` fails and the old object is unrecoverable.

**Mitigation:** The `post-rewrite` hook captures the mapping before gc runs. If the hook was not installed, backfill must run before gc prunes the reflog. The remap event in `audit.jsonl` becomes the sole record of the old hash's existence -- this is acceptable and expected.

### Squash Merges

`git merge --squash` + `git commit` fires `post-commit` (no old/new mapping) not `post-rewrite`. N original commits collapse to 1 new commit with no git-provided mapping.

**Mitigation:** Use `ostk merge --squash --bead bd-XXX` to emit N `commit.remapped` events explicitly. If the squash commit message contains bead IDs (via commit message convention), `backfill --fix-rewrites` can recover by matching bead IDs. If the squash commit lacks bead references, backfill emits `commit.orphaned` and `audit check` reports the gap.

### Force-Push

No local git hook fires for a force-push performed on another machine. The local repository discovers divergence only on fetch.

**Mitigation:** `ostk audit check` compares audit hashes against `git log`. Phantom hashes trigger the backfill recovery path. This is detection after the fact, not prevention -- consistent with forward-recovery design.

### Dropped Commits (Interactive Rebase)

When a commit is removed from the interactive rebase todo list, `post-rewrite` silently omits it from the old/new pairs. No explicit "this commit was dropped" signal is provided.

**Mitigation (MVP):** Backfill detects the phantom hash and, finding no replacement, emits `commit.orphaned`. **Mitigation (post-MVP):** A `pre-rebase` hook records tracked hashes; `post-rewrite` diffs against them to detect drops explicitly.

### Missing Hook Installation

If a repository was not initialized with `ostk init`, no hooks fire. Rebases and amends produce phantom hashes silently.

**Mitigation:** `ostk audit check` detects phantom hashes and runs the backfill recovery path. `audit check` also warns if the `post-rewrite` hook is not installed, prompting the user to run `ostk init`.

## Dissent Record

**D3 (CLI Architect) originally opposed hooks entirely** (D3-R1), arguing:

1. `post-rewrite` provides partial coverage that looks complete -- it misses squash merges, cherry-picks across repos, and force-pushes from other machines.
2. Agents don't install hooks; hook installation is a setup step that breaks the "invisible infrastructure" principle.
3. Hook execution is synchronous and blocking, making the write path visible.
4. Backfill alone, extending the existing `audit backfill` pattern, was sufficient.

**Why D3 conceded** (D3-R2): D1 demonstrated that `git gc` prunes unreachable objects, making backfill's `git cat-file -t` verification unreliable after garbage collection. Without hooks, there is a race between "when did you run backfill?" and "when did gc prune the old objects?" -- and gc always wins eventually. D3 acknowledged: "Hooks capture the mapping before gc can destroy evidence. That's a genuine advantage I missed." D3's revised position: hooks as primary capture, backfill as safety net, with the explicit understanding that hooks provide known partial coverage (not false complete coverage) since the gaps (squash, force-push) are well-documented and handled by other mechanisms.

## Acceptance Criteria

- [ ] `commit.remapped` event schema defined and documented
- [ ] `commit.orphaned` event schema defined and documented
- [ ] `post-rewrite` hook implemented: reads stdin pairs, appends `commit.remapped` events to `audit.jsonl`
- [ ] `ostk init` installs `post-rewrite` hook via `core.hooksPath`
- [ ] `ostk init` chains with existing hooks if present (does not clobber)
- [ ] `audit backfill --fix-rewrites` detects phantom hashes via `git cat-file -e`
- [ ] `audit backfill --fix-rewrites` resolves phantom hashes via `git log --all --grep=<bead-id>`
- [ ] `audit backfill --fix-rewrites` emits `commit.remapped` for recovered hashes
- [ ] `audit backfill --fix-rewrites` emits `commit.orphaned` for unrecoverable hashes
- [ ] `ostk audit check` runs backfill recovery automatically when phantom hashes are found
- [ ] `ostk audit check` warns if `post-rewrite` hook is not installed
- [ ] `ostk trace` resolves remap chains transitively (A -> B -> C resolves to C)
- [ ] `ostk trace` reports orphaned commits as gaps in the chain
- [ ] Concurrent appends from multiple worktrees do not corrupt `audit.jsonl` (O_APPEND atomicity)
- [ ] `ostk merge --squash --bead bd-XXX` emits N `commit.remapped` events for squashed commits
- [ ] Remap events survive `git gc` (events recorded before gc preserve provenance)
- [ ] End-to-end test: rebase rewrites hash, `trace` resolves through remap to current commit
- [ ] End-to-end test: phantom hash with no replacement produces `commit.orphaned`
