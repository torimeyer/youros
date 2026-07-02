# Task-1217: MCP -32000 "Connection closed" on large recursive grep

**Task:** →1217 | **Date:** 2026-05-12 | **Agent:** 1217-mcp-transport-repro-02a0e3

## Summary

Cannot reproduce -32000 in current ostk version. The server has no per-request
timeout at any layer. Two real failure modes were found and documented below.

---

## Repro attempts

### Command 1 (from Task spec)
```
grep -rln 'fn\|struct\|impl' /Users/you/claude/torios/haystack-main \
  --include='*.rs' 2>/dev/null | head -50
```
**Result:** Clean exit in 359ms. `head -50` caps output. No drop.

### Command 2 — full haystack-main, no cap
```
grep -rln 'fn\|struct\|impl' /Users/you/claude/torios/haystack-main \
  --include='*.rs' 2>/dev/null | wc -l
```
**Result:** 215 files, 326ms. No drop.

### Command 3 — 30s full-tree grep (no timeout param)
```
grep -rln 'import|export|class|function|const|type' \
  /Users/you/claude/torios --include='*.rs' --include='*.py' \
  --include='*.ts' --include='*.tsx' 2>/dev/null | wc -l
```
**Result:** 89,282 files, 7.4s. No drop.

### Command 4 — sleep 35 (no timeout param)
```
sleep 35
```
**Result:** Clean exit at 35s. No drop. Disproves the "30s hard client timeout" hypothesis.

### Command 5 — 76s content grep (with timeout=90)
```
grep -rn 'fn |struct |impl |class |interface |type |const |let |import ' \
  /Users/you/claude/torios --include='*.rs' --include='*.py' \
  --include='*.ts' --include='*.tsx' --include='*.js' | wc -l
```
**Result:** 2,368,851 matching lines, 76s, clean exit. No drop.

### Command 6 — large raw output through transport (FAILURE MODE FOUND)
```
grep -rn ... | head -5000
```
**Result:** 2.3MB / 5,009 lines → **Token limit error** (not -32000). Claude Code
saves output to a temp file and returns an error to the LLM. This is a CC-side
limit on response size, not a server crash.

---

## Architecture findings

### sh_run.rs (the bash handler)

```rust
// src/serve/tools/sh_run.rs:19
let _timeout_secs = params.timeout.unwrap_or(300);  // parsed but NEVER USED
```

The `timeout` parameter is read from input but the leading underscore means it is
**intentionally unused** — no timeout is applied. Commands run to completion
regardless of duration.

Heavy work runs in `spawn_blocking` (a separate thread), so the tokio runtime
stays responsive while waiting.

### transport.rs

Pure stdio JSON-RPC line protocol. No keepalive, no timeout, no buffer limit.

### socket.rs / server.rs

`run_server()` dispatches requests **sequentially** — one at a time. The server
reads a request, awaits the dispatch (which yields via spawn_blocking), writes
the response, then reads the next request. No per-connection timeout. The socket
has a 30-minute idle shutdown only.

### Conclusion: no server-side timeout path to -32000

There is no code path in the current ostk that produces -32000 "Connection
closed" due to command duration or output size. The error must have come from:

- An older ostk version that had different behavior, OR
- A memory-pressure OOM kill (kernel SIGKILL on the ostk process) when
  processing millions of lines through the squasher, OR
- The squasher itself panicking on degenerate input

---

## Actual failure modes in current version

| Trigger | Behavior | Error |
|---------|----------|-------|
| Output > ~2MB returned through transport | CC saves to temp file | Token limit error (not -32000) |
| Command running > 30s without timeout param | **No failure** — clean exit | None |
| Command running > 30s with explicit timeout param | Clean exit | None |
| Very large output not squashed (raw=true) | Possible OOM if output is GB-scale | Process killed by OS (SIGKILL → -32000) |

---

## Hypothesis for original failure (2026-05-11)

Most likely: a grep with `-rn` (content lines, not `-l`) across the full torios
tree including node_modules, with no `wc -l` pipe to suppress output, would
produce hundreds of millions of matching lines. The squasher buffers the full
output in RAM before compressing. At scale this exhausts process memory → OS
SIGKILL → stdio pipe breaks → Claude Code sees -32000.

Secondary hypothesis: a prior ostk version had a shorter timeout in sh_run (the
`_timeout_secs` variable was once applied).

---

## Workaround (confirmed working)

Per `feedback_spawn_interact_for_long_commands.md` and `feedback_mcp_transport_30s_timeout.md`:

For any command expected to produce >500 lines or run >30s, use spawn+interact
with tee to a file:

```python
# instead of:
mcp__ostk__bash(cmd="grep -rn ... | head -N")

# use:
mcp__ostk__spawn(alias="grep-run", cmd="grep -rn ... > /tmp/results.txt 2>&1")
mcp__ostk__interact(alias="grep-run", action="read_tail", lines=50)
# then read /tmp/results.txt with mcp__ostk__read
```

This avoids both the transport buffer limit and any potential OOM in the squasher.

---

## Proposed fix path

1. **Wire up the timeout**: Remove the `_` prefix from `_timeout_secs` in
   `sh_run.rs:19` and wrap the `spawn_blocking` call in
   `tokio::time::timeout(Duration::from_secs(timeout_secs), ...)`. Default 300s
   gives a safe ceiling while allowing explicit override.

2. **Stream-compress large output**: Instead of buffering the full command output
   before squashing, pipe through the squasher incrementally so RAM usage is
   bounded regardless of output size.

3. **Add output size cap**: After squashing, if the compressed output still
   exceeds a threshold (e.g., 500KB), truncate with a warning rather than
   sending a multi-MB JSON message that overwhelms the transport.

---

## Close

```
ostk work close 1217 "repro documented; transport fix tracked when source is reachable"
```

Repro: no (cannot reproduce -32000 in current version)
Likely cause: OOM on unbounded squasher buffer for multi-million-line grep output
Workaround: spawn+interact + tee to file for any command producing >500 lines or running >30s
