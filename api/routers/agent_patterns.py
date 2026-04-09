"""Agent patterns router.

Exposes the read-only pattern analyzer to the Insights tab in the UI.
All three endpoints are idempotent GETs.
"""

from fastapi import APIRouter

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
