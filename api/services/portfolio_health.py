"""Portfolio health: vendor-neutral rollup audit + computed health + draft confidence notes.

This service is the engine behind the "Executive Summary" page. It is fully
vendor-neutral: NO source-specific identifiers (custom-field IDs, JQL strings,
KR-page URLs, vendor names) live here. The source-specific mapping is read at
runtime from ``~/.youros/portfolio.json`` via :func:`load_portfolio_config`. With
no config present the feature is "unconfigured" and writes nothing.

Capabilities:
  - load_portfolio_config / is_configured: read the operator-supplied mapping.
  - compute_rollup_audit: per-ticket coverage findings (missing parent, KR link,
    description, reference docs). Mirrors the spec_audit scoring style.
  - compute_health: on_track | at_risk | off_track from real execution signal.
  - draft_confidence_note: action-oriented mitigation text (DRAFT only).
  - build_health_report: assemble the GET /api/portfolio/health payload.
  - build_weekly_action_item: surface "N updates awaiting approval" as a briefing
    action item (never an auto-write).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional
from services.youros_paths import youros_home

_log = logging.getLogger(__name__)

MYOS_DIR = youros_home()
PORTFOLIO_CONFIG_PATH = MYOS_DIR / "portfolio.json"

HEALTH_ON_TRACK = "on_track"
HEALTH_AT_RISK = "at_risk"
HEALTH_OFF_TRACK = "off_track"

# A reference doc is any http(s) link in the description.
_URL_RE = re.compile(r"https?://", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Config (source-specific values live OUTSIDE main, in ~/.youros/portfolio.json)
# ---------------------------------------------------------------------------

def load_portfolio_config(path: Path | None = None) -> dict:
    """Read the vendor-neutral portfolio mapping from ~/.youros/portfolio.json.

    Recognised keys (all source-specific values supplied by the operator):
      - confidence_field_id: the custom field that holds the confidence level
      - board_or_jql:        the board id or query identifying the portfolio
      - kr_page_id:          the page id for the top-level KR rollup
      - mitigation_field_id: (optional) custom field for mitigation text

    Returns {} when the file is missing, empty, or unreadable.
    """
    cfg_path = path or PORTFOLIO_CONFIG_PATH
    try:
        raw = cfg_path.read_text()
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def is_configured(config: dict | None = None) -> bool:
    """True when the minimum mapping (confidence_field_id) is present.

    With no config the feature renders an unconfigured empty state and writes
    nothing, which is the vendor-neutral default.
    """
    if config is None:
        config = load_portfolio_config()
    field = config.get("confidence_field_id")
    return isinstance(field, str) and bool(field.strip())


# ---------------------------------------------------------------------------
# Rollup coverage audit (asks #1 + #2 from the plan)
# ---------------------------------------------------------------------------

def _has_kr_link(link_entry: dict) -> bool:
    """True when an issue's links contain at least one related/parent issue."""
    for link in link_entry.get("issuelinks", []) or []:
        if link.get("outwardIssue") or link.get("inwardIssue"):
            return True
    return False


def compute_rollup_audit(issues: list[dict], links: dict[str, dict]) -> list[dict]:
    """Per-ticket coverage findings.

    For each issue returns ``{"key": str, "audit": {...}}`` where audit carries
    four booleans, mirroring the spec_audit coverage-scoring style:
      - missing_initiative_parent: no parent issue linkage
      - missing_kr_link:           not linked to any rollup (KR) issue
      - missing_description:       description is blank/whitespace
      - missing_ref_docs:          no reference link in the description
    """
    rows: list[dict] = []
    for issue in issues:
        key = issue.get("key", "")
        parent = issue.get("parent")
        description = (issue.get("description_html") or issue.get("description") or "").strip()
        link_entry = links.get(key, {}) if links else {}

        audit = {
            "missing_initiative_parent": not bool(parent),
            "missing_kr_link": not _has_kr_link(link_entry),
            "missing_description": not bool(description),
            "missing_ref_docs": not bool(_URL_RE.search(description)),
        }
        rows.append({"key": key, "audit": audit})
    return rows


# ---------------------------------------------------------------------------
# Computed health from real execution signal (ask #3 input)
# ---------------------------------------------------------------------------

