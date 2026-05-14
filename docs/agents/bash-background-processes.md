# Background Processes in mcp__ostk__bash

## TL;DR

**Use `mcp__ostk__spawn` for long-running background services** (Vite, uvicorn, etc.).

When you must background a process inside `mcp__ostk__bash`, always redirect stdout and stderr:

```bash
# CORRECT — pipe write-end is closed before the background process starts
nohup scripts/dev-frontend.sh > /tmp/dev-frontend.log 2>&1 < /dev/null & disown

# WRONG — background process inherits the pipe write-end; mcp__ostk__bash blocks until it dies
nohup scripts/dev-frontend.sh &
```

---

## The Failure Mode

`mcp__ostk__bash` captures subprocess output via Python's `subprocess.communicate()`, which
reads from the stdout pipe until EOF. EOF only arrives when **every process holding the pipe
write-end (fd 1) has closed it**.

When you background a process without redirecting stdout:

```bash
nohup long-running-server &   # no > redirect
sleep 6
curl https://127.0.0.1:3010 ...
```

What happens:
1. Shell forks a child for `nohup long-running-server &`
2. The child inherits fd 1 = the pipe write-end going to `mcp__ostk__bash`
3. `nohup` sees that stdout is not a terminal, so it does NOT redirect stdout
4. The server starts with fd 1 = the orchestrator pipe still open
5. The foreground commands (`sleep 6; curl ...`) complete and the shell exits
6. The shell closes its fd 1 — but the server still holds its copy
7. `mcp__ostk__bash`'s `communicate()` waits for pipe EOF
8. EOF never comes until the server is killed
9. **The tool call blocks for as long as the server runs** (minutes to hours)

## Why `> file 2>&1` Fixes It

When the shell redirects stdout *before* the fork:

```bash
nohup long-running-server > /tmp/log 2>&1 &
```

1. Shell forks the child
2. Child applies `> /tmp/log`: `dup2(open("/tmp/log"), 1)` — this **closes** the old fd 1 (the pipe)
3. `2>&1`: `dup2(1, 2)` — fd 2 also becomes the log file, old fd 2 pipe closed too
4. `nohup` execs the server; it has fd 1 = log file, fd 2 = log file
5. The orchestrator pipe write-end has zero remaining holders in the background process
6. When the shell exits, `communicate()` gets EOF immediately

## Lab Results (2026-05-14, needle 1350)

| Pattern | Outer call time | Blocks? |
|---------|----------------|---------|
| `nohup sleep 10 &` (no redirect, pipe context) | ~10s | Yes — blocked until sleep exited |
| `nohup sleep 10 > /dev/null 2>&1 &` | < 1s | No |
| Variant A: `nohup dev-frontend.sh > log 2>&1 &` | ~2s | No |
| Variant B: `setsid dev-frontend.sh > log < /dev/null & disown` | ~2s | No |
| Variant C: `nohup dev-frontend.sh > log 2>&1 < /dev/null & disown` | ~2s | No |

All three variants return quickly because all three redirect stdout. The `< /dev/null` and
`disown` add defensive hygiene but are not what prevents the pipe hang.

## Other Causes of Long Tool Calls

Even with proper stdout redirect, `mcp__ostk__bash` can appear to hang if foreground
commands take too long:

- `curl` with no timeout flags, waiting for a server that is slow to respond
- A `sleep N` that is shorter than the server's actual startup time

**Always use timeout flags in curl readiness checks:**

```bash
# WRONG — curl waits indefinitely if the server is slow or TLS handshake hangs
curl -ks https://127.0.0.1:3010 -o /dev/null

# CORRECT — fail fast, let the caller retry or poll
curl --connect-timeout 3 -m 10 -ks https://127.0.0.1:3010 -o /dev/null || true
```

## The Safe Background Pattern

```bash
# Full safe pattern for any long-running process started via mcp__ostk__bash:
nohup some-command arg1 arg2 \
  > /tmp/some-command.log 2>&1 \
  < /dev/null \
  & disown
```

- `> /tmp/file.log 2>&1` — closes the orchestrator pipe write-ends in the child (the fix)
- `< /dev/null` — closes stdin; prevents blocking on PTY or terminal reads
- `nohup` — makes the process survive SIGHUP when the shell exits
- `disown` — removes the job from the shell's job table; prevents "job N: done" noise

## Canonical Approach: Use mcp__ostk__spawn

For long-running services, **use `mcp__ostk__spawn` instead of bash**:

```
mcp__ostk__spawn(
  alias="frontend",
  cmd="scripts/dev-frontend.sh",
  wait_for="ready on port 3010"
)
```

`spawn` uses kernel-side process supervision, returns immediately, and never creates pipe
inheritance issues. Use `interact(alias, action="kill")` to tear down the server cleanly.

## File Descriptors in mcp__ostk__bash Subprocesses

When `mcp__ostk__bash` runs, the bash subprocess has these key file descriptors:

- fd 0: `/dev/null` (stdin)
- fd 1: pipe write-end to the orchestrator's stdout (what communicate() reads)
- fd 2: pipe write-end to the orchestrator's stderr (a separate pipe)

Any background process that inherits fd 1 or fd 2 without closing them will block the tool
until it exits. Higher-numbered fds (ptmx devices visible via `lsof -a -p $$`) are inherited
from Claude Code's process table and do NOT affect the tool's return timing.
