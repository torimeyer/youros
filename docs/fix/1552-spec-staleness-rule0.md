# →1552: spec-staleness Rule 0 added to subagent brief template

**What changed**: `memory/feedback_subagent_prompt_template.md` now has a **Rule 0** section that agents must follow before editing any file named in a brief.

**The rule**: Before editing any file the brief mentions, verify it exists and its content matches the description. Flag divergences in the scaffold commit message and proceed against the current repo state.

**Source**: Retro F13 — parent was typing this preamble 8+ times per session manually.
