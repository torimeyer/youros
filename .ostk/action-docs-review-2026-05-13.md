# →1241 action doc review — 2026-05-13

Reviewed commit e00e555 (`feat(→1241): action doc + per-template buttons on Recent Agents`).

## What this commit added

Two things surface on the Recent Agents card after a template agent completes:

1. **`actionable_doc` field** — a one-liner shown below the agent name. Set dynamically to
   whatever summary the completing agent sends to `/api/agents/{name}/complete`. Not hardcoded
   per template; entirely runtime-generated.

2. **Per-template buttons** — defined in `KNOWN_TEMPLATE_ACTIONS` in
   `app/src/components/RecentAgentActions.tsx`. Each template key maps to a labeled button.

---

## Part 1: Per-template button labels (hardcoded strings)

| Template key | Button label | Plain language? | Actionable? | Em-dash? | Jargon? | Verdict |
|---|---|---|---|---|---|---|
| `planner` | "Replan" | Yes | Yes | No | No | **PASS** |
| `daily-planner` | "Replan" | Yes | Yes | No | No | **PASS** |
| `daily planner` | "Replan" | Yes | Yes | No | No | **PASS** |
| `gem-builder` | "Save to gems" | Yes | Yes | No | No | **PASS** |
| _(all other templates)_ | "Run again" | Yes | Yes | No | No | **PASS** |

All button labels are short, plain, and unambiguous. No em-dashes, no engineering jargon.

---

## Part 2: The `actionable_doc` field (dynamic)

**Key finding: there are no per-template `actionable_doc` strings.** The field is set to whatever
the completing agent writes as its `/complete` summary. The code:

```python
# api/routers/agents.py line 6774-6775
if _tpl and _completion_summary:
    agent_metadata[name]["actionable_doc"] = _completion_summary
```

This means:
- Quality varies by run, not by template
- No template instructs its agent on what format or tone to use for the summary
- The string could be past-tense ("I did X") instead of the intended "what to do next"
- Jargon risk: if an agent writes "committed 3 files to the worktree," that shows verbatim

The one example in tests (`test_agents_template_actions.py:95`):
> "Reviewed your tasks and built a focused plan for today."

That specific string is plain, past-tense but clear, no jargon — **PASS as an example**.
But it's a hand-written test fixture, not a real agent output.

---

## Part 3: Structural gap — template name mismatches

The `KNOWN_TEMPLATE_ACTIONS` keys (`planner`, `daily-planner`, `daily planner`, `gem-builder`)
**do not match any existing builtin template** in `BUILTIN_AGENT_TEMPLATES`.

How the stored template name is set (from `api/routers/agents.py`):
- `spawn_meta["template"] = body.template` (line 5150) — stores the raw UI value
- Builtins like "Builder" → stored as "Builder", lowercased to "builder" at display time
- Builtins like "Meal Planner" → stored as "Meal Planner", lowercased to "meal planner"

None of the 40+ builtins produce a match for "planner", "daily-planner", or "gem-builder".
This means the specific buttons ("Replan", "Save to gems") are **silently dead** for all
existing templates. Every template agent gets only "Run again."

This is either:
- a) Templates planned but not yet built ("planner" and "gem-builder" are future additions)
- b) A key-naming mismatch introduced with the feature

---

## Summary

| Category | Count pass | Count needs-rewrite |
|---|---|---|
| Button labels | 5 | 0 |
| Test example string | 1 | 0 |

**No string-level rewrites needed.** All visible text is plain, actionable, and jargon-free.

**Structural issues found (not string quality, but system behavior):**

1. `actionable_doc` is 100% dynamic — no per-template guidance on what agents should write.
   Risk: jargon or unhelpful strings appear verbatim on the card.

2. `KNOWN_TEMPLATE_ACTIONS` template keys don't match any existing builtin.
   The "Replan" and "Save to gems" buttons are unreachable until matching templates are created
   or the keys are updated to match actual template names.

---

## Needles filed

- See needle filed for structural gap #2 (template name mismatch).
