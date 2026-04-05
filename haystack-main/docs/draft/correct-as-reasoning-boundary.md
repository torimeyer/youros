---
title: ":correct as Reasoning Boundary"
status: draft
version: 1
author: scottmeyer (emerged session 2026-03-08)
created: 2026-03-11
evidence: docs/insights-session-2026-03-08.md line 187
needle: "->613"
compounds: HUMANFILE, intent-dynamic-programming, tack
---

# :correct as Reasoning Boundary

> Reasoning happens at the correction boundary. When the human says :correct, the agent's inference meets reality and adjusts. The correction is not a failure -- it is the reasoning event.

## The Insight

Without :correct, the agent runs inference indefinitely, producing plausible-but-wrong output that compounds errors. The correction boundary is where:

1. Agent inference meets ground truth
2. The HUMANFILE memoization table updates
3. Future lookup cost decreases
4. The OS learns what the human actually means

## The Compounding Effect

```
Session 1:  :correct X        -> 3 turns to calibrate
Session 2:  :correct X        -> 1 turn
Session 5:  :c X              -> instant
Session 10: the agent doesn't make that mistake anymore
```

Each :correct is a dp[i] = solution entry. The HUMANFILE is the memoization table. The dynamic programming runs across sessions.

## Acceptance Criteria

- [ ] :correct updates recorded in audit trail with before/after state
- [ ] HUMANFILE correction entries have TTL and decay rate
- [ ] Correction frequency per-verb measurable (which verbs need most correction?)
- [ ] Correction-free streaks tracked (boot confidence signal)
