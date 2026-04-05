---
title: llmOS Network Layer
status: spec
version: 1
created: 2026-03-10
needles: →604 →605 →606
---

# llmOS Network Layer

> Agents claim ports, define protocols, share state. All through the filesystem. Law 3 holds.

## Three Primitives

### Store — shared key-value
```
.haystack/store/{namespace}/{key}     # write: any agent
.haystack/store/{namespace}/{key}     # read: any agent
```
- Gen-tracked (CAS-protected like any file)
- Namespace = agent alias or shared name
- Example: `agent-697` writes `.haystack/store/build/status = "passing"`
- Any agent reads it. Hot PR resolves conflicts.

### Ports — service discovery
```
.haystack/ports/{port}.lock           # bind: claim this port
.haystack/ports/{port}.lock           # discover: read to find who owns it
```
- Lock file contains: `{"alias":"agent-697","pid":1234,"protocol":"my-api.tack"}`
- Claim = write the lockfile (CAS — fails if already claimed)
- Release = delete the lockfile at shutdown
- Connect = read lockfile, send via sh_run or nudge

### Protocols — interface contracts
```
.haystack/protocols/{name}.tack       # agent-defined tack format
```
Format:
```tack
# my-api.tack
.accepts  :query :target :result
.rejects  :exec :mutate
.response tack | json | text
```
Any agent can advertise what tack it accepts. Callers can verify before sending.

## Unix mapping
| Unix | haystack network |
|------|-----------------|
| /var/run/app.pid | .haystack/ports/3000.lock |
| shared memory | .haystack/store/namespace/ |
| socket protocol | .haystack/protocols/api.tack |
| bind() | write port lockfile (CAS) |
| connect() | read port lockfile, contact agent |
| interface contract | .tack protocol file |

## Law 3 preserved
No messaging between agents. No inbox. No broadcast.
Agents write to store/ports/protocols. Other agents read.
The filesystem IS the network. Hot PR IS the network stack.
