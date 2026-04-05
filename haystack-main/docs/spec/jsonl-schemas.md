---
title: JSONL Data Schemas
needle: →776
status: spec
version: 1
author: fleet-agent
created: 2026-03-17
implements: audit.jsonl, needles/issues.jsonl
---

# JSONL Data Schemas

> Schema documentation for the two append-oriented JSONL files that form ostk's persistent state. Malformed entries cause silent parse failures — this spec defines what "well-formed" means.

---

## 1. General Conventions

- **Encoding**: UTF-8, one JSON object per line, newline-terminated.
- **Timestamps**: ISO 8601 format (`YYYY-MM-DDTHH:MM:SSZ`), always UTC.
- **IDs**: Needle IDs use the `→NNN` format (Unicode arrow U+2192 + zero-padded 3-digit number). Legacy IDs use `bd-NNN` or `nd-NNN` prefixes.
- **Append-only invariant**: `audit.jsonl` is strictly append-only. Writes use `O_APPEND` (see `src/lib.rs::append_audit`). In-place rewrites violate this invariant — see →608 which found `run_remap()` doing an in-place rewrite. The fix was to emit `commit.remapped` append events instead.
- **Needle file**: `issues.jsonl` is read-modify-write under `flock` (see `src/lib.rs::with_needles_locked`). It is **not** append-only — the entire file is rewritten on mutation. The lock file is `issues.lock`.

---

## 2. audit.jsonl

**Path**: `.ostk/audit.jsonl`

**Writer**: `append_audit(root, &event)` in `src/lib.rs` (lines 77-89)

**Reader**: `read_audit_events(root)` in `src/lib.rs` (lines 247-262). Silently skips malformed lines (`if let Ok(v) = serde_json::from_str`).

### 2.1 Common Fields

Every event **must** have:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | string | yes | Event type identifier (see below) |
| `timestamp` | string | yes | ISO 8601 UTC timestamp |

### 2.2 Event Types

#### `task.added`

Emitted when a needle is created via `ostk needle add`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | `"task.added"` | yes | |
| `id` | string | yes | Needle ID (e.g., `→042`) |
| `title` | string | yes | Needle title |
| `priority` | string | yes | `P0`, `P1`, or `P2` |
| `issue_type` | string | no | Default `"task"` |
| `milestone` | string | no | Milestone name if set |
| `timestamp` | string | yes | |

```json
{"event":"task.added","id":"→042","title":"fix flock race condition","priority":"P0","issue_type":"task","timestamp":"2026-03-08T00:24:20Z"}
```

#### `task.claimed`

Emitted when an agent claims a needle via `ostk pull`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | `"task.claimed"` | yes | |
| `id` | string | yes | Needle ID |
| `assignee` | string | yes | Agent alias |
| `timestamp` | string | yes | |

```json
{"event":"task.claimed","id":"→042","assignee":"agent-1","timestamp":"2026-03-08T00:30:00Z"}
```

#### `task.closed`

Emitted when a needle is closed via `ostk needle close`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | `"task.closed"` | yes | |
| `id` | string | yes | Needle ID |
| `reason` | string | yes | Close reason (or `"none"`) |
| `timestamp` | string | yes | |

```json
{"event":"task.closed","id":"→042","reason":"flock guard implemented","timestamp":"2026-03-08T00:35:00Z"}
```

#### `bead.committed`

Emitted by `ostk commit` when a commit references a needle or spec.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | `"bead.committed"` | yes | |
| `bead` / `bead_id` | string | no | Needle ID (field name varies — `bead` from commit, `bead_id` from backfill) |
| `commit` | string | yes | Git commit hash (full SHA) |
| `spec_ref` | string | no | Spec path (e.g., `docs/spec/audit-hash-integrity.md`) |
| `agent` | string | no | Agent that made the commit |
| `retroactive` | bool | no | `true` if added by `audit backfill` |
| `retroactive_added` | string | no | When the backfill event was added |
| `timestamp` | string | yes | |

```json
{"event":"bead.committed","bead":"→042","commit":"abc1234def5678","spec_ref":"docs/spec/audit-hash-integrity.md","agent":"agent-1","timestamp":"2026-03-08T00:36:00Z"}
```

