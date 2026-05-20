"""Plan-mode store for mychat (→1533).

Holds pending plans between the model's plan proposal and the user's
Approve/Reject/Revise response. Plans are in-memory only and scoped to the
current server process.
"""

import re
from typing import Optional


# Action keywords that make a message non-trivial regardless of length.
_ACTION_KEYWORDS = re.compile(
    r"\b(build|implement|fix|create|add|refactor|change|modify|update|write|"
    r"delete|remove|generate|spawn|rewrite|migrate|deploy|install|scaffold)\b",
    re.IGNORECASE,
)

_PLAN_TAG = re.compile(r"<plan>(.*?)</plan>", re.DOTALL)


def is_trivial(message: str) -> bool:
    """Return True when the message does not need a plan before execution.

    A message is trivial when it is a short question or a slash command that
    does not ask the model to build, fix, or change anything.
    """
    text = message.strip()
    if text.startswith("/"):
        return True
    if len(text) > 200:
        return False
    if _ACTION_KEYWORDS.search(text):
        # Short "fix" with no target is still trivial.
        if len(text) <= 10:
            return True
        return False
    return True


def extract_plan(text: str) -> Optional[str]:
    """Return the content inside the first <plan>...</plan> block, or None."""
    m = _PLAN_TAG.search(text)
    if not m:
        return None
    return m.group(1).strip()


class PlanModeStore:
    """In-memory registry of pending plan approvals.

    Key: plan_id (str UUID)
    Value: dict with messages, tab_id, model
    """

    def __init__(self) -> None:
        self._plans: dict[str, dict] = {}

    def store(
        self,
        plan_id: str,
        messages: list[dict],
        tab_id: str,
        model: str,
    ) -> None:
        self._plans[plan_id] = {
            "messages": messages,
            "tab_id": tab_id,
            "model": model,
        }

    def consume(self, plan_id: str) -> Optional[dict]:
        """Remove and return the pending plan, or None if not found."""
        return self._plans.pop(plan_id, None)


plan_mode_store = PlanModeStore()
