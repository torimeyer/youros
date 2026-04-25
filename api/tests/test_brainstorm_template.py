"""Tests for the builtin-brainstorm agent template and agentfile."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.agent_templates_store import BUILTIN_AGENT_TEMPLATES
from services.agentfile_parser import get_agent_config_by_template


def test_builtin_brainstorm_template_registered():
    """builtin-brainstorm is in BUILTIN_AGENT_TEMPLATES with correct fields."""
    entry = next((t for t in BUILTIN_AGENT_TEMPLATES if t["id"] == "builtin-brainstorm"), None)
    assert entry is not None, "builtin-brainstorm missing from BUILTIN_AGENT_TEMPLATES"
    assert entry["name"] == "Brainstorm"
    assert entry["installed"] is True
    assert entry["personas"] == []


def test_brainstorm_agentfile_resolves_quick_mode_and_pin():
    """agents/brainstorm.agent resolves with quick_mode=True and PIN default."""
    config = get_agent_config_by_template("brainstorm")
    assert config is not None, "get_agent_config_by_template('brainstorm') returned None"
    assert config.quick_mode is True, "brainstorm.agent must set LIMIT quick_mode true"
    assert config.pin_policy.name == "default", "brainstorm.agent must set PIN default"
