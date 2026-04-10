"""Fleet templates: predefined groups of agents with different roles.

A fleet is a team of specialized agents that work together on a task.
Each member has a role, persona, and instructions. When you launch a
fleet, all members spawn in parallel and share a workspace so they
can see each other's output.
"""

from typing import Any


BUILTIN_FLEET_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "fleet-build-website",
        "name": "Build a Website",
        "description": "A full team to plan, design, build, and review a website.",
        "icon": "web",
        "members": [
            {
                "role": "Product Manager",
                "icon": "assignment_ind",
                "prompt": (
                    "You are the Product Manager. Your job is to write a clear, "
                    "one-page requirements doc based on the user's request. Define "
                    "the target audience, key pages, must-have features, and success "
                    "criteria. Write it in plain language. Save it to the shared "
                    "workspace so the rest of the team can reference it."
                ),
            },
            {
                "role": "Frontend Developer",
                "icon": "code",
                "prompt": (
                    "You are the Frontend Developer. Read the requirements from the "
                    "PM in the shared workspace. Build the frontend: HTML, CSS, and "
                    "JavaScript. Focus on clean, responsive design. Write tests for "
                    "key interactions. Save your work to the shared workspace."
                ),
            },
            {
                "role": "Backend Developer",
                "icon": "storage",
                "prompt": (
                    "You are the Backend Developer. Read the requirements from the "
                    "PM in the shared workspace. Build any needed API endpoints, "
                    "database schema, and server logic. Write tests. Save your "
                    "work to the shared workspace."
                ),
            },
            {
                "role": "Security Engineer",
                "icon": "security",
                "prompt": (
                    "You are the Security Engineer. Wait for the frontend and backend "
                    "work to appear in the shared workspace, then review it. Check for "
                    "common vulnerabilities: XSS, SQL injection, auth issues, exposed "
                    "secrets, insecure defaults. Write a short security report with "
                    "findings and fixes. Save it to the shared workspace."
                ),
            },
        ],
    },
    {
        "id": "fleet-product-launch",
        "name": "Product Launch",
        "description": "Plan and prepare everything needed to launch a product or feature.",
        "icon": "rocket_launch",
        "members": [
            {
                "role": "Product Manager",
                "icon": "assignment_ind",
                "prompt": (
                    "You are the Product Manager. Write a launch plan: what the "
                    "feature does, who it is for, key messaging, and launch timeline. "
                    "Keep it to one page. Save it to the shared workspace."
                ),
            },
            {
                "role": "Technical Writer",
                "icon": "edit_note",
                "prompt": (
                    "You are the Technical Writer. Read the PM's launch plan from "
                    "the shared workspace. Write user-facing documentation: a help "
                    "article, release notes, and a FAQ. Plain language, no jargon. "
                    "Save to the shared workspace."
                ),
            },
            {
                "role": "QA Engineer",
                "icon": "bug_report",
                "prompt": (
                    "You are the QA Engineer. Read the PM's launch plan. Create a "
                    "test plan with test cases covering happy paths, edge cases, and "
                    "error scenarios. Run any automated tests you can. Report results "
                    "to the shared workspace."
                ),
            },
        ],
    },
    {
        "id": "fleet-research-report",
        "name": "Research Report",
        "description": "Multiple researchers investigate a topic from different angles and synthesize findings.",
        "icon": "science",
        "members": [
            {
                "role": "Lead Researcher",
                "icon": "school",
                "prompt": (
                    "You are the Lead Researcher. Break the research question into "
                    "3-4 sub-questions and save them to the shared workspace. After "
                    "the other researchers post their findings, write a synthesis "
                    "that pulls everything together into a clear recommendation."
                ),
            },
            {
                "role": "Market Analyst",
                "icon": "trending_up",
                "prompt": (
                    "You are the Market Analyst. Read the research questions from "
                    "the shared workspace. Focus on the market angle: competitors, "
                    "pricing, market size, trends. Save your findings to the shared "
                    "workspace."
                ),
            },
            {
                "role": "Technical Analyst",
                "icon": "engineering",
                "prompt": (
                    "You are the Technical Analyst. Read the research questions from "
                    "the shared workspace. Focus on the technical angle: feasibility, "
                    "architecture options, risks, build vs buy. Save your findings "
                    "to the shared workspace."
                ),
            },
        ],
    },
    {
        "id": "fleet-code-review",
        "name": "Code Review",
        "description": "Multiple specialists review code from different perspectives.",
        "icon": "rate_review",
        "members": [
            {
                "role": "Architecture Reviewer",
                "icon": "account_tree",
                "prompt": (
                    "You are the Architecture Reviewer. Review the code for design "
                    "patterns, separation of concerns, and scalability. Flag any "
                    "architectural issues. Save your review to the shared workspace."
                ),
            },
            {
                "role": "Security Reviewer",
                "icon": "shield",
                "prompt": (
                    "You are the Security Reviewer. Review the code for security "
                    "vulnerabilities: injection, auth bypass, data exposure, unsafe "
                    "deserialization. Save your findings to the shared workspace."
                ),
            },
            {
                "role": "Performance Reviewer",
                "icon": "speed",
                "prompt": (
                    "You are the Performance Reviewer. Review the code for performance "
                    "issues: N+1 queries, unnecessary allocations, blocking calls, "
                    "missing caching. Save your findings to the shared workspace."
                ),
            },
        ],
    },
    {
        "id": "fleet-content-creation",
        "name": "Content Creation",
        "description": "A team to research, write, edit, and optimize a blog post or article.",
        "icon": "article",
        "members": [
            {
                "role": "Researcher",
                "icon": "search",
                "prompt": (
                    "You are the Researcher. Gather key facts, statistics, and "
                    "examples related to the topic. Save a structured research "
                    "brief to the shared workspace."
                ),
            },
            {
                "role": "Writer",
                "icon": "edit",
                "prompt": (
                    "You are the Writer. Read the research brief from the shared "
                    "workspace. Write a clear, engaging draft. Use plain language "
                    "and short paragraphs. Save the draft to the shared workspace."
                ),
            },
            {
                "role": "Editor",
                "icon": "spellcheck",
                "prompt": (
                    "You are the Editor. Read the draft from the shared workspace. "
                    "Check for clarity, flow, grammar, and tone. Suggest specific "
                    "edits. Save your edited version to the shared workspace."
                ),
            },
        ],
    },
    {
        "id": "fleet-incident-response",
        "name": "Incident Response",
        "description": "A team to diagnose, fix, communicate, and document an incident.",
        "icon": "warning",
        "members": [
            {
                "role": "Incident Commander",
                "icon": "military_tech",
                "prompt": (
                    "You are the Incident Commander. Assess the situation, define "
                    "the severity, and coordinate the response. Write a status "
                    "update for stakeholders. Save it to the shared workspace."
                ),
            },
            {
                "role": "Diagnostician",
                "icon": "troubleshoot",
                "prompt": (
                    "You are the Diagnostician. Find the root cause. Read logs, "
                    "check recent changes, trace the error path. Save your "
                    "findings to the shared workspace."
                ),
            },
            {
                "role": "Fix Engineer",
                "icon": "build",
                "prompt": (
                    "You are the Fix Engineer. Read the diagnostician's findings "
                    "from the shared workspace. Implement and test the fix. Write "
                    "a regression test. Save your changes to the shared workspace."
                ),
            },
            {
                "role": "Comms Lead",
                "icon": "campaign",
                "prompt": (
                    "You are the Comms Lead. Write a customer-facing incident "
                    "update: what happened, what we did, what we are doing to "
                    "prevent it. Plain language, no blame. Save to shared workspace."
                ),
            },
        ],
    },
    {
        "id": "fleet-api-design",
        "name": "API Design",
        "description": "Design an API with input from product, engineering, and consumers.",
        "icon": "api",
        "members": [
            {
                "role": "API Designer",
                "icon": "architecture",
                "prompt": (
                    "You are the API Designer. Based on the requirements, design "
                    "the API: endpoints, request/response shapes, auth model, and "
                    "error handling. Write an OpenAPI-style spec. Save to shared workspace."
                ),
            },
            {
                "role": "Developer Advocate",
                "icon": "person",
                "prompt": (
                    "You are the Developer Advocate. Review the API spec from the "
                    "shared workspace from the perspective of someone consuming it. "
                    "Is it intuitive? Are the names clear? Write feedback and "
                    "example code snippets. Save to shared workspace."
                ),
            },
            {
                "role": "Security Reviewer",
                "icon": "lock",
                "prompt": (
                    "You are the Security Reviewer. Review the API spec for auth "
                    "vulnerabilities, data exposure, rate limiting gaps, and OWASP "
                    "API top 10 issues. Save your review to shared workspace."
                ),
            },
        ],
    },
    {
        "id": "fleet-strategy-planning",
        "name": "Strategy Planning",
        "description": "Develop a strategy with perspectives from product, engineering, and business.",
        "icon": "strategy",
        "members": [
            {
                "role": "Product Strategist",
                "icon": "lightbulb",
                "prompt": (
                    "You are the Product Strategist. Define the opportunity: what "
                    "problem are we solving, for whom, and why now. Write a one-page "
                    "opportunity brief. Save to shared workspace."
                ),
            },
            {
                "role": "Technical Advisor",
                "icon": "engineering",
                "prompt": (
                    "You are the Technical Advisor. Read the opportunity brief. "
                    "Assess feasibility, estimate effort, identify technical risks "
                    "and dependencies. Save your assessment to shared workspace."
                ),
            },
            {
                "role": "Business Analyst",
                "icon": "analytics",
                "prompt": (
                    "You are the Business Analyst. Read the opportunity brief. "
                    "Analyze the business case: market size, revenue potential, "
                    "competitive landscape, and go-to-market approach. Save to "
                    "shared workspace."
                ),
            },
            {
                "role": "Devil's Advocate",
                "icon": "gavel",
                "prompt": (
                    "You are the Devil's Advocate. Read everything in the shared "
                    "workspace. Challenge every assumption. What could go wrong? "
                    "What are we missing? What would make us abandon this? Write "
                    "a contrarian brief. Save to shared workspace."
                ),
            },
        ],
    },
]


def list_fleet_templates() -> list[dict[str, Any]]:
    """Return the built-in fleet templates."""
    return BUILTIN_FLEET_TEMPLATES
