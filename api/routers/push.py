"""Push notifications router.

Endpoints for managing Web Push subscriptions and sending test
notifications. Subscriptions are stored in ~/.youros/push_subscriptions.json.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from services.push import push_service

router = APIRouter(tags=["push"])


class PushSubscription(BaseModel):
    """Browser PushSubscription object."""
    endpoint: str
    keys: dict
    expirationTime: Optional[float] = None


class TestPushRequest(BaseModel):
    """Optional overrides for the test notification."""
    title: Optional[str] = None
    body: Optional[str] = None


@router.get("/push/vapid-public-key")
async def get_vapid_public_key():
    """Return the VAPID public key so the browser can subscribe."""
    return {"public_key": push_service.get_public_key()}


@router.post("/push/subscribe")
async def subscribe(subscription: PushSubscription):
    """Save a push subscription from the browser."""
    push_service.add_subscription(subscription.model_dump())
    return {"result": "ok"}


@router.post("/push/unsubscribe")
async def unsubscribe(subscription: PushSubscription):
    """Remove a push subscription."""
    found = push_service.remove_subscription(subscription.endpoint)
    return {"result": "ok", "found": found}


@router.post("/push/test")
async def test_push(req: TestPushRequest = TestPushRequest()):
    """Send a test push notification to all subscriptions."""
    title = req.title or "myOS Test"
    body = req.body or "Push notifications are working."
    count = push_service.send_to_all(title=title, body=body, tag="test")
    return {"result": "ok", "delivered": count}


@router.get("/push/subscriptions/count")
async def subscription_count():
    """Return the number of active push subscriptions."""
    return {"count": len(push_service.list_subscriptions())}
