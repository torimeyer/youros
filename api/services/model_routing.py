"""Model routing: tier resolution and auto-escalation.

Single source of truth for how a tier name ("sonnet", "opus", "haiku")
maps to a concrete model id, and for the auto-escalation path that retries
a failed Sonnet call on Opus.

Design contract
---------------
- Default tier: ``sonnet`` (cheap, fast, handles routine work).
- Explicit ``opus`` tier: reserved for hard reasoning, architecture decisions,
  or tasks the caller explicitly flags as complex.
- Auto-escalation: if a Sonnet call fails with a retriable signal
  (non-zero exit, quota cap, or ``NEEDS_OPUS`` in output), callers can
  call ``escalate_to_opus()`` to get the Opus id and log the escalation.
- No silent downgrades of anything currently user-facing.

Escalation counters are appended to ``.ostk/escalation_log.jsonl`` so
cost impact can be measured:  ``grep NEEDS_OPUS .ostk/escalation_log.jsonl | wc -l``
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Literal, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier → model id map.  Edit here when Anthropic ships a new generation.
# ---------------------------------------------------------------------------
TIER_MODEL_MAP: dict[str, str] = {
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-7",
    "haiku":  "claude-haiku-4-5-20251001",
}

# Retired model IDs that must be silently remapped to their current equivalent.
# Used by resolve_model() so stale hardcodes or saved settings are healed at
# call time rather than reaching the Anthropic API as a 404.
RETIRED_MODELS: dict[str, str] = {
    "claude-sonnet-4-20250514": "claude-sonnet-4-6",
    "claude-sonnet-4-5":        "claude-sonnet-4-6",
    "claude-haiku-4-5":         "claude-haiku-4-5-20251001",
}

DEFAULT_TIER: str = "sonnet"
DEFAULT_MODEL: str = TIER_MODEL_MAP[DEFAULT_TIER]

# Sentinel string subagents can emit to request escalation.
ESCALATION_SIGNAL: str = "NEEDS_OPUS"

# Path for escalation events (relative to project root, resolved at runtime).
_ESCALATION_LOG_REL = ".ostk/escalation_log.jsonl"


def _escalation_log_path() -> str:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        # Fallback: walk up from this file to find .ostk/
        here = os.path.dirname(os.path.abspath(__file__))
        for _ in range(6):
            candidate = os.path.join(here, _ESCALATION_LOG_REL)
            if os.path.exists(os.path.dirname(candidate)):
                return candidate
            here = os.path.dirname(here)
    return os.path.join(project_dir, _ESCALATION_LOG_REL)


def resolve_model(tier: Optional[str] = None) -> str:
    """Return the concrete model id for a tier alias.

    ``tier`` can be ``"sonnet"``, ``"opus"``, ``"haiku"``, a full model id,
    or ``None``.  If it is already a full id (contains ``-4-``), it is
    returned as-is.  Unknown short aliases fall back to the default tier.

    >>> resolve_model("sonnet")
    'claude-sonnet-4-6'
    >>> resolve_model("opus")
    'claude-opus-4-7'
    >>> resolve_model(None)
    'claude-sonnet-4-6'
    >>> resolve_model("claude-opus-4-7")
    'claude-opus-4-7'
    """
    if not tier:
        return DEFAULT_MODEL
    if tier in TIER_MODEL_MAP:
        return TIER_MODEL_MAP[tier]
    # Retired model IDs are silently healed to their current equivalent.
    if tier in RETIRED_MODELS:
        current = RETIRED_MODELS[tier]
        logger.info("model_routing: retired model %r remapped to %s", tier, current)
        return current
    # Looks like a full model id already — pass through.
    if "-4-" in tier or tier.startswith("claude-"):
        return tier
    # Unknown alias: log a warning and default to Sonnet.
    logger.warning("model_routing: unknown tier %r, defaulting to %s", tier, DEFAULT_MODEL)
    return DEFAULT_MODEL


def escalation_needed(output: str, exit_code: int) -> bool:
    """Return True if a Sonnet result warrants escalation to Opus.

    Triggers on:
    - Non-zero exit code (subagent failed).
    - ``NEEDS_OPUS`` appearing anywhere in the output.
    """
    if exit_code != 0:
        return True
    if ESCALATION_SIGNAL in (output or ""):
        return True
    return False


def escalate_to_opus(agent_name: str, reason: str) -> str:
    """Log an escalation event and return the Opus model id.

    Writes one JSON line to ``.ostk/escalation_log.jsonl`` so escalation
    rate can be measured without adding a full database dependency.

    Returns the Opus model id to use for the retry.
    """
    opus_model = TIER_MODEL_MAP["opus"]
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent_name,
        "reason": reason,
        "escalated_to": opus_model,
    }
    try:
        log_path = _escalation_log_path()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as exc:
        logger.warning("model_routing: could not write escalation log: %s", exc)

    logger.info(
        "model_routing: escalating agent=%s reason=%r -> %s",
        agent_name,
        reason,
        opus_model,
    )
    return opus_model
