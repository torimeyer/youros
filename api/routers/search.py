from fastapi import APIRouter, HTTPException, Query

from services.ostk import ostk, OstkError

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(q: str = Query(..., min_length=1, description="Search topic or keyword")):
    """Search across tasks and ideas by topic.

    Uses ostk concept search to find related tasks and scans
    ideas for matching text. Results are grouped by type.
    """
    try:
        results = await ostk.search_near(q)
        return results
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))
