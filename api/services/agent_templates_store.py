"""Store for agent templates.

All templates (built-in and marketplace) live in a single unified store.
Built-ins have ``source="builtin"`` and are always installed.
Marketplace templates have ``source="marketplace"`` and start with
``installed=False`` until the user installs them.

Each template may carry ``aliases`` (list of alternate names) and
``personas`` (list of persona IDs that get this template by default
on onboarding). Each template's system prompt lives in ``prompt_template``.

Disk store: custom/installed state lives in ``~/.myos/agent_templates.json``
so ``git pull`` never clobbers user data.

Public surface:
- ``AGENT_TEMPLATES_PATH`` -- module-level Path constant (required by data-safety check)
- ``AgentTemplatesStore`` -- load / save / CRUD for custom agent templates
- ``agent_templates_store`` -- singleton instance
- ``BUILTIN_AGENT_TEMPLATES`` -- list of built-in template dicts (read-only)
- ``MIGRATIONS`` -- old-name -> new-name table for backward compat
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json, atomic_write_text

AGENT_TEMPLATES_PATH = Path.home() / ".myos" / "agent_templates.json"
TEMPLATE_ALIASES_PATH = Path.home() / ".myos" / "template_aliases.json"
# User-edited descriptions for built-in or marketplace templates. This lets a
# user rewrite the short blurb without touching the shipped prompt. Custom
# templates already persist their description via the overrides file, so the
# descriptions file is only consulted for builtin / marketplace entries.
TEMPLATE_DESCRIPTIONS_PATH = Path.home() / ".myos" / "template_descriptions.json"

# Agentfile directories for marketplace and custom templates.
# These are populated at runtime; the directories are created lazily.
# Built-ins stay in agents/<stem>.agent (unchanged).
# Marketplace seeds go to agents/marketplace/<stem>.agent (inside repo, tracked).
# Custom agentfiles go to ~/.myos/agents/custom/<stem>.agent (outside repo).
try:
    from config import PROJECT_ROOT
    MARKETPLACE_AGENTS_DIR = PROJECT_ROOT / "agents" / "marketplace"
except Exception:
    MARKETPLACE_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent / "agents" / "marketplace"

CUSTOM_AGENTS_DIR = Path.home() / ".myos" / "agents" / "custom"

# Sandbox defaults per source type, applied when seeding new agentfiles.
_SANDBOX_DEFAULTS: dict[str, str] = {
    "marketplace": "read",
    "custom": "write",
}

# Read-only tool defaults for newly seeded templates.
_DEFAULT_TOOLS = ["shell", "file:read"]
_DEFAULT_TOKEN_LIMIT = 100000


def _name_to_stem(name: str) -> str:
    """Convert a template display name to a safe filename stem.

    'Competitive Scan' -> 'competitive-scan'
    'PRD Draft' -> 'prd-draft'
    """
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def _invalidate_templates_cache() -> None:
    """Drop the cached /agents/templates list.

    Lazy import avoids a circular dependency: ``routers.agents`` imports
    this store at module load time. A missing or broken router is never
    fatal here because the cache is a pure perf optimization.
    """
    try:
        from routers import agents as agents_router  # local import

        agents_router.invalidate_templates_cache()
    except Exception:
        # Either the router has not loaded yet (cold start) or it does
        # not expose the hook (older build). Safe to skip: the next
        # request will see the mtime change and repopulate.
        pass


def _make_agentfile_text(template: dict) -> str:
    """Build Agentfile text for a marketplace or custom template dict.

    Applies conservative defaults: read-only tools, 100k token limit, no AC,
    no REVIEW, no STANDARDS. SANDBOX is 'read' for marketplace, 'write' for
    custom (user opted in).
    """
    from services.agentfile_parser import AgentfileConfig, LimitPolicy, serialize_agentfile

    source = template.get("source", "marketplace")
    sandbox = _SANDBOX_DEFAULTS.get(source, "read")

    cfg = AgentfileConfig()
    cfg.name = template.get("name", "")
    cfg.description = template.get("description", "")
    cfg.model = "auto"
    cfg.prompt = template.get("prompt_template", "")
    cfg.tools = list(_DEFAULT_TOOLS)
    cfg.limits = LimitPolicy(tokens=_DEFAULT_TOKEN_LIMIT, test_coverage=0)
    cfg.token_limit = _DEFAULT_TOKEN_LIMIT
    # Store sandbox as isolation field.  Use "nono" for read sandbox (light),
    # "none" for write (custom templates user created, trusted).
    cfg.isolation = "nono" if sandbox == "read" else "none"

    return serialize_agentfile(cfg)


def _seed_marketplace_agentfiles() -> None:
    """Write an agentfile for each marketplace template if not already present.

    Called once at module import. Idempotent: skips any file that already exists
    so hand-edited files are never overwritten.
    """
    MARKETPLACE_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    for t in BUILTIN_AGENT_TEMPLATES:
        if t.get("source") != "marketplace":
            continue
        stem = _name_to_stem(t["name"])
        path = MARKETPLACE_AGENTS_DIR / f"{stem}.agent"
        if not path.exists():
            try:
                path.write_text(_make_agentfile_text(t))
            except OSError:
                pass  # Non-fatal: capabilities will be None for this template


def _load_agentfile_capabilities(source: str, name: str) -> Optional[dict]:
    """Load capabilities summary for a template by reading its agentfile.

    Returns None if the file does not exist or fails to parse.
    Callers treat None as "no capability data available".
    """
    try:
        from services.agentfile_parser import (
            build_capabilities_summary,
            parse_agentfile,
            AgentfileParseError,
        )
    except ImportError:
        return None

    stem = _name_to_stem(name)

    if source == "builtin":
        try:
            from config import PROJECT_ROOT
            builtin_dir = PROJECT_ROOT / "agents"
        except Exception:
            builtin_dir = Path(__file__).resolve().parent.parent.parent / "agents"
        path = builtin_dir / f"{stem}.agent"
    elif source == "marketplace":
        path = MARKETPLACE_AGENTS_DIR / f"{stem}.agent"
    elif source == "custom":
        path = CUSTOM_AGENTS_DIR / f"{stem}.agent"
    else:
        return None

    if not path.exists():
        return None

    try:
        cfg = parse_agentfile(path)
        return build_capabilities_summary(cfg)
    except Exception:
        return None

# Name migration table: if a stored template has an old name, replace it
# with the canonical new name on load. This ensures user disk stores that
# were saved before renames continue to work correctly.
MIGRATIONS: dict[str, str] = {
    "PRD Draft": "PRD",
    "diagnose": "Diagnose",
    "test": "Test",
    "research": "Research",
    "review": "Review",
    "Comprehensive": "Builder",
    "comprehensive": "Builder",
}

# Single unified template list. Built-ins have source="builtin".
# Marketplace entries have source="marketplace".
# ``aliases`` lets the spawn endpoint match by alternate names (e.g. "saa").
# ``personas`` controls which onboarding personas install this by default.
# ``installed`` is only meaningful for marketplace entries (True = shown in
# the installed list, False = shown in the marketplace browser only).
BUILTIN_AGENT_TEMPLATES: list[dict] = [
    # --- Agentfile-backed templates (source=builtin, personas=all) ---
    {
        "id": "builtin-builder",
        "name": "Builder",
        "aliases": ["saa", "comprehensive"],
        "description": (
            "Your go-to builder. Reads the task, plans an approach, builds "
            "it, writes tests, and makes sure everything works before finishing."
        ),
        "icon": "engineering",
        "prompt_template": (
            "You are a myOS comprehensive build agent. Follow this pattern "
            "strictly: (1) Read the task and plan your approach. (2) Build the "
            "solution. (3) Write tests and run them. (4) Verify everything "
            "passes before marking complete. Report progress in plain language."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "builtin",
        "personas": [],
        "installed": True,
        "builtin": True,
    },
    {
        "id": "builtin-diagnose",
        "name": "Diagnose",
        "aliases": [],
        "description": (
            "Tracks down bugs. Reproduces the problem, finds the root cause, "
            "fixes it, and writes a test to make sure it stays fixed."
        ),
        "icon": "bug_report",
        "prompt_template": (
            "You are a myOS diagnose agent. Your job: (1) Reproduce the bug. "
            "(2) Find the root cause by reading code, logs, and traces. "
            "(3) Fix the root cause, not the symptom. (4) Write a regression "
            "test that fails before the fix and passes after. Report findings "
            "in plain language."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "builtin",
        "personas": [],
        "installed": True,
        "builtin": True,
    },
    {
        "id": "builtin-research",
        "name": "Research",
        "aliases": [],
        "description": (
            "Finds information for you. Searches the web, reads sources, "
            "and writes a clear summary of what it found."
        ),
        "icon": "search",
        "prompt_template": (
            "You are a research specialist for myOS. You find information, "
            "check links, locate images, and gather data from the web. "
            "Report findings clearly in plain language."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": [],
        "installed": True,
        "builtin": False,
        "produces_doc": True,
    },
    {
        "id": "builtin-review",
        "name": "Review",
        "aliases": ["Code Review"],
        "description": (
            "Reads through code changes and flags bugs, missing edge cases, "
            "style issues, and security concerns. Gives clear, actionable "
            "feedback."
        ),
        "icon": "rate_review",
        "prompt_template": (
            "You are a myOS code review agent. Review the code changes or "
            "pasted code for: "
            "(1) Bugs and logic errors. (2) Missing edge cases. "
            "(3) Style consistency. (4) Performance concerns. "
            "(5) Test coverage gaps. Return critical issues, suggested "
            "improvements, and nits. Each item is one line with file:line "
            "reference when possible. Provide clear, actionable feedback in "
            "plain language. No jargon."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": [],
        "installed": True,
        "builtin": False,
    },
    {
        "id": "builtin-test",
        "name": "Test",
        "aliases": [],
        "description": (
            "Runs your tests and tells you what passed and what broke. "
            "For failures, it reads the code and suggests how to fix them."
        ),
        "icon": "science",
        "prompt_template": (
            "You are a myOS test runner agent. Your job: (1) Discover and "
            "run all relevant tests. (2) Report which tests pass and which "
            "fail. (3) For failures, read the test code and source code to "
            "suggest a fix. (4) Never skip failing tests. Report results in "
            "plain language."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": [],
        "installed": True,
        "builtin": False,
    },
    # --- PM templates (marketplace, personas=["pm"]) ---
    {
        "id": "builtin-pm-competitive-scan",
        "name": "Competitive Scan",
        "aliases": [],
        "description": "Research what competitors are shipping in a product area.",
        "icon": "monitor_heart",
        "prompt_template": (
            "You are a product researcher. Given a product area, search public "
            "sources and summarize what the top 3-5 competitors have shipped in "
            "the last 6 months. Focus on features, positioning, and pricing. "
            "Call out gaps where the user could differentiate."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["pm"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
    },
    {
        "id": "builtin-pm-prd",
        "name": "PRD",
        "aliases": ["PRD Draft"],
        "description": "Turn a rough idea into a product requirements doc.",
        "icon": "article",
        "prompt_template": (
            "You are a senior PM. Turn the user's rough feature idea into a PRD "
            "with: problem, user, goal, non-goals, user stories, acceptance "
            "criteria, open questions. Plain language, no jargon. Keep the "
            "whole PRD under 400 words and stop after the first draft."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["pm"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
    },
    {
        "id": "builtin-pm-customer-interviews",
        "name": "Customer Interview Notes",
        "aliases": [],
        "description": "Turn raw interview notes into themes and insights.",
        "icon": "record_voice_over",
        "prompt_template": (
            "You are a researcher. Read the raw interview transcript the user "
            "pastes and return: top 3 themes, memorable quotes for each, the "
            "single most important unmet need, and two follow-up questions."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["pm"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
    },
    {
        "id": "builtin-pm-launch-checklist",
        "name": "Launch Checklist",
        "aliases": [],
        "description": "Generate a launch checklist for a new feature.",
        "icon": "checklist",
        "prompt_template": (
            "You are a launch-experienced PM. Given a feature name and rough "
            "scope, output a concrete launch checklist grouped by: engineering "
            "readiness, docs, internal comms, external comms, metrics, rollout "
            "plan. Each item should be a one-line action, owner unassigned."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["pm"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
    },
    {
        "id": "builtin-pm-roadmap",
        "name": "Roadmap",
        "aliases": [],
        "description": "Draft a multi-year quarterly roadmap from a set of initiatives.",
        "icon": "timeline",
        # Quick-mode, single-shot JSON output so the spawn finishes in
        # seconds instead of minutes. The marketplace agentfile at
        # ``agents/marketplace/roadmap.agent`` carries the matching
        # ``LIMIT quick_mode true`` flag so the spawn path skips the full
        # mailbox block and warm-up. Keep this prompt in sync with the
        # agentfile so re-seeding produces the same text.
        "prompt_template": (
            "You are a senior PM. Produce a 3-year roadmap as a JSON array "
            "of 12 quarters. Each quarter is an object with these exact "
            "keys: quarter (string like 'Q1 2026'), theme (one short "
            "sentence), initiatives (array of 3 to 5 short strings). Plain "
            "language, no jargon. Reply with ONLY the JSON array. No "
            "preamble, no trailing text, no markdown fences."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["pm"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
    },
    {
        "id": "builtin-pm-stakeholder-update",
        "name": "Stakeholder Update",
        "aliases": [],
        "description": "Write a weekly update for your leadership team.",
        "icon": "campaign",
        "prompt_template": (
            "You are a PM writing a weekly leadership update. From the user's "
            "raw notes, produce: what shipped, what is on track, what is at "
            "risk (with reason and ask), and a one-line headline. Plain "
            "language, no jargon. Under 200 words."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["pm"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
    },
    # --- Engineers ---
    # NOTE: "Code Review" is retired. It is now an alias on the built-in
    # "Review" template, which covers both code-change review and pasted-code
    # review. Spawn-by-old-name ("Code Review") still resolves via alias.
    {
        "id": "builtin-eng-write-tests",
        "name": "Write Tests",
        "aliases": [],
        "description": "Generate test cases for your code.",
        "icon": "bug_report",
        "prompt_template": (
            "You are a test engineer. For the function or module the user "
            "pastes, write tests covering the happy path, edge cases, and "
            "error paths. Prefer the project's existing test framework. "
            "Output only the test code."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["engineer"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-eng-bug-finder",
        "name": "Bug Finder",
        "aliases": [],
        "description": "Analyze code for potential bugs and security issues.",
        "icon": "pest_control",
        "prompt_template": (
            "You are a code auditor. Scan the pasted code for bugs, security "
            "issues (injection, auth bypass, exposed secrets), and "
            "concurrency problems. Return a ranked list with severity, "
            "location, and a one-line fix."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["engineer"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-eng-debug-helper",
        "name": "Debug Helper",
        "aliases": [],
        "description": "Read an error log, find the root cause, and suggest a fix.",
        "icon": "bug_report",
        "prompt_template": (
            "You are a debugger. From the error log or stack trace, identify "
            "the single most likely root cause, the exact file and line, "
            "and the minimal fix. Under 150 words."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["engineer"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-eng-refactor-plan",
        "name": "Refactor Plan",
        "aliases": [],
        "description": "Review messy code and propose a clean refactoring plan.",
        "icon": "auto_fix_high",
        "prompt_template": (
            "You are a refactoring expert. Review the pasted code and produce "
            "a step-by-step refactor plan that keeps behavior identical. Each "
            "step must be independently landable and have a one-line test "
            "plan."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["engineer"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
    },
    # --- Sales and customer success ---
    {
        "id": "builtin-sales-prospect-research",
        "name": "Prospect Research",
        "aliases": [],
        "description": "Dig into a company and decision maker before an outreach call.",
        "icon": "business",
        "prompt_template": (
            "You are a sales researcher. For the company and contact the user "
            "names, produce a one-page brief: recent news, likely pain points, "
            "the decision maker's background, and 3 conversation openers. "
            "Plain language, no hype."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["sales"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
    },
    {
        "id": "builtin-sales-cold-outreach",
        "name": "Cold Outreach Draft",
        "aliases": [],
        "description": "Draft a personalized outreach email to a prospect.",
        "icon": "outgoing_mail",
        "prompt_template": (
            "You are a sales writer. Draft a short, personalized outreach "
            "email (under 120 words) based on the prospect info and value "
            "prop the user provides. One clear ask. No jargon. No "
            "em-dashes."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["sales"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-sales-call-prep",
        "name": "Call Prep",
        "aliases": [],
        "description": "Build a 1-page call brief for an upcoming customer meeting.",
        "icon": "support_agent",
        "prompt_template": (
            "You are a sales coach. Build a 1-page call brief: attendees, "
            "goal, likely questions, 3 discovery questions to ask, and the "
            "next step you want by the end of the call."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["sales"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-sales-follow-up",
        "name": "Follow Up",
        "aliases": [],
        "description": "Turn a call into a recap email and next steps.",
        "icon": "forward_to_inbox",
        "prompt_template": (
            "You are a sales assistant. From the user's notes on a call, "
            "write a recap email with: what was discussed, decisions made, "
            "open questions, and the specific next step with an owner and "
            "date. Under 150 words."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["sales"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-sales-objection-handling",
        "name": "Objection Handling",
        "aliases": [],
        "description": "Help you prep answers to common customer objections.",
        "icon": "question_answer",
        "prompt_template": (
            "You are a sales coach. For the product and objection the user "
            "names, propose 3 response options ranked by tone (empathetic, "
            "direct, curious). Each response is one paragraph. Suggest the "
            "discovery question you would ask instead of answering."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["sales"],
        "installed": False,
        "builtin": True,
    },
    # --- Writers and creators ---
    {
        "id": "builtin-writer-blog-post",
        "name": "Blog Post",
        "aliases": [],
        "description": "Write a draft blog post from an outline or rough idea.",
        "icon": "edit_note",
        "prompt_template": (
            "You are a blog writer. From the user's outline or idea, produce "
            "a draft post: strong opening, 3-5 body sections with subheads, a "
            "short closing. Plain, warm, specific. Under 700 words."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["writer"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
    },
    {
        "id": "builtin-writer-social-post",
        "name": "Social Post",
        "aliases": [],
        "description": "Turn a long post into short, punchy social versions.",
        "icon": "share",
        "prompt_template": (
            "You are a social copywriter. Turn the long input into 3 "
            "versions: a LinkedIn post (under 200 words), a Twitter thread "
            "(5 short tweets), and an Instagram caption. Keep the voice "
            "specific and human."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["writer"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-writer-headlines",
        "name": "Headline Generator",
        "aliases": [],
        "description": "Write 10 headline options for the same piece of content.",
        "icon": "title",
        "prompt_template": (
            "You are a headline writer. For the content the user describes, "
            "produce 10 headline options covering a mix of: curiosity, "
            "benefit, number-driven, contrarian, and direct. No clickbait."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["writer"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-writer-proofreader",
        "name": "Proofreader",
        "aliases": [],
        "description": "Catch typos, grammar issues, and awkward phrasing.",
        "icon": "spellcheck",
        "prompt_template": (
            "You are a proofreader. Review the text for typos, grammar, "
            "punctuation, and awkward phrasing. Return the corrected text "
            "plus a short bulleted list of notable fixes. Do not rewrite "
            "for style."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["writer"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-writer-name-generator",
        "name": "Name Generator",
        "aliases": [],
        "description": "Come up with names for projects, features, or products.",
        "icon": "label",
        "prompt_template": (
            "You are a naming expert. For the thing the user describes, "
            "produce 15 name candidates grouped by vibe (playful, serious, "
            "abstract, descriptive). One line each. Flag any names that "
            "might conflict with a well-known brand."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["writer"],
        "installed": False,
        "builtin": True,
    },
    # --- Home and family ---
    {
        "id": "builtin-home-meal-planner",
        "name": "Meal Planner",
        "aliases": [],
        "description": "Plan a week of meals based on what is in the fridge.",
        "icon": "restaurant",
        "prompt_template": (
            "You are a home cook. From the ingredients the user lists, plan "
            "7 dinners for the week, reusing ingredients to reduce waste. "
            "One line per meal. Note any ingredients to buy."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["home"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-home-grocery-list",
        "name": "Grocery List",
        "aliases": [],
        "description": "Turn a meal plan into an organized shopping list.",
        "icon": "shopping_cart",
        "prompt_template": (
            "You are a shopper. Turn the user's meal plan into a grocery "
            "list grouped by aisle: produce, dairy, meat/fish, pantry, "
            "frozen, other. One line per item with quantity."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["home"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-home-trip-planner",
        "name": "Trip Planner",
        "aliases": [],
        "description": "Plan a day trip or vacation with budget and time constraints.",
        "icon": "flight_takeoff",
        "prompt_template": (
            "You are a travel planner. From destination, dates, budget, and "
            "group, produce a day-by-day plan with activities, approximate "
            "costs, and gaps for downtime. Flag any bookings to make in "
            "advance."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["home"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-home-gift-finder",
        "name": "Gift Finder",
        "aliases": [],
        "description": "Suggest gift ideas for a specific person, budget, and occasion.",
        "icon": "redeem",
        "prompt_template": (
            "You are a gift advisor. From the recipient's interests, budget, "
            "and occasion, suggest 8 gift options across 3 price tiers. One "
            "line each, with why it fits them and where to find it."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["home"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-home-homework-helper",
        "name": "Homework Helper",
        "aliases": [],
        "description": "Walk a kid through a tricky homework problem step by step.",
        "icon": "school",
        "prompt_template": (
            "You are a patient tutor. Walk the student through the problem "
            "step by step. Ask one guiding question at a time instead of "
            "giving the answer. Adjust to the grade level the user names."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["home"],
        "installed": False,
        "builtin": True,
    },
    # --- Students ---
    {
        "id": "builtin-student-study-guide",
        "name": "Study Guide",
        "aliases": [],
        "description": "Turn class notes into a study guide with key concepts and example questions.",
        "icon": "menu_book",
        "prompt_template": (
            "You are a study coach. Turn the user's class notes into a "
            "study guide with key concepts, one-line definitions, and 5 "
            "example exam questions with short answer keys. Under 500 "
            "words."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["student"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
    },
    {
        "id": "builtin-student-essay-outline",
        "name": "Essay Outline",
        "aliases": [],
        "description": "Build an outline for a paper based on a prompt or topic.",
        "icon": "format_list_numbered",
        "prompt_template": (
            "You are an essay coach. For the prompt the user pastes, "
            "produce an outline with thesis, 3-5 body paragraphs (each with "
            "topic sentence and evidence ideas), counterargument, and "
            "conclusion. Plain language."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["student"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
    },
    {
        "id": "builtin-student-flash-cards",
        "name": "Flash Cards",
        "aliases": [],
        "description": "Turn a reading into a set of flash-card style Q&A pairs.",
        "icon": "quiz",
        "prompt_template": (
            "You are a study-aid writer. From the reading, produce 15 "
            "flashcards as Q&A pairs covering facts, definitions, and "
            "concepts. Keep each answer under 30 words."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["student"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-student-citation-helper",
        "name": "Citation Helper",
        "aliases": [],
        "description": "Format sources in APA, MLA, or Chicago style.",
        "icon": "format_quote",
        "prompt_template": (
            "You are a citation helper. Format the sources the user pastes "
            "in the style they request (APA, MLA, or Chicago). Return one "
            "formatted citation per line and flag any sources with missing "
            "fields."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["student"],
        "installed": False,
        "builtin": True,
    },
    {
        "id": "builtin-student-concept-explainer",
        "name": "Concept Explainer",
        "aliases": [],
        "description": "Explain a hard concept in plain language with an example.",
        "icon": "lightbulb",
        "prompt_template": (
            "You are a patient teacher. Explain the concept the user names "
            "at two levels: a one-paragraph plain-English version, and a "
            "more detailed version with a worked example. No jargon in the "
            "first version."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["student"],
        "installed": False,
        "builtin": True,
    },
    # --- General utility templates (available to every persona) ---
    # "explain-plain" is the plain-language explainer. It replaces the old
    # user-specific "elit" command. The matcher and spawn endpoints accept
    # "elit" as an alias so muscle memory keeps working.
    {
        "id": "builtin-explain-plain",
        "name": "Explain Plain",
        "aliases": ["elit"],
        "description": (
            "Plain-language explanation of anything you point it at. No jargon, "
            "no omissions. Covers every relevant point and uses analogies for "
            "technical concepts."
        ),
        "icon": "record_voice_over",
        "prompt_template": (
            "You are a patient teacher. Explain the subject in plain language "
            "so someone with no background in the field can follow it. Rules: "
            "(1) No jargon. If a specialist term is unavoidable, define it in "
            "the same sentence. (2) Cover every relevant point. Do not skip "
            "material for brevity. Finish the explanation. (3) Use analogies "
            "for technical or abstract concepts. (4) No code. No formulas "
            "unless asked. (5) Never use em-dashes. Start with a one-paragraph "
            "summary, then go deeper in clearly labeled sections."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "builtin",
        "personas": [],
        "installed": True,
        "builtin": True,
    },
]


# Build a fast lookup: builtin-id -> template dict
_BUILTIN_BY_ID: dict[str, dict] = {t["id"]: t for t in BUILTIN_AGENT_TEMPLATES}

# Seed marketplace agentfiles on module import (idempotent: skips existing files).
_seed_marketplace_agentfiles()


def _resolve_alias(name: str) -> Optional[str]:
    """Return the canonical template ID for a name or alias, or None.

    Checks (first match wins):
    0. User-set aliases from ~/.myos/template_aliases.json (highest priority).
    1. Exact match on ``name`` field (case-insensitive).
    2. Match in any template's ``aliases`` list (case-insensitive).
    3. Migration table (old names mapped to new canonical names).
    """
    lower = name.strip().lower()

    # User aliases take priority so custom shortcuts always win.
    try:
        user_aliases = _load_user_aliases()
        tid = user_aliases.get(lower)
        if tid:
            return tid
    except Exception:
        pass

    for t in BUILTIN_AGENT_TEMPLATES:
        if t["name"].lower() == lower:
            return t["id"]
        if any(a.lower() == lower for a in t.get("aliases", [])):
            return t["id"]
        # Also match the canonical id directly. The Templates page can
        # POST the id as the ``template`` field (e.g. "builtin-pm-roadmap")
        # and that has to round-trip back to the same id so the spawn
        # resolver can find the agentfile.
        if t["id"].lower() == lower:
            return t["id"]
    # Try migrated name
    migrated = MIGRATIONS.get(name.strip())
    if migrated:
        for t in BUILTIN_AGENT_TEMPLATES:
            if t["name"].lower() == migrated.lower():
                return t["id"]
    return None


def _load_user_aliases() -> dict[str, str]:
    """Load user aliases from disk. Returns {alias: template_id}."""
    if not TEMPLATE_ALIASES_PATH.exists():
        return {}
    try:
        data = json.loads(TEMPLATE_ALIASES_PATH.read_text())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _apply_migrations(templates: list[dict]) -> list[dict]:
    """Apply name migrations to a list of stored templates on load.

    If a stored template has a name that appears in the migration table,
    its name is updated to the canonical new name so the UI stays
    consistent after renames.
    """
    migrated = []
    for t in templates:
        name = t.get("name", "")
        new_name = MIGRATIONS.get(name, name)
        if new_name != name:
            t = {**t, "name": new_name}
        migrated.append(t)
    return migrated


# Source priority: higher wins when two entries share a name or id.
# Tori's rule: "custom" means user-created, so user picks beat any shipped
# version, and a marketplace entry beats a builtin of the same name.
_SOURCE_PRIORITY: dict[str, int] = {
    "custom": 3,
    "marketplace": 2,
    "builtin": 1,
}


def _dedupe_templates(templates: list[dict]) -> list[dict]:
    """Return templates deduplicated by id then by normalized name.

    Preserves input order for the first occurrence. If a later entry has
    a higher-priority source than the winner already seen, it replaces it
    in place so the caller sees one row per (id, name) with the best
    source attribution.
    """
    by_id: dict[str, int] = {}
    by_name: dict[str, int] = {}
    result: list[dict] = []
    for t in templates:
        tid = t.get("id") or ""
        name_key = (t.get("name") or "").strip().lower()
        src = t.get("source") or "builtin"
        prio = _SOURCE_PRIORITY.get(src, 0)

        existing_idx: Optional[int] = None
        if tid and tid in by_id:
            existing_idx = by_id[tid]
        elif name_key and name_key in by_name:
            existing_idx = by_name[name_key]

        if existing_idx is None:
            idx = len(result)
            result.append(t)
            if tid:
                by_id[tid] = idx
            if name_key:
                by_name[name_key] = idx
        else:
            existing = result[existing_idx]
            existing_src = existing.get("source") or "builtin"
            existing_prio = _SOURCE_PRIORITY.get(existing_src, 0)
            if prio > existing_prio:
                result[existing_idx] = t
                if tid:
                    by_id[tid] = existing_idx
                if name_key:
                    by_name[name_key] = existing_idx
    return result


class AgentTemplatesStore:
    """CRUD for agent templates.

    Disk layout: ``~/.myos/agent_templates.json`` stores override records.
    Each record may be:
    - A custom template (id starts with "custom-").
    - An install record for a marketplace template (id matches a builtin id,
      ``installed=True`` overrides the default ``installed=False``).

    ``list_all()`` merges disk overrides with static builtins so callers
    always see a consistent view.
    ``list_installed()`` returns only installed templates.
    ``list_marketplace()`` returns uninstalled marketplace templates.
    """

    # In-memory cache for list_for_persona / list_user_custom. Both
    # endpoints back the Templates tab and were previously re-reading
    # the overrides file plus walking BUILTIN_AGENT_TEMPLATES on every
    # request. The Templates tab triggers two of these calls on every
    # page open, so caching them shaves the Roadmap card lag down to
    # microseconds. The signature is the mtime of the overrides file
    # plus the path itself so a swapped path (tests) invalidates.
    _persona_cache: dict[str, object] = {
        "signature": None,  # tuple of (path_str, mtime_ns) or None
        "for_persona": {},  # persona_id -> list[dict]
        "user_custom": None,  # list[dict] or None
    }

    @classmethod
    def _invalidate_persona_cache(cls) -> None:
        cls._persona_cache["signature"] = None
        cls._persona_cache["for_persona"] = {}
        cls._persona_cache["user_custom"] = None

    @classmethod
    def _persona_cache_signature(cls) -> tuple:
        try:
            mtime = AGENT_TEMPLATES_PATH.stat().st_mtime_ns
        except OSError:
            mtime = 0
        return (str(AGENT_TEMPLATES_PATH), mtime)

    @classmethod
    def _persona_cache_check(cls) -> None:
        sig = cls._persona_cache_signature()
        if cls._persona_cache.get("signature") != sig:
            cls._persona_cache["signature"] = sig
            cls._persona_cache["for_persona"] = {}
            cls._persona_cache["user_custom"] = None

    def _ensure_exists(self) -> None:
        AGENT_TEMPLATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not AGENT_TEMPLATES_PATH.exists():
            atomic_write_text(AGENT_TEMPLATES_PATH, "[]")

    def _load_overrides(self) -> list[dict]:
        self._ensure_exists()
        try:
            raw = json.loads(AGENT_TEMPLATES_PATH.read_text())
            if isinstance(raw, list):
                return _apply_migrations(raw)
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def _save(self, overrides: list[dict]) -> None:
        self._ensure_exists()
        atomic_write_json(AGENT_TEMPLATES_PATH, overrides)
        # Any write invalidates the persona cache so the next list call
        # re-reads from disk and re-applies overrides.
        self._invalidate_persona_cache()

    def list_all(self) -> list[dict]:
        """Return all templates, merging builtins with disk overrides.

        Builtin templates (source=builtin) are always included and always
        installed. Marketplace templates get their ``installed`` flag
        overridden by the disk store. Custom templates are appended last.

        Every template gets a ``capabilities`` field populated from its
        agentfile on disk. Templates with no agentfile get ``capabilities=None``.
        """
        overrides = self._load_overrides()
        override_map: dict[str, dict] = {o["id"]: o for o in overrides if "id" in o}
        desc_overrides = self._load_descriptions()

        result = []
        for t in BUILTIN_AGENT_TEMPLATES:
            override = override_map.get(t["id"])
            if override:
                merged = {**t, "installed": override.get("installed", t["installed"])}
            else:
                merged = dict(t)
            # Apply a user-edited description on top of the shipped blurb so
            # the UI shows whatever the user saved last.
            user_desc = desc_overrides.get(t["id"])
            if isinstance(user_desc, str) and user_desc.strip():
                merged["description"] = user_desc
            # Enrich with agentfile capabilities.
            merged["capabilities"] = _load_agentfile_capabilities(
                merged.get("source", "marketplace"), merged["name"]
            )
            result.append(merged)

        builtin_ids = {t["id"] for t in BUILTIN_AGENT_TEMPLATES}
        for o in overrides:
            if o.get("id", "").startswith("custom-") and o["id"] not in builtin_ids:
                row = dict(o)
                # Defensive: anything saved with id prefix 'custom-' is
                # user-created by construction. Force source=custom so a
                # stale record written by an older build with a wrong
                # source field cannot leak the "custom" badge anywhere.
                row["source"] = "custom"
                row["capabilities"] = _load_agentfile_capabilities("custom", row.get("name", ""))
                result.append(row)

        return _dedupe_templates(result)

    def list_installed(self) -> list[dict]:
        """Return only installed templates.

        ``source=builtin`` is always installed.
        ``source=marketplace`` requires ``installed=True`` in the disk store.
        Custom templates are always shown.
        """
        return [
            t for t in self.list_all()
            if t.get("source") == "builtin"
            or t.get("installed", False)
            or t.get("id", "").startswith("custom-")
        ]

    def list_marketplace(self) -> list[dict]:
        """Return marketplace templates that are not yet installed.

        Suppresses any marketplace entry whose normalized name or alias
        matches a built-in template, since built-ins are always installed
        and rendering a marketplace copy alongside them causes duplicates.
        """
        builtin_names: set[str] = set()
        for t in BUILTIN_AGENT_TEMPLATES:
            if t.get("source") == "builtin":
                builtin_names.add(t["name"].strip().lower())
                for alias in t.get("aliases", []):
                    builtin_names.add(alias.strip().lower())

        return [
            t for t in self.list_all()
            if t.get("source") == "marketplace"
            and not t.get("installed", False)
            and t["name"].strip().lower() not in builtin_names
        ]

    def install_for_persona(self, persona_id: str) -> list[dict]:
        """Install all marketplace templates whose personas list includes persona_id.

        Only installs templates not already installed. Returns newly installed list.
        """
        overrides = self._load_overrides()
        override_map: dict[str, dict] = {o["id"]: o for o in overrides if "id" in o}
        installed = []
        for t in BUILTIN_AGENT_TEMPLATES:
            if t.get("source") != "marketplace":
                continue
            if persona_id not in t.get("personas", []):
                continue
            tid = t["id"]
            if override_map.get(tid, {}).get("installed"):
                continue
            override_map[tid] = {"id": tid, "installed": True}
            installed.append(t)
        self._save(list(override_map.values()))
        return installed

    def install(self, template_id: str) -> bool:
        """Mark a marketplace template as installed. Returns True if changed."""
        tpl = _BUILTIN_BY_ID.get(template_id)
        if not tpl or tpl.get("source") == "builtin":
            return False
        overrides = self._load_overrides()
        override_map: dict[str, dict] = {o["id"]: o for o in overrides if "id" in o}
        if override_map.get(template_id, {}).get("installed"):
            return False
        override_map[template_id] = {"id": template_id, "installed": True}
        self._save(list(override_map.values()))
        return True

    def get_by_id(self, template_id: str) -> Optional[dict]:
        for t in self.list_all():
            if t.get("id") == template_id:
                return t
        return None

    def get_by_name_or_alias(self, name: str) -> Optional[dict]:
        """Return a template matching name or any alias. Used by spawn resolution."""
        tid = _resolve_alias(name)
        if tid:
            return _BUILTIN_BY_ID.get(tid)
        lower = name.strip().lower()
        for t in self._load_overrides():
            if t.get("name", "").lower() == lower:
                return t
        return None

    # ---- Custom template CRUD ----

    def list_for_persona(self, persona_id: str) -> list[dict]:
        """Return installed templates for a given persona.

        Includes:
        - All ``source=builtin`` templates (always available to everyone).
        - ``source=marketplace`` templates that list ``persona_id`` in their
          ``personas`` field AND are currently installed.

        Deduplicates by id first, falling back to normalized name. When two
        entries collide the higher-priority source wins:
        custom > marketplace > builtin. This matches how the Templates page
        expects to render cards: the user's own version beats a shipped one
        with the same name, and marketplace beats builtin.

        Cached in memory keyed by persona_id. Invalidated whenever the
        overrides file is written (install, uninstall, custom CRUD) so the
        list always reflects the latest disk state.
        """
        # Ensure the overrides file exists before sampling its mtime so
        # the cache signature is stable across calls. Without this the
        # FIRST call would create the file (no mtime) and the SECOND
        # call would see a brand-new mtime, dropping the cache.
        self._ensure_exists()
        self._persona_cache_check()
        cache = self._persona_cache["for_persona"]
        assert isinstance(cache, dict)
        if persona_id in cache:
            return cache[persona_id]  # type: ignore[no-any-return]

        candidates = [
            t for t in self.list_installed()
            if t.get("source") == "builtin"
            or (
                t.get("source") == "marketplace"
                and persona_id in t.get("personas", [])
                and t.get("installed", False)
            )
        ]
        result = _dedupe_templates(candidates)
        cache[persona_id] = result
        return result

    def list_user_custom(self) -> list[dict]:
        """Return only user-created custom templates (persona-agnostic).

        These are templates with id starting with 'custom-'. They are never
        scoped to a persona and appear for every persona. Always stamps
        ``source="custom"`` so the frontend never mislabels a user pick.

        Cached in memory until the overrides file changes. The Templates
        tab fetches this on every page open, so the cache hit returns in
        microseconds.
        """
        # Same first-call mtime stabilization as list_for_persona. See
        # the comment there for the full reasoning.
        self._ensure_exists()
        self._persona_cache_check()
        cached = self._persona_cache.get("user_custom")
        if cached is not None:
            return cached  # type: ignore[return-value]

        rows: list[dict] = []
        for o in self._load_overrides():
            if not o.get("id", "").startswith("custom-"):
                continue
            row = dict(o)
            row["source"] = "custom"
            rows.append(row)
        self._persona_cache["user_custom"] = rows
        return rows

    def list_custom(self) -> list[dict]:
        """Return only user-created custom templates (id starts with 'custom-').

        Alias for list_user_custom() kept for backwards compatibility.
        """
        return self.list_user_custom()

    def create(self, data: dict) -> dict:
        overrides = self._load_overrides()
        new_template: dict = {
            "id": f"custom-{uuid.uuid4().hex[:8]}",
            "name": data.get("name", "").strip(),
            "aliases": data.get("aliases", []),
            "description": data.get("description", "").strip(),
            "icon": data.get("icon", "smart_toy").strip(),
            "prompt_template": data.get("prompt_template", "").strip(),
            "model": data.get("model", "sonnet").strip(),
            "budget": float(data.get("budget", 2.0)),
            "source": "custom",
            "personas": data.get("personas", []),
            "installed": True,
            "builtin": False,
        }
        overrides.append(new_template)
        self._save(overrides)
        # Write agentfile for the new custom template.
        self._write_custom_agentfile(new_template)
        return new_template

    def update(self, template_id: str, data: dict) -> Optional[dict]:
        overrides = self._load_overrides()
        old_name: Optional[str] = None
        for i, t in enumerate(overrides):
            if t.get("id") == template_id:
                old_name = t.get("name", "")
                for field in ("name", "description", "icon", "prompt_template", "model"):
                    if field in data:
                        t[field] = str(data[field]).strip()
                if "budget" in data:
                    t["budget"] = float(data["budget"])
                if "aliases" in data:
                    t["aliases"] = list(data["aliases"])
                overrides[i] = t
                self._save(overrides)
                # Rename agentfile if name changed; always rewrite to stay in sync.
                if old_name and old_name != t.get("name", ""):
                    self._remove_custom_agentfile(old_name)
                self._write_custom_agentfile(t)
                return t
        return None

    def delete(self, template_id: str) -> bool:
        overrides = self._load_overrides()
        target = next((t for t in overrides if t.get("id") == template_id), None)
        updated = [t for t in overrides if t.get("id") != template_id]
        if len(updated) == len(overrides):
            return False
        self._save(updated)
        # Remove the agentfile for this custom template.
        if target and target.get("source") == "custom":
            self._remove_custom_agentfile(target.get("name", ""))
        return True

    # ---- Agentfile helpers for custom templates ----

    def _write_custom_agentfile(self, template: dict) -> None:
        """Write (or overwrite) the agentfile for a custom template."""
        name = template.get("name", "")
        if not name:
            return
        CUSTOM_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        stem = _name_to_stem(name)
        path = CUSTOM_AGENTS_DIR / f"{stem}.agent"
        try:
            path.write_text(_make_agentfile_text(template))
        except OSError:
            pass  # Non-fatal: capabilities will be None for this template
        # Custom templates do not live in AGENTS_DIR (they go to
        # ~/.myos/agents/custom/), so the /agents/templates cache
        # signature keyed on AGENTS_DIR will not see them. Call the
        # invalidation hook so the next request rebuilds the list.
        _invalidate_templates_cache()

    def _remove_custom_agentfile(self, name: str) -> None:
        """Remove the agentfile for a custom template if it exists."""
        if not name:
            return
        stem = _name_to_stem(name)
        path = CUSTOM_AGENTS_DIR / f"{stem}.agent"
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
        _invalidate_templates_cache()


    # ---- User alias management ----

    def _load_aliases(self) -> dict[str, str]:
        """Load user aliases from ~/.myos/template_aliases.json.

        Returns a dict mapping alias -> template_id.
        """
        TEMPLATE_ALIASES_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not TEMPLATE_ALIASES_PATH.exists():
            return {}
        try:
            data = json.loads(TEMPLATE_ALIASES_PATH.read_text())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _save_aliases(self, aliases: dict[str, str]) -> None:
        TEMPLATE_ALIASES_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(TEMPLATE_ALIASES_PATH, aliases)

    def get_alias(self, template_id: str) -> Optional[str]:
        """Return the user alias for a template, or None."""
        aliases = self._load_aliases()
        for alias, tid in aliases.items():
            if tid == template_id:
                return alias
        return None

    def set_alias(self, template_id: str, alias: str) -> dict:
        """Set a user alias for a template. Returns the updated template.

        Validates:
        - alias is 2-30 chars, lowercase letters, digits, hyphens only
        - no collision with existing template names or other aliases
        Raises ValueError with a plain-language message on failure.
        """
        alias = alias.strip().lower()
        if not re.match(r'^[a-z0-9][a-z0-9-]{0,28}[a-z0-9]$', alias) and len(alias) >= 2:
            # For exactly 2-char aliases, the regex above works. Refine:
            pass
        # Strict validation
        if len(alias) < 2 or len(alias) > 30:
            raise ValueError("Alias must be between 2 and 30 characters.")
        if not re.match(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$', alias):
            raise ValueError(
                "Alias can only contain lowercase letters, numbers, and hyphens. "
                "It must start and end with a letter or number."
            )

        # Check collision with existing template names (case-insensitive)
        for t in BUILTIN_AGENT_TEMPLATES:
            if t["name"].lower() == alias:
                raise ValueError(
                    f"'{alias}' is already the name of a template. Pick a different alias."
                )
            for a in t.get("aliases", []):
                if a.lower() == alias:
                    raise ValueError(
                        f"'{alias}' is already a built-in alias. Pick a different alias."
                    )

        # Check collision with other user aliases (except this template's own)
        existing = self._load_aliases()
        for existing_alias, existing_tid in existing.items():
            if existing_alias == alias and existing_tid != template_id:
                raise ValueError(
                    f"'{alias}' is already used as an alias for another template. "
                    "Pick a different alias."
                )

        # Check collision with custom template names
        for t in self._load_overrides():
            if t.get("name", "").lower() == alias and t.get("id") != template_id:
                raise ValueError(
                    f"'{alias}' is already the name of a custom template. Pick a different alias."
                )

        existing[alias] = template_id
        # Remove any previous alias for this template (one alias per template)
        to_remove = [k for k, v in existing.items() if v == template_id and k != alias]
        for k in to_remove:
            del existing[k]
        self._save_aliases(existing)

        tpl = self.get_by_id(template_id)
        if tpl:
            tpl["user_alias"] = alias
        return tpl or {"id": template_id, "user_alias": alias}

    def clear_alias(self, template_id: str) -> dict:
        """Remove any user alias for a template. Returns the updated template."""
        existing = self._load_aliases()
        to_remove = [k for k, v in existing.items() if v == template_id]
        for k in to_remove:
            del existing[k]
        self._save_aliases(existing)

        tpl = self.get_by_id(template_id)
        if tpl:
            tpl["user_alias"] = None
        return tpl or {"id": template_id, "user_alias": None}

    def get_all_user_aliases(self) -> dict[str, str]:
        """Return all user aliases as {alias: template_id}."""
        return self._load_aliases()

    def resolve_user_alias(self, name: str) -> Optional[str]:
        """Given a name, return the template_id if it matches a user alias."""
        aliases = self._load_aliases()
        return aliases.get(name.strip().lower())

    # ---- User-edited descriptions ----

    def _load_descriptions(self) -> dict[str, str]:
        """Load user description overrides from disk. Returns ``{template_id: description}``.

        Keyed by template id so builtin and marketplace templates can have an
        edited blurb. Custom templates already store their description in the
        main overrides file, so they do not need this file.
        """
        TEMPLATE_DESCRIPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not TEMPLATE_DESCRIPTIONS_PATH.exists():
            return {}
        try:
            data = json.loads(TEMPLATE_DESCRIPTIONS_PATH.read_text())
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, str)}
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _save_descriptions(self, descriptions: dict[str, str]) -> None:
        TEMPLATE_DESCRIPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(TEMPLATE_DESCRIPTIONS_PATH, descriptions)

    def get_description(self, template_id: str) -> Optional[str]:
        """Return the merged description for a template (user override wins).

        Returns ``None`` when the template does not exist at all.
        """
        tpl = self.get_by_id(template_id)
        if tpl is None:
            return None
        return tpl.get("description", "") or ""

    def set_description(self, template_id: str, description: str) -> dict:
        """Save a user-edited description for a builtin or marketplace template.

        For custom templates, routes through ``update()`` so both the primary
        override record and the agentfile stay in sync. For shipped templates,
        persists the override to ``template_descriptions.json`` so a ``git pull``
        cannot clobber it.

        Returns the updated template dict (with the merged description).
        Raises ``ValueError`` if the template does not exist, or if the
        description is empty.
        """
        if not isinstance(description, str):
            raise ValueError("Description must be text.")
        stripped = description.strip()
        if not stripped:
            raise ValueError("Description cannot be empty. Pick a short summary for this template.")
        if len(stripped) > 2000:
            raise ValueError("Description is too long. Keep it under 2000 characters.")

        tpl = self.get_by_id(template_id)
        if tpl is None:
            raise ValueError(f"No template with id '{template_id}'.")

        # Custom templates: edit through the standard update path so the
        # agentfile and the overrides file both reflect the new value.
        if template_id.startswith("custom-"):
            updated = self.update(template_id, {"description": stripped})
            if updated is None:
                raise ValueError(f"Could not update custom template '{template_id}'.")
            return updated

        # Builtin / marketplace: save in the descriptions override file.
        descriptions = self._load_descriptions()
        descriptions[template_id] = stripped
        self._save_descriptions(descriptions)

        merged = self.get_by_id(template_id) or {}
        return merged

    def clear_description(self, template_id: str) -> dict:
        """Remove any user-edited description for a template.

        For custom templates this is a no-op (they must carry a description).
        For builtin / marketplace templates, deletes the override so the
        shipped description shows again. Returns the template with the reset
        description.
        """
        tpl = self.get_by_id(template_id)
        if tpl is None:
            raise ValueError(f"No template with id '{template_id}'.")
        if template_id.startswith("custom-"):
            return tpl

        descriptions = self._load_descriptions()
        if template_id in descriptions:
            del descriptions[template_id]
            self._save_descriptions(descriptions)
        merged = self.get_by_id(template_id) or {}
        return merged


agent_templates_store = AgentTemplatesStore()
