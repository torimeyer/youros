"""Heuristic for defaulting claude-code subagent spawns to a git worktree.

Parallel "spawn burst" bursts race on ``git add``/``git commit`` in the
main worktree and contaminate each other's diffs (see memory entry
``feedback_spawn_burst_commit_contamination.md``). The Task tool supports
``isolation: "worktree"`` which puts each subagent in its own git
worktree, so those commits stay isolated.

Callers rarely remember to set this. This module picks a sensible
default: if the spawn's description or prompt looks like a code-edit
task (verbs like ``edit``, ``fix``, ``implement``, ``build``, ``add``,
``refactor``, etc.) we default to ``"worktree"``. Research-only verbs
(``explore``, ``read``, ``review``, ``audit``, ``report``, etc.) stay in
the main worktree so their transcript and commits surface where the user
expects them.

The caller can always opt out by setting ``isolation`` to any non-empty
string. ``"none"`` is an explicit sentinel that forces the main
worktree, useful for tests or for agents that intentionally share the
tree with the parent.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Verbs that strongly suggest the subagent will edit, create, or commit
# code. Order is not significant; match is substring-word.
CODE_EDIT_VERBS = frozenset(
    {
        "edit",
        "edits",
        "editing",
        "write",
        "writes",
        "writing",
        "commit",
        "commits",
        "committing",
        "fix",
        "fixes",
        "fixing",
        "refactor",
        "refactors",
        "refactoring",
        "implement",
        "implements",
        "implementing",
        "build",
        "builds",
        "building",
        "add",
        "adds",
        "adding",
        "create",
        "creates",
        "creating",
        "saa",  # torios verb for "spawn agent to do it"
        "diagnose",  # torios verb: find root cause, fix, regression tests
        "patch",
        "patches",
        "patching",
        "update",
        "updates",
        "updating",
        "migrate",
        "migrates",
        "migrating",
        "rename",
        "renames",
        "renaming",
        "delete",
        "deletes",
        "deleting",
        "remove",
        "removes",
        "removing",
    }
)

# Verbs that suggest read-only work. When ONLY these verbs appear we keep
# the subagent in the main worktree. If a code-edit verb also appears,
# the edit verb wins.
RESEARCH_VERBS = frozenset(
    {
        "explore",
        "explores",
        "exploring",
        "read",
        "reads",
        "reading",
        "search",
        "searches",
        "searching",
        "review",
        "reviews",
        "reviewing",
        "audit",
        "audits",
        "auditing",
        "report",
        "reports",
        "reporting",
        "investigate",
        "investigates",
        "investigating",
        "analyze",
        "analyzes",
        "analyzing",
        "inspect",
        "inspects",
        "inspecting",
        "summarize",
        "summarizes",
        "summarizing",
        "describe",
        "describes",
        "describing",
    }
)

# Sentinel for explicit opt-out. Callers who really want the main
# worktree (tests, shared-tree agents) pass this instead of None.
ISOLATION_NONE = "none"
ISOLATION_WORKTREE = "worktree"

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z_-]*")


def _tokenize(text: str) -> set[str]:
    """Return a lowercased set of word tokens for matching."""
    if not text:
        return set()
    return {m.group(0).lower() for m in _WORD_RE.finditer(text)}


def decide_isolation(
    *,
    description: Optional[str] = None,
    prompt: Optional[str] = None,
    explicit: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> str:
    """Pick the isolation level for a claude-code subagent spawn.

    Rules:
      1. If ``explicit`` is any non-empty string (including ``"none"``),
         return it unchanged. Explicit caller choice always wins.
      2. If the description or prompt contains any code-edit verb, return
         ``"worktree"``.
      3. Otherwise return ``"none"`` (main worktree).

    The decision is logged at INFO so operators can audit false
    positives and negatives after the fact.
    """
    if explicit:
        explicit_norm = explicit.strip().lower()
        logger.info(
            "spawn.isolation.explicit agent=%s isolation=%s",
            agent_name or "?",
            explicit_norm,
        )
        return explicit_norm

    tokens = _tokenize(description or "") | _tokenize(prompt or "")
    edit_hits = tokens & CODE_EDIT_VERBS
    research_hits = tokens & RESEARCH_VERBS

    if edit_hits:
        decision = ISOLATION_WORKTREE
        reason = f"edit_verbs={sorted(edit_hits)[:3]}"
    elif research_hits:
        decision = ISOLATION_NONE
        reason = f"research_verbs={sorted(research_hits)[:3]}"
    else:
        # No signal either way. Default to the main worktree so we do
        # not pay the fork-a-worktree cost for pure chat / status pings.
        decision = ISOLATION_NONE
        reason = "no_verb_match"

    logger.info(
        "spawn.isolation.decided agent=%s isolation=%s reason=%s",
        agent_name or "?",
        decision,
        reason,
    )
    return decision
