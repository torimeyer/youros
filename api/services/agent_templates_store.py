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
        "description": "Write unit tests covering the happy path, edge cases, and error conditions for the code you paste.",
        "icon": "bug_report",
        "prompt_template": (
            "You are a test engineer. For the function or module the user pastes: "
            "(1) Identify what needs to be tested: happy path, edge cases, error paths, and boundary conditions. "
            "(2) Write tests using the project's existing test framework (infer from imports; default to pytest for Python, Jest for JS). "
            "(3) Name each test to describe what it asserts (e.g. test_returns_empty_list_when_input_is_none). "
            "(4) After the tests, add a 3-line comment block listing what you covered and what you deliberately skipped. "
            "Output: test code first, then the coverage comment."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["engineer"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "code", "label": "Paste the function or module to test", "placeholder": "Paste the code you want tests written for", "type": "textarea", "required": True, "advanced": False},
        ],
    },
    {
        "id": "builtin-eng-debug-helper",
        "name": "Interactive Debug",
        "aliases": [],
        "description": "Walk through a tricky bug step by step — the agent asks one question at a time to narrow down the cause, instead of guessing from a one-shot description.",
        "icon": "bug_report",
        "prompt_template": (
            "You are an interactive debugger. Do NOT give an answer immediately. "
            "(1) Read the error log or stack trace the user pastes. "
            "(2) Ask ONE clarifying question that would most narrow the root cause. "
            "(3) Wait for the answer, then ask the next question or state the root cause. "
            "(4) Once identified, state the exact file/line and the minimal fix. "
            "Diagnose by conversation, not by guessing."
        ),
        "model": "sonnet",
        "budget": 2.0,
        "source": "marketplace",
        "personas": ["engineer"],
        "installed": False,
        "builtin": True,
        "user_inputs": [
            {"key": "error", "label": "Paste the error log or stack trace", "placeholder": "Paste the full error output here", "type": "textarea", "required": True, "advanced": False},
        ],
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
        "description": "Dig into a company and decision maker before an outreach call.",
        "icon": "business",
        "prompt_template": (
            "You are a sales researcher. For the company and contact the user "
            "provides, produce a one-page brief: recent news, likely pain points, "
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
        "user_inputs": [
            {"key": "company", "label": "Company name", "placeholder": "e.g. Acme Corp", "type": "text", "required": True, "advanced": False},
            {"key": "contact", "label": "Contact name and role", "placeholder": "e.g. Jane Smith, VP of Engineering", "type": "text", "required": True, "advanced": False},
        ],
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
        "user_inputs": [
            {"key": "prospect", "label": "Who is the prospect? (role and company)", "placeholder": "e.g. Head of Engineering at a 50-person fintech startup", "type": "text", "required": True, "advanced": False},
            {"key": "value_prop", "label": "What's your value prop for them?", "placeholder": "e.g. We cut deployment time by 80% for teams like theirs", "type": "text", "required": True, "advanced": False},
            {"key": "tone", "label": "Tone", "placeholder": "", "type": "chips", "options": ["Warm", "Professional", "Direct"], "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-sales-call-prep",
        "name": "Call Prep",
        "aliases": [],
        "description": "Build a one-page brief for an upcoming customer call, with fresh research on the company and the people you're meeting.",
        "icon": "support_agent",
        "prompt_template": (
            "You are a sales coach. Before building the brief, search for any recent news about "
            "the company or attendees (last 30 days). Then produce a one-page call brief: "
            "(1) Who's on the call and their roles. "
            "(2) Goal for this call and what success looks like. "
            "(3) Three discovery questions tailored to their recent activity or context. "
            "(4) Likely pushback and how to respond. "
            "(5) The exact next step you will ask for by end of call. "
            "Flag anything you could not find (\"unknown — ask directly\")."
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
        ],
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
        "user_inputs": [
            {"key": "notes", "label": "Paste your call notes", "placeholder": "Bullet points are fine", "type": "textarea", "required": True, "advanced": False},
            {"key": "next_step", "label": "What's the agreed next step?", "placeholder": "e.g. send proposal by Friday", "type": "text", "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-sales-objection-handling",
        "name": "Objection Handling",
        "aliases": [],
        "description": "Help you prep answers to common customer objections.",
        "icon": "question_answer",
        "prompt_template": (
            "You are a sales coach. For the product and objection the user "
            "provides, propose 3 response options ranked by tone (empathetic, "
            "direct, curious). Each response is one paragraph. Suggest the "
            "discovery question you would ask instead of answering."
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
        ],
    },
    # --- Writers and creators ---
    {
        "id": "builtin-writer-blog-post",
        "name": "Blog Post",
        "aliases": [],
        "description": "Write a draft blog post from an outline or rough idea.",
        "icon": "edit_note",
        "prompt_template": (
            "You are a blog writer. From the user's topic, produce "
            "a draft post: strong opening, 3-5 body sections with subheads, a "
            "short closing. Plain, warm, specific. Match the length the user "
            "requests, or default to under 700 words."
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
        ],
    },
    {
        "id": "builtin-writer-social-post",
        "name": "Social Post",
        "aliases": [],
        "description": "Turn a long post into short, punchy social versions.",
        "icon": "share",
        "prompt_template": (
            "You are a social copywriter. Turn the long input into versions "
            "for the platforms the user selects. LinkedIn: under 200 words. "
            "Twitter/X: a 5-tweet thread. Instagram: a short caption. Keep "
            "the voice specific and human. Only produce the selected platforms."
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
        ],
    },
    {
        "id": "builtin-writer-headlines",
        "name": "Headline Generator",
        "aliases": [],
        "description": "Write 10 headline options for the same piece of content.",
        "icon": "title",
        "prompt_template": (
            "You are a headline writer. For the content the user describes, "
            "produce 10 headline options. If the user selected specific styles, "
            "weight toward those; otherwise cover a mix of: curiosity, "
            "benefit, number-driven, contrarian, and direct. No clickbait."
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
        "user_inputs": [
            {"key": "text", "label": "Paste the text to proofread", "placeholder": "Paste your draft here", "type": "textarea", "required": True, "advanced": False},
            {"key": "focus", "label": "Focus on", "placeholder": "", "type": "multi_chips", "options": ["Grammar", "Style", "Clarity", "Tone"], "required": False, "advanced": True},
        ],
    },
    {
        "id": "builtin-writer-name-generator",
        "name": "Name Generator",
        "aliases": [],
        "description": "Come up with names for projects, features, or products.",
        "icon": "label",
        "prompt_template": (
            "You are a naming expert. For the thing the user describes, "
            "produce 15 name candidates. Weight toward the vibes the user "
            "selected; if none selected, spread across playful, serious, "
            "abstract, and descriptive. One line each. Flag any names that "
            "might conflict with a well-known brand."
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
        "user_inputs": [
            {"key": "ingredients", "label": "What ingredients do you have?", "placeholder": "List what's in the fridge and pantry", "type": "textarea", "required": True, "advanced": False},
            {"key": "restrictions", "label": "Dietary restrictions or preferences?", "placeholder": "e.g. vegetarian, no gluten, nut allergy", "type": "text", "required": False, "advanced": True},
        ],
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
        "user_inputs": [
            {"key": "destination", "label": "Where and when? (destination and dates)", "placeholder": "e.g. Barcelona, June 10-17", "type": "text", "required": True, "advanced": False},
            {"key": "group", "label": "Budget and group size", "placeholder": "e.g. $3000, 2 adults and 1 kid", "type": "text", "required": True, "advanced": False},
            {"key": "trip_type", "label": "Trip type", "placeholder": "", "type": "chips", "options": ["Day trip", "Weekend getaway", "Full vacation", "Business travel"], "required": False, "advanced": False},
        ],
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
        "user_inputs": [
            {"key": "recipient", "label": "Who is this gift for?", "placeholder": "e.g. my mom, 60s, loves gardening and cooking", "type": "text", "required": True, "advanced": False},
            {"key": "occasion", "label": "What's the occasion and budget?", "placeholder": "e.g. birthday, budget around $75", "type": "text", "required": True, "advanced": False},
        ],
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
            "giving the answer. Adjust to the grade level the user provides."
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
        ],
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
        "user_inputs": [
            {"key": "notes", "label": "Paste your class notes", "placeholder": "Paste your notes, slides, or reading material here", "type": "textarea", "required": True, "advanced": False},
        ],
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
        "user_inputs": [
            {"key": "prompt", "label": "Paste the essay prompt or describe the topic", "placeholder": "e.g. Analyze the causes of World War I, or paste the assignment", "type": "textarea", "required": True, "advanced": False},
            {"key": "type", "label": "Essay type", "placeholder": "", "type": "chips", "options": ["Argumentative", "Analytical", "Compare & contrast", "Research paper"], "required": False, "advanced": False},
        ],
    },
    {
        "id": "builtin-student-citation-helper",
        "name": "Citation Helper",
        "aliases": [],
        "description": "Format sources in APA, MLA, or Chicago style.",
        "icon": "format_quote",
        "prompt_template": (
            "You are a citation helper. Format the sources the user pastes "
            "in the style they select (APA, MLA, or Chicago). Return one "
            "formatted citation per line and flag any sources with missing "
            "fields."
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
            "Turn a product feature or launch into a campaign brief -- messaging, "
            "target audience, channels, and call to action."
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
        ],
    },
    # --- Finance / home templates ---
    {
        "id": "builtin-finance-budget-builder",
        "name": "Budget Builder",
        "aliases": [],
        "description": (
            "Take a list of income and expenses and show where the money goes, "
            "what's over budget, and where to cut."
        ),
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
            "Turn raw notes from the past month into a crisp investor update -- "
            "metrics, progress, blockers, the one ask."
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
        "description": (
            "Draft a clear, empathetic response to a customer complaint or support ticket -- "
            "with a resolution, an explanation, and a next step."
        ),
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
            "Review a design for usability, clarity, and accessibility -- "
            "with specific, prioritized feedback."
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
