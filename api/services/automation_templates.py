"""Built-in automation templates.

These are ready-made automations that the user can start from with one click.
They live in-memory (not persisted) and are surfaced via
`GET /api/workflows/templates`. The templates reference the same step
structure used by the workflows service, so pre-filling the builder works
without any translation layer.
"""

from typing import Any


BUILTIN_AUTOMATION_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "builtin-daily-standup",
        "name": "Daily standup",
        "description": "Every weekday morning, summarize yesterday's work and today's priorities.",
        "icon": "wb_sunny",
        "steps": [
            {
                "name": "Gather yesterday's closed tasks",
                "prompt": "List all tasks closed in the last 24 hours from ostk.",
            },
            {
                "name": "Gather today's priorities",
                "prompt": "List all P0 and P1 open tasks from ostk.",
            },
            {
                "name": "Write the standup summary",
                "prompt": (
                    "Combine the previous two steps into a short plain-language daily "
                    "standup post. Max 5 bullets."
                ),
            },
        ],
    },
    {
        "id": "builtin-weekly-review",
        "name": "Weekly review",
        "description": "Every Friday afternoon, summarize the week's wins, misses, and carry-overs.",
        "icon": "rate_review",
        "steps": [
            {
                "name": "Closed this week",
                "prompt": "Pull all tasks closed in the last 7 days from ostk.",
            },
            {
                "name": "Still open",
                "prompt": "Pull all tasks still open with P0 or P1 priority.",
            },
            {
                "name": "Write the review",
                "prompt": (
                    "Write a short weekly review: wins, misses, carry-overs. Plain "
                    "language. Max 10 bullets."
                ),
            },
        ],
    },
    {
        "id": "builtin-meeting-prep",
        "name": "Meeting prep",
        "description": "Before a meeting, generate a prep doc with context from calendar and recent work.",
        "icon": "event_note",
        "steps": [
            {
                "name": "Fetch meeting context",
                "prompt": "Pull the next calendar event and any linked tasks or documents.",
            },
            {
                "name": "Summarize related work",
                "prompt": (
                    "Summarize recent tasks and threads related to the meeting topic "
                    "in plain language."
                ),
            },
            {
                "name": "Draft talking points",
                "prompt": "Draft 3-5 talking points for the meeting based on the prior steps.",
            },
        ],
    },
    {
        "id": "builtin-inbox-triage",
        "name": "Inbox triage",
        "description": "Scan recent unread Gmail and turn anything actionable into a task.",
        "icon": "inbox",
        "steps": [
            {
                "name": "Read unread emails",
                "prompt": "Fetch recent unread Gmail messages (max 10).",
            },
            {
                "name": "Classify each one",
                "prompt": "For each email, decide if it needs an action or is just FYI.",
            },
            {
                "name": "Create tasks",
                "prompt": (
                    "For each actionable email, create an ostk task with a descriptive "
                    "title."
                ),
            },
        ],
    },
]


def list_templates() -> list[dict[str, Any]]:
    """Return the built-in automation templates."""
    return BUILTIN_AUTOMATION_TEMPLATES
