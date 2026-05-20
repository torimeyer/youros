"""Tests for plan-mode toggle in mychat (→1533).

Covers:
- is_trivial() helper: short Q&A vs action-heavy requests
- PlanModeStore: store/consume lifecycle, TTL expiry
- plan_mode_store module import
"""

import pytest

# ---- is_trivial -----------------------------------------------------------

def test_trivial_short_question():
    from services.plan_mode_store import is_trivial
    assert is_trivial("what is 2+2") is True


def test_trivial_one_word():
    from services.plan_mode_store import is_trivial
    assert is_trivial("hi") is True


def test_trivial_show_me_phrasing():
    from services.plan_mode_store import is_trivial
    assert is_trivial("show me the tasks page") is True


def test_non_trivial_implement():
    from services.plan_mode_store import is_trivial
    assert is_trivial("implement a new auth flow for the login page") is False


def test_non_trivial_fix():
    from services.plan_mode_store import is_trivial
    assert is_trivial("fix the bug in user_memory_store.py where memories aren't saved") is False


def test_non_trivial_build():
    from services.plan_mode_store import is_trivial
    assert is_trivial("build me a chart library that renders bar charts using d3") is False


def test_non_trivial_refactor():
    from services.plan_mode_store import is_trivial
    assert is_trivial("refactor user_memory_store to use Pydantic models") is False


def test_non_trivial_create():
    from services.plan_mode_store import is_trivial
    assert is_trivial("create a new API endpoint for getting user stats") is False


def test_trivial_slash_command():
    from services.plan_mode_store import is_trivial
    assert is_trivial("/help") is True


def test_trivial_short_with_action_word_still_trivial_if_short():
    from services.plan_mode_store import is_trivial
    # "fix" alone is too short to be non-trivial without a target
    assert is_trivial("fix") is True


def test_non_trivial_long_message_without_keywords():
    from services.plan_mode_store import is_trivial
    # Messages longer than 200 chars are non-trivial even without action keywords
    long_msg = "a" * 201
    assert is_trivial(long_msg) is False


# ---- extract_plan ---------------------------------------------------------

def test_extract_plan_from_tags():
    from services.plan_mode_store import extract_plan
    text = "Sure! <plan>Step 1: read the file\nStep 2: edit it</plan> Done."
    assert extract_plan(text) == "Step 1: read the file\nStep 2: edit it"


def test_extract_plan_none_when_no_tag():
    from services.plan_mode_store import extract_plan
    assert extract_plan("I will just do the thing") is None


def test_extract_plan_strips_whitespace():
    from services.plan_mode_store import extract_plan
    text = "<plan>  \n  do the thing  \n  </plan>"
    assert extract_plan(text) == "do the thing"


# ---- PlanModeStore --------------------------------------------------------

def test_store_and_consume():
    from services.plan_mode_store import PlanModeStore
    store = PlanModeStore()
    plan_id = "abc-123"
    messages = [{"role": "user", "content": "build X"}]
    store.store(plan_id=plan_id, messages=messages, tab_id="tab1", model="claude")
    result = store.consume(plan_id)
    assert result is not None
    assert result["messages"] == messages
    assert result["tab_id"] == "tab1"
    assert result["model"] == "claude"


def test_consume_returns_none_for_unknown():
    from services.plan_mode_store import PlanModeStore
    store = PlanModeStore()
    assert store.consume("no-such-id") is None


def test_consume_removes_entry():
    from services.plan_mode_store import PlanModeStore
    store = PlanModeStore()
    store.store("x", [], "tab", "claude")
    store.consume("x")
    assert store.consume("x") is None


def test_module_level_singleton():
    from services.plan_mode_store import plan_mode_store, PlanModeStore
    assert isinstance(plan_mode_store, PlanModeStore)
