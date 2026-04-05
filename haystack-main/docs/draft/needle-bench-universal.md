# needle-bench: A Universal Intelligence Benchmark

> Can you find what matters?

## The Claim

Every intelligence benchmark ever built tests a skill. HumanEval tests code generation. GPQA tests domain knowledge. SWE-bench tests debugging. needle-bench tests the capability that precedes all of them: **finding meaningful signal in a noisy environment with no guidance.**

This is not a coding benchmark. It is an orientation benchmark. The thing being measured is discovery — how quickly an intelligence, dropped into a dark room, locates the thing that matters.

## The Abstraction

A **needle** is any important signal hidden in noise. A **ostk** is any complex environment where that signal lives.

| Intelligence | Needle | ostk |
|-------------|--------|----------|
| LLM agent | A bug causing test failure | A broken Docker container |
| New engineer | The architecture decisions that matter | A 200k-line codebase on day one |
| Crashed agent | What changed while compacted | Stale boot state + live filesystem |
| Returning human | The state of the project after a week away | 47 commits, 12 closed issues, 3 new specs |
| Incident responder | The root cause | 10,000 log lines, 4 dashboards, 2am |

The environment changes. The task does not. Find the signal. Ignore the noise. Act on what you found.

## The Metrics Generalize

needle-bench scores three dimensions, and all three are domain-invariant:

**Turns to discovery** is **time to orientation**. How many actions before the intelligence identifies the actual problem — not a symptom, not a guess, the real thing? For an LLM, turns. For a human, hours. For a team, days. The unit changes. The measurement does not.

**Blind discovery** is **no hints needed**. Did the intelligence find the signal without being told where to look? A benchmark that says "the bug is in `auth.py`" tests coding. A benchmark that says "something is broken" tests intelligence. The gap between those two scores — guided versus unguided — is the discovery delta, and it is the most revealing number needle-bench produces.

**Signal-to-noise ratio** measures environment hostility. A 10-file project with one bug is easy. A 500-file project with misleading error messages, red herrings in the logs, and three bugs where only one matters — that is the ostk getting darker. The same intelligence, scored across increasing noise, produces a degradation curve. Robust intelligence degrades slowly. Brittle intelligence collapses.

## Why This Matters

The hardest moment for any intelligence is the first moment. Before you know the codebase. Before you know the system. Before you know what is broken versus what merely looks broken. Every subsequent action depends on the quality of that initial orientation.

Current benchmarks skip this moment entirely. They hand the model a problem statement, a file path, a function signature. They measure execution, not discovery. But discovery is the bottleneck. An engineer who knows exactly what is wrong fixes it in minutes. An engineer who spends four hours looking at the wrong subsystem never fixes it at all.

needle-bench measures the bottleneck.

## The Recursive Property

A system that helps intelligence orient faster — a boot file that surfaces the right context, a digest that highlights what changed, an OS that compresses noise — can be measured by the same benchmark. Give an agent a raw codebase: score X. Give the same agent the same codebase with ostk running: score Y. The delta is the value of the infrastructure, measured in discovery acceleration.

The benchmark does not just test intelligence. It tests everything that makes intelligence effective.

## The Tagline

**Your worst day, everyone's benchmark.**

Not your worst code. Not your hardest question. Your worst *day* — when you are dropped into a system you do not understand, with no map, and the thing that matters is hidden in everything that does not.

That is the test. For every intelligence. On every scale.
