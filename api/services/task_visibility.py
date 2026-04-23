"""Shared task visibility helper for user-facing task lists.

The Tasks page hides three classes of tasks by default:
  1. e2e smoke-test tasks (titles starting with "e2e-", case-insensitive).
  2. Session tasks auto-filed by the SessionStart hook.
  3. Closed or shelved tasks.

Any user-facing endpoint that renders tasks (dashboard focus, briefing
action items, etc.) must run candidate tasks through ``is_visible_task``
so the UI agrees everywhere. Admin and debug endpoints, plus
``/api/tasks?include_test_data=true``, should bypass this helper.
"""

from __future__ import annotations

import re

_SESSION_TITLE_RE = re.compile(r"^Claude Code session claude-code-", re.IGNORECASE)
# Tasks created by automation_outputs.auto_create_tasks embed the source
# name inside the description as ``called 'NAME'``. An ``e2e-`` prefix
# there means the task came from an e2e probe run, even when the title
# itself reads like a real user task.
_E2E_DESCRIPTION_SOURCE_RE = re.compile(r"called ['\"]e2e-", re.IGNORECASE)


def is_e2e_task(task: dict) -> bool:
    """Return True when the task is an e2e smoke-test leftover.

    Matches on either the title prefix (``e2e-``) or an embedded
    ``called 'e2e-...'`` marker inside the description. The description
    check catches leaks from ``/chat/roadmap/create-tasks`` where the
    generated title is a real user-readable phrase but the automation
    source name is e2e- prefixed.
    """
    title = (task.get("title") or "").lower()
    if title.startswith("e2e-"):
        return True
    description = task.get("description") or ""
    if _E2E_DESCRIPTION_SOURCE_RE.search(description):
        return True
    return False


def is_session_task(task: dict) -> bool:
    """Return True for tasks auto-filed by the SessionStart hook.

    Matches the frontend ``isSessionTask`` helper and the GET /tasks
    filter in ``api/routers/tasks.py``.
    """
    desc = task.get("description") or ""
    title = task.get("title") or ""
    if desc.startswith("session-task:"):
        return True
    if "Auto-filed by SessionStart hook" in desc:
        return True
    if _SESSION_TITLE_RE.match(title):
        return True
    return False


_TEST_TASK_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^test[:\s_]", re.IGNORECASE),
    re.compile(r"^test$", re.IGNORECASE),
    re.compile(r"^diagnose_test", re.IGNORECASE),
    re.compile(r"\bpls\s+delete\b", re.IGNORECASE),
    re.compile(r"\bplease\s+delete\b", re.IGNORECASE),
    re.compile(r"^diagnose\d+$", re.IGNORECASE),
)


def is_test_task(task: dict) -> bool:
    """Return True when the task title is a test/diagnostic placeholder.

    Matches patterns like "DIAGNOSE_TEST: pls delete", "DIAGNOSE2", "TEST",
    "test task", so they are excluded from user-facing summaries.
    """
    title = (task.get("title") or "").strip()
    for pat in _TEST_TASK_PATTERNS:
        if pat.search(title):
            return True
    return False


# Throwaway title patterns: broader than ``is_test_task``. Covers:
#   * The prefix standing alone as the entire title: ``DIAGNOSE``,
#     ``PROBE``, ``SCRATCH``, ``TEMP``, any case.
#   * The prefix in ALL CAPS followed by a non-letter, e.g.
#     ``DIAGNOSE pls delete``, ``PROBE: 42``, ``TEMP-123``. Real task
#     titles use sentence case like ``Diagnose slow DB queries``, which
#     is not in all caps, so they fall through.
#   * Already-handled cases (``DIAGNOSE_TEST``, ``DIAGNOSE2``, etc) still
#     match via the earlier ``_TEST_TASK_PATTERNS`` entries used by
#     ``is_test_task``.
# The two-branch design lets us extend junk prefixes without swatting
# real mixed-case tasks like ``Diagnose 500 api_error in other sessions``.
_THROWAWAY_PREFIX_RE = re.compile(
    r"^(diagnose|probe|scratch|temp)$",
    re.IGNORECASE,
)
_THROWAWAY_UPPER_PREFIX_RE = re.compile(
    r"^(DIAGNOSE|PROBE|SCRATCH|TEMP)(?:[^A-Za-z]|$)",
)

# Accidental LLM dumps that got filed as tasks. These are usually long
# paragraphs that start with a descriptive verb or an article. We match
# titles that start with one of these phrases AND are long, so a short
# real task like "The login flow" still gets through.
_LLM_DUMP_START_PHRASES: tuple[str, ...] = (
    "chat interface displays",
    "the user ",
    "this ",
    "a user ",
    "an ",
    "here ",
    "here's ",
    "here is ",
    "i will ",
    "i'll ",
    "we will ",
    "we'll ",
    "as an ai",
    "user can ",
    "users can ",
    "both models ",
    "both agents ",
    "both ai ",
    "each model ",
    "when the user",
    "when a user",
)


def _looks_like_llm_dump(title: str) -> bool:
    """Return True when the title looks like an accidental LLM response.

    Two signals:
      * Length over 100 characters. Real task titles are short
        imperatives like "Ship the roadmap" or "Fix login flow". A
        100+ char title is almost always pasted text, a feature spec
        sentence, or an LLM response that got filed as a task.
      * Title starts with a phrase typical of LLM descriptive prose
        (``The user ``, ``This ``, ``Both models ``, etc).
    Either signal alone is enough.
    """
    if not title:
        return False
    if len(title) > 100:
        return True
    lowered = title.lower()
    for phrase in _LLM_DUMP_START_PHRASES:
        if lowered.startswith(phrase):
            return True
    return False


def is_throwaway_task(task: dict) -> bool:
    """Return True when a task should be excluded from briefings everywhere.

    One helper used by every briefing enumeration (open tasks, closed
    yesterday, action items) so the rule stays consistent across surfaces.
    Previously ``is_test_task`` was applied to closed_yesterday only, and
    patterns like a bare ``DIAGNOSE`` prefix or an accidental LLM response
    leaked through. This helper extends the rule to:

      * Case-insensitive prefix of DIAGNOSE, TEST, PROBE, SCRATCH, TEMP.
      * Titles longer than 120 chars, or titles that start with one of
        several tell-tale LLM response phrases (``The ``, ``Chat interface
        displays``, etc).
      * The literal ``pls delete`` / ``please delete`` substring anywhere.

    Extending the rule: add patterns to ``_THROWAWAY_PREFIX_RE`` or
    ``_LLM_DUMP_START_PHRASES`` and add a row to the test table.
    """
    if is_test_task(task):
        return True
    title = (task.get("title") or "").strip()
    if not title:
        return False
    if _THROWAWAY_PREFIX_RE.match(title):
        return True
    if _THROWAWAY_UPPER_PREFIX_RE.match(title):
        return True
    if _looks_like_llm_dump(title):
        return True
    return False


def is_closed_or_shelved(task: dict) -> bool:
    """Return True when the task is closed or shelved."""
    return task.get("status") in ("closed", "shelved")


def is_visible_task(task: dict) -> bool:
    """Return True when the task belongs in a user-facing list.

    A task is visible when it is NOT an e2e leftover, NOT a session
    task, and NOT closed or shelved. This mirrors the default filter
    applied to the /api/tasks list view so the Dashboard, the briefing
    action items, and any other surface stay in sync.
    """
    if is_e2e_task(task):
        return False
    if is_session_task(task):
        return False
    if is_closed_or_shelved(task):
        return False
    return True
