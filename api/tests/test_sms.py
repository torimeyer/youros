"""Tests for the SMS command dispatch layer.

Covers parse_sms_command (pure function) and dispatch_sms_command (async,
uses a lightweight ostk stub instead of the real service).
"""
from __future__ import annotations

import pytest

from services.sms import dispatch_sms_command, parse_sms_command


# ---------------------------------------------------------------------------
# parse_sms_command — pure, no I/O
# ---------------------------------------------------------------------------


def test_parse_add_task_basic():
    cmd = parse_sms_command("add task: review the auth flow")
    assert cmd == {"kind": "add_task", "title": "review the auth flow"}


def test_parse_add_task_case_insensitive():
    cmd = parse_sms_command("ADD TASK: fix login bug")
    assert cmd is not None
    assert cmd["kind"] == "add_task"
    assert cmd["title"] == "fix login bug"


def test_parse_add_task_extra_spaces():
    cmd = parse_sms_command("add task :  update docs  ")
    assert cmd is not None
    assert cmd["title"] == "update docs"


def test_parse_add_task_empty_title_not_matched():
    assert parse_sms_command("add task:") is None
    assert parse_sms_command("add task:   ") is None


def test_parse_list_tasks_variants():
    for text in ("list tasks", "List Tasks", "my tasks", "My Tasks",
                 "what are my tasks", "tasks", "task"):
        cmd = parse_sms_command(text)
        assert cmd == {"kind": "list_tasks"}, f"failed for: {text!r}"


def test_parse_unknown_returns_none():
    assert parse_sms_command("hello world") is None
    assert parse_sms_command("spawn agent: do stuff") is None
    assert parse_sms_command("") is None


# ---------------------------------------------------------------------------
# dispatch_sms_command — async, uses an ostk stub
# ---------------------------------------------------------------------------


class _FakeOstk:
    def __init__(self, tasks=None):
        self.added = []
        self._tasks = tasks or []

    async def add_task(self, title, **_):
        self.added.append(title)

    async def list_tasks(self, status=None):
        return self._tasks


@pytest.mark.asyncio
async def test_dispatch_add_task_calls_ostk_and_replies():
    ostk = _FakeOstk()
    reply = await dispatch_sms_command("add task: review the auth flow", ostk)
    assert reply == "Added: review the auth flow"
    assert ostk.added == ["review the auth flow"]


@pytest.mark.asyncio
async def test_dispatch_add_task_case_insensitive():
    ostk = _FakeOstk()
    reply = await dispatch_sms_command("ADD TASK: fix login", ostk)
    assert reply == "Added: fix login"


@pytest.mark.asyncio
async def test_dispatch_list_tasks_formats_numbered_list():
    tasks = [
        {"id": "→1", "title": "Fix login bug"},
        {"id": "→2", "title": "Update docs"},
    ]
    ostk = _FakeOstk(tasks=tasks)
    reply = await dispatch_sms_command("list tasks", ostk)
    assert reply == "1. Fix login bug (→1)\n2. Update docs (→2)"


@pytest.mark.asyncio
async def test_dispatch_list_tasks_my_tasks_variant():
    tasks = [{"id": "→3", "title": "Deploy staging"}]
    ostk = _FakeOstk(tasks=tasks)
    reply = await dispatch_sms_command("my tasks", ostk)
    assert reply == "1. Deploy staging (→3)"


@pytest.mark.asyncio
async def test_dispatch_list_tasks_empty():
    ostk = _FakeOstk(tasks=[])
    reply = await dispatch_sms_command("tasks", ostk)
    assert reply == "No open tasks."


@pytest.mark.asyncio
async def test_dispatch_list_tasks_caps_at_ten():
    tasks = [{"id": f"→{i}", "title": f"Task {i}"} for i in range(1, 15)]
    ostk = _FakeOstk(tasks=tasks)
    reply = await dispatch_sms_command("list tasks", ostk)
    lines = reply.splitlines()
    assert len(lines) == 10
    assert lines[0].startswith("1.")
    assert lines[9].startswith("10.")


@pytest.mark.asyncio
async def test_dispatch_unknown_returns_none():
    ostk = _FakeOstk()
    reply = await dispatch_sms_command("hello world", ostk)
    assert reply is None
    assert ostk.added == []
