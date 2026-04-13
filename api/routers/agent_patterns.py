"""Agent patterns router.

Exposes the pattern analyzer to the Insights tab in the UI.
Includes read-only analysis endpoints plus proven template management.
"""

from fastapi import APIRouter, HTTPException

from services import agent_patterns

router = APIRouter(tags=["agent-patterns"])


@router.get("/agent-patterns/recommendations")
async def get_recommendations():
    """Return the actionable recommendations, grouped client-side by severity."""
    return {"recommendations": agent_patterns.recommendations()}


@router.get("/agent-patterns/template-stats")
async def get_template_stats():
    """Return per-template aggregate stats (success rate, median duration, etc)."""
    return {"stats": agent_patterns.template_stats()}


@router.get("/agent-patterns/runs")
async def get_runs(limit: int = 100, offset: int = 0):
    """Return analyzed runs (latest first), paginated."""
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500
    if offset < 0:
        offset = 0
    all_runs = agent_patterns.analyze_runs()
    sliced = all_runs[offset: offset + limit]
    return {
        "runs": sliced,
        "total": len(all_runs),
        "limit": limit,
        "offset": offset,
    }


@router.get("/agent-patterns/proven")
async def get_proven_templates():
    """Return all proven (high-confidence) templates."""
    return {"proven": agent_patterns.proven_templates()}


@router.post("/agent-patterns/promote/{template_name}")
async def promote_template(template_name: str):
    """Promote a template to proven status.

    The template must exist in the stats with >= 2 runs and >= 75%
    success rate. Returns the promoted entry on success.
    """
    entry = agent_patterns.promote_template(template_name)
    if entry is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Template '{template_name}' was not found or does not qualify. "
                "It needs at least 2 runs and 75% or higher success rate."
            ),
        )
    return {"promoted": entry}


@router.get("/agent-patterns/adjustments")
async def get_suggested_adjustments():
    """Return suggested adjustments for templates that keep failing."""
    return {"adjustments": agent_patterns.suggested_adjustments()}
