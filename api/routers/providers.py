from fastapi import APIRouter
from services.provider_detection import detect_providers as _detect

router = APIRouter()


@router.get("/providers/detect")
async def detect_providers_endpoint() -> dict:
    """Report which AI providers are available on this machine.

    Returns booleans only — no secrets or key values are included.
    No auth gate: detection is safe to call during onboarding before
    any credentials are stored.
    """
    return await _detect()