def compute_health(issue: dict, signals: dict) -> dict:
    """Derive ``{health, reasons}`` from real signal.

    Signal inputs (all optional, defaulting to a neutral value):
      - blocked:        membership in the blocked-issues set
      - due_slipped:    the due date is in the past while work remains
      - children_total / children_done: child completion ratio
      - stale:          no recent activity

    Priority: blocked => off_track; due slipped or low completion => at_risk;
    otherwise on_track. Returns an enum that is always one of the three values.
    """
    reasons: list[str] = []
    blocked = bool(signals.get("blocked"))
    due_slipped = bool(signals.get("due_slipped"))
    stale = bool(signals.get("stale"))
    total = int(signals.get("children_total") or 0)
    done = int(signals.get("children_done") or 0)
    ratio = (done / total) if total else None

    if blocked:
        reasons.append("Blocked by another ticket.")
    if due_slipped:
        reasons.append("Due date has slipped.")
    if ratio is not None and ratio < 0.5:
        reasons.append(f"Only {done} of {total} child items done.")
    if stale:
        reasons.append("No recent activity.")

    if blocked:
        health = HEALTH_OFF_TRACK
    elif due_slipped or (ratio is not None and ratio < 0.5) or stale:
        health = HEALTH_AT_RISK
    else:
        health = HEALTH_ON_TRACK
        if ratio is not None:
            reasons.append(f"{done} of {total} child items done.")
        elif not reasons:
            reasons.append("Progressing as planned.")

    return {"health": health, "reasons": reasons}


# ---------------------------------------------------------------------------
# LLM draft (DRAFT ONLY: never auto-written to the source)
# ---------------------------------------------------------------------------

_DRAFT_MODEL = "claude-sonnet-4-20250514"

_DRAFT_PROMPT = (
    "You are a PM writing a one-line confidence update for a leadership rollup. "
    "Given the work item and its computed health, write the update in this exact "
    "shape on a single line:\n\n"
    "Mitigation: <the in-flight actions being taken>. Help needed: <the blockers "
    "or asks, or 'none' if there are none>.\n\n"
    "Plain language. Under 60 words. No vague signals like 'moving forward'. "
    "Name the specific action and the specific ask.\n\n"
    "Work item: {summary}\n"
    "Computed health: {health}\n"
    "Reasons: {reasons}\n"
)


