---
title: metrics and telemetry
status: draft
created_at: 2026-03-08T02:46:42Z
author: orchestrator
---

# Metrics & Telemetry -- The Adoption Story

> "Your first 100M tokens free on ANY model."
> We don't host anything. Our cost is the binary. The value is what we measure.

## The Statusline

Every ostk session shows a persistent statusline:
```
[ostk] tokens saved: 47,832 | conflicts resolved: 12 | turns saved: 8 | agents: 3
```
This is the value proof. The user sees, in real-time, what ostk is doing for them.

## What We Measure

### Token Economics (the headline metric)
- Tokens saved via read elision (304): ~800 per elided read
- Tokens saved via output compression (squasher): raw vs compressed delta
- Tokens saved via digest suppression: files NOT re-sent
- Tokens added by digest overhead: ~40-80 per response
- Net token savings: the number users care about

### Coordination Value
- Conflicts auto-merged (Tier 1): edits that would have clobbered
- Conflicts assisted (Tier 2): resolved in one turn vs full retry
- Conflicts escalated (Tier 3): manual rebase needed
- Turns saved by auto-merge: each saves 2-3 turns
- Agent crashes detected via heartbeat
- DMA bypasses caught

### Agent Performance
- Agent uptime, context burn rate, tool calls per bead
- Recovery success rate (resumed vs started fresh)

### Audit Health
- Audit completeness %: beads with commits / total beads
- Orphaned commits, remap chain depth

## Collection Architecture

### Local (always, free tier)
- All metrics computed locally from existing event streams
- .ostk/metrics.jsonl -- append-only
- `ostk metrics` shows session summary
- `ostk metrics --lifetime` shows cumulative
- Statusline reads from metrics in real-time

### Enterprise (opt-in)
- Anonymous aggregate telemetry
- Fleet-wide dashboards
- Model cost comparison (sonnet vs opus ROI per task type)

## Business Model

We don't host models. We don't proxy API calls. Our cost is:
- Binary distribution (static, pennies)
- Telemetry ingestion (enterprise only)
- Support + SLA (enterprise only)

Free tier is free FOREVER. No marginal cost. Adoption flywheel:
1. Install ostk (free, single binary)
2. Statusline shows "47K tokens saved this session"
3. User sees value without paying
4. Enterprise wants fleet visibility -> paid tier

## Prior Art in Existing Docs
- resource-limits.md: budget enforcement, cost tracking
- llmOS.md: "scarce resources are cognitive"
- agent-lifecycle.md: "context burn rate >2%/min" as health signal
- milestones.md: "150 agent-minutes, zero shipped code" -- the metric that proved subagent pattern

## Acceptance Criteria

- [ ] Statusline shows token savings, conflict resolutions, active agents
- [ ] Token savings computed from read elision, compression, digest suppression
- [ ] Coordination metrics from Hot PR events
- [ ] `ostk metrics` shows session summary
- [ ] `ostk metrics --lifetime` shows cumulative
- [ ] Metrics in .ostk/metrics.jsonl (append-only)
- [ ] Zero performance impact on agent operations
- [ ] Enterprise telemetry is opt-in, anonymous, aggregate
- [ ] Free tier has no expiration, no feature gates, no usage limits