#### `needle.committed`

Legacy alias for `bead.committed`. Both are recognized by `audit check`.

#### `commit.remapped`

Emitted when a git rebase/amend changes a commit hash. Replaces the in-place rewrite that →608 identified as an append-only violation.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | `"commit.remapped"` | yes | |
| `old_commit` | string | yes | Original commit hash |
| `new_commit` | string | yes | New commit hash after rebase/amend |
| `cause` | string | yes | `"post-rewrite"`, `"backfill"`, or `"manual"` |
| `agent` | string | no | Usually `"orchestrator"` |
| `timestamp` | string | yes | |

```json
{"event":"commit.remapped","old_commit":"abc1234","new_commit":"def5678","cause":"post-rewrite","agent":"orchestrator","timestamp":"2026-03-08T01:00:00Z"}
```

#### `commit.orphaned`

Emitted when a commit hash cannot be recovered after rebase — no reachable replacement found.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | `"commit.orphaned"` | yes | |
| `commit` | string | yes | Unreachable commit hash |
| `original_event` | string | no | The event type that referenced this hash |
| `bead` | string | no | Needle ID if known |
| `reason` | string | yes | e.g., `"no_reachable_replacement"` |
| `timestamp` | string | yes | |

#### `hay.filed`

Emitted by `ostk hay` — a raw observation/thought before compilation.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | `"hay.filed"` | yes | |
| `straw` | string | yes | The observation text |
| `source` | string | yes | Who filed it (e.g., `"agent"`, `"user"`) |
| `timestamp` | string | yes | |

```json
{"event":"hay.filed","straw":"flock race condition in counter","source":"agent","timestamp":"2026-03-08T00:20:00Z"}
```

#### `tack.resolved`

Emitted when fcp-ostk resolves a tack expression.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | `"tack.resolved"` | yes | |
| `input` | string | yes | Raw tack input |
| `verb` | string | yes | Extracted verb |
| `tier` | number | yes | Resolution tier (1=exact, 2=pattern, 3=LLM) |
| `source` | string | yes | `"static"`, `"language"`, `"humanfile"`, or `"inferred"` |
| `resolved` | bool | yes | Whether resolution succeeded |
| `timestamp` | string | yes | |

#### `session.shutdown`

Emitted by `ostk shutdown`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | `"session.shutdown"` | yes | |
| `agent` | string | no | Agent alias |
| `timestamp` | string | yes | |

#### `heartbeat_injected`

Emitted periodically by the kernel to signal liveness.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | `"heartbeat_injected"` | yes | |
| `agent` | string | yes | Usually `"kernel"` |
| `ts` | string | yes | Timestamp (note: uses `ts` not `timestamp`) |

#### `reap`

Emitted when zombie agents are reaped.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | `"reap"` | yes | |
| `reaped` | number | yes | Count of reaped agents |
| `remaining` | number | yes | Agents still alive |
| `stale_purged` | number | no | Stale entries purged |
| `zombies` | array | no | Array of `{alias, pid, last_seen, registered_at}` |
| `timestamp` | string | yes | |

#### `request.submitted` / `request.granted` / `request.denied` / `request.revoked`

Kernel request lifecycle events (e.g., secret access requests).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | string | yes | One of the four request events |
| `request_id` | string | yes | Unique request ID |
| `request_type` | string | for `.submitted` | e.g., `"secret"` |
| `target` | string | for `.submitted` | e.g., `"ANTHROPIC_API_KEY"` |
| `agent` | string | yes | Requesting agent alias |
| `reason` | string | no | Reason for the action |
| `timestamp` | string | yes | |

#### Other Event Types

