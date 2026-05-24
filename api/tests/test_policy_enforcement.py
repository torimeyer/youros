"""Tests for enterprise policy enforcement.

Covers budget limits, approval thresholds, isolation permissions,
and effective permissions in both solo and enterprise modes.
"""

from __future__ import annotations

import json
from typing import Optional
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enterprise_json(
    max_budget: float = 10.0,
    approval_above: float = 5.0,
    isolation_level: str = "open",
) -> dict:
    """Return a minimal enterprise.json structure with the given policies."""
    return {
        "org": {"id": "test", "name": "TestCo"},
        "members": [],
        "policies": {
            "max_agent_budget": max_budget,
            "require_approval_above": approval_above,
            "isolation_level": isolation_level,
        },
    }


def _patch_enterprise(tmp_path, data: Optional[dict] = None):
    """Patch enterprise_store to read from a temp file.

    If data is None, the file does not exist (solo mode).
    """
    ent_path = tmp_path / "enterprise.json"
    if data is not None:
        ent_path.write_text(json.dumps(data))
    return patch("services.enterprise_store.ENTERPRISE_PATH", ent_path)


# ---------------------------------------------------------------------------
# Budget checks
# ---------------------------------------------------------------------------

def test_budget_allowed_solo_mode(tmp_path):
    """In solo mode (no enterprise), any budget is allowed."""
    from services.policy_enforcement import check_budget

    with _patch_enterprise(tmp_path, data=None):
        allowed, reason = check_budget(999.0)
    assert allowed is True
    assert reason == ""


def test_budget_blocked_over_limit(tmp_path):
    """Budget exceeding the org limit should be blocked."""
    from services.policy_enforcement import check_budget

    with _patch_enterprise(tmp_path, _enterprise_json(max_budget=5.0)):
        allowed, reason = check_budget(6.0)
    assert allowed is False
    assert "$6.00" in reason
    assert "$5.00" in reason


def test_budget_allowed_under_limit(tmp_path):
    """Budget at or under the org limit should pass."""
    from services.policy_enforcement import check_budget

    with _patch_enterprise(tmp_path, _enterprise_json(max_budget=10.0)):
        allowed, reason = check_budget(10.0)
    assert allowed is True
    assert reason == ""


# ---------------------------------------------------------------------------
# Approval threshold
# ---------------------------------------------------------------------------

def test_approval_required_above_threshold(tmp_path):
    """Budget above the approval threshold requires approval."""
    from services.policy_enforcement import check_approval_required

    with _patch_enterprise(tmp_path, _enterprise_json(approval_above=3.0)):
        result = check_approval_required(4.0)
    assert result is True


def test_approval_not_required_below_threshold(tmp_path):
    """Budget at or below the threshold does not require approval."""
    from services.policy_enforcement import check_approval_required

    with _patch_enterprise(tmp_path, _enterprise_json(approval_above=5.0)):
        result = check_approval_required(5.0)
    assert result is False


def test_approval_not_required_solo_mode(tmp_path):
    """In solo mode, approval is never required."""
    from services.policy_enforcement import check_approval_required

    with _patch_enterprise(tmp_path, data=None):
        result = check_approval_required(999.0)
    assert result is False


# ---------------------------------------------------------------------------
# Isolation permissions
# ---------------------------------------------------------------------------

def test_isolation_sealed_blocks_shell(tmp_path):
    """Sealed mode should block shell access."""
    from services.policy_enforcement import check_isolation_permissions

    with _patch_enterprise(tmp_path, _enterprise_json(isolation_level="sealed")):
        allowed, reason = check_isolation_permissions(["shell"])
    assert allowed is False
    assert "shell" in reason
    assert "Sealed" in reason


def test_isolation_governed_blocks_file_write(tmp_path):
    """Governed mode should block file:write."""
    from services.policy_enforcement import check_isolation_permissions

    with _patch_enterprise(tmp_path, _enterprise_json(isolation_level="governed")):
        allowed, reason = check_isolation_permissions(["file:write"])
    assert allowed is False
    assert "file:write" in reason
    assert "Governed" in reason


def test_isolation_open_allows_everything(tmp_path):
    """Open mode allows all standard tools."""
    from services.policy_enforcement import check_isolation_permissions

    tools = ["shell", "file:read", "file:write", "file:edit", "web:search", "web:fetch"]
    with _patch_enterprise(tmp_path, _enterprise_json(isolation_level="open")):
        allowed, reason = check_isolation_permissions(tools)
    assert allowed is True
    assert reason == ""


def test_isolation_solo_mode_allows_everything(tmp_path):
    """Solo mode (no enterprise) allows everything."""
    from services.policy_enforcement import check_isolation_permissions

    with _patch_enterprise(tmp_path, data=None):
        allowed, reason = check_isolation_permissions(["shell", "file:write", "anything"])
    assert allowed is True
    assert reason == ""


# ---------------------------------------------------------------------------
# Effective permissions
# ---------------------------------------------------------------------------

def test_effective_permissions_returns_correct_list(tmp_path):
    """Effective permissions should match the isolation level config."""
    from services.policy_enforcement import get_effective_permissions, ISOLATION_LEVELS

    with _patch_enterprise(tmp_path, _enterprise_json(isolation_level="governed")):
        perms = get_effective_permissions()
    assert perms == ISOLATION_LEVELS["governed"]["agent_permissions"]


def test_effective_permissions_solo_mode(tmp_path):
    """Solo mode returns full open permissions."""
    from services.policy_enforcement import get_effective_permissions, ISOLATION_LEVELS

    with _patch_enterprise(tmp_path, data=None):
        perms = get_effective_permissions()
    assert perms == ISOLATION_LEVELS["open"]["agent_permissions"]


# ---------------------------------------------------------------------------
# Isolation-to-permission-mode mapping
# ---------------------------------------------------------------------------

def test_isolation_to_permission_mode():
    """Each isolation level maps to the correct CLI permission mode."""
    from services.policy_enforcement import isolation_to_permission_mode

    assert isolation_to_permission_mode("open") == "bypassPermissions"
    assert isolation_to_permission_mode("governed") == "default"
    assert isolation_to_permission_mode("sealed") == "plan"
    # Unknown levels default to bypassPermissions
    assert isolation_to_permission_mode("unknown") == "bypassPermissions"
