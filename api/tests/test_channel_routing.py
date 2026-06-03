"""Unit tests for channel routing payload construction."""

import pytest
from routers.channel_routing import build_action, build_reply_payload


def test_spawn_routes_to_spawn_endpoint():
    intent = {"action": "spawn", "agent_name": "researcher", "task": "find bugs"}
    result = build_action(intent)
    assert result.endpoint == "POST /api/agents/spawn"
    assert result.payload["name"] == "researcher"
    assert result.payload["task"] == "find bugs"
    assert result.payload["source"] == "channel"
    assert not result.skipped


def test_nudge_routes_to_nudge_endpoint():
    intent = {"action": "nudge", "target": "my-agent", "message": "please stop"}
    result = build_action(intent)
    assert result.endpoint == "POST /api/agents/my-agent/nudge"
    assert result.payload["message"] == "please stop"
    assert not result.skipped


def test_status_routes_to_agents_list():
    intent = {"action": "status"}
    result = build_action(intent)
    assert result.endpoint == "GET /api/agents"
    assert not result.skipped


def test_unknown_is_skipped():
    intent = {"action": "unknown", "raw": "blah blah"}
    result = build_action(intent)
    assert result.skipped
    assert result.endpoint == ""


def test_reply_payload_structure():
    payload = build_reply_payload(recipient="+15555551234", text="All done!")
    assert payload["recipient"] == "+15555551234"
    assert payload["text"] == "All done!"


def test_spawn_payload_has_source_channel():
    intent = {"action": "spawn", "agent_name": "helper", "task": "do stuff"}
    result = build_action(intent)
    assert result.payload["source"] == "channel"


def test_nudge_endpoint_includes_target_name():
    intent = {"action": "nudge", "target": "build-agent-xyz", "message": "hi"}
    result = build_action(intent)
    assert "build-agent-xyz" in result.endpoint
