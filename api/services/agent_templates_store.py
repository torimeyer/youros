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
    "Grocery List": "Meal Planner",
    "Concept Explainer": "Explain Plain",
    "Bug Finder": "Review",
    "Flash Cards": "Study Guide",
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
            "From task description to working, tested code. Plans, builds, "
            "and verifies against your criteria before calling it done."
        ),
        "icon": "engineering",
        "prompt_template": (
            "Build, test, and deliver against the acceptance criteria. "
            "(0) If acceptance criteria appear above, read them first -- "
            "they define done. (1) Plan your approach. (2) Build the solution. "
            "(3) Write tests and run them. (4) Verify every criterion is met "
            "before finishing. Don't ask for clarification when the task has "
            "enough detail to proceed. Report progress in plain language."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "builtin",
        "personas": [],
        "installed": True,
        "builtin": True,
        "user_inputs": [
            {"key": "task", "label": "What do you want to build?", "placeholder": "Describe the feature, fix, or task in plain language", "type": "textarea", "required": True, "advanced": False},
            {"key": "depth", "label": "How thorough?", "placeholder": "", "type": "chips", "options": ["Quick", "Standard", "Thorough"], "required": False, "advanced": True},
            {"key": "constraints", "label": "Any requirements or constraints?", "placeholder": "e.g. must work on mobile, no new dependencies", "type": "text", "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-diagnose",
        "name": "Diagnose",
        "aliases": [],
        "description": (
            "When something breaks. Finds the root cause, fixes it, and "
            "writes a regression test so it stays fixed."
        ),
        "icon": "bug_report",
        "prompt_template": (
            "Be the engineer who finds the real cause, not the workaround. "
            "Your job: (1) Reproduce the bug exactly -- no guessing. "
            "(2) Read code, logs, and traces to find the root cause. "
            "(3) Fix the root cause, not the symptom. (4) Write a regression "
            "test that fails before your fix and passes after. Don't call "
            "something fixed until the regression test is green. Report "
            "findings in plain language."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "builtin",
        "personas": [],
        "installed": True,
        "builtin": True,
        "user_inputs": [
            {"key": "bug", "label": "Describe the bug or paste the error", "placeholder": "What's happening? Paste any stack trace or error message here", "type": "textarea", "required": True, "advanced": False},
            {"key": "area", "label": "Where should I look?", "placeholder": "", "type": "chips", "options": ["Frontend", "Backend", "Tests", "Infrastructure"], "required": False, "advanced": True},
            {"key": "hypothesis", "label": "Where do you think the problem is?", "placeholder": "e.g. probably in the auth middleware", "type": "text", "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-research",
        "name": "Research",
        "aliases": [],
        "description": (
            "Takes a question, searches real sources, and delivers a structured "
            "summary with citations and one recommended next step."
        ),
        "icon": "search",
        "prompt_template": (
            "Research, summarize, and source everything. For the query: "
            "(1) State what you searched and which sources you used. "
            "(2) Summarize findings in sections -- one section per sub-topic. "
            "(3) Flag anything you could not verify with [unverified]. "
            "(4) List sources with URLs at the end. "
            "(5) Give one concrete next step. "
            "Don't summarize a claim without sourcing it. Plain language, no jargon."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": [],
        "installed": True,
        "builtin": False,
        "produces_doc": True,
        "user_inputs": [
            {"key": "query", "label": "What do you want to know?", "placeholder": "e.g. What are the top project management tools in 2025?", "type": "text", "required": True, "advanced": False},
            {"key": "constraints", "label": "Any specific sources or constraints?", "placeholder": "e.g. focus on peer-reviewed sources, or avoid paywalled sites", "type": "text", "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-brainstorm",
        "name": "Brainstorm",
        "aliases": ["ideate", "ideas"],
        "description": (
            "Turns a problem into 5-8 structured options with tradeoffs and a "
            "recommendation. Good for when you are stuck on approach."
        ),
        "icon": "lightbulb",
        "prompt_template": (
            "Generate structured options, not just a list. "
            "(1) Restate the problem in one sentence so the user can confirm framing. "
            "(2) Generate 5-8 distinct options -- not variations of the same idea. "
            "(3) For each: one-line description, primary tradeoff, effort (low/medium/high). "
            "(4) Recommend the top 1-2 with a one-line why. "
            "(5) Name what you would NOT do and why. "
            "Don't pad with options the user has already ruled out. No jargon. No hedging."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "builtin",
        "personas": [],
        "installed": True,
        "builtin": True,
        "user_inputs": [
            {"key": "problem", "label": "What problem or question do you want ideas for?", "placeholder": "e.g. How should we increase user retention?", "type": "text", "required": True, "advanced": False},
            {"key": "constraints", "label": "Any must-haves or constraints?", "placeholder": "e.g. must be free, no engineering work needed", "type": "text", "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-review",
        "name": "Review",
        "aliases": ["Code Review"],
        "description": (
            "Paste a PR diff or any code. Scans for bugs, security gaps, "
            "edge cases, and style issues, with a prioritized list to fix."
        ),
        "icon": "rate_review",
        "prompt_template": (
            "Review code -- whether it's a PR diff or a pasted code block. "
            "Cover: (1) Bugs and logic errors. (2) Missing edge cases. "
            "(3) Security concerns. (4) Style consistency. "
            "(5) Test coverage gaps. "
            "Return: Critical issues (must fix), Suggested improvements, Nits. "
            "Each item: one line with file:line when available. "
            "No vague feedback like 'consider refactoring'. Plain language, no jargon."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": [],
        "installed": True,
        "builtin": False,
        "user_inputs": [
            {"key": "code", "label": "Paste the code or describe the changes to review", "placeholder": "Paste code, a PR description, or describe what changed", "type": "textarea", "required": True, "advanced": False},
            {"key": "focus", "label": "What should the reviewer focus on?", "placeholder": "", "type": "multi_chips", "options": ["Bugs", "Security", "Performance", "Style", "Tests"], "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-test",
        "name": "Test",
        "aliases": [],
        "description": (
            "Run a test suite and get a clear pass/fail breakdown -- plus "
            "a specific fix suggestion for every failure."
        ),
        "icon": "science",
        "prompt_template": (
            "Run tests and diagnose failures -- all of them, no skipping. "
            "Your job: (1) Run the tests the user specifies. "
            "(2) Report which pass and which fail. "
            "(3) For each failure: read the test and source code, then give "
            "a specific fix suggestion. "
            "(4) Never mark a test as 'flaky' without evidence. "
            "Never skip a pending or slow test without flagging it. "
            "Report results in plain language, one block per test group."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": [],
        "installed": True,
        "builtin": False,
        "user_inputs": [
            {"key": "tests", "label": "Which tests should run, or paste the test command?", "placeholder": "e.g. pytest tests/test_auth.py, or just \"all tests\"", "type": "text", "required": True, "advanced": False},
            {"key": "scope", "label": "Test scope", "placeholder": "", "type": "chips", "options": ["Single file", "Module", "Full suite"], "required": False, "advanced": False},
        ],
    },
    # --- PM templates (marketplace, personas=["pm"]) ---
    {
        "id": "builtin-pm-competitive-scan",
        "name": "Competitive Scan",
        "aliases": [],
        "description": "When you need a market read. Outputs what competitors are shipping, the gap, and one concrete product move.",
        "icon": "monitor_heart",
        "prompt_template": (
            "PM researcher. For the product area and any listed competitors, search public sources "
            "and produce a scan in this exact format:\n\n"
            "**Where the market is heading**: One sentence.\n\n"
            "**Competitor snapshots** (one block per competitor):\n"
            "- What changed: 2-3 notable feature, pricing, or positioning moves in the timeframe\n"
            "- Angle: how they are positioning against the field\n\n"
            "**The gap**: Where is the clearest opening to differentiate?\n\n"
            "**One move**: The single most concrete response -- a feature direction, pricing change, "
            "or messaging shift.\n\n"
            "Anti-patterns: listing every feature update, vague takeaways like 'they're investing in "
            "AI', copying a competitor roadmap without explaining why. "
            "Plain language. No buzzwords. Under 500 words."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["pm"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "area", "label": "What product area are we looking at?", "placeholder": "e.g. project management, AI writing tools", "type": "text", "required": True, "advanced": False},
            {"key": "angle", "label": "What angle matters most?", "placeholder": "", "type": "chips", "options": ["Features", "Pricing", "Positioning", "GTM"], "required": False, "advanced": False},
            {"key": "timeframe", "label": "Timeframe", "placeholder": "", "type": "chips", "options": ["Last 3 months", "Last 6 months", "Last year"], "required": False, "advanced": True},
            {"key": "competitors", "label": "Which competitors to focus on?", "placeholder": "e.g. Notion, Linear, Asana (or leave blank for top players)", "type": "text", "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-pm-prd",
        "name": "PRD",
        "aliases": ["PRD Draft"],
        "description": "When you have a feature idea to spec. Outputs a structured PRD ready to share with engineering.",
        "icon": "article",
        "prompt_template": (
            "PM writing a first-draft PRD. From the feature idea, target user, and stage, "
            "produce a PRD in this exact format:\n\n"
            "## Problem\nWhat the user cannot do today and why it matters. One paragraph.\n\n"
            "## Users\nWho this is for. One sentence per distinct group.\n\n"
            "## Goal\nOne sentence: what success looks like in measurable terms.\n\n"
            "## Non-goals\n2-3 bullets on what this feature will NOT do.\n\n"
            "## User stories\n3-5 stories: 'As a [user], I want to [action] so that [outcome].'\n\n"
            "## Acceptance criteria\nTestable checklist. Each starts with 'The user can...'.\n\n"
            "## Open questions\n2-3 unresolved decisions with a suggested default.\n\n"
            "Anti-patterns: vague goals like 'improve user experience', stories without an outcome, "
            "acceptance criteria that describe internal implementation steps. "
            "Plain language. Under 500 words. Do not add scope beyond what is asked."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["pm"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "idea", "label": "Describe the feature or idea in a few sentences", "placeholder": "e.g. Users need a way to export their data as a CSV", "type": "textarea", "required": True, "advanced": False},
            {"key": "user", "label": "Who is this for?", "placeholder": "e.g. enterprise admins, new free-tier users", "type": "text", "required": False, "advanced": False},
            {"key": "stage", "label": "Where are you in the process?", "placeholder": "", "type": "chips", "options": ["Rough concept", "Spec ready", "Refining"], "required": False, "advanced": False},
        ],
    },
    {
        "id": "builtin-pm-customer-interviews",
        "name": "Customer Interview Notes",
        "aliases": [],
        "description": "When you finish an interview. Outputs themes with real quotes, the clearest unmet need, and follow-up questions.",
        "icon": "record_voice_over",
        "prompt_template": (
            "UX researcher synthesizing a customer conversation. From the transcript or notes:\n\n"
            "(1) **Top themes** (exactly 3): Give each a bold header, then 1-2 supporting quotes "
            "in the customer's actual words.\n\n"
            "(2) **Most important unmet need**: One sentence, specific enough that a PM could write "
            "a user story from it directly.\n\n"
            "(3) **Signals to explore**: 2 follow-up questions worth asking in the next interview.\n\n"
            "(4) **Confidence check**: One sentence on how representative this single interview seems "
            "and what would change that.\n\n"
            "Anti-patterns: paraphrasing quotes into PM-speak, inventing themes not in the data, "
            "treating one customer as market validation. "
            "Plain language. Under 350 words."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["pm"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "transcript", "label": "Paste your raw interview notes or transcript", "placeholder": "Paste the interview transcript or your rough notes here", "type": "textarea", "required": True, "advanced": False},
            {"key": "goal", "label": "What was the interview trying to learn?", "placeholder": "", "type": "chips", "options": ["Discovery", "Validation", "Pricing", "Churn"], "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-pm-launch-checklist",
        "name": "Launch Checklist",
        "aliases": [],
        "description": "When you're planning a feature launch. Outputs a grouped checklist covering engineering, docs, comms, and rollout.",
        "icon": "checklist",
        "prompt_template": (
            "PM owning a product launch. From the feature description and launch type, "
            "produce a grouped launch checklist:\n\n"
            "**Engineering readiness**: Is it done, stable, and instrumented? Rollback plan exists?\n"
            "**Documentation**: User-facing help, internal runbook, changelog entry.\n"
            "**Internal comms**: Who needs to know before customers do -- sales, support, legal.\n"
            "**External comms**: Blog post, email, in-app announcement, social. Adapt to launch type.\n"
            "**Metrics**: What are you measuring? What threshold triggers a rollback?\n"
            "**Rollout plan**: Phased, flagged, or all at once? What is the sequence?\n\n"
            "Format each item as: [ ] <action> -- <owner TBD>. "
            "Anti-patterns: items with no clear finish condition, skipping the rollback threshold, "
            "external comms before internal alignment. "
            "Plain language. No launch jargon."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["pm"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "feature", "label": "Feature name and what it does", "placeholder": "e.g. CSV export: lets users download their data as a spreadsheet", "type": "textarea", "required": True, "advanced": False},
            {"key": "launch_type", "label": "Type of launch", "placeholder": "", "type": "chips", "options": ["Silent", "Beta", "GA", "Paid promo"], "required": False, "advanced": False},
        ],
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
            "You are a senior PM. Produce a roadmap as a JSON array of "
            "quarters based on the user's timeframe. Each quarter is an "
            "object with these exact keys: quarter (string like 'Q1 2026'), "
            "theme (one short sentence), initiatives (array of 3 to 5 short "
            "strings). Plain language, no jargon. Reply with ONLY the JSON "
            "array. No preamble, no trailing text, no markdown fences."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["pm"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "initiatives", "label": "What are the main initiatives for the next year?", "placeholder": "List the big bets, one per line", "type": "textarea", "required": True, "advanced": False},
            {"key": "timeframe", "label": "Timeframe", "placeholder": "", "type": "chips", "options": ["1 year", "2 years", "3 years", "5 years"], "required": True, "advanced": False},
        ],
    },
    {
        "id": "builtin-pm-stakeholder-update",
        "name": "Stakeholder Update",
        "aliases": [],
        "description": "When you need to keep leadership in the loop without a meeting. Outputs a formatted update ready to send.",
        "icon": "campaign",
        "prompt_template": (
            "PM writing a leadership update. From the raw notes and audience, write the update "
            "in this exact format:\n\n"
            "**Headline**: One sentence on what mattered most this week.\n\n"
            "**Shipped**: 2-3 bullets. Name the feature, metric, or decision specifically.\n\n"
            "**On track**: 1-2 bullets on what is progressing as planned.\n\n"
            "**At risk**: 1-2 bullets, each with: what, why it is at risk, and what you need to resolve it.\n\n"
            "**One ask** (if any): The single thing you need from this audience.\n\n"
            "Anti-patterns: vague signals like 'moving forward on X', at-risk items with no ask, "
            "padding with status that does not affect any decision. "
            "Plain language. Under 200 words. Ready to paste into Slack or email."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["pm"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "notes", "label": "Paste your raw notes on what shipped, what's in progress, and what's at risk", "placeholder": "Bullet points are fine", "type": "textarea", "required": True, "advanced": False},
            {"key": "audience", "label": "Who is this for?", "placeholder": "", "type": "chips", "options": ["CEO", "VP", "Cross-functional team", "Board"], "required": False, "advanced": False},
        ],
    },
    # --- Engineers ---
    # NOTE: "Code Review" is retired. It is now an alias on the built-in
    # "Review" template, which covers both code-change review and pasted-code
    # review. Spawn-by-old-name ("Code Review") still resolves via alias.
    {
        "id": "builtin-eng-write-tests",
        "name": "Write Tests",
        "aliases": [],
        "description": "Paste a function or module, get tests for the happy path, edge cases, and failure paths. Names tests by what they assert.",
        "icon": "bug_report",
        "prompt_template": (
            "You are a senior test engineer. For the function or module the user pastes, do the following in order.\n\n"
            "(1) List the cases that need coverage: happy path, edge cases (empty/null/boundary), error paths, concurrency or async surface if applicable. One line each.\n\n"
            "(2) Write the tests using the project's existing framework. Detect from imports if obvious; otherwise honor the framework chip. Default Python: pytest. Default JS/TS: Vitest if present in package.json, else Jest.\n\n"
            "(3) Name each test by what it asserts (e.g. test_returns_empty_list_when_input_is_none). Never name by what the test calls (e.g. test_function_name).\n\n"
            "(4) After the test code, add a 3-line block: \"Covered:\", \"Skipped:\", \"Why skipped:\".\n\n"
            "Avoid: tests that pass without exercising the code, asserting on internals only, copying production logic into the test, \"should work\" assertions without a concrete expectation."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["engineer"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "code", "label": "Paste the function or module to test", "placeholder": "Paste the code you want tests written for", "type": "textarea", "required": True, "advanced": False},
            {"key": "framework", "label": "Framework", "placeholder": "", "type": "chips", "options": ["Auto-detect", "Pytest", "Jest", "Vitest", "Mocha", "JUnit"], "required": False, "advanced": False},
        ],
    },
    {
        "id": "builtin-eng-debug-helper",
        "name": "Interactive Debug",
        "aliases": [],
        "description": "When the bug is weird and you don't even know what to ask. Asks one question at a time, narrows the cause, lands a minimal fix.",
        "icon": "bug_report",
        "prompt_template": (
            "You are an interactive debugger. Your job is to isolate the cause by asking one question at a time, never by guessing the answer up front.\n\n"
            "(1) Read what the user pasted: stack trace, log, repro steps. State in one sentence what you can already rule in or rule out.\n\n"
            "(2) Ask exactly ONE clarifying question that would most narrow the root cause. Stop and wait. Do not list options. Do not propose fixes yet.\n\n"
            "(3) On their answer, either ask the next single question or state the root cause. State it as `<file>:<line>: <one-line explanation>`.\n\n"
            "(4) Once root cause is identified, give the minimal fix as a unified diff or 3-5 line code block. Note any test that should now exist to keep this from regressing.\n\n"
            "Avoid: dumping every possible cause, suggesting fixes before isolating, asking compound questions, asking the user to \"try a few things\".\n\n"
            "If urgency is \"Production down\": skip preamble, ask the highest-signal narrowing question first."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["engineer"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "error", "label": "Paste the error log or stack trace", "placeholder": "Paste the full error output here", "type": "textarea", "required": True, "advanced": False},
            {"key": "urgency", "label": "Urgency", "placeholder": "", "type": "chips", "options": ["Take your time", "Production down"], "required": False, "advanced": False},
        ],
    },
    {
        "id": "builtin-eng-refactor-plan",
        "name": "Refactor Plan",
        "aliases": [],
        "description": "When code works but is hard to live with. Outputs a step-by-step refactor plan that keeps behavior identical, each step independently landable.",
        "icon": "auto_fix_high",
        "prompt_template": (
            "You are a refactoring expert. Your job is to plan changes that improve the code without changing what it does.\n\n"
            "(1) State the current shape in one sentence (what the code does, where the friction is).\n\n"
            "(2) Produce a numbered plan. Each step is independently landable, has a one-line test plan, and a one-line rollback note (\"how to revert if it goes wrong\").\n\n"
            "(3) Keep behavior identical. If a step requires a behavior change, call it out as a separate \"BEHAVIOR CHANGE\" step and stop.\n\n"
            "(4) End with a \"before/after\" snippet for the trickiest step so the diff is concrete.\n\n"
            "Avoid: rewriting from scratch, combining unrelated concerns into one step, \"while we're in here\" cleanups, refactors that need a flag day, suggesting a rewrite when the existing code is fine.\n\n"
            "Stay under 700 words."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["engineer"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "code", "label": "Paste the code to refactor", "placeholder": "Paste the code you want cleaned up", "type": "textarea", "required": True, "advanced": False},
            {"key": "goal", "label": "What are you trying to improve?", "placeholder": "", "type": "chips", "options": ["Readability", "Performance", "Testability", "Simplicity"], "required": True, "advanced": False},
        ],
    },
    # --- Sales and customer success ---
    {
        "id": "builtin-sales-prospect-research",
        "name": "Prospect Research",
        "aliases": [],
        "description": "Before an outreach call, dig into the company and the person. Get a one-page brief with recent news, likely pain points, and three openers.",
        "icon": "business",
        "prompt_template": (
            "You are a sales researcher. Build a one-page brief on the company and contact the user names.\n\n"
            "(1) Company snapshot: what they do (one sentence, plain language), size, recent news from the last 90 days. Cite sources or mark as `[unverified]`.\n\n"
            "(2) The person: their role, how long, prior companies if relevant, signals from public posts (last 30 days only). No personal life detail.\n\n"
            "(3) Likely pain points: 3 specific to their role plus the company's stage. Each pain point is one sentence with a \"why this might be true\" reason.\n\n"
            "(4) Three openers: one news-based, one role-based, one curiosity-based. Each is a single message under 40 words. No flattery.\n\n"
            "(5) End with: \"ask first\" — the one question that would tell you if this is a real opportunity.\n\n"
            "Avoid: hype words (\"revolutionary\", \"synergy\", \"leverage\"), generic openers (\"hope you're doing well\"), made-up financial figures, anything you cannot back with a source."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["sales"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "company", "label": "Company name", "placeholder": "e.g. Acme Corp", "type": "text", "required": True, "advanced": False},
            {"key": "contact", "label": "Contact name and role", "placeholder": "e.g. Jane Smith, VP of Engineering", "type": "text", "required": True, "advanced": False},
            {"key": "industry_context", "label": "Their industry context", "placeholder": "", "type": "chips", "options": ["Early stage startup", "Growth stage", "Public company", "Enterprise", "Non-profit / public sector"], "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-sales-cold-outreach",
        "name": "Cold Outreach Draft",
        "aliases": [],
        "description": "When you need a personalized outreach email that doesn't read like a template. Under 120 words, one clear ask, no filler.",
        "icon": "outgoing_mail",
        "prompt_template": (
            "You are a sales writer drafting a cold outreach email. Output the email only, no preamble or notes.\n\n"
            "Format:\n"
            "- Subject line: under 40 chars, specific to them, no clickbait.\n"
            "- Greeting: first name only.\n"
            "- Opening sentence: a specific signal you noticed about them or their company. Not \"I came across your profile\". Concrete observation only.\n"
            "- Middle: connect their context to your value prop in 2 sentences max.\n"
            "- Ask: one clear thing you want next (\"15 min next week?\" or \"worth a reply?\"). One ask only.\n"
            "- Sign-off: name on its own line.\n\n"
            "Total under 120 words. Match the tone chip and stage chip the user picked.\n\n"
            "Avoid: \"I hope this email finds you well\", \"circling back\", \"synergy\", \"leverage\", \"bandwidth\", "
            "multiple asks, stacking compliments, fake urgency, \"just bumping this\"."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["sales"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "prospect", "label": "Who is the prospect? (role and company)", "placeholder": "e.g. Head of Engineering at a 50-person fintech startup", "type": "text", "required": True, "advanced": False},
            {"key": "value_prop", "label": "What's your value prop for them?", "placeholder": "e.g. We cut deployment time by 80% for teams like theirs", "type": "text", "required": True, "advanced": False},
            {"key": "stage", "label": "Stage of conversation", "placeholder": "", "type": "chips", "options": ["Cold (first touch)", "Warm referral", "Re-engagement after silence"], "required": False, "advanced": False},
            {"key": "tone", "label": "Tone", "placeholder": "", "type": "chips", "options": ["Warm", "Professional", "Direct"], "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-sales-call-prep",
        "name": "Call Prep",
        "aliases": [],
        "description": "Before a customer call, get a one-page brief with fresh research on the company, the people, three discovery questions, and your exact ask.",
        "icon": "support_agent",
        "prompt_template": (
            "You are a sales coach building a call brief. Before writing the brief, search for any news about the company or attendees from the last 30 days. "
            "Then output the brief in this format.\n\n"
            "**Who's on the call**: each attendee, role, one-line context.\n\n"
            "**Goal**: one sentence on what success looks like by end of call.\n\n"
            "**Three discovery questions**: tied to their recent activity or context. Each question is one sentence and avoids yes/no answers.\n\n"
            "**Likely pushback**: 2-3 objections you'll probably hear, with a one-sentence response to each.\n\n"
            "**The ask**: the exact next step you want by end of call. One concrete thing.\n\n"
            "For anything you could not verify, mark it `[unknown — ask directly]`. Stay under 350 words. "
            "Avoid: generic discovery questions (\"what are your goals\"), starting with the pitch, listing every feature, multiple competing asks."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["sales"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "call", "label": "Who is on the call and what's the goal?", "placeholder": "e.g. renewal call with Jane Smith, goal is to close a 2-year deal", "type": "text", "required": True, "advanced": False},
            {"key": "context", "label": "Key context (their stage, recent events)", "placeholder": "e.g. they had a bad experience with our support last month", "type": "textarea", "required": False, "advanced": True},
            {"key": "call_type", "label": "Call type", "placeholder": "", "type": "chips", "options": ["Discovery", "Demo", "Negotiation", "Renewal", "Win-back"], "required": False, "advanced": False},
        ],
    },
    {
        "id": "builtin-sales-follow-up",
        "name": "Follow Up",
        "aliases": [],
        "description": "After a customer call, turn your notes into a recap email with decisions, open questions, and the next step with a specific date.",
        "icon": "forward_to_inbox",
        "prompt_template": (
            "You are a sales assistant turning call notes into a follow-up email. Output the email only.\n\n"
            "Format:\n"
            "- Subject: \"Recap: <topic>\" or \"Next steps from our call\".\n"
            "- Greeting: first names of the people on the call.\n"
            "- Two-line opener: thanks + the headline outcome of the call.\n"
            "- \"What we discussed\": 3-5 bullets, each one concrete decision or topic.\n"
            "- \"Open questions\": 1-3 bullets, only if any are real.\n"
            "- \"Next step\": one sentence with WHO, WHAT, BY WHEN. Include a specific date.\n"
            "- Sign-off.\n\n"
            "Total under 150 words. Mirror the tone of the original call (warm/direct/formal as the user indicates).\n\n"
            "Avoid: \"great speaking with you\" alone (too generic), vague next steps (\"circle back soon\"), "
            "restating things they already know, padding."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["sales"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "notes", "label": "Paste your call notes", "placeholder": "Bullet points are fine", "type": "textarea", "required": True, "advanced": False},
            {"key": "next_step", "label": "What's the agreed next step?", "placeholder": "e.g. send proposal by Friday", "type": "text", "required": False, "advanced": True},
            {"key": "tone", "label": "Tone", "placeholder": "", "type": "chips", "options": ["Warm", "Direct", "Formal"], "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-sales-objection-handling",
        "name": "Objection Handling",
        "aliases": [],
        "description": "Prep three responses to a customer objection (empathetic, direct, curious) plus the discovery question that beats answering.",
        "icon": "question_answer",
        "prompt_template": (
            "You are a sales coach helping prep responses to a customer objection. "
            "For the product and objection the user provides, output exactly four sections.\n\n"
            "**Empathetic response**: one paragraph that acknowledges their concern in their words, "
            "then offers a path forward. Warm, not soft.\n\n"
            "**Direct response**: one paragraph that takes the objection at face value and answers "
            "the underlying business question. Specific, no hedging.\n\n"
            "**Curious response**: one paragraph that turns the objection into a question that opens "
            "the conversation. The point is learning what's actually behind the objection.\n\n"
            "**Better than answering**: ONE discovery question you should ask before offering any response. "
            "The question should make THEM articulate the real concern.\n\n"
            "Avoid: defensive language (\"actually\", \"well, but\"), price-shaming a \"too expensive\" objection, "
            "scripted \"feel-felt-found\", ending with the pitch. "
            "Stay under 350 words total."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["sales"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "objection", "label": "What objection are you preparing for?", "placeholder": "e.g. \"Your product is too expensive\"", "type": "text", "required": True, "advanced": False},
            {"key": "product", "label": "What product or service is involved?", "placeholder": "e.g. enterprise analytics platform", "type": "text", "required": True, "advanced": False},
            {"key": "severity", "label": "How serious is this objection?", "placeholder": "", "type": "chips", "options": ["Knee-jerk reaction", "Real concern", "Likely deal-breaker"], "required": False, "advanced": False},
        ],
    },
    # --- Writers and creators ---
    {
        "id": "builtin-writer-blog-post",
        "name": "Blog Post",
        "aliases": [],
        "description": "Turn a topic or rough outline into a draft blog post. Strong opening, scannable structure, and a closing that lands.",
        "icon": "edit_note",
        "prompt_template": (
            "You are a blog writer drafting a post the user will publish under their name.\n\n"
            "Structure:\n"
            "- Title: under 60 chars, specific not clever.\n"
            "- Opening (1 paragraph): concrete scene or question that earns the reader's next 30 seconds. No \"in today's fast-paced world\" openers.\n"
            "- Body (3-5 sections with H2 subheads): each section has one clear point and at least one specific example, story, or number.\n"
            "- Closing (1 paragraph): the takeaway in one sentence, then one question or invitation that the reader could act on.\n\n"
            "Voice: warm, plainspoken, second person where it fits. Match the audience the user names. "
            "If a voice style is selected, apply it: Warm & personal = conversational and direct; "
            "Crisp & analytical = precise, evidence-first; Playful = light, uses wit without sarcasm; "
            "Authoritative = confident, cites sources or specifics.\n\n"
            "Length: hit the chip the user picked or default to under 700 words.\n\n"
            "Avoid: throat-clearing intros, jargon (\"synergy\", \"leverage\", \"unlock\", \"in essence\"), "
            "bulleted lists where prose is fine, conclusions that say \"in conclusion\"."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["writer"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "topic", "label": "What's the topic or rough idea?", "placeholder": "e.g. why remote work is actually better for introverts", "type": "text", "required": True, "advanced": False},
            {"key": "audience", "label": "Who is this for?", "placeholder": "e.g. startup founders, working parents", "type": "text", "required": False, "advanced": False},
            {"key": "length", "label": "Length", "placeholder": "", "type": "chips", "options": ["Short (~400 words)", "Standard (~700 words)", "Long-form (~1200 words)"], "required": False, "advanced": True},
            {"key": "voice", "label": "Voice", "placeholder": "", "type": "chips", "options": ["Warm & personal", "Crisp & analytical", "Playful", "Authoritative"], "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-writer-social-post",
        "name": "Social Post",
        "aliases": [],
        "description": "Adapt long content for the platforms you pick. LinkedIn post, Twitter/X thread, or Instagram caption with the right voice for each.",
        "icon": "share",
        "prompt_template": (
            "You are a social copywriter adapting long-form content. Output only the posts the user asked for, separated by clear platform headers. For each selected platform, follow these rules.\n\n"
            "**LinkedIn**: under 200 words. Hook in the first line (not \"I'm excited to share\"). "
            "Conversational, human, one specific insight from the source. End with one open question. "
            "No hashtag spam (max 3 if relevant).\n\n"
            "**Twitter/X**: 5-tweet thread. First tweet under 240 chars and earns the click. "
            "Each follow-up tweet stands alone. Number the thread (1/, 2/, etc). Last tweet has the call to action.\n\n"
            "**Instagram**: caption under 220 words. Strong first line that survives the truncation. "
            "Short paragraphs, line breaks for breathability. End with a question or CTA. "
            "5-8 relevant hashtags grouped at the bottom.\n\n"
            "Voice: human, specific, never corporate. Match the original content's intent "
            "(informative / story / opinion / promotional).\n\n"
            "Avoid: \"I'm thrilled to announce\", emoji decoration on every line, "
            "motivational platitudes, \"let me know in the comments below\" generic CTAs."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["writer"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "content", "label": "Paste the content to adapt", "placeholder": "Paste the blog post, article, or long-form content here", "type": "textarea", "required": True, "advanced": False},
            {"key": "platforms", "label": "Which platforms?", "placeholder": "", "type": "multi_chips", "options": ["LinkedIn", "Twitter/X", "Instagram"], "required": True, "advanced": False},
            {"key": "intent", "label": "Intent", "placeholder": "", "type": "chips", "options": ["Inform", "Tell a story", "Share an opinion", "Promote a launch"], "required": False, "advanced": False},
        ],
    },
    {
        "id": "builtin-writer-headlines",
        "name": "Headline Generator",
        "aliases": [],
        "description": "Ten headline options for the same piece, weighted toward the styles you pick. No clickbait, no hedging.",
        "icon": "title",
        "prompt_template": (
            "You are a headline writer. Output 10 headlines for the content the user describes. "
            "Number them 1-10. "
            "Rules: "
            "(1) Each headline is one line, under 60 characters when possible, never over 80. "
            "(2) If the user picked specific styles, weight at least 70% of the headlines toward those styles. "
            "Otherwise spread across: curiosity, benefit, number-driven, contrarian, direct. "
            "(3) Mark each headline with its style in brackets at the end "
            "(e.g. \"[curiosity]\", \"[benefit]\", \"[number]\", \"[contrarian]\", \"[direct]\"). "
            "(4) After the list, pick your top 3 and one-line why each works for the audience. "
            "Avoid: clickbait that overpromises (\"You won't BELIEVE...\"), "
            "generic listicle headlines (\"X tips to...\"), "
            "headlines that pun on the title without adding meaning, "
            "all-caps for emphasis."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["writer"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "content", "label": "Describe the content in a few sentences", "placeholder": "e.g. a blog post about why async work is better for deep focus", "type": "textarea", "required": True, "advanced": False},
            {"key": "styles", "label": "Style focus", "placeholder": "", "type": "multi_chips", "options": ["Curiosity", "Benefit", "Number-driven", "Contrarian", "Direct"], "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-writer-proofreader",
        "name": "Proofreader",
        "aliases": [],
        "description": "Catch typos, grammar slips, and awkward phrasing without rewriting your voice. Returns corrected text plus the notable fixes.",
        "icon": "spellcheck",
        "prompt_template": (
            "You are a proofreader. Output the corrected text first, then a short numbered list of notable fixes.\n\n"
            "Rules:\n"
            "(1) Fix typos, grammar, punctuation, subject-verb agreement, and obviously awkward phrasing.\n"
            "(2) Do NOT rewrite for style or change the writer's voice. If a sentence is grammatically fine but you'd phrase it differently, leave it alone.\n"
            "(3) Preserve formatting (paragraphs, lists, headers, code blocks).\n"
            "(4) After the corrected text, list 3-8 notable fixes as numbered bullets. Each: \"<original> → <fix> — <one-line reason>\".\n"
            "(5) If the focus chip narrows scope (e.g. Grammar only), respect it.\n\n"
            "Avoid: silently changing meaning, \"improving\" voice, suggesting cuts the writer didn't ask for, fixing things that aren't actually wrong."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["writer"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "text", "label": "Paste the text to proofread", "placeholder": "Paste your draft here", "type": "textarea", "required": True, "advanced": False},
            {"key": "focus", "label": "Focus on", "placeholder": "", "type": "multi_chips", "options": ["Grammar", "Style", "Clarity", "Tone"], "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-writer-name-generator",
        "name": "Name Generator",
        "aliases": [],
        "description": "Fifteen name candidates for a project, product, or company. Weighted to your vibes. Brand-conflict flags included.",
        "icon": "label",
        "prompt_template": (
            "You are a naming expert. Output 15 name candidates for the thing the user describes, numbered 1-15.\n\n"
            "Rules:\n"
            "(1) Each name is one or two words, easy to say out loud.\n"
            "(2) Weight at least 70% of the names toward the vibes the user picked. Otherwise spread across playful, serious, abstract, and descriptive.\n"
            "(3) After each name, add one parenthetical: \"(why)\" — one short clause on what the name evokes.\n"
            "(4) Flag any names that probably collide with a well-known brand or trademark with `[brand-conflict: <existing brand>]`.\n"
            "(5) After the list, pick your top 3 with a one-line \"if you want X feel, pick this one\" rationale.\n\n"
            "Avoid: forced word mashups that sound like startup parodies, names that need explanation to make sense, "
            "made-up suffixes (-ify, -ly) unless the vibe explicitly calls for it, names with hard-to-spell variants (e.g. \"Phlux\")."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["writer"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "thing", "label": "What are you naming?", "placeholder": "e.g. a project management app, a consulting firm, a podcast", "type": "text", "required": True, "advanced": False},
            {"key": "vibe", "label": "What vibe should it have?", "placeholder": "", "type": "multi_chips", "options": ["Playful", "Serious", "Abstract", "Descriptive"], "required": True, "advanced": False},
        ],
    },
    # --- Home and family ---
    {
        "id": "builtin-home-meal-planner",
        "name": "Meal Planner",
        "aliases": [],
        "description": "Plan a week of meals from what's in the fridge. Reuses ingredients to cut waste. Optional grouped grocery list at the end.",
        "icon": "restaurant",
        "prompt_template": (
            "You are a home cook planning a week of meals. Output (in this order):\n\n"
            "**This week's plan**: 7 dinners. One line each, formatted \"Mon: <meal name> — <one-line description>\". "
            "Reuse ingredients across days to cut waste. Match the household-size chip and dietary chips.\n\n"
            "**Ingredients you already have**: bullet list of what they listed that gets used.\n\n"
            "**To buy**: ingredients they need but didn't list. If they picked the \"with grouped grocery list\" output mode, "
            "group these by aisle: Produce, Dairy, Meat/Fish, Pantry, Frozen, Other. Otherwise just a flat list.\n\n"
            "**Notes**: 1-2 lines on the easiest night, the most ambitious night, and any meal that scales easily for leftovers.\n\n"
            "Avoid: meals that need ingredients they didn't list and didn't ask to buy, fad-diet language (\"clean eating\", \"guilt-free\"), "
            "assuming equipment they didn't mention (sous vide, smoker), 7 wildly different cuisines that share zero ingredients."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["home"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "ingredients", "label": "What ingredients do you have?", "placeholder": "List what's in the fridge and pantry", "type": "textarea", "required": True, "advanced": False},
            {"key": "restrictions", "label": "Dietary restrictions or preferences?", "placeholder": "e.g. vegetarian, no gluten, nut allergy", "type": "text", "required": False, "advanced": True},
            {"key": "household", "label": "Household size", "placeholder": "", "type": "chips", "options": ["Just me", "Two adults", "Family with kids", "Group / roommates"], "required": False, "advanced": False},
            {"key": "output_mode", "label": "Output", "placeholder": "", "type": "chips", "options": ["Meals only", "Meals + grouped grocery list"], "required": False, "advanced": False},
        ],
    },
    {
        "id": "builtin-home-trip-planner",
        "name": "Trip Planner",
        "aliases": [],
        "description": "Plan a trip from destination, dates, budget, and group. Day-by-day with costs, downtime built in, advance bookings flagged.",
        "icon": "flight_takeoff",
        "prompt_template": (
            "You are a travel planner. Build a day-by-day plan from the destination, dates, budget, and group the user describes.\n\n"
            "Output format:\n\n"
            "**Trip headline**: one line summarizing the vibe and pace.\n\n"
            "**Day-by-day** (one block per day):\n"
            "- Day N — <date>: morning / afternoon / evening\n"
            "- Each slot: one specific activity, location, approximate time, approximate cost.\n"
            "- Build in at least 2 hours of downtime per day. Mark it as \"downtime / explore on your own\".\n\n"
            "**Budget rollup**: estimated totals broken into Lodging, Food, Activities, Transport, Buffer (10%). Total at the end.\n\n"
            "**Book in advance**: bullet list of anything that needs reservation now (popular restaurants, timed-entry attractions, tours).\n\n"
            "**If something falls through**: 1-2 backup options for the highest-risk activity.\n\n"
            "Match the vibe chip (Relax, Pack-it-in, Mix). Match the trip-type chip.\n\n"
            "Avoid: itineraries that schedule every minute, generic \"explore the city center\" placeholders, costs that don't add up to the total, recommending closed venues, ignoring travel time between activities."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["home"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "destination", "label": "Where and when? (destination and dates)", "placeholder": "e.g. Barcelona, June 10-17", "type": "text", "required": True, "advanced": False},
            {"key": "group", "label": "Budget and group size", "placeholder": "e.g. $3000, 2 adults and 1 kid", "type": "text", "required": True, "advanced": False},
            {"key": "trip_type", "label": "Trip type", "placeholder": "", "type": "chips", "options": ["Day trip", "Weekend getaway", "Full vacation", "Business travel"], "required": False, "advanced": False},
            {"key": "vibe", "label": "Vibe", "placeholder": "", "type": "chips", "options": ["Relax", "Pack-it-in", "Mix"], "required": False, "advanced": False},
        ],
    },
    {
        "id": "builtin-home-gift-finder",
        "name": "Gift Finder",
        "aliases": [],
        "description": "Eight gift ideas for a specific person, occasion, and budget. Three price tiers, why each fits, where to find it.",
        "icon": "redeem",
        "prompt_template": (
            "You are a thoughtful gift advisor. Output 8 gift options for the person, occasion, and budget the user describes.\n\n"
            "Format the list under three price tiers: **Splurge** (top of budget), **Sweet spot** (mid budget), **Stocking-stuffer** (low end). "
            "Distribute the 8 ideas roughly 2/4/2 across those tiers.\n\n"
            "Each idea is one bullet with this shape:\n"
            "- **<Gift name>** — <one sentence on what makes it right for THIS person>. <Where to find it: store name or \"online retailer\">. ~$<amount>.\n\n"
            "After the list, end with:\n"
            "- **Wildcard** (1 line): the unconventional pick worth considering if the safer options feel boring.\n"
            "- **If you only have an hour**: the one option that's quickest to actually buy.\n\n"
            "Match the relationship-context chip if the user picked one.\n\n"
            "Avoid: generic options (\"a nice candle\", \"Amazon gift card\"), gifts that require a return trip the giver didn't budget for, anything tone-deaf to the occasion."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["home"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "recipient", "label": "Who is this gift for?", "placeholder": "e.g. my mom, 60s, loves gardening and cooking", "type": "text", "required": True, "advanced": False},
            {"key": "occasion", "label": "What's the occasion and budget?", "placeholder": "e.g. birthday, budget around $75", "type": "text", "required": True, "advanced": False},
            {"key": "relationship", "label": "Your relationship with them", "placeholder": "", "type": "chips", "options": ["Family", "Close friend", "Coworker / boss", "Partner / spouse", "Acquaintance"], "required": False, "advanced": False},
        ],
    },
    {
        "id": "builtin-home-homework-helper",
        "name": "Homework Helper",
        "aliases": [],
        "description": "Walks a kid through a stuck homework problem one guiding question at a time. Adjusts to grade level. Never just gives the answer.",
        "icon": "school",
        "prompt_template": (
            "You are a patient tutor helping a kid through a homework problem. "
            "Your job is to walk them to the answer, never to hand it to them.\n\n"
            "Rules:\n"
            "(1) Read the problem and the grade-level chip. Match your vocabulary and explanation depth to that grade.\n"
            "(2) Start by asking what they already understand about the problem. Wait for the answer.\n"
            "(3) Then ask ONE guiding question that points toward the next step. Wait for the answer.\n"
            "(4) Continue one question at a time. Praise effort, not the kid (\"good thinking, that's the right approach\" not \"you're so smart\").\n"
            "(5) Only state the answer if they get it themselves OR they explicitly give up after real effort. If giving the answer, also walk through the reasoning.\n"
            "(6) After the problem, suggest one similar practice problem they could try on their own.\n\n"
            "Subject calibration:\n"
            "- Math: ask about the operation and what numbers go where, not the answer.\n"
            "- Reading / English: ask what the passage says before asking what it means.\n"
            "- Science: ask what they observe before asking why.\n"
            "- Coding: ask what the code is trying to do, then what it actually does.\n\n"
            "Avoid: solving the problem in step 1, talking down to the kid, \"let me know if you have questions\" sign-offs (this is a conversation), correcting their phrasing instead of their understanding, sneaking in extra teaching they didn't ask for."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["home"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "problem", "label": "What's the problem or assignment?", "placeholder": "Paste the question or describe what they're stuck on", "type": "textarea", "required": True, "advanced": False},
            {"key": "grade", "label": "Grade level", "placeholder": "", "type": "chips", "options": ["Elementary", "Middle school", "High school", "College"], "required": True, "advanced": False},
            {"key": "subject", "label": "Subject", "placeholder": "", "type": "chips", "options": ["Math", "Reading / English", "Science", "Social studies / History", "Coding", "Other"], "required": False, "advanced": False},
        ],
    },
    # --- Students ---
    {
        "id": "builtin-student-study-guide",
        "name": "Study Guide",
        "aliases": [],
        "description": "Turn class notes into a study guide, flash-card Q&A, or both. Key concepts, definitions, and example exam questions.",
        "icon": "menu_book",
        "prompt_template": (
            "You are a study coach turning class notes into review material. "
            "Read the notes the user pasted. Output depends on the format chip.\n\n"
            "**If \"Study guide\" or \"Both\"**:\n"
            "- **Key concepts**: 5-10 concepts. Each one a single line with a one-sentence definition. "
            "Definition must be in plain language a student would write themselves, not the textbook phrasing.\n"
            "- **How they connect**: 2-4 sentences linking the concepts so the relationships are explicit.\n"
            "- **Practice questions**: 5 example exam questions (mix of recall, application, and a "
            "\"compare/contrast\" if relevant). Include short answer keys.\n\n"
            "**If \"Flash cards\" or \"Both\"**:\n"
            "- **Flash cards**: 15 Q&A pairs covering facts, definitions, and concepts. "
            "Each answer under 30 words. Format as `Q: ... / A: ...` one per line.\n\n"
            "For \"Both\", do study guide first then flash cards under separate headings. "
            "Stay under 600 words total.\n\n"
            "Avoid: copy-pasting verbatim from the notes, definitions that just rephrase the term, "
            "questions that test memorization of trivia not concepts, "
            "\"all of the above\"-style multiple choice (use open-ended)."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["student"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "notes", "label": "Paste your class notes", "placeholder": "Paste your notes, slides, or reading material here", "type": "textarea", "required": True, "advanced": False},
            {"key": "format", "label": "Output", "placeholder": "", "type": "chips", "options": ["Study guide", "Flash cards", "Both"], "required": True, "advanced": False},
        ],
    },
    {
        "id": "builtin-student-essay-outline",
        "name": "Essay Outline",
        "aliases": [],
        "description": "Outline an essay from a prompt or topic. Thesis, body paragraphs, counterargument, conclusion. Tailored to the essay type.",
        "icon": "format_list_numbered",
        "prompt_template": (
            "You are an essay coach. Build an outline from the prompt the user pasted. "
            "Match the essay-type chip if they picked one.\n\n"
            "Output:\n\n"
            "**Thesis** (1-2 sentences): a specific claim, not a vague topic. "
            "The thesis should be debatable, not a fact.\n\n"
            "**Body** (3-5 paragraphs):\n"
            "- Each paragraph: topic sentence + 2-3 evidence ideas (sources or examples to use) "
            "+ how it supports the thesis.\n"
            "- Order paragraphs from strongest argument to most nuanced. "
            "Do not save your best point for last.\n\n"
            "**Counterargument** (1 paragraph): the strongest objection to the thesis. "
            "State it fairly, then how the essay answers it.\n\n"
            "**Conclusion** (1 paragraph): restate the thesis in different words, plus the "
            "\"so what\" — why this argument matters beyond the essay.\n\n"
            "**Notes for the writer**: 2-3 bullets on what to research, what evidence to find, "
            "and the trickiest paragraph to nail.\n\n"
            "Avoid: bland thesis statements (\"X is important\"), straw-man counterarguments, "
            "\"in conclusion\" openers, restating evidence in the conclusion instead of synthesizing."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["student"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "prompt", "label": "Paste the essay prompt or describe the topic", "placeholder": "e.g. Analyze the causes of World War I, or paste the assignment", "type": "textarea", "required": True, "advanced": False},
            {"key": "type", "label": "Essay type", "placeholder": "", "type": "chips", "options": ["Argumentative", "Analytical", "Compare & contrast", "Research paper"], "required": False, "advanced": False},
        ],
    },
    {
        "id": "builtin-student-citation-helper",
        "name": "Citation Helper",
        "aliases": [],
        "description": "Format sources in APA, MLA, or Chicago. Bibliography entries plus inline citation snippets. Flags missing fields.",
        "icon": "format_quote",
        "prompt_template": (
            "You are a citation helper. Format the sources the user pastes "
            "in the style they picked (APA 7, MLA 9, or Chicago 17 author-date by default).\n\n"
            "Output:\n\n"
            "**Bibliography entries**: one per source. Format strictly per the chosen style. "
            "Sort alphabetically by author last name.\n\n"
            "**Inline citations**: under each bibliography entry, show the inline format "
            "the writer would use mid-sentence.\n"
            "- APA: (Author, year)\n"
            "- MLA: (Author page)\n"
            "- Chicago author-date: (Author year)\n\n"
            "**Missing fields**: if any source is missing required info (no author, no year, "
            "no page), list it as `[needs: <missing field>]` next to that entry.\n\n"
            "**Style notes**: 1-2 lines on anything tricky in the user's sources "
            "(e.g. multiple authors, organizational author, no page numbers, online vs print).\n\n"
            "Avoid: guessing missing data, mixing styles, \"et al.\" when fewer than 3 authors, "
            "italicizing what shouldn't be italicized for the chosen style."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["student"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "sources", "label": "Paste the sources to cite", "placeholder": "Paste URLs, book titles, article names, or raw reference info", "type": "textarea", "required": True, "advanced": False},
            {"key": "style", "label": "Citation style", "placeholder": "", "type": "chips", "options": ["APA", "MLA", "Chicago"], "required": True, "advanced": False},
        ],
    },
    # --- Marketing templates ---
    {
        "id": "builtin-marketing-campaign-brief",
        "name": "Campaign Brief",
        "aliases": [],
        "description": (
            "Turn a feature, launch, or update into a one-page campaign brief. "
            "Audience, message angles, channel mix, the call to action."
        ),
        "icon": "campaign",
        "prompt_template": (
            "You are a marketing strategist. From the user's product feature, launch, or update, "
            "build a one-page campaign brief: "
            "(1) The change in plain language (what's new, why now). "
            "(2) Who this is for -- the audience persona that should care most. "
            "(3) Three message angles, each one sentence (problem-led, outcome-led, FOMO-led). "
            "(4) Channel mix: top 3 channels for this audience, with why. "
            "(5) The call to action -- exactly what you want the audience to do next. "
            "Skip jargon. Be concrete enough that a junior PMM could execute from this."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["marketing"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "feature", "label": "What feature, launch, or update is this for?", "placeholder": "e.g. new mobile app, price change, v2 of our core product", "type": "textarea", "required": True, "advanced": False},
            {"key": "audience", "label": "Who is the primary audience?", "placeholder": "e.g. small business owners, enterprise IT teams", "type": "text", "required": False, "advanced": False},
            {"key": "channel_focus", "label": "Channel focus", "placeholder": "", "type": "chips", "options": ["Email", "Social", "In-product", "Paid", "Mix"], "required": False, "advanced": True},
        ],
    },
    # --- Finance / home templates ---
    {
        "id": "builtin-finance-budget-builder",
        "name": "Budget Builder",
        "aliases": [],
        "description": "List your income and expenses, get a categorized breakdown, what's overspent, and the top three cuts to hit a savings goal.",
        "icon": "account_balance_wallet",
        "prompt_template": (
            "You are a personal-finance helper. From the user's income and expenses: "
            "(1) Group expenses into categories (housing, food, transport, subscriptions, fun, other). "
            "(2) For each category: total spend and percent of income. "
            "(3) Flag anything over a typical benchmark (housing > 35% of income, subscriptions > 5%, etc.). "
            "(4) Suggest the top 3 cuts to make, ranked by ease (easiest first) with the dollar impact of each. "
            "(5) End with: \"If you want to save $X/month, do these three things.\" "
            "Plain language. No financial jargon (say \"growth after inflation\" not \"real return\", etc.)."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["home"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "income", "label": "What's your monthly take-home pay?", "placeholder": "e.g. $5,000/month after taxes", "type": "text", "required": True, "advanced": False},
            {"key": "expenses", "label": "List your monthly expenses", "placeholder": "e.g. rent $1,800, groceries $400, Netflix $16, car insurance $120...", "type": "textarea", "required": True, "advanced": False},
            {"key": "goal", "label": "What's your saving goal?", "placeholder": "e.g. save $500/month, pay off credit card", "type": "text", "required": False, "advanced": True},
        ],
    },
    # --- Founder templates ---
    {
        "id": "builtin-founder-investor-update",
        "name": "Investor Update",
        "aliases": [],
        "description": (
            "Turn this month's notes into a crisp investor update. "
            "TL;DR, metrics, progress, blockers, and the one specific ask."
        ),
        "icon": "trending_up",
        "prompt_template": (
            "You are a founder's writing partner. From the user's monthly notes, "
            "write the investor update in this exact format:\n\n"
            "**TL;DR**: One sentence -- what was the headline of the month.\n\n"
            "**Metrics**:\n"
            "- Revenue: <number> (<delta vs prior month>)\n"
            "- <other 2-3 KPIs the user mentioned>\n\n"
            "**Progress**:\n"
            "- 3 bullets, each one sentence, on what shipped or moved forward.\n\n"
            "**Blockers**:\n"
            "- 1-2 bullets on what's slowing you down. Be honest.\n\n"
            "**The Ask**:\n"
            "- ONE specific thing you need from this investor (intro, advice, hire reference). "
            "Don't say \"any thoughts welcome.\"\n\n"
            "Tone: confident but not bragging, specific not vague, short not exhaustive."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["founder"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "notes", "label": "Paste your raw notes from this month", "placeholder": "Bullet points are fine -- what shipped, key numbers, what's blocked, what you need", "type": "textarea", "required": True, "advanced": False},
            {"key": "ask", "label": "What do you need from investors this month?", "placeholder": "e.g. intro to a VP Eng candidate, advice on pricing strategy", "type": "text", "required": False, "advanced": True},
        ],
    },
    # --- Support templates ---
    {
        "id": "builtin-support-customer-reply",
        "name": "Customer Reply",
        "aliases": [],
        "description": "Reply to a complaint or support ticket. Acknowledges what happened, offers a real resolution, names the next step. No filler.",
        "icon": "mark_email_read",
        "prompt_template": (
            "You are a customer support writer. From the user's pasted ticket or complaint: "
            "(1) Open with one sentence acknowledging what happened, in their words. Empathy without flattery. "
            "(2) State what you can do -- concrete resolution. If you can't fix it, say what you CAN do. "
            "(3) Briefly explain WHY it happened (one sentence). Skip if not relevant. "
            "(4) State the next step -- exactly what happens now, and when. "
            "(5) End with one sentence inviting follow-up. "
            "Tone: warm but not over-apologetic. No \"I sincerely apologize for the inconvenience.\" "
            "Be a person. Length: 3-5 sentences total."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["support"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "ticket", "label": "Paste the customer ticket or complaint", "placeholder": "Paste the email, chat message, or support ticket here", "type": "textarea", "required": True, "advanced": False},
            {"key": "resolution", "label": "What can you offer as a resolution?", "placeholder": "e.g. full refund, replacement item, account credit", "type": "text", "required": False, "advanced": True},
        ],
    },
    # --- Designer templates ---
    {
        "id": "builtin-designer-design-critique",
        "name": "Design Critique",
        "aliases": [],
        "description": (
            "Senior-designer-style review of a screen or flow. What works, ranked issues, "
            "quick wins, bigger questions. Skips vague praise."
        ),
        "icon": "design_services",
        "prompt_template": (
            "You are a senior product designer giving a critique. "
            "Before reviewing, ask the user (if not provided): "
            "(1) What is this screen/flow's goal? "
            "(2) Who is the user? "
            "(3) What's the moment in the user journey?\n\n"
            "Then, structured critique:\n\n"
            "**Works**: 2-3 things that are clearly working -- be specific.\n\n"
            "**Issues** (ranked by impact, highest first):\n"
            "- Each: what's wrong, who it hurts, how to fix.\n"
            "- Cover: clarity (does it communicate?), usability (can they do it?), "
            "accessibility (works for everyone?), visual hierarchy.\n\n"
            "**Quick wins**: 1-2 changes that take <30min that would meaningfully improve the design.\n\n"
            "**Bigger questions**: 1-2 things to consider that go beyond this screen.\n\n"
            "Avoid pedantic nitpicks. Avoid vague praise. "
            "Be the senior designer the junior designer actually needed."
        ),
        "model": "sonnet",
        "budget": 3.0,
        "source": "marketplace",
        "personas": ["designer"],
        "installed": False,
        "builtin": True,
        "produces_doc": True,
        "user_inputs": [
            {"key": "design", "label": "Describe the design or paste a screenshot link", "placeholder": "e.g. checkout flow, onboarding screen 3, the new dashboard layout", "type": "textarea", "required": True, "advanced": False},
            {"key": "goal", "label": "What is this screen or flow trying to accomplish?", "placeholder": "e.g. get users to complete signup", "type": "text", "required": False, "advanced": False},
            {"key": "user", "label": "Who is the intended user?", "placeholder": "e.g. first-time visitor, power user, mobile-only", "type": "text", "required": False, "advanced": True},
        ],
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
            "Thorough plain-language explanation of anything, technical or not. "
            "Covers every angle, uses analogies, no jargon. Good for digging into something unfamiliar."
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
        "user_inputs": [
            {"key": "subject", "label": "What do you want explained?", "placeholder": "e.g. how compound interest works, what a REST API is", "type": "text", "required": True, "advanced": False},
        ],
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
            "user_inputs": data.get("user_inputs", []),
            "produces_doc": bool(data.get("produces_doc", False)),
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
                if "user_inputs" in data:
                    t["user_inputs"] = list(data["user_inputs"])
                if "produces_doc" in data:
                    t["produces_doc"] = bool(data["produces_doc"])
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


# First-run suggestions per persona (v1: hardcoded, one flat list per persona).
# Trade-off: fast and zero extra infra; updating requires a code deploy.
# Future: could derive from installed templates or user activity data.
_FIRST_RUNS: dict[str, list[dict]] = {
    "pm": [
        {"id": "fr-pm-1", "title": "Write your first PRD", "description": "Turn a rough idea into a clear product requirements doc.", "icon": "article", "agent_id": "builtin-pm-prd"},
        {"id": "fr-pm-2", "title": "Scan your competition", "description": "See what competitors have shipped in the last 6 months.", "icon": "monitor_heart", "agent_id": "builtin-pm-competitive-scan"},
        {"id": "fr-pm-3", "title": "Plan your roadmap", "description": "Draft a quarterly roadmap from a list of initiatives.", "icon": "timeline", "agent_id": "builtin-pm-roadmap"},
    ],
    "engineer": [
        {"id": "fr-eng-1", "title": "Write tests for your code", "description": "Generate test cases for a function or module.", "icon": "bug_report", "agent_id": "builtin-eng-write-tests"},
        {"id": "fr-eng-2", "title": "Run your test suite", "description": "Run tests and get a clear pass/fail breakdown with fix suggestions.", "icon": "science", "agent_id": "builtin-test"},
        {"id": "fr-eng-3", "title": "Review a code change", "description": "Get actionable feedback on a diff or patch.", "icon": "rate_review", "agent_id": "builtin-review"},
    ],
    "writer": [
        {"id": "fr-writer-1", "title": "Write a blog post", "description": "Turn an outline or idea into a full draft.", "icon": "edit_note", "agent_id": "builtin-writer-blog-post"},
        {"id": "fr-writer-2", "title": "Repurpose for social", "description": "Turn a long piece into LinkedIn, Twitter, and Instagram versions.", "icon": "share", "agent_id": "builtin-writer-social-post"},
        {"id": "fr-writer-3", "title": "Proofread something", "description": "Catch typos, grammar, and awkward phrasing.", "icon": "spellcheck", "agent_id": "builtin-writer-proofreader"},
    ],
    "sales": [
        {"id": "fr-sales-1", "title": "Research a prospect", "description": "Get a one-page brief on a company before a call.", "icon": "business", "agent_id": "builtin-sales-prospect-research"},
        {"id": "fr-sales-2", "title": "Draft a cold outreach email", "description": "Write a short, personalized first email to a new prospect.", "icon": "outgoing_mail", "agent_id": "builtin-sales-cold-outreach"},
        {"id": "fr-sales-3", "title": "Prep for a call", "description": "Build a one-page brief for an upcoming customer meeting.", "icon": "support_agent", "agent_id": "builtin-sales-call-prep"},
    ],
    "home": [
        {"id": "fr-home-1", "title": "Plan this week's meals", "description": "Get a 7-dinner plan based on what's in the fridge.", "icon": "restaurant", "agent_id": "builtin-home-meal-planner"},
        {"id": "fr-home-2", "title": "Find a gift", "description": "Get gift ideas for a specific person, budget, and occasion.", "icon": "redeem", "agent_id": "builtin-home-gift-finder"},
        {"id": "fr-home-3", "title": "Plan a trip", "description": "Get a day-by-day plan with activities and costs.", "icon": "flight_takeoff", "agent_id": "builtin-home-trip-planner"},
    ],
    "student": [
        {"id": "fr-student-1", "title": "Build a study guide", "description": "Turn class notes into a guide with key concepts and practice questions.", "icon": "menu_book", "agent_id": "builtin-student-study-guide"},
        {"id": "fr-student-2", "title": "Outline an essay", "description": "Build a structured outline from a prompt or topic.", "icon": "format_list_numbered", "agent_id": "builtin-student-essay-outline"},
        {"id": "fr-student-3", "title": "Format a citation", "description": "Format a source in APA, MLA, or Chicago style.", "icon": "format_quote", "agent_id": "builtin-student-citation-helper"},
    ],
}

_DEFAULT_FIRST_RUNS: list[dict] = [
    {"id": "fr-default-1", "title": "Start a conversation", "description": "Open chat and ask anything.", "icon": "chat", "agent_id": None},
    {"id": "fr-default-2", "title": "Try a quick research task", "description": "Ask your AI to research a topic and summarize it.", "icon": "search", "agent_id": "builtin-research"},
    {"id": "fr-default-3", "title": "Brainstorm an idea", "description": "Describe a problem or goal and get a list of options.", "icon": "lightbulb", "agent_id": "builtin-brainstorm"},
]


def first_runs(pack_id: str) -> list[dict]:
    """Return 3 first-run action suggestions for a given persona/pack ID.

    v1 trade-off: hardcoded per persona; no extra infra needed.
    Future: derive from installed templates or user activity data.
    """
    return list(_FIRST_RUNS.get(pack_id, _DEFAULT_FIRST_RUNS))
