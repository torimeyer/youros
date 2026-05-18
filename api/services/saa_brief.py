"""saa_brief: generate spawn briefs for different task kinds.

When myOS dispatches a sub-agent via saa, the orchestrator builds the
agent's initial prompt from two layers:

  1. A task description (what to do).
  2. A brief that tells the agent *how* to approach that class of work.

This module owns layer 2 for the standard task kinds:
  bug-fix    — user-visible regression; requires BDD invariant test
  feature    — new behavior; standard TDD
  cosmetic   — rename / icon / padding; no invariant needed
  backend    — server-side only; unit + integration per Rule 2b
  generic    — fallback for anything else

The key rule (needle →1403) is that bug-fix briefs must require a
*BDD/acceptance-level invariant test*, not just a symptom-specific unit
test.  A unit test on a function passes even when the caller is passing
the wrong prop — which is exactly the class of bug that keeps
re-emerging.  An integration-shaped test that simulates the protocol
sequence catches the whole class.

Public surface:
  generate_saa_brief(kind: str) -> str
      Returns the brief text for the given task kind.  ``kind`` is
      case-insensitive and is normalised internally.  Unknown kinds fall
      back to the generic brief.

  BDD_INVARIANT_SECTION: str
      The BDD invariant block that is injected for kind=bug-fix.
      Exposed so tests can assert against it without duplicating the
      expected text.

  SAA_KINDS: tuple[str, ...]
      Canonical kind names.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical kind names
# ---------------------------------------------------------------------------

SAA_KINDS: tuple[str, ...] = ("bug-fix", "feature", "cosmetic", "backend", "generic")

# ---------------------------------------------------------------------------
# Shared preamble — injected into every brief
# ---------------------------------------------------------------------------

_PREAMBLE = """\
Use mcp__ostk__* tools (shell, fs_read, fs_write, edit, search). If deferred, \
reload via ToolSearch. Prefer ostk over native Bash/Read/Edit/Write/Grep/Glob. \
Never call grep — use mcp__ostk__search. Register, heartbeat every 60 s, \
long-poll /nudges, post /reply for each nudge, call /complete when done.\
"""

# ---------------------------------------------------------------------------
# BDD invariant block — the core addition of needle →1403
# ---------------------------------------------------------------------------

BDD_INVARIANT_SECTION = """\
## BDD invariant (mandatory for bug-fix briefs, Rule 2c)

Before reading any code, state the user-visible invariant in one sentence:

    "The invariant being pinned is: <plain-language user-facing rule>."

Examples of good invariant statements:
  - "Bold markdown renders as bold once the text stops mutating, regardless \
of whether tool calls are still pending."
  - "After clicking Delete the row disappears from the list within 200 ms."
  - "Submitting an empty form shows an inline error, not a navigation."

### Test requirements

Write ONE behavioral/integration test (NOT a unit test with stubbed props):
  - Simulate the full protocol or user sequence end-to-end (state machine, \
SSE handler, form submit, etc.).
  - Assert the user-visible outcome (DOM state, API response, rendered text).
  - Name it: test_<behavior>_invariant  \
(e.g. test_markdown_renders_when_stream_settles_invariant).
  - Confirm the test FAILS on buggy code before your fix.
  - Confirm the test PASSES after your fix.

### Enumerate other ways the invariant could fire

State at least ONE other scenario where the same invariant would be violated \
that your test does not cover. You do not have to write a test for it, but \
naming it proves the fix is invariant-level, not symptom-level.

The bug is not fixed until the invariant test is green.\
"""

# ---------------------------------------------------------------------------
# Kind-specific brief fragments
# ---------------------------------------------------------------------------

_BUG_FIX_BRIEF = f"""\
{_PREAMBLE}

## Approach: bug-fix

Use superpowers:systematic-debugging before proposing any fix.
Root cause FIRST — no symptom patches.

{BDD_INVARIANT_SECTION}

## TDD (after the invariant test is written)

Write the invariant test first (RED). Watch it fail. Then implement the \
fix (GREEN). Commit only when both the invariant test and any existing \
regression tests pass.

Scaffold commit within 2 minutes of starting: create an empty test file \
at the target path, write a one-line plan to /tmp/<task>.plan, commit as \
`scaffold: <description>`.

If you find pre-existing test failures: fix them if cheap and adjacent, \
or file a needle via `ostk work add`. Never commit saying \
"pre-existing, out of scope".\
"""

_FEATURE_BRIEF = f"""\
{_PREAMBLE}

## Approach: feature

Use superpowers:test-driven-development. Write a failing test first (RED), \
implement minimal code (GREEN), refactor.

Scaffold commit within 2 minutes of starting.

If you find pre-existing test failures: fix or file a needle.\
"""

_COSMETIC_BRIEF = f"""\
{_PREAMBLE}

## Approach: cosmetic

Label / icon / padding / copy change only. No invariant test required. \
Verify visually or with a snapshot test. Keep diff minimal.\
"""

_BACKEND_BRIEF = f"""\
{_PREAMBLE}

## Approach: backend-only

No user-visible surface. Use unit + integration tests per Rule 2b. \
Write the failing test first, then implement.\
"""

_GENERIC_BRIEF = f"""\
{_PREAMBLE}

## Approach

Follow superpowers:test-driven-development. Write tests before \
implementation. Scaffold commit within 2 minutes.\
"""

# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

_KIND_MAP: dict[str, str] = {
    "bug-fix": _BUG_FIX_BRIEF,
    "bug_fix": _BUG_FIX_BRIEF,
    "bugfix": _BUG_FIX_BRIEF,
    "diagnose": _BUG_FIX_BRIEF,   # "diagnose" is the UI alias for bug-fix
    "feature": _FEATURE_BRIEF,
    "cosmetic": _COSMETIC_BRIEF,
    "backend": _BACKEND_BRIEF,
    "backend-only": _BACKEND_BRIEF,
    "generic": _GENERIC_BRIEF,
}


def generate_saa_brief(kind: str) -> str:
    """Return the spawn brief for the given task kind.

    Args:
        kind: Task kind string.  Case-insensitive.  Supported values:
              "bug-fix" / "bug_fix" / "bugfix" / "diagnose",
              "feature", "cosmetic", "backend" / "backend-only",
              "generic".  Unknown values fall back to the generic brief.

    Returns:
        A multi-line string ready to append to the agent's spawn prompt.
    """
    normalised = kind.strip().lower()
    return _KIND_MAP.get(normalised, _GENERIC_BRIEF)