async def _llm_complete(prompt: str) -> Optional[str]:
    """One-shot Anthropic completion. Returns text or None when unavailable.

    Mirrors the gmail_triage one-shot pattern. No streaming, no websocket.
    """
    try:
        import anthropic
        from services.chat_providers import _resolve_api_key
    except ImportError:
        return None

    api_key = await _resolve_api_key("anthropic_api_key")
    if not api_key:
        return None

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(
            model=_DRAFT_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001 - any provider error => fall back
        _log.warning("portfolio draft LLM call failed: %s", exc)
        return None

    for block in response.content or []:
        if getattr(block, "type", "") == "text":
            return (getattr(block, "text", "") or "").strip()
    return None


def _fallback_note(issue: dict, health: dict) -> str:
    """Deterministic templated note used when no LLM is available. Never empty."""
    reasons = health.get("reasons") or []
    reason_text = "; ".join(reasons) if reasons else "see linked work item"
    state = health.get("health", HEALTH_AT_RISK)
    if state == HEALTH_OFF_TRACK:
        return f"Mitigation: addressing the issues ({reason_text}). Help needed: review the blockers above."
    if state == HEALTH_AT_RISK:
        return f"Mitigation: working to get back on plan ({reason_text}). Help needed: flag if priorities should shift."
    return "Mitigation: on plan, continuing execution. Help needed: none."


async def draft_confidence_note(issue: dict, health: dict) -> str:
    """Action-oriented mitigation text. DRAFT only; never auto-written.

    Falls back to a deterministic templated note when no LLM is available, so
    the caller always gets a usable, non-empty draft to review.
    """
    prompt = _DRAFT_PROMPT.format(
        summary=issue.get("summary", issue.get("key", "")),
        health=health.get("health", ""),
        reasons=", ".join(health.get("reasons", []) or []),
    )
    text = await _llm_complete(prompt)
    if text and "Mitigation" in text:
        return text
    return _fallback_note(issue, health)


# ---------------------------------------------------------------------------
# Report assembly + weekly briefing action item
# ---------------------------------------------------------------------------

# Sentinel empty payload returned whenever the feature is unconfigured.
_EMPTY_REPORT = {
    "configured": False,
    "krs": [],
    "audit_findings": [],
    "pending_approvals": [],
}


async def build_health_report(config: dict | None = None) -> dict:
    """Assemble the GET /api/portfolio/health payload.

    When unconfigured returns the exact empty payload (writes nothing, reads
    nothing). When configured, walks the source via the (already vendor-neutral)
    atlassian read helpers, computes audit + health, and drafts confidence notes.
    """
    if config is None:
        config = load_portfolio_config()
    if not is_configured(config):
        return dict(_EMPTY_REPORT)

    from services import atlassian

    # Read the portfolio. board_or_jql is operator-supplied (vendor-neutral here).
    try:
        issues = await atlassian.list_assigned_issues()
    except Exception as exc:  # noqa: BLE001 - surface as empty rather than 500
        _log.warning("portfolio read failed: %s", exc)
        return dict(_EMPTY_REPORT)

    try:
        blocked = await atlassian.list_blocked_issues()
        blocked_keys = {b.get("key") for b in blocked}
    except Exception:  # noqa: BLE001
        blocked_keys = set()

    links: dict[str, dict] = {}
    for issue in issues:
        key = issue.get("key", "")
        if not key:
            continue
        try:
            links[key] = await atlassian.get_issue_links(key)
        except Exception:  # noqa: BLE001
            links[key] = {"issuelinks": []}

    audit_rows = compute_rollup_audit(issues, links)
    audit_by_key = {r["key"]: r["audit"] for r in audit_rows}

    initiatives: list[dict] = []
    pending_approvals: list[dict] = []
    audit_findings: list[dict] = []

    for issue in issues:
        key = issue.get("key", "")
        link_entry = links.get(key, {})
        children = link_entry.get("issuelinks", []) or []
        signals = {
            "blocked": key in blocked_keys,
            "children_total": len(children),
            "children_done": 0,  # child status not fetched in this pass
            "due_slipped": False,
        }
        health = compute_health(issue, signals)
        audit = audit_by_key.get(key, {})

        initiatives.append({
            "key": key,
            "title": issue.get("summary", ""),
            "health": health["health"],
            "reasons": health["reasons"],
            "audit": audit,
        })

        for finding, present in audit.items():
            if present:
                audit_findings.append({
                    "key": key,
                    "finding": finding,
                    "detail": finding.replace("_", " ").capitalize(),
                })

        if health["health"] in (HEALTH_AT_RISK, HEALTH_OFF_TRACK):
            note = await draft_confidence_note(issue, health)
            pending_approvals.append({
                "key": key,
                "title": issue.get("summary", ""),
                "draft_value": health["health"],
                "draft_note": note,
                "why": "; ".join(health["reasons"]) if health["reasons"] else "",
            })

    krs = [{
        "key": config.get("kr_page_id", "PORTFOLIO"),
        "title": "Portfolio rollup",
        "health": _rollup_health(initiatives),
        "reasons": [],
        "initiatives": initiatives,
    }] if initiatives else []

    return {
        "configured": True,
        "krs": krs,
        "audit_findings": audit_findings,
        "pending_approvals": pending_approvals,
    }


def _rollup_health(initiatives: list[dict]) -> str:
    """Roll child health up to a parent: worst-child wins."""
    states = {i["health"] for i in initiatives}
    if HEALTH_OFF_TRACK in states:
        return HEALTH_OFF_TRACK
    if HEALTH_AT_RISK in states:
        return HEALTH_AT_RISK
    return HEALTH_ON_TRACK


async def build_weekly_action_item() -> Optional[dict]:
    """Surface "N confidence updates awaiting your approval" as a briefing item.

    Returns None when unconfigured or when there is nothing pending. This is an
    information surface, never an auto-write.
    """
    if not is_configured():
        return None
    report = await build_health_report()
    pending = report.get("pending_approvals", [])
    if not pending:
        return None
    n = len(pending)
    return {
        "type": "review_confidence_updates",
        "label": f"{n} confidence update{'s' if n != 1 else ''} awaiting your approval",
        "action_url": "/portfolio/health",
        "context": "myOS drafted confidence updates from real execution signal. Review and approve to write them back.",
    }


# ---------------------------------------------------------------------------
# Weekly scheduler (reuses the reminders scheduler pattern: asyncio loop)
# ---------------------------------------------------------------------------

# One week between regenerations. Surfaced as a briefing action item, never an
# auto-write to the source.
_WEEKLY_INTERVAL_SECONDS = 7 * 24 * 60 * 60


async def refresh_weekly_briefing() -> Optional[dict]:
    """Regenerate the draft summary and merge the action item into the briefing.

    Returns the action item written (or None when unconfigured / nothing
    pending). This is the unit the weekly loop calls each tick.
    """
    item = await build_weekly_action_item()
    if item is None:
        return None

    try:
        from services import briefing
        state = briefing._load_state()
        items = [i for i in state.get("action_items", []) if i.get("type") != item["type"]]
        items.insert(0, item)
        state["action_items"] = items
        briefing._save_state(state)
    except Exception as exc:  # noqa: BLE001 - never let the loop die on briefing I/O
        _log.warning("portfolio weekly briefing merge failed: %s", exc)

    return item


async def start_portfolio_scheduler():
    """Start a background weekly loop that refreshes confidence drafts.

    Mirrors reminders.start_reminder_scheduler: an asyncio loop guarded against
    exceptions. No-op effect when unconfigured (refresh returns None).
    """
    import asyncio

    async def _loop() -> None:
        while True:
            try:
                await refresh_weekly_briefing()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(_WEEKLY_INTERVAL_SECONDS)

    import asyncio as _a
    return _a.create_task(_loop())
