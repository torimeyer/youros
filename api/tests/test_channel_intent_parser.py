"""Unit tests for channel_intent_parser — covers all 4 intent shapes."""

import pytest
from services.channel_intent_parser import parse_intent


def test_spawn_basic():
    result = parse_intent("spawn researcher to find all Python files")
    assert result["action"] == "spawn"
    assert result["agent_name"] == "researcher"
    assert result["task"] == "find all Python files"


def test_spawn_case_insensitive():
    result = parse_intent("SPAWN helper to do the thing")
    assert result["action"] == "spawn"
    assert result["agent_name"] == "helper"
    assert result["task"] == "do the thing"


def test_spawn_strips_whitespace():
    result = parse_intent("  spawn worker to   fix the bug  ")
    assert result["action"] == "spawn"
    assert result["task"] == "fix the bug"


def test_nudge_basic():
    result = parse_intent("nudge build-agent check the logs")
    assert result["action"] == "nudge"
    assert result["target"] == "build-agent"
    assert result["message"] == "check the logs"


def test_nudge_case_insensitive():
    result = parse_intent("NUDGE my-agent please stop")
    assert result["action"] == "nudge"
    assert result["target"] == "my-agent"
    assert result["message"] == "please stop"


def test_status():
    result = parse_intent("status")
    assert result["action"] == "status"


def test_status_case_insensitive():
    result = parse_intent("STATUS")
    assert result["action"] == "status"


def test_unknown_returns_raw():
    result = parse_intent("hello world this is not a command")
    assert result["action"] == "unknown"
    assert result["raw"] == "hello world this is not a command"


def test_empty_string_is_unknown():
    result = parse_intent("")
    assert result["action"] == "unknown"


def test_spawn_requires_to_keyword():
    result = parse_intent("spawn helper just do stuff")
    assert result["action"] == "unknown"


def test_nudge_requires_message():
    result = parse_intent("nudge agent")
    assert result["action"] == "unknown"