| Event | Source | Key Fields |
|-------|--------|------------|
| `project.initialized` | `ostk init` | `timestamp` |
| `project.installed` | `ostk install` | `timestamp` |
| `spec.promoted` | `ostk promote` | `timestamp` |
| `spec.amended` | `ostk amend` | `timestamp` |
| `draft.created` | `ostk draft` | `timestamp` |
| `needles.created` | `ostk decompose` | `timestamp` |
| `needle.shelved` | `ostk shelve` | `id`, `timestamp` |
| `needle.unshelved` | `ostk unshelve` | `id`, `timestamp` |
| `import.created` | `ostk import` | `timestamp` |
| `merge` | `ostk merge` | `timestamp` |
| `purge` | `ostk purge` | `timestamp` |
| `secret.set` | `ostk secret set` | `key`, `timestamp` |
| `secret.injected` | `ostk run` | `key`, `agent`, `timestamp` |
| `agent.spawn` | `ostk run` | `agent`, `timestamp` |
| `agent.spawned` | `ostk fleet` | `agent`, `timestamp` |
| `bench.run` | `ostk bench` | `scenario`, `result`, `timestamp` |
| `bench.docker` | `ostk bench` | `scenario`, `model`, `result`, `duration_ms`, `timestamp` |
| `fcp.resolved` | fcp driver | `input`, `verb`, `timestamp` |
| `fcp.unknown` | fcp driver | `input`, `timestamp` |
| `filesystem.mutated` | lib.rs | path info, `timestamp` |

---

## 3. needles/issues.jsonl

**Path**: `.ostk/needles/issues.jsonl`

**Writer**: `write_needles(root, &needles)` in `src/lib.rs` — rewrites entire file.

**Reader**: `read_needles(root)` in `src/lib.rs` — parses each line, skips empties, errors on malformed JSON.

**Locking**: `with_needles_locked(root, closure)` acquires `flock` on `issues.lock` before read-modify-write.

### 3.1 Needle Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Needle ID (`→NNN`, `bd-NNN`, or `nd-NNN`) |
| `title` | string | yes | Short description |
| `status` | string | yes | See status values below |
| `priority` | string | yes | `P0`, `P1`, or `P2` |
| `issue_type` | string | yes | `"task"` (only value currently used) |
| `created_at` | string | yes | ISO 8601 timestamp |
| `closed_at` | string | no | Set when status becomes `"closed"` |
| `close_reason` | string | no | Why the needle was closed |
| `commit_ref` | string | no | Git commit hash that resolved this needle |
| `assignee` | string | no | Agent alias that claimed the needle |
| `milestone` | string | no | Milestone name |
| `tags` | array | no | Array of string tags |
| `description` | string | no | Longer description text |

### 3.2 Status Values

| Status | Meaning |
|--------|---------|
| `open` | Not yet claimed or worked |
| `in_progress` | Claimed by an agent (has `assignee`) |
| `closed` | Resolved (has `close_reason` and `closed_at`) |

### 3.3 Priority Values

| Priority | Meaning |
|----------|---------|
| `P0` | Critical — blocks other work |
| `P1` | Standard priority |
| `P2` | Low priority / nice-to-have |

### 3.4 Example Entry

```json
{"id":"→042","title":"fix flock race condition","status":"closed","priority":"P0","issue_type":"task","created_at":"2026-03-08T00:24:20Z","closed_at":"2026-03-08T00:29:15Z","close_reason":"fs2 flock on counter","commit_ref":"a88edb6fa8cec7ded3d9cf6297c7328ae2198998"}
```

### 3.5 ID Allocation

Needle IDs are allocated by `next_needle_id()` in `src/lib.rs`:

1. Open `.ostk/needles/counter` with `flock_exclusive`
2. Read current value (defaults to 0 if empty/missing/non-numeric)
3. Increment by 1
4. Write back, release lock
5. Return `→{next:03}` (zero-padded to 3 digits)

IDs are **monotonic** — they never decrease. Gaps can occur if a counter increment succeeds but the needle write fails.

---

## 4. Integrity Notes

- `audit.jsonl` reader (`read_audit_events`) silently skips unparseable lines. This is by design — a single malformed line should not block reading the rest of the audit trail.
- `issues.jsonl` reader (`read_needles`) returns `Err` on malformed lines. A single bad line blocks reading all needles. This is stricter because needle state is critical for scheduling.
- `audit check` (src/commands/audit.rs) verifies referential integrity between `audit.jsonl`, `issues.jsonl`, and git history.
- `audit backfill` scans git log and creates retroactive `bead.committed` events for commits that reference needles or specs but have no audit trail entry.
