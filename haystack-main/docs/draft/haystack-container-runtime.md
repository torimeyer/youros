---
title: "ostk as Container Runtime"
status: draft
version: 1
author: scottmeyer + inner-claude (emerged session 2026-03-07)
created: 2026-03-11
evidence: transcripts/2026-03-07/1018-cc.md
needle: "->615"
compounds: emergent-os, eject-the-harness, os-isolation
---

# ostk as Container Runtime

> Like Docker made namespaces invisible to processes, ostk makes coordination invisible to agents.

## The Analogy

| Docker | ostk |
|--------|----------|
| Container image | Agentfile |
| Namespace isolation | Per-agent gen table view |
| cgroups (resource limits) | Agent token budgets |
| Union filesystem | CAS write path (str_replace) |
| Container registry | fcp-* driver ecosystem |
| docker run | ostk spawn |
| docker ps | ostk ps |
| docker logs | ostk audit |
| Orchestrator (k8s) | The human operator (via tack) |

## The Key Insight

The SWE-bench symlink trick (mish->bash, ss->cat/sed) proves agents CAN be containerized without knowing it. The agent calls `bash` but gets `ostk`. The agent calls `cat` but gets `ss`. Byte-for-byte passthrough for commands, with coordination injected invisibly.

This is exactly what Docker did: processes call `open()` but get a namespaced filesystem. The process doesn't know. The infrastructure handles it.

## Why This Matters

If ostk is a container runtime, then:
1. fcp-* drivers are container images (domain intelligence packaged for reuse)
2. The mesh protocol is container networking
3. Agent lifecycle (spawn, crash, recover) is container lifecycle
4. The write path is the union filesystem

## Acceptance Criteria

- [ ] Document the Docker-to-ostk mapping formally
- [ ] Validate that agent code runs identically with/without ostk
- [ ] Measure overhead of the invisible interception layer
- [ ] fcp-* packaging model follows container image patterns (FROM, COPY, etc.)
