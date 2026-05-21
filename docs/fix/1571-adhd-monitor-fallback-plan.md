# →1571 ADHD Monitor Fallback Plan

## Problem
ADHD mode (`~/.myos/.adhd_mode` exists) requires every Agent spawn to be paired with a Monitor in the same turn. In the →1570 session, the Monitor invocation returned `[Tool result missing due to internal error]` — a known harness bug (→1563). No fallback fired, leaving the user with no proactive heartbeats.

Two failures stacked:
1. Monitor itself failed silently (harness-side)
2. pclaude continued without rearming or substituting `ScheduleWakeup` as a fallback

## Investigation Plan

### Q1: Is there a hook that enforces ADHD-mode Monitor pairing?
- Search `.claude/hooks/` for ADHD-related guards
- If none: hole #1 confirmed

### Q2: Why does Monitor fail with internal error?
Hypotheses:
- (a) Script complexity exceeds Monitor command-parse limit
- (b) Monitor harness throttles when called immediately after Agent spawn
- (c) `> /dev/null` redirects or curl `-k` confuses wait_for/output detector
- (d) Something else

### Q3: Root-cause fix (cheapest-first)
- Tier 1: memory update — "when Monitor returns internal error, fall back to ScheduleWakeup every 60s"
- Tier 2: PostToolUse hook that detects Monitor internal-error and emits system reminder
- Tier 3: Hook that auto-arms ScheduleWakeup when ADHD mode is on and Agent spawned without successful Monitor

## Acceptance Criteria
- [ ] Repro documented (exact failing call quoted or script written)
- [ ] Fix implemented at appropriate tier
- [ ] If tier 1: `feedback_adhd_mode_auto_arm_monitor.md` updated with fallback rule
- [ ] If tier 2/3: shell test in `tests/hooks/`
- [ ] `ostk work close "→1571"` after verification
