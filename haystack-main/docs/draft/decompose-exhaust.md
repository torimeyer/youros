---
title: "Decompose Exhaust — Organic vs Mechanical Needles"
status: draft
version: 1
author: scottmeyer (emerged retro sprints 3-4)
created: 2026-03-11
evidence: transcripts/discussions/meta-analysis-needle-guidance.md
needle: "->614"
compounds: needle-spec, meta-analysis-needle-guidance
---

# Decompose Exhaust

> A needle finds the thing. Decompose exhaust buries it.

## The Evidence

From meta-analysis of 231 needles across 4 sprints:

| Source | Total | Closed | Close Rate |
|--------|-------|--------|------------|
| Organic (self-discovered) | 79 | 35 | **44%** |
| Decomposed (mechanical) | 152 | 34 | **22%** |

Agents are 2x more effective at closing work they discovered themselves versus work mechanically generated from specs.

## Why Decompose Exhaust Fails

1. **No file paths.** Decomposed beads say "implement X" but not where.
2. **No test expectations.** Aspirational acceptance criteria, not verifiable assertions.
3. **No code sketch.** The orchestrator didn't do the research.
4. **No sequential order.** 18 beads dumped at once, no dependency graph.

## What Good Needles Look Like

- bd-001: "next_bead_id has no flock -- concurrent agents get duplicate IDs." One fix, one file.
- bd-151 through bd-163: 13 beads, all closed, one commit. Each = single mechanical step + test assertion.

## The Fix

Decompose should warn on needle quality, not just generate acceptance criteria checkboxes. A good needle has: verb + location + test. A bad needle has: a sentence describing a desired state.

## Acceptance Criteria

- [ ] `ostk decompose` warns when generated needles lack file paths
- [ ] `ostk decompose` warns when generated needles lack test expectations
- [ ] Needle quality score: has_verb + has_path + has_test (0-3)
- [ ] Organic vs decomposed close rate tracked in audit trail
