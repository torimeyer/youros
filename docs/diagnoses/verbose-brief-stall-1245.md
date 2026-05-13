# Verbose brief stall — root cause (→1245)

Hypothesis class (a): extended thinking from large context accumulation.

## Evidence

Verbose agent (diagnose-verbose-brief-stall-664017):
- Spawn prompt: 2144 chars, 1 DO NOT, 0 numbered steps
- Grepped 969MB of session files (1323 files) to find spawn prompts (took 2.5 min)
- Accumulated 4388-char prompt + transcripts in context
- At minute 7: entered 20-min thinking freeze, never resumed
- Transcript flat at 1923 bytes; heartbeats continued (process alive, model ceased)

Tight-brief control (fix-1240-cwd-leak-bb772e):
- Specific file+line instructions, no open-ended searches
- Completed in 14 min, 3319 bytes transcript

## Falsification

- (b) DO NOT count: agent had 1 DO NOT, stalled anyway — not the trigger
- (c) Bridge corruption: 7 min of active transcript shows prompt intact
- (d) Cache miss: one-call latency penalty, not a 20-min freeze

## Fix

Pre-resolve file:line before spawning. Never write "find X in Y."
Memory: `feedback_verbose_brief_stall_root_cause.md`
