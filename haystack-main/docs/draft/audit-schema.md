---
title: Audit Log Schema
status: draft
version: 1
author: round-table
created: 2026-03-13
needle: →607
---

# Audit Log Schema — Round Table

> The audit log is provenance. Not telemetry. Not a debug log. Not a metrics sink.
> It answers one question: what decisions were made, by whom, and when?

---

## Round 1 — Security Architect Analysis

### What the audit log is for

ostk is an OS where actors are humans, AI agents, and kernel processes. Unlike a conventional
OS, you cannot reconstruct agent intent from stack traces or core dumps — an agent that crashes
leaves no process state behind. The audit log is the *only* durable record of what an agent
decided to do and why. This gives the audit log a purpose distinct from any other log format in
the stack:

- **NOT** performance telemetry (use metrics.jsonl for that)
- **NOT** debug logging (ephemeral, verbosity-controlled, discarded)
- **NOT** event sourcing for state reconstruction (the filesystem is the state)
- **YES** provenance: what actor caused what change to what resource, and under what authority

This distinction matters for schema design. Every field must earn its place by serving
provenance. Fields that don't serve provenance belong somewhere else.

### Actor taxonomy

ostk has three actor classes. All must be distinguishable in the log:

| Class | Identity form | Persistence |
|-------|---------------|-------------|
| Human | `human:<username>` | Stable across sessions |
| Agent | `agent:<id>` (kernel-assigned) | Session-bound — dies with session |
| Kernel | `kernel` | Always present, no session |

Agent identity is ephemeral by design (Law 2). The audit log is *how* agent provenance
survives agent death. If you cannot distinguish which agent class made a decision, the log
fails its primary purpose.

### Event category analysis

Five categories belong in an OS audit log where actors include humans, agents, and kernel:

**1. Work lifecycle** — the primary purpose of the system. What was decided and committed.
```
task.added, task.closed, task.claimed, task.shelved
needle.committed, bead.committed
draft.created, spec.promoted, spec.amended
```

**2. Knowledge operations** — the hay/needle pipeline is how intelligence persists.
```
hay.filed, hay.compiled
```

**3. Security-sensitive operations** — must appear; must be minimal.
```
secret.set          (key name only — value NEVER logged)
identity.minted     (new kernel-assigned agent identity issued)
trust.escalated     (agent granted elevated authority)
trust.revoked
```

**4. Kernel lifecycle** — infrastructure provenance for crash recovery and audit trails.
```
agent.spawned, agent.reaped
session.shutdown
bench.run
```

**5. Integrity events** — append-only corrections for things the kernel cannot prevent.
```
commit.remapped     (git rebase/amend rewrote a hash)
commit.orphaned     (hash missing, no replacement found)
```

### What must never appear in the log

The log is a boundary between the kernel and human operators, including auditors who have
legitimate read access. Any field that provides useful capability to an adversary with
read access to the log must be excluded unconditionally:

**Absolute exclusions (no exceptions):**
- Secret values, tokens, passwords, API keys — even hashed or truncated
- Full file contents of any kind
- LLM prompt text or response text (this is telemetry, not provenance)
- Personal data beyond what is necessary for actor identification (GDPR minimisation)
- Stack traces or error details that reveal internal kernel paths to an attacker

**Design rule**: if a field value would be useful to an attacker who has only the audit log,
that field must not exist in the log. Audit the *event* (what happened), not the *payload*
(what the content was).

### Handling sensitive events

Three event types require special treatment:

**`secret.set`** — the key name is auditable (you need to know which secrets exist).
The value is never logged under any circumstances. The current implementation in
`src/commands/secret.rs` is correct: it records `"key": key` and nothing else.

**`identity.minted`** — when the kernel assigns an agent identity, this must be logged
because identity is the anchor for all subsequent provenance in that session. Log the
assigned identity and the model/session context, not the full session transcript.

**`trust.escalated`** — any operation that grants an agent authority beyond its default
scope must be audited with: who granted it, what scope was granted, and what triggered
the grant. This is the most security-critical event category. Missing trust escalation
events are the canonical audit failure mode.

---

## Round 2 — Steelman the Opposing View

> What are the strongest arguments *against* a strict append-only log?

### Argument 1: Append-only is mathematically incompatible with GDPR

The right to erasure (GDPR Art. 17) requires that personal data be deleted upon request.
An append-only log that contains names, usernames, or any personal identifier cannot
comply without destroying the log's integrity properties.

This is a real tension, not a theoretical one. If ostk is used by a team and a team
member leaves, their identity appears in potentially thousands of audit events. "Delete
the events" violates append-only. "Keep the events" violates GDPR.

The steelman position: **strict append-only is a legal liability for any deployment that
involves personal data**. The system must either accept that it cannot be GDPR-compliant,
or it must design around personal identity from the start.

