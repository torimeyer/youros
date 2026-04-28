"""Plan templates: starter plans with pre-written goal, acceptance criteria, and tasks.

A plan template is a starting point for a new Plan on the Plans page.
Each template carries a pre-written goal (markdown body), an acceptance
criteria checklist, and a list of task titles ready for decompose.

Templates seeded here include migrations of the fleet team templates
(so "Build a Website" still works as a one-click starter) plus a few
templates that do not correspond to a fleet (weekly review writeup,
launch announcement). The fleet service still exists for backwards
compatibility with the old /agents/fleets/spawn endpoint, but the
Plans page uses this module exclusively.
"""

from typing import Any, Optional


BUILTIN_SPEC_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "build-a-website",
        "name": "Build a Website",
        "description": "Plan, design, build, and review a new website end to end.",
        "icon": "web",
        "produces_doc": True,
        "goal_markdown": (
            "## What we want\n"
            "Plan and ship a new website. A product manager writes a short PRD, "
            "a frontend developer builds the landing page, a backend developer "
            "stubs the supporting API, and a security engineer reviews the risks. "
            "Everyone works from the same plan.\n"
        ),
        "acceptance_criteria": [
            "A short PRD exists with audience, pages, features, and success criteria.",
            "The landing page renders with at least three key sections.",
            "A small backend API returns mock data for the landing page.",
            "A security review lists the top risks and one mitigation each.",
            "All four artifacts are saved in the shared workspace.",
        ],
        "tasks": [
            "Write the PRD",
            "Build the frontend landing page",
            "Build the backend API stub",
            "Run a security review",
        ],
        "user_inputs": [
            {"key": "purpose", "label": "What is the website for?", "placeholder": "e.g. portfolio, SaaS product, marketing site", "type": "text", "required": True},
            {"key": "audience", "label": "Who is the audience?", "placeholder": "e.g. small business owners, developers", "type": "text", "required": False},
        ],
    },
    {
        "id": "product-launch",
        "name": "Product Launch",
        "description": "Plan and prepare everything needed to launch a product or feature.",
        "icon": "rocket_launch",
        "produces_doc": True,
        "goal_markdown": (
            "## What we want\n"
            "Get a product or feature ready to launch. The plan covers the launch "
            "story, the customer-facing docs, and a test plan so nothing slips "
            "through the cracks on launch day.\n"
        ),
        "acceptance_criteria": [
            "A one-page launch plan exists with audience, messaging, and timeline.",
            "User-facing docs cover the help article, release notes, and FAQ.",
            "A test plan lists happy paths, edge cases, and error scenarios.",
            "All artifacts are saved in the shared workspace.",
        ],
        "tasks": [
            "Write the launch plan",
            "Write the user-facing documentation",
            "Write the test plan",
        ],
        "user_inputs": [
            {"key": "product", "label": "What product or feature is launching?", "placeholder": "e.g. new checkout flow, mobile app v2", "type": "text", "required": True},
            {"key": "date", "label": "What's the target launch date?", "placeholder": "e.g. May 15, end of Q2", "type": "text", "required": False},
        ],
    },
    {
        "id": "research-report",
        "name": "Research Report",
        "description": "Investigate a topic from several angles and pull the findings together.",
        "icon": "science",
        "produces_doc": True,
        "goal_markdown": (
            "## What we want\n"
            "Answer a research question with a synthesis that pulls together "
            "market context, technical feasibility, and any other relevant angles. "
            "The lead researcher frames the questions, specialists dig in, and the "
            "final output is a clear recommendation.\n"
        ),
        "acceptance_criteria": [
            "The research question is broken into three or four sub-questions.",
            "A market angle is covered: competitors, pricing, and trends.",
            "A technical angle is covered: feasibility, architecture, and risks.",
            "A synthesis ties everything together with a clear recommendation.",
        ],
        "tasks": [
            "Frame the research questions",
            "Run the market analysis",
            "Run the technical analysis",
            "Write the synthesis and recommendation",
        ],
        "user_inputs": [
            {"key": "question", "label": "What's the research question?", "placeholder": "e.g. Should we build a mobile app?", "type": "text", "required": True},
            {"key": "angles", "label": "Any specific angles to cover?", "placeholder": "e.g. focus on competitor pricing", "type": "text", "required": False},
        ],
    },
    {
        "id": "code-review",
        "name": "Code Review",
        "description": "Review code from architecture, security, and performance angles.",
        "icon": "rate_review",
        "produces_doc": True,
        "goal_markdown": (
            "## What we want\n"
            "Run a thorough code review that covers more than one angle. Each "
            "reviewer focuses on their lane and flags problems before they ship.\n"
        ),
        "acceptance_criteria": [
            "An architecture review flags design and scalability issues.",
            "A security review flags injection, auth, and data handling risks.",
            "A performance review flags slow operations and missing optimizations.",
            "All findings are saved together in the shared workspace.",
        ],
        "tasks": [
            "Run the architecture review",
            "Run the security review",
            "Run the performance review",
        ],
        "user_inputs": [
            {"key": "codebase", "label": "What codebase or PR is being reviewed?", "placeholder": "e.g. PR #123, the auth module", "type": "text", "required": True},
            {"key": "focus", "label": "Any specific focus areas?", "placeholder": "e.g. focus on the database layer", "type": "text", "required": False},
        ],
    },
    {
        "id": "content-creation",
        "name": "Content Creation",
        "description": "Research, write, edit, and polish a blog post or article.",
        "icon": "article",
        "produces_doc": True,
        "goal_markdown": (
            "## What we want\n"
            "Produce a clear, engaging article. The plan covers background "
            "research, a clean draft, and a polished edit so the piece is ready "
            "to publish.\n"
        ),
        "acceptance_criteria": [
            "A research brief lists the key facts, stats, and examples.",
            "A draft is written in plain language with short paragraphs.",
            "An editor pass cleans up grammar, clarity, and tone.",
            "The final version is saved in the shared workspace.",
        ],
        "tasks": [
            "Research the topic",
            "Write the draft",
            "Edit and polish",
        ],
        "user_inputs": [
            {"key": "topic", "label": "What's the topic?", "placeholder": "e.g. why remote work is here to stay", "type": "text", "required": True},
            {"key": "audience", "label": "Who is the target audience?", "placeholder": "e.g. startup founders, engineers", "type": "text", "required": False},
        ],
    },
    {
        "id": "incident-response",
        "name": "Incident Response",
        "description": "Diagnose, fix, communicate, and document an incident.",
        "icon": "warning",
        "produces_doc": True,
        "goal_markdown": (
            "## What we want\n"
            "Respond to an incident with a clear commander, a diagnosis, a fix, "
            "and honest customer communication. Nothing falls through the cracks.\n"
        ),
        "acceptance_criteria": [
            "Severity is assessed and a status update is written for stakeholders.",
            "The root cause is found and written up.",
            "A fix is implemented with a regression test.",
            "A customer-facing update explains what happened and the next steps.",
        ],
        "tasks": [
            "Assess severity and write the status update",
            "Diagnose the root cause",
            "Implement and test the fix",
            "Write the customer communication",
        ],
        "user_inputs": [
            {"key": "incident", "label": "Describe the incident and symptoms", "placeholder": "e.g. login is failing for all users since 2pm", "type": "textarea", "required": True},
        ],
    },
    {
        "id": "api-design",
        "name": "API Design",
        "description": "Design an API with input from design, developer experience, and security.",
        "icon": "api",
        "produces_doc": True,
        "goal_markdown": (
            "## What we want\n"
            "Design an API that is clean to use, safe to call, and easy to consume. "
            "The plan covers the spec itself, a developer experience review, and a "
            "security review.\n"
        ),
        "acceptance_criteria": [
            "An API spec exists with endpoints, request and response shapes, auth, and error handling.",
            "A developer experience review confirms the API is intuitive and well named.",
            "A security review flags login, data exposure, and rate limiting gaps.",
        ],
        "tasks": [
            "Draft the API spec",
            "Run the developer experience review",
            "Run the security review",
        ],
        "user_inputs": [
            {"key": "api", "label": "What API are you designing?", "placeholder": "e.g. payments API, user profile API", "type": "text", "required": True},
            {"key": "consumers", "label": "Who will use it?", "placeholder": "e.g. mobile apps, third-party developers", "type": "text", "required": False},
        ],
    },
    {
        "id": "strategy-planning",
        "name": "Strategy Planning",
        "description": "Develop a strategy with product, technical, business, and skeptic angles.",
        "icon": "strategy",
        "produces_doc": True,
        "goal_markdown": (
            "## What we want\n"
            "Work out whether and how to pursue an opportunity. The plan includes "
            "a product brief, a technical feasibility read, a business case, and a "
            "devil's advocate challenge so we do not miss the downside.\n"
        ),
        "acceptance_criteria": [
            "An opportunity brief defines the problem, audience, and urgency.",
            "A technical assessment covers feasibility, effort, and risks.",
            "A business case covers market size, revenue potential, and go-to-market.",
            "A contrarian brief challenges every key assumption.",
        ],
        "tasks": [
            "Write the opportunity brief",
            "Write the technical assessment",
            "Write the business case",
            "Write the contrarian brief",
        ],
        "user_inputs": [
            {"key": "opportunity", "label": "What opportunity or decision are you evaluating?", "placeholder": "e.g. build a native mobile app vs. improve the web app", "type": "text", "required": True},
            {"key": "constraints", "label": "Any key constraints or context?", "placeholder": "e.g. 3-person team, 6-month runway", "type": "text", "required": False},
        ],
    },
    {
        "id": "ia-review",
        "name": "IA Review",
        "description": "Review an app's information architecture from several UX angles.",
        "icon": "account_tree",
        "produces_doc": True,
        "goal_markdown": (
            "## What we want\n"
            "Look at the app's navigation, labels, and discoverability through "
            "multiple lenses. The goal is a concrete set of recommendations that "
            "reduce cognitive load.\n"
        ),
        "acceptance_criteria": [
            "An IA analysis maps the page hierarchy and flags where users get lost.",
            "A first-time-user walkthrough captures confusion in the first five minutes.",
            "A naming audit lists inconsistent labels and jargon to fix.",
            "A consolidation proposal recommends a simpler navigation structure.",
        ],
        "tasks": [
            "Map the information architecture",
            "Do the first-time-user walkthrough",
            "Run the naming audit",
            "Write the consolidation proposal",
        ],
        "user_inputs": [
            {"key": "product", "label": "What app or product are you reviewing?", "placeholder": "e.g. the onboarding flow, the settings page", "type": "text", "required": True},
            {"key": "concern", "label": "What's the main concern?", "placeholder": "e.g. users keep getting lost in navigation", "type": "text", "required": False},
        ],
    },
    {
        "id": "weekly-review-writeup",
        "name": "Weekly review writeup",
        "description": "Turn the week's work into a short, honest writeup.",
        "icon": "event_note",
        "produces_doc": True,
        "goal_markdown": (
            "## What we want\n"
            "A short writeup that captures what shipped this week, what got stuck, "
            "and the top priorities for next week. Plain language, no spin.\n"
        ),
        "acceptance_criteria": [
            "A list of what shipped this week is included.",
            "A list of what got stuck or slipped is included.",
            "The top two or three priorities for next week are named.",
        ],
        "tasks": [
            "Collect what shipped this week",
            "Collect what got stuck",
            "Name next week's priorities",
        ],
        "user_inputs": [
            {"key": "notes", "label": "Paste your raw notes from the week", "placeholder": "What shipped, what got stuck, what's coming up...", "type": "textarea", "required": True},
        ],
    },
    {
        "id": "launch-announcement",
        "name": "Launch announcement",
        "description": "Write an announcement that explains what shipped and why it matters.",
        "icon": "campaign",
        "produces_doc": True,
        "goal_markdown": (
            "## What we want\n"
            "A short announcement that tells customers what just shipped, who it "
            "helps, and how to try it. Plain language, no jargon, one clear call "
            "to action.\n"
        ),
        "acceptance_criteria": [
            "The announcement names what shipped in one sentence.",
            "The audience and the benefit are clearly stated.",
            "A single call to action is included.",
            "The tone is plain language and friendly.",
        ],
        "tasks": [
            "Draft the announcement",
            "Add the call to action",
            "Proofread and polish",
        ],
        "user_inputs": [
            {"key": "what", "label": "What shipped?", "placeholder": "e.g. dark mode, new billing page", "type": "text", "required": True},
            {"key": "audience", "label": "Who is the announcement for?", "placeholder": "e.g. customers, internal team, press", "type": "text", "required": False},
        ],
    },
]


def list_spec_templates() -> list[dict[str, Any]]:
    """Return the built-in plan templates."""
    return BUILTIN_SPEC_TEMPLATES


def get_spec_template(template_id: str) -> Optional[dict[str, Any]]:
    """Return a single plan template by id, or None if not found."""
    for t in BUILTIN_SPEC_TEMPLATES:
        if t["id"] == template_id:
            return t
    return None
