"""Portfolio health: vendor-neutral rollup audit + computed health + draft confidence notes.

This service is the engine behind the "Executive Summary" page. It is fully
vendor-neutral: NO source-specific identifiers (custom-field IDs, JQL, KR page
URLs, vendor names) live here. The source-specific mapping is read at runtime
from ``~/.myos/portfolio.json`` via :func:`load_portfolio_config`. With no config
present the feature is "unconfigured" and writes nothing.

Three capabilities:
  - compute_rollup_audit: per-ticket coverage findings (missing parent, KR link,
    description, reference docs). Mirrors the spec_audit scoring style.
  - compute_health: on_track | at_risk | off_track from real execution signal.
  - draft_confidence_note: action-oriented mitigation text (DRAFT only).
"""

from __future__ import annotations

import json
from pathlib import Path

MYOS_DIR = Path.home() / ".myos"
PORTFOLIO_CONFIG_PATH = MYOS_DIR / "portfolio.json"

HEALTH_ON_TRACK = "on_track"
HEALTH_AT_RISK = "at_risk"
HEALTH_OFF_TRACK = "off_track"


def load_portfolio_config(path: Path | None = None) -> dict:
    """Read the vendor-neutral portfolio mapping from ~/.myos/portfolio.json.

    Recognised keys (all source-specific values supplied by the operator):
      - confidence_field_id: the custom field that holds the confidence level
      - board_or_jql:        the board id or query identifying the portfolio
      - kr_page_id:          the page id for the top-level KR rollup
      - mitigation_field_id: (optional) custom field for mitigation text

    Returns {} when the file is missing, empty, or unreadable. A config is
    considered "configured" only when confidence_field_id is a non-empty string.
    """
    raise NotImplementedError


def is_configured(config: dict | None = None) -> bool:
    """True when the minimum mapping (confidence_field_id) is present."""
    raise NotImplementedError


def compute_rollup_audit(issues: list[dict], links: dict[str, dict]) -> list[dict]:
    """Per-ticket coverage findings. Returns list of {key, audit: {...}} dicts."""
    raise NotImplementedError


def compute_health(issue: dict, signals: dict) -> dict:
    """Return {health, reasons} derived from real execution signal."""
    raise NotImplementedError


async def draft_confidence_note(issue: dict, health: dict) -> str:
    """Action-oriented mitigation text. DRAFT only; never auto-written."""
    raise NotImplementedError