### Argument 2: The current codebase already violates append-only

`src/commands/audit.rs` contains `run_remap()` (lines 340–397) which performs an in-place
rewrite of `audit.jsonl` — it reads the file, mutates commit hashes, and writes it back.
This is a direct violation of the invariant that `docs/spec/audit-hash-integrity.md` calls
"non-negotiable."

The steelman position: **if the codebase already has in-place rewrite, the append-only
invariant is aspirational, not actual**. The spec should reflect reality, or the
implementation should be fixed before the spec is written.

### Argument 3: Append-only logs become unmanageable at scale

Every command that touches the kernel emits an event. A long-running ostk instance
with multiple agents will accumulate thousands of events per day. Reading `audit.jsonl`
requires full linear scan (the current implementation in `src/commands/audit.rs` reads
the entire file). There is no indexing, no compaction, no rotation.

The steelman position: **append-only JSONL does not scale past a few thousand events**.
The design needs either a compaction strategy (which breaks append-only) or an admission
that the audit log is only viable for small, single-human deployments.

### Argument 4: An append-only log cannot correct mistakes

If an agent logs incorrect data — wrong bead ID, wrong spec ref, wrong agent identity —
there is no mechanism to correct it without mutating the file. The `retroactive: true`
pattern in `audit.rs backfill` suggests the system already acknowledges that events are
sometimes wrong and need to be added after the fact.

The steelman position: **append-only assumes infallible event emission, but event emission
in a distributed agent system is neither atomic nor infallible**. A log that cannot be
corrected becomes increasingly inaccurate over time.

### Argument 5: Provenance and telemetry separation is operationally costly

Splitting events across `audit.jsonl` and `metrics.jsonl` means operators must query
two files to understand what happened. The distinction between "provenance" and "telemetry"
is clear in theory and murky in practice: is `bench.run` provenance (what was tested)
or telemetry (performance data)?

The steelman position: **the two-file model creates cognitive overhead without clear
benefit for small deployments**. A unified event log with a `category` field would be
simpler and queryable without choosing which file to look in.

---

## Round 3 — Synthesis

> The minimum viable audit schema that satisfies both provenance and practical concerns.

### Resolution of opposing arguments

**GDPR**: Resolved by pseudonymisation at schema level, not by allowing deletion.
Agent identities are kernel-assigned labels (`agent-042`), not personal names. Human
actors use the system username (`human:scott`), which is pseudonymous. If a deployment
requires full GDPR compliance, the operator may replace usernames with opaque IDs at the
kernel configuration layer. The audit log design does not need to change; the identity
assignment does. This is the correct boundary.

**In-place rewrite violation**: The `run_remap()` function in `audit.rs` must be deleted
and replaced with the `commit.remapped` append pattern from `docs/spec/audit-hash-integrity.md`.
This is a known implementation debt, not a design decision. The spec reflects the correct
design. The implementation needs to catch up. This draft spec calls that out explicitly.

**Scale**: The audit log is explicitly scoped to provenance events, not all events. This
keeps event volume low. The design accepts that full linear scan is acceptable up to
~10,000 events (roughly 2 years of active use for a single-human deployment). Rotation and
indexing are post-MVP concerns that can be added without changing the schema.

**Correction of mistakes**: Corrections are always new events. A `correction.noted` event
type may reference the original event's position (by timestamp + event type, since there
is no line-number stability in an append-only log) and record what was wrong. This is
analogous to a correction notice in a newspaper — the original stays, the correction is
appended.

**Two-file model**: The split is maintained. The distinction is: if the event's purpose
is to answer "who decided what and when," it belongs in `audit.jsonl`. If its purpose is
to answer "how did the system perform," it belongs in `metrics.jsonl`. `bench.run` is a
boundary case; it belongs in audit because the decision to run a benchmark is a provenance
event (what was validated), but its performance data (timing, scores) belongs in metrics.

---

## Minimum Viable Audit Schema

### Base event structure

Every audit event MUST contain these fields:

```json
{
  "event": "<category>.<action>",
  "actor": "<actor-class>:<actor-id>",
  "timestamp": "<ISO 8601 UTC>",
  "session": "<session-id>"
}
```

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `event` | string | yes | Dot-separated `<category>.<action>`. No spaces. Lowercase. |
| `actor` | string | yes | `human:<id>`, `agent:<id>`, or `kernel`. Never a personal name. |
| `timestamp` | string | yes | ISO 8601 UTC. Millisecond precision. `2026-03-13T14:32:01.123Z` |
| `session` | string | yes | Kernel-assigned session ID. Stable for the duration of one agent session. |

### Fields that must never appear

The following field names are permanently banned from `audit.jsonl`:

