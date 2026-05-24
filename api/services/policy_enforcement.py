"""Policy enforcement for enterprise mode.

Pure functions that check enterprise policies (budget limits, approval
thresholds, isolation permissions) and return allow/deny decisions.

In solo mode (no enterprise org), everything is allowed.
"""

from __future__ import annotations

from services import enterprise_store

ISOLATION_LEVELS = {
    "open": {
        "label": "Open",
        "description": "Full agent access, minimal restrictions. Best for solo use.",
        "agent_permissions": ["shell", "file:read", "file:write", "file:edit", "web:search", "web:fetch"],
        "require_approval": False,
        "allow_destructive": True,
    },
    "governed": {
        "label": "Governed",
        "description": "Agents need approval for destructive operations. Best for teams.",
        "agent_permissions": ["shell:readonly", "file:read", "file:edit", "web:search"],
        "require_approval": True,
        "allow_destructive": False,
    },
    "sealed": {
        "label": "Sealed",
        "description": "Strict enforcement. Agents can only read and suggest. Best for compliance.",
        "agent_permissions": ["file:read", "web:search"],
        "require_approval": True,
        "allow_destructive": False,
    },
}


def check_budget(requested_budget: float) -> tuple[bool, str]:
    """Return (True, "") if allowed, (False, "reason") if blocked."""
    if not enterprise_store.is_enterprise():
        return (True, "")
    policies = enterprise_store.get_policies()
    max_budget = policies.get("max_agent_budget", 10.0)
    if requested_budget > max_budget:
        return (
            False,
            f"Requested budget ${requested_budget:.2f} exceeds the org limit of ${max_budget:.2f}. "
            f"Ask an admin to raise the limit in Settings > Enterprise > Policies.",
        )
    return (True, "")


def check_approval_required(requested_budget: float) -> bool:
    """Return True if this budget needs admin approval."""
    if not enterprise_store.is_enterprise():
        return False
    policies = enterprise_store.get_policies()
    threshold = policies.get("require_approval_above", 5.0)
    return requested_budget > threshold


def check_isolation_permissions(requested_tools: list[str]) -> tuple[bool, str]:
    """Check if requested tools are allowed under the current isolation level."""
    if not enterprise_store.is_enterprise():
        return (True, "")
    policies = enterprise_store.get_policies()
    level = policies.get("isolation_level", "open")
    level_config = ISOLATION_LEVELS.get(level, ISOLATION_LEVELS["open"])
    allowed = set(level_config["agent_permissions"])
    for tool in requested_tools:
        if tool not in allowed:
            return (
                False,
                f"Tool '{tool}' is not allowed under the '{level_config['label']}' isolation level. "
                f"Allowed tools: {', '.join(sorted(allowed))}.",
            )
    return (True, "")


def get_effective_permissions() -> list[str]:
    """Return the permission list for the current isolation level."""
    if not enterprise_store.is_enterprise():
        return list(ISOLATION_LEVELS["open"]["agent_permissions"])
    policies = enterprise_store.get_policies()
    level = policies.get("isolation_level", "open")
    level_config = ISOLATION_LEVELS.get(level, ISOLATION_LEVELS["open"])
    return list(level_config["agent_permissions"])


def get_isolation_level() -> str:
    """Return the current isolation level name."""
    if not enterprise_store.is_enterprise():
        return "open"
    policies = enterprise_store.get_policies()
    return policies.get("isolation_level", "open")


def isolation_to_permission_mode(level: str) -> str:
    """Map an isolation level to a Claude CLI permission mode.

    - open -> bypassPermissions (current default, full autonomy)
    - governed -> default (interactive approval for destructive ops)
    - sealed -> plan (read-only, suggest only)
    """
    return {
        "open": "bypassPermissions",
        "governed": "default",
        "sealed": "plan",
    }.get(level, "bypassPermissions")
