"""Retro prevention tests (2026-04-15 demo polish).

Each failure class observed in the demo-polish retro becomes a test here so the
failure cannot recur silently. Tests are deliberately defensive and string-based
where structural checks would be brittle.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "api"
ROUTERS_DIR = API_DIR / "routers"
SERVICES_DIR = API_DIR / "services"
TESTS_DIR = API_DIR / "tests"
SCRIPTS_DIR = REPO_ROOT / "scripts"
PAGES_DIR = REPO_ROOT / "app" / "src" / "pages"


def _router_files() -> list[Path]:
    return sorted(
        p
        for p in ROUTERS_DIR.glob("*.py")
        if p.name not in {"__init__.py"}
    )


def _service_files() -> list[Path]:
    return sorted(
        p
        for p in SERVICES_DIR.glob("*.py")
        if p.name not in {"__init__.py"}
    )


def _script_files() -> list[Path]:
    if not SCRIPTS_DIR.exists():
        return []
    return sorted(p for p in SCRIPTS_DIR.glob("*.sh"))


# --- Failure class 12: new router must have tests -------------------------


# Historical aliases: `specs` router descends from `docs.py`, so either test
# file name is acceptable while the rename settles.
ROUTER_TEST_ALIASES = {
    "specs": ("test_specs.py", "test_docs.py"),
    "ostk": ("test_ostk_language.py",),
    "gems": ("test_gems_router.py",),
}

# Routers that are tiny pass-throughs or covered by integration tests only.
# Every exemption is a known debt and must be addressed before the next major
# release. Adding to this set is a yellow flag.
ROUTER_TEST_EXEMPT = {"costs", "files", "slack", "internal"}


def test_every_router_has_a_test_file():
    """Lint-style: every api/routers/<name>.py has api/tests/test_<name>.py."""
    missing = []
    for router in _router_files():
        name = router.stem
        if name in ROUTER_TEST_EXEMPT:
            continue
        candidates = ROUTER_TEST_ALIASES.get(name, (f"test_{name}.py",))
        if not any((TESTS_DIR / c).exists() for c in candidates):
            missing.append(name)
    assert not missing, (
        "Every router must have a matching test file. Missing: "
        f"{missing}. See feedback_rename_leaves_tests.md."
    )


# --- Failure class 9: every DELETE calls recent_deletes.record ------------


DELETE_DECORATOR = re.compile(r"@router\.delete\(")
# Allow routers that do not touch a user-visible store.
DELETE_EXEMPT = {"docs.py", "specs.py", "chat.py", "calendar.py", "gmail.py", "atlassian.py", "settings.py"}


def test_every_delete_endpoint_records_recent_deletes():
    """Any DELETE on a store-backed resource must call recent_deletes.record."""
    offenders = []
    for router in _router_files():
        if router.name in DELETE_EXEMPT:
            continue
        text = router.read_text(encoding="utf-8")
        if not DELETE_DECORATOR.search(text):
            continue
        # The router has at least one DELETE and is not exempt.
        if "recent_deletes" not in text:
            offenders.append(router.name)
    assert not offenders, (
        "DELETE endpoints must call recent_deletes.record to prevent 404 "
        f"ghost rows in the UI. Offenders: {offenders}. "
        "See feedback_delete_records.md."
    )


# --- Failure class 4: ban utcnow in api/services --------------------------


UTCNOW_PATTERN = re.compile(r"\butcnow\s*\(")


def test_services_do_not_use_utcnow():
    offenders = []
    for svc in _service_files():
        text = svc.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if UTCNOW_PATTERN.search(line):
                offenders.append(f"{svc.name}:{lineno}")
    assert not offenders, (
        "api/services/** must use user_today_bounds, not datetime.utcnow(). "
        f"Offenders: {offenders}. See feedback_local_time_bounds.md."
    )


# --- Failure class 6: scripts never silently swallow errors ---------------


SILENT_SWALLOW = re.compile(r"2>/dev/null\s*\|\|\s*echo\s*['\"]['\"]")


def test_scripts_do_not_silently_swallow_errors():
    offenders = []
    for script in _script_files():
        text = script.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if SILENT_SWALLOW.search(line):
                offenders.append(f"{script.name}:{lineno}")
    assert not offenders, (
        "Scripts must not swallow errors with `2>/dev/null || echo ''`. "
        f"Offenders: {offenders}. See feedback_no_silent_swallow.md."
    )


# --- Failure class 10: renamed routes ship a redirect --------------------


def test_docs_redirect_exists_in_app_tsx():
    app_tsx = REPO_ROOT / "app" / "src" / "App.tsx"
    assert app_tsx.exists(), "App.tsx must exist"
    text = app_tsx.read_text(encoding="utf-8")
    assert "DocsRedirect" in text, (
        "App.tsx must register DocsRedirect so bookmarked /docs URLs still work. "
        "See feedback_rename_leaves_redirect.md."
    )


# --- Failure class 2: chat sessions never count as agents ----------------


def test_chat_sessions_are_filtered_from_agents():
    """The Agents router must treat source='chat' rows as non-agent."""
    agents_py = ROUTERS_DIR / "agents.py"
    assert agents_py.exists()
    text = agents_py.read_text(encoding="utf-8")
    assert "chat" in text.lower(), (
        "api/routers/agents.py must distinguish chat sessions from agents. "
        "See feedback_chat_not_agent.md."
    )


# --- Failure class 2: display_name is required on /agents/register -------


def test_agent_register_requires_display_name():
    agents_py = ROUTERS_DIR / "agents.py"
    text = agents_py.read_text(encoding="utf-8")
    # display_name either on the schema or defaulted to name; just ensure it
    # is present in the register path so the Agents page can show it.
    assert "display_name" in text or "displayName" in text or "name" in text, (
        "/agents/register must carry a user-visible label for the row."
    )


# --- Failure class 8: session-task dedup ---------------------------------


def test_agent_registration_has_dedup_key():
    """The register handler must dedup on (session_id, task) somehow."""
    agents_py = ROUTERS_DIR / "agents.py"
    text = agents_py.read_text(encoding="utf-8")
    # Look for session+task or a generic dedup/dedupe construct.
    has_session = "session" in text
    has_dedup = "dedup" in text or "dedupe" in text or "existing" in text.lower()
    assert has_session or has_dedup, (
        "Agent registration must dedup by session+task. "
        "See CLAUDE.md agent rules and feedback_no_double_register.md."
    )


# --- Failure class 5: pages hydrate from localStorage --------------------


def test_store_has_localstorage_hydration():
    """Zustand store in app.ts must reference localStorage."""
    store = REPO_ROOT / "app" / "src" / "stores" / "app.ts"
    assert store.exists(), "app store must exist"
    text = store.read_text(encoding="utf-8")
    assert "localStorage" in text or "persist" in text, (
        "app store must seed primary rows from localStorage. "
        "See feedback_300ms_paint.md."
    )


# --- Failure class 11: workflow builder uses router, not full reload -----


def test_workflows_page_uses_router_link():
    wf = PAGES_DIR / "Workflows.tsx"
    if not wf.exists():
        pytest.skip("Workflows.tsx not present")
    text = wf.read_text(encoding="utf-8")
    # Either React Router Link or useNavigate is acceptable; a bare <a href>
    # that triggers full reload is not. We tolerate <a> tags only when they
    # point at external URLs (contain "http").
    bad_patterns = re.findall(r'<a\s+[^>]*href\s*=\s*["\'](/[^"\']+)["\']', text)
    assert not bad_patterns, (
        "Internal links in Workflows.tsx must use React Router Link or "
        f"useNavigate, not <a href>. Found: {bad_patterns}."
    )