| Banned field | Why |
|--------------|-----|
| `value` | Secret values, file contents, or any payload |
| `password` | Self-evident |
| `token` | Includes API tokens, auth tokens, bearer tokens |
| `content` | File content, prompt text, response text |
| `prompt` | LLM prompt text |
| `response` | LLM response text |
| `key_value` | Alias for banned `value` |
| `secret` | The key name is auditable; the secret field is not |
| `data` | Untyped payload field — too broad, too dangerous |

Any field name that could plausibly hold secret material must be reviewed before adding.
The burden of proof is on inclusion, not exclusion.

### Canonical event catalog

#### Work lifecycle

**`task.added`**
```json
{
  "event": "task.added",
  "actor": "human:scott",
  "timestamp": "2026-03-13T14:32:01.123Z",
  "session": "s-0042",
  "id": "→607",
  "title": "audit log schema"
}
```

**`task.closed`**
```json
{
  "event": "task.closed",
  "actor": "agent:a-017",
  "timestamp": "2026-03-13T14:45:00.000Z",
  "session": "s-0042",
  "id": "→607",
  "resolution": "completed"
}
```
`resolution` values: `completed`, `shelved`, `superseded`, `wont-do`

**`task.claimed`**
```json
{
  "event": "task.claimed",
  "actor": "agent:a-017",
  "timestamp": "2026-03-13T14:33:00.000Z",
  "session": "s-0042",
  "id": "→607"
}
```

**`needle.committed`** / **`bead.committed`**
```json
{
  "event": "needle.committed",
  "actor": "agent:a-017",
  "timestamp": "2026-03-13T15:00:00.000Z",
  "session": "s-0042",
  "bead_id": "→607",
  "commit": "a3f9e21c7a4b...",
  "spec_ref": "docs/spec/audit-schema.md"
}
```

Note: `bead.committed` and `needle.committed` are aliases; prefer `needle.committed` going
forward. Both are accepted by `audit check`.

**`draft.created`**
```json
{
  "event": "draft.created",
  "actor": "agent:a-017",
  "timestamp": "2026-03-13T14:00:00.000Z",
  "session": "s-0042",
  "path": "docs/draft/audit-schema.md"
}
```

**`spec.promoted`**
```json
{
  "event": "spec.promoted",
  "actor": "human:scott",
  "timestamp": "2026-03-13T16:00:00.000Z",
  "session": "s-0042",
  "from": "docs/draft/audit-schema.md",
  "to": "docs/spec/audit-schema.md"
}
```

**`spec.amended`**
```json
{
  "event": "spec.amended",
  "actor": "human:scott",
  "timestamp": "2026-03-13T16:30:00.000Z",
  "session": "s-0042",
  "path": "docs/spec/audit-schema.md",
  "severity": "breaking",
  "affected_beads": ["→607", "→608"]
}
```

#### Knowledge operations

**`hay.filed`**
```json
{
  "event": "hay.filed",
  "actor": "agent:a-017",
  "timestamp": "2026-03-13T14:10:00.000Z",
  "session": "s-0042",
  "source": "observation",
  "straw": "short-summary-of-what-was-filed"
}
```

The `straw` field is a brief label, not the content. Maximum 200 characters. No file content.

**`hay.compiled`**
```json
{
  "event": "hay.compiled",
  "actor": "agent:a-017",
  "timestamp": "2026-03-13T14:20:00.000Z",
  "session": "s-0042",
  "needles_created": 3,
  "needles_updated": 1
}
```

#### Security-sensitive operations

**`secret.set`**
```json
{
  "event": "secret.set",
  "actor": "human:scott",
  "timestamp": "2026-03-13T14:05:00.000Z",
  "session": "s-0042",
  "key": "ANTHROPIC_API_KEY"
}
```

Only the key name. No value. No hash of the value. No prefix of the value.

**`identity.minted`**
```json
{
  "event": "identity.minted",
  "actor": "kernel",
  "timestamp": "2026-03-13T14:00:00.000Z",
  "session": "s-0042",
  "assigned_id": "agent:a-017",
  "model_class": "claude-sonnet",
  "agentfile": "agents/worker.Agentfile"
}
```

`model_class` is a category label (not a full model ID that might leak version details
that are confidential). Acceptable: `claude-sonnet`, `claude-haiku`, `gemini`, `gpt-4`.

**`trust.escalated`**
```json
{
  "event": "trust.escalated",
  "actor": "human:scott",
  "timestamp": "2026-03-13T14:01:00.000Z",
  "session": "s-0042",
  "target_agent": "agent:a-017",
  "scope": "write:docs/spec/",
  "reason": "promoted to spec editor for this session"
}
```

**`trust.revoked`**
```json
{
  "event": "trust.revoked",
  "actor": "kernel",
  "timestamp": "2026-03-13T15:59:00.000Z",
  "session": "s-0042",
  "target_agent": "agent:a-017",
  "scope": "write:docs/spec/",
  "reason": "session.shutdown"
}
```

