"""Executive Summary API (internal code namespace: portfolio).

Vendor-neutral. Returns configured:false and writes nothing when the
~/.myos/portfolio.json mapping is absent. On EM approval, writes the confidence
field + a "why" comment back to the source via the existing atlassian helpers.

The user-visible name is "Executive Summary"; "portfolio" is internal only.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from services import atlassian
from services import portfolio_health
from services.portfolio_health import build_health_report

_log = logging.getLogger(__name__)

router = APIRouter()


class ConfidenceApproval(BaseModel):
    value: str
    note: str = ""
    why: str = ""


@router.get("/portfolio/health")
async def get_portfolio_health() -> dict:
    """Return the rollup tree + audit findings + computed health + draft confidence.

    When unconfigured returns the exact empty payload and reads nothing.
    """
    return await build_health_report()


@router.post("/portfolio/confidence/{key}/approve")
async def approve_confidence(key: str, body: ConfidenceApproval) -> dict:
    """On EM approval, write the confidence field + the "why" comment back.

    The confidence field id and (optional) mitigation field id come from the
    operator-supplied ~/.myos/portfolio.json. With no config we write nothing
    and return ok:false (information, not a silent failure).
    """
    config = portfolio_health.load_portfolio_config()
    if not portfolio_health.is_configured(config):
        return {
            "key": key,
            "written_value": None,
            "comment_id": None,
            "ok": False,
            "reason": "unconfigured",
        }

    confidence_field_id = config["confidence_field_id"]
    fields = {confidence_field_id: body.value}
    # Optionally mirror the mitigation text into a dedicated field if mapped.
    mitigation_field_id = config.get("mitigation_field_id")
    if mitigation_field_id and body.note:
        fields[mitigation_field_id] = body.note

    await atlassian.update_issue_fields(key, fields)

    comment_id = None
    if body.why:
        try:
            comment = await atlassian.add_comment(key, body.why)
            comment_id = comment.get("id")
        except Exception as exc:  # noqa: BLE001 - field write already succeeded
            _log.warning("portfolio approve: comment failed for %s: %s", key, exc)

    return {
        "key": key,
        "written_value": body.value,
        "comment_id": comment_id,
        "ok": True,
    }
