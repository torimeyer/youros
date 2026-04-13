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


@router.get("/search/deep")
async def deep_search(q: str = Query(..., min_length=1, description="Search query")):
    """Deep search across all kernel state: needles, audit events, and transcripts.

    Uses ostk pitchfork to search everything the kernel knows about,
    including closed tasks, decisions, threads, and audit history.
    """
    try:
        results = await ostk.pitchfork(q)
        return results
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search/recall")
async def recall_search(q: str = Query(..., min_length=1, description="Search query")):
    """Search kernel state and past session transcripts.

    Uses ostk recall which includes transcript content in addition
    to the kernel state that pitchfork covers. This can be slower
    so it is a separate endpoint, loaded on demand.
    """
    try:
        results = await ostk.recall(q)
        return results
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))