#### Kernel lifecycle

**`agent.spawned`**
```json
{
  "event": "agent.spawned",
  "actor": "kernel",
  "timestamp": "2026-03-13T14:00:00.000Z",
  "session": "s-0042",
  "agent_id": "agent:a-017",
  "agentfile": "agents/worker.Agentfile",
  "parent_session": "s-0041"
}
```

`parent_session` is null for human-initiated spawns.

**`agent.reaped`**
```json
{
  "event": "agent.reaped",
  "actor": "kernel",
  "timestamp": "2026-03-13T16:00:00.000Z",
  "session": "s-0042",
  "agent_id": "agent:a-017",
  "cause": "context_exhausted"
}
```

`cause` values: `context_exhausted`, `task_complete`, `session_shutdown`, `crash`, `timeout`

**`session.shutdown`**
```json
{
  "event": "session.shutdown",
  "actor": "human:scott",
  "timestamp": "2026-03-13T16:00:00.000Z",
  "session": "s-0042",
  "active_agents": ["agent:a-017"],
  "cause": "explicit"
}
```

`cause` values: `explicit` (human-initiated), `crash` (kernel died), `timeout`

**`bench.run`**
```json
{
  "event": "bench.run",
  "actor": "agent:a-017",
  "timestamp": "2026-03-13T15:00:00.000Z",
  "session": "s-0042",
  "suite": "core",
  "pass": 644,
  "fail": 0,
  "result": "pass"
}
```

Aggregate counts only. No per-test output. Detailed results belong in `metrics.jsonl`.

#### Integrity events

**`commit.remapped`**
```json
{
  "event": "commit.remapped",
  "actor": "kernel",
  "timestamp": "2026-03-13T15:10:00.000Z",
  "session": "s-0042",
  "old_commit": "06c5a81b3f...",
  "new_commit": "a3f9e21c7a...",
  "cause": "rebase"
}
```

`cause` values: `rebase`, `amend`, `squash`, `backfill`

**`commit.orphaned`**
```json
{
  "event": "commit.orphaned",
  "actor": "kernel",
  "timestamp": "2026-03-13T15:10:00.000Z",
  "session": "s-0042",
  "commit": "06c5a81b3f...",
  "original_event": "bead.committed",
  "bead_id": "→607",
  "reason": "no_reachable_replacement"
}
```

**`correction.noted`** (new — for append-only correction of bad events)
```json
{
  "event": "correction.noted",
  "actor": "human:scott",
  "timestamp": "2026-03-13T15:20:00.000Z",
  "session": "s-0042",
  "refers_to_timestamp": "2026-03-13T15:00:00.000Z",
  "refers_to_event": "bead.committed",
  "correction": "bead_id was →606, should be →607"
}
```

The `correction` field is free text describing what was wrong. It is the auditor's
annotation, not a machine-readable patch. The original event is never modified.

---

## Implementation Notes

### The `run_remap` violation

`src/commands/audit.rs` contains `run_remap()` which rewrites `audit.jsonl` in place.
This is a known violation of the append-only invariant. It must be replaced with the
`commit.remapped` append-event pattern before this schema is marked `spec`.

**Required fix**: Delete `run_remap()`. Implement `commit.remapped` event emission via
`append_audit()`. Update `ostk audit remap <old> <new>` to emit a `commit.remapped`
event rather than rewrite the file.

### Field name stability

Event and field names are part of the public interface of the audit log. Operators may
build tooling against them. Any field rename is a breaking change and requires a new
event version or a `commit.remapped`-style forwarding event.

### Retention and rotation

Out of scope for v1. The schema is designed to support future rotation: events reference
each other by timestamp and event type (not line number), so a rotated log can be
reconstructed by replaying events in timestamp order.

### GDPR boundary

Personal identifiers in the log are scoped to system usernames (`human:<username>`).
This is acceptable pseudonymisation for most jurisdictions. Deployments requiring full
anonymisation should configure kernel identity assignment to use opaque IDs. The schema
does not need to change; the actor assignment layer does.

---

## Open Questions (not resolved in this draft)

1. Should `bench.run` move to `metrics.jsonl` entirely, with only a pointer event in
   `audit.jsonl`? The current position is "audit gets the provenance event, metrics gets
   the data." This needs operator feedback.

2. How do we handle trust escalation for the kernel itself? The kernel is always actor
   `kernel` with no session, which means trust escalation events that grant the kernel
   new authority have no natural audit record. This may not be a real concern (the kernel
   doesn't grant itself authority — humans do) but deserves explicit confirmation.

3. The `session` field on kernel events is ambiguous when the kernel is acting outside
   any agent session (e.g., during `ostk init`). Proposal: use `session: "kernel"`
   as a reserved sentinel. This needs agreement before implementation.
