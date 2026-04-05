# Hot Online Agent PR

## The Problem

With OCC (optimistic concurrency control) via gen counters, when two agents edit the same file, the second writer gets a bare rejection: "gen stale, retry." The agent must re-read the entire file, figure out what changed, mentally rebase, and try again. Expensive tokens, wasted turns.

## The Fix

When a write conflicts, instead of a bare rejection, return the **diff** of what changed plus the agent's intended edit rebased on the new version. Like a PR — but between agents, in real-time, with no branch.

### Conflict Response

```
CONFLICT on file.py (your base: gen 5, current: gen 6)

--- gen 5
+++ gen 6
@@ -42,3 +42,7 @@
 def process(data):
+    if not data:
+        raise ValueError("empty input")  # Agent A added this
     result = transform(data)

Your intended change (against gen 5):
  old_str: "result = transform(data)"
  new_str: "result = transform(data, validate=True)"

Suggested merge (gen 6 + yours):
  [AUTO-MERGEABLE: changes don't overlap]
```

### Three Tiers

1. **Auto-merge** — changes don't touch the same lines. Apply automatically, bump gen. Agent B never sees a conflict.
2. **Assisted merge** — changes overlap but diff is small. Show the agent the patch + suggested resolution. One confirmation turn instead of a full re-read.
3. **Manual rebase** — deep semantic conflict. Show the full diff, let the agent decide.

## Why This Works

LLMs are the best merge tools ever invented. Traditional 3-way merge is syntactic — fails on anything non-trivial. An LLM reads the diff and understands "Agent A added validation, I'm adding a parameter — these compose cleanly." Semantic merge for free.

## What Already Exists

- slipstream tracks gen counters per file
- slipstream has the `old_str` (CAS value) from the failed write
- Diff between gen N and N+1 is trivially computable
- The conflict response just needs to include the diff instead of a bare error

Change the error format from `"conflict: gen stale"` to `"conflict: here's what changed, here's your change rebased, confirm?"` — multi-agent file editing goes from "optimistic with expensive retries" to "optimistic with cheap rebases."
