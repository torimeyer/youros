---
title: sprint 5 launch plan
discussion: transcripts/discussions/retro-sprints-3-4/
promoted_at: 2026-03-08T04:43:11Z
created_at: 2026-03-08T04:42:37Z
status: spec
author: round-table
implements: []
---

# Sprint 5: Launch Plan

## Phase 1: Fix + Wire (1 hour)
- [ ] Fix create_file overwrite bug — error if file exists, not silent overwrite
- [ ] Wire nudge pop into dispatch — pop_nudges on every tool response, inject as [nudge]
- [ ] Wire recovery logging into dispatch — log_tool_call on every tool call
- [ ] Wire recovery generation into dispatch — generate_recovery on alias reconnect

## Phase 2: Integration Tests (gate Monday launch)
- [ ] CAS round-trip test: Tier 1 auto-merge through full MCP JSON-RPC path
- [ ] CAS round-trip test: Tier 2 assisted merge through full MCP path
- [ ] CAS round-trip test: Tier 3 conflict through full MCP path
- [ ] 304 read elision through full MCP path — first read Full, second read 304
- [ ] Digest injection — every tool response includes [procs] line
- [ ] Multi-process race — two processes, flock coordination, no corruption
- [ ] Create_file overwrite guard — verify error on existing file
- [ ] PTY passthrough — bash -c through shim produces correct output
- [ ] Cutover smoke — symlink bash to ostk, run 10 common commands

## Phase 3: File Layer (native Rust ss/ss_session)
- [ ] ss tool: str_replace with CAS, batch ops support (ops=[...] array)
- [ ] ss tool: file creation (path + new_str, no old_str)
- [ ] ss_session tool: read with line range (start:N end:N)
- [ ] ss_session tool: read with 304 elision
- [ ] ss_session tool: open, flush, close, status, list session lifecycle
- [ ] Batch read: multiple file reads in one call
- [ ] All ss operations go through gen_table + Hot PR
- [ ] --agents guide updated with ss/ss_session tool documentation

## Phase 4: SWE Bench (one run, full stack)
- [ ] ostk replaces both mish AND ss in bench harness
- [ ] Zero prompt injection — interstitial + --agents guide only
- [ ] Full kernel active: Hot PR, 304, digest, identity, heartbeat
- [ ] Measure: cost, resolve rate, tool adoption, token savings
- [ ] Compare against v18 baseline (7/10, $5.80, 20% savings)

## Phase 5: TUI + Cutover
- [ ] ostk tui shows: beads, needles, audit trail, agent status
- [ ] Statusline shows token savings in real time
- [ ] Cut over dev environment: bash symlink to ostk
- [ ] Retire mish+ss from dev workflow
- [ ] Monday launch: free tool, solicit bug reports
