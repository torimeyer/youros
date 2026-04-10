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
    {
        "id": "builtin-eod-recap",
        "name": "End of day recap",
        "description": "Summarize what you got done today and what's carrying over to tomorrow.",
        "icon": "nightlight",
        "steps": [
            {
                "name": "Today's closed tasks",
                "prompt": "List all tasks closed today from ostk.",
            },
            {
                "name": "Still in progress",
                "prompt": "List open P0 and P1 tasks that were touched today.",
            },
            {
                "name": "Write the recap",
                "prompt": (
                    "Write a short end-of-day recap: what got done, what carries over, "
                    "and one thing to start with tomorrow. Plain language, 3-5 sentences."
                ),
            },
        ],
    },
    {
        "id": "builtin-stale-tasks",
        "name": "Stale task cleanup",
        "description": "Find tasks that have been open too long and suggest what to do with them.",
        "icon": "hourglass_empty",
        "steps": [
            {
                "name": "Find stale tasks",
                "prompt": "List all open tasks that have been open for more than 7 days.",
            },
            {
                "name": "Classify each one",
                "prompt": (
                    "For each stale task, decide: still relevant (keep), outdated (close), "
                    "or blocked (flag what's blocking it)."
                ),
            },
            {
                "name": "Suggest actions",
                "prompt": (
                    "Write a short list of recommended actions: which to close, which to "
                    "reprioritize, and which need help getting unblocked."
                ),
            },
        ],
    },
    {
        "id": "builtin-project-status",
        "name": "Project status update",
        "description": "Generate a status update for a project based on recent task activity.",
        "icon": "assessment",
        "steps": [
            {
                "name": "Gather recent activity",
                "prompt": "Pull all tasks opened and closed in the last 7 days.",
            },
            {
                "name": "Group by label",
                "prompt": "Group the tasks by their labels to show progress by area.",
            },
            {
                "name": "Write the update",
                "prompt": (
                    "Write a project status update suitable for sharing with a team. "
                    "Include progress, risks, and next steps. Plain language, no jargon."
                ),
            },
        ],
    },
    {
        "id": "builtin-email-followup",
        "name": "Email follow-up",
        "description": "Check for emails that need a reply and draft responses.",
        "icon": "reply",
        "steps": [
            {
                "name": "Find emails needing replies",
                "prompt": "Fetch recent Gmail messages where someone asked a question or requested something.",
            },
            {
                "name": "Draft replies",
                "prompt": (
                    "For each email that needs a response, draft a short, friendly reply. "
                    "Keep it professional and concise."
                ),
            },
        ],
    },
    {
        "id": "builtin-sprint-planning",
        "name": "Sprint planning",
        "description": "Review open tasks and plan the next sprint based on priority and capacity.",
        "icon": "rocket_launch",
        "steps": [
            {
                "name": "Review open backlog",
                "prompt": "List all open tasks grouped by priority (P0, P1, P2).",
            },
            {
                "name": "Check recent velocity",
                "prompt": "Count how many tasks were closed in each of the last 3 weeks.",
            },
            {
                "name": "Build the sprint plan",
                "prompt": (
                    "Based on the backlog and recent velocity, recommend which tasks to "
                    "commit to this sprint. Start with P0, then P1. Be realistic about "
                    "capacity."
                ),
            },
        ],
    },
]


def list_templates() -> list[dict[str, Any]]:
    """Return the built-in automation templates."""
    return BUILTIN_AUTOMATION_TEMPLATES
