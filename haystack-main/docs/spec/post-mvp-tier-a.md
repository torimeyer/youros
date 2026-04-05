---
status: spec
author: round-table
created: 2026-03-08
discussion: transcripts/discussions/post-mvp-tier-a/
participants: [agentfile-designer, merge-architect, token-economist]
rounds: 1
depends_on: [ostk-mvp]
implements: []
---

# Post-MVP Tier A Spec

> This spec depends on ostk-mvp.md. All features assume the kernel (PTY-owning MCP server, generation table, Hot PR Tier 1+3, identity, digest) is shipped.

**Layer boundary:** Agentfile is userspace. Hot PR Tier 2 and read elision are kernel. See `docs/draft/layer-boundary-kernel-vs-ostk-userspace.md` for the distinction.

**Related needle:** bd-100 (ostk nudge -- kernel injects context into agent's next tool response as a digest annotation) emerged from this session's dogfooding. Nudge uses the same kernel injection mechanism as Tier 2 conflict responses and 304 elision. Consider it alongside Tier 2 when implementing kernel response shaping.

---

## 1. Agentfile

Declarative agent definition using Dockerfile-style directive syntax. One Agentfile describes one agent type. Fleet composition is a separate file.

### Directives

Six directives. No more until shipped and learned.

```dockerfile
# Agentfile
FROM claude-sonnet-4-6
PROMPT "You are a Rust systems engineer. You fix bugs and write tests."
TOOL mish
TOOL ss
SKILL commit
LIMIT context_pct 80
LIMIT budget_usd 2.00
WORK tags=rust,bug priority>=P1
```

| Directive | Purpose | Cardinality |
|-----------|---------|-------------|
| `FROM` | Model selection. The most important decision; goes first. | Exactly 1 |
| `PROMPT` | System prompt. Inline string or `file://path`. | 1 (multiple concatenated in order) |
| `TOOL` | MCP tool the agent can access. Omitted tools are not shimmed. | 0..N |
| `SKILL` | Higher-level capability from the skill registry (tools + prompt fragments). | 0..N |
| `LIMIT` | Resource constraint enforced by the kernel. `context_pct`, `budget_usd`. | 0..N |
| `WORK` | Pull filter. Tag expressions and priority floors. Without WORK, the agent is one-shot. | 0..1 |

### `ostk run` Wiring

```
ostk run rust-fixer.Agentfile
  1. Parse directives
  2. Resolve FROM -> model API endpoint
  3. Build shim directory filtered by TOOL directives
     (only symlink tools declared in TOOL lines)
  4. Set OSTK_AGENT from file hash + instance counter
  5. Set OSTK_SOCKET from project root hash
  6. Set OSTK_MODEL from FROM directive
  7. Inject PROMPT as system prompt via MCP config
  8. Register WORK filters with the daemon's pull scheduler
  9. Apply LIMIT constraints to the agent's token meter
  10. Prepend shim dir to PATH
  11. exec the agent runtime
```

Tool omission is capability restriction. If the Agentfile says `TOOL mish` but not `TOOL ss`, the shim directory contains `bash` but not `cat`. The agent literally cannot call what is not shimmed.

### Fleet Composition (Separate File)

```yaml
# ostk-compose.yaml
agents:
  rust-fixer:
    agentfile: agents/rust-fixer.Agentfile
    replicas: 2
  reviewer:
    agentfile: agents/reviewer.Agentfile
    replicas: 1
```

YAML is correct here because it describes desired state, not a build sequence. `ostk up` reads this file and starts agents. `ostk up --headless` for CI.

The Agentfile does not know about other agents. The compose file does not know about prompts or tools.

### WORK Declares Pull, Not Push

The WORK directive replaces any `PULL auto` / `PULL_FILTER` / `PULL_THRESHOLD` trio from drafts. One directive instead of three. The pull threshold comes from `LIMIT context_pct`. The filter comes from the WORK tag expression.

An agent without WORK is one-shot: `ostk run` starts it, it does its task, it exits. An agent with WORK is a long-running worker: it pulls tasks, completes them, pulls more. The distinction is declarative in the Agentfile, not a runtime flag.

### Acceptance Criteria

- [ ] Agentfile parser handles all six directives (FROM, PROMPT, TOOL, SKILL, LIMIT, WORK)
- [ ] `FROM` resolves to a model API endpoint
- [ ] `PROMPT` supports inline strings and `file://` references
- [ ] `TOOL` directives control shim directory construction (only declared tools are symlinked)
- [ ] Omitting a TOOL prevents the agent from calling that tool (capability restriction via PATH)
- [ ] `SKILL` loads tool bundles + prompt fragments from the skill registry
- [ ] `LIMIT context_pct` is enforced by the kernel as a pull threshold
- [ ] `LIMIT budget_usd` is enforced as a spending cap
- [ ] `WORK` tag expressions and priority floors register with the pull scheduler
- [ ] Agent without WORK directive runs as one-shot (exits after task completion)
- [ ] Agent with WORK directive runs as long-lived worker (pulls from work queue)
- [ ] `ostk run <agentfile>` executes the full wiring sequence (parse, shim, env, exec)
- [ ] `ostk-compose.yaml` controls fleet composition (agent types, replicas)
- [ ] `ostk up` starts agents from compose file
- [ ] `ostk up --headless` works for CI
- [ ] Agentfile does not reference other agents; compose file does not reference prompts or tools

---

## 2. Hot PR Tier 2 (Assisted Merge)

Kernel feature. When CAS fails and the edits are close but not too complex, the kernel presents a suggested merge to the agent. No new tools. No sampling. The agent confirms or rejects via normal `ss()`.

### Tier Classification

The CAS fails. The kernel computes the diff between the agent's base gen and current gen, then locates both edits by line range.

| Condition | Tier | Action |
|-----------|------|--------|
| Edit ranges separated by >3 lines | Tier 1 | Silent auto-merge (MVP) |
| Edit ranges overlap OR within 3 lines (proximity zone) | Tier 2 | Assisted merge response |
| Edit touches >30 changed lines, or >2 disjoint conflict regions | Tier 3 | Manual rebase (MVP) |

The proximity threshold (3 lines) is a kernel constant. Adjacent edits that don't textually overlap can still break each other -- a function signature change on line 10 and a new call on line 12 need the agent's eyes. The 30-line / 2-region threshold for Tier 3 is the point where a suggested merge is more confusing than a fresh read.

### Response Format

A Tier 2 conflict returns a single `ss` tool response with the `[conflict]` status tag. Same skeleton as every other kernel response:

**Simple case (agent's old_str still exists in current file):**

```
[conflict] src/main.rs:gen=6 diff:+/-8 suggest:ready

--- base (gen 5, yours)
+++ current (gen 6, by ridge)
@@ -41,4 +41,6 @@
 def process(data):
+    if not data:
+        raise ValueError("empty input")
     result = transform(data)
     return result

Your edit (against gen 5):
  old: "result = transform(data)"
  new: "result = transform(data, validate=True)"

Suggested merge (applies your edit to gen 6):
  old: "result = transform(data)"
  new: "result = transform(data, validate=True)"

To accept: ss("src/main.rs", "result = transform(data)", "result = transform(data, validate=True)")
```

**Interacting edits (agent's old_str modified by the other agent):**

```
[conflict] src/lib.rs:gen=12 diff:+/-5 suggest:ready

--- base (gen 11, yours)
+++ current (gen 12, by vane)
@@ -20,3 +20,3 @@
-fn connect(host: &str) -> Connection {
+fn connect(host: &str, timeout: Duration) -> Connection {

Your edit (against gen 11):
  old: "fn connect(host: &str) -> Connection {"
  new: "fn connect(host: &str, port: u16) -> Connection {"

Suggested merge (applies your edit to gen 12):
  old: "fn connect(host: &str, timeout: Duration) -> Connection {"
  new: "fn connect(host: &str, port: u16, timeout: Duration) -> Connection {"

To accept: ss("src/lib.rs", "fn connect(host: &str, timeout: Duration) -> Connection {", "fn connect(host: &str, port: u16, timeout: Duration) -> Connection {")
```

### Agent Confirmation

Same `ss()` call. No new tool. The agent:

- **Accepts:** Issues the exact `ss()` call shown in "To accept." Normal CAS, normal gen bump.
- **Modifies:** Issues a different `ss()` call using the current file content (which the diff already revealed). The agent is now working against the current gen, not its stale base.
- **Rejects:** Reads the file fresh and starts over. Voluntary downgrade to Tier 3 behavior.

No confirmation token, no `hp_accept()`, no special flag. The write path stays invisible.

### Merge Suggestion: Kernel Logic, Not Sampling

The kernel does NOT use MCP sampling for Tier 2. The suggestion is mechanical:

1. Parse the agent's `old_str`/`new_str` as a text substitution.
2. Locate `old_str` in the current file (gen N+1). If it still exists verbatim, the suggestion is trivial: same substitution, new base.
3. If `old_str` doesn't exist verbatim but the diff is small, apply the agent's intended transformation to the corresponding region in gen N+1 using line-level alignment.
4. When mechanical rebasing fails (the transformation can't be aligned), escalate to Tier 3.

Sampling is reserved for Tier 4 diagnostic queries to fcp-* drivers. Tier 2 is a textual operation. Adding an LLM call to the write path violates the latency contract and introduces non-determinism into conflict resolution.

### Generation Table Interaction

One bump, not two. The Tier 2 response does not write anything. The gen stays at N+1 (from the other agent's edit). When the resolving agent issues the accepting `ss()` call, that's a normal successful write: gen goes to N+2. Same as two sequential non-conflicting edits.

No phantom gen bumps. No "conflict-in-progress" state in the table. The generation table only reflects successful writes.

### Acceptance Criteria

- [ ] Proximity threshold (3 lines) implemented as a kernel constant
- [ ] Tier 2 fires when edit ranges overlap or are within 3 lines of each other
- [ ] Tier 2 does NOT fire when >30 changed lines or >2 disjoint conflict regions (escalates to Tier 3)
- [ ] Conflict response uses `[conflict]` status tag in normal `ss` tool response
- [ ] Response includes: base gen diff, agent's intended edit, suggested merge, "To accept" ss() call
- [ ] Suggested merge is computed mechanically (line-level alignment), not via LLM sampling
- [ ] When agent's `old_str` still exists verbatim in current gen, suggestion is trivial (same substitution)
- [ ] When `old_str` was modified, suggestion applies agent's transformation to current content via line alignment
- [ ] When mechanical rebasing fails, conflict escalates to Tier 3 (no partial suggestions)
- [ ] Agent accepts by issuing the exact `ss()` call from the suggestion -- normal CAS, normal gen bump
- [ ] Agent can modify by issuing a different `ss()` call against current file content
- [ ] Agent can reject by reading the file fresh (voluntary Tier 3 downgrade)
- [ ] No new tools, no confirmation tokens, no special flags
- [ ] Single gen bump on resolution (N+1 from other agent, N+2 from resolution -- same as sequential edits)
- [ ] No phantom gen bumps or "conflict-in-progress" state in the generation table
- [ ] bd-100 (ostk nudge) mechanism considered: Tier 2 responses and nudge injections use the same kernel response injection path

---

## 3. Read Elision (304 Not Modified)

Kernel feature. When an agent reads a file it has already read and the file hasn't changed, the kernel returns a 5-token confirmation instead of the full file contents.

### 304 Response Format

When `hwm[agent][path] == gen_table[path].gen`:

```
[304] src/main.rs:gen=7 (current)
[procs] cc:running:85m gem:running:52s
[files] (none stale)
```

5 tokens replaces ~800. The gen number confirms identity, not just "unchanged." If the agent's hwm is missing (first read, or compaction wiped context), always return full content and set the hwm.

### High-Water Mark Tracking

Per-agent, per-file. The kernel maintains `hwm[agent][path]` recording the last gen each agent read for each file. Updated on every successful read (full content or 304). The hwm lives in the generation table alongside file gens, writer identity, and heartbeat timestamps.

### stat-on-access: External Modification Bypass

Agents or users may write via raw `cat > file` or an editor, bypassing `ss`. The gen table doesn't know.

On every `ss_session("read")`, compare `mtime` against the gen table's last-known mtime:

- **mtime matches:** Trust gen table, apply 304 logic.
- **mtime differs:** File was modified outside ostk. Invalidate all agent hwms for that file. Bump gen. Return full content. Log `[bypass] src/main.rs externally modified, gen 7->8`.

Cost: one `stat()` syscall per read. Negligible. This is exactly how HTTP caches validate with `Last-Modified`.

### Two-Layer Token Defense

1. **Digest suppression:** File absent from `[files]` digest = current = agent doesn't bother reading (~0 tokens).
2. **304 interception:** Agent reads anyway = kernel returns confirmation (~5 tokens).

Both layers are invisible. The agent never learns a protocol. It just pays less.

### Delta Response (Deferred)

When a file has changed, the kernel could return only the diff since the agent's hwm gen instead of full content (~40-80 tokens vs ~800). This requires the kernel to store edit history per gen (a write-ahead edit log). Deferred: the 304 path captures the majority of savings because agents re-read files far more often than files actually change.

### Token Math

Constants: 200-line file = ~800 tokens. 304 response = ~5 tokens.

**Single agent, single file, 10 reads:**
- Without elision: 10 x 800 = **8,000 tokens**
- File changes twice (reads 3 and 7): 2 x 800 + 8 x 5 = **1,640 tokens**
- Savings: **6,360 tokens (79%)**

**5 agents, 10 files each, 10 reads per file:**
- Without elision: 5 x 10 x 10 x 800 = **400,000 tokens**
- Each file changes ~3 times: 5 x 10 x (3 x 800 + 7 x 5) = **121,750 tokens**
- Savings: **278,250 tokens (70%)**

At $3/M input tokens (Claude): $1.20 per session dropped to $0.37. Across 20 sessions/day: **$16.60/day saved**. Real-world ratio is better -- agents compulsively re-read files that haven't changed. Empirically 80-90% of reads are redundant.

### Acceptance Criteria

- [ ] Per-agent, per-file high-water mark (hwm) tracked in the generation table
- [ ] hwm updated on every successful read (full content or 304)
- [ ] When `hwm[agent][path] == gen_table[path].gen`, return `[304]` response (~5 tokens) instead of full content (~800 tokens)
- [ ] 304 response includes gen number, `[procs]` digest, and `[files]` digest
- [ ] Missing hwm (first read, compaction) always returns full content and sets hwm
- [ ] stat-on-access: `mtime` compared against gen table's last-known mtime on every read
- [ ] mtime mismatch invalidates all agent hwms for that file, bumps gen, returns full content
- [ ] External modification logged as `[bypass]` with path and gen transition
- [ ] Digest suppression: current files absent from `[files]` digest (first defense layer)
- [ ] 304 interception: redundant reads caught by kernel (second defense layer)
- [ ] Both layers invisible to the agent (no protocol to learn)
- [ ] Delta response deferred (no write-ahead edit log required for this tier)
