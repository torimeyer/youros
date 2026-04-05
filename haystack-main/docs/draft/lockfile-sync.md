---
status: draft
author: scottmeyer
created: 2026-03-07
---

# Lockfile Synchronization Primitive

> Cheap, dirty, filesystem-based coordination. No polling, event-driven.

## Problem

Orchestrator sends work to agent, needs to know when it's done. Today's
options all suck:
- `read_tail` polling: wastes orchestrator turns, O(n) per agent
- `wait_for` regex: blocks a mish task, still polling under the hood
- Shared Python REPL: crashed the daemon

## The Primitive

```
1. Orchestrator: touch /tmp/ostk-locks/<agent>.pending
2. Orchestrator: sends work to agent
3. Agent: does work, writes output to agreed path
4. Agent: rm /tmp/ostk-locks/<agent>.pending
5. Mish: kqueue/inotify on lockfile dir → fires event to orchestrator
6. Orchestrator: reads output file
```

### For `claude -p` workers (spawn primitive):

```bash
touch /tmp/ostk-locks/bd-050.pending
cat spec.md | claude -p --team-name ostk --agent-id bd-050 \
  --agent-name writer "..." > /tmp/ostk-output/bd-050.md
rm /tmp/ostk-locks/bd-050.pending
```

The orchestrator watches the locks dir. When `bd-050.pending` disappears,
the output is ready at `/tmp/ostk-output/bd-050.md`.

### For mish integration:

New `sh_run` option or new tool:

```
sh_watch(path="/tmp/ostk-locks/", event="delete", timeout=300)
→ blocks until any file is deleted in the watched dir
→ returns: {event: "delete", path: "bd-050.pending", elapsed_ms: 12400}
```

Or simpler — a background process:

```
sh_spawn(alias="watcher", cmd="inotifywait -e delete /tmp/ostk-locks/")
```

macOS equivalent: `fswatch -1 /tmp/ostk-locks/`

### For multi-agent:

```
touch /tmp/ostk-locks/bd-050.pending
touch /tmp/ostk-locks/bd-051.pending
touch /tmp/ostk-locks/bd-052.pending

# Spawn 3 workers in background...

# Wait for ALL to complete:
while ls /tmp/ostk-locks/*.pending 2>/dev/null | grep -q .; do sleep 1; done
echo "all done"
```

Or wait for ANY (first-to-finish):
```
fswatch -1 /tmp/ostk-locks/  # returns on first file event
```

## Why Filesystem

- Works across processes (no shared memory needed)
- Works across mish instances (no daemon needed)
- Survives daemon crashes (files persist)
- Visible to humans (`ls /tmp/ostk-locks/`)
- kqueue/inotify = zero-cost wait (kernel does the work)
- Atomic operations (touch, rm are atomic)

## Acceptance Criteria

- [ ] Lock creation before work dispatch
- [ ] Lock removal by agent on completion
- [ ] Watcher detects removal within 1s
- [ ] Multi-agent: wait-for-all and wait-for-any patterns
- [ ] Works with `claude -p` spawn primitive
- [ ] Survives daemon restart
