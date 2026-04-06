"""Legacy goals endpoint was replaced by the labels system.

These tests verify the /goals endpoint no longer exists (returns 404 or 405)
and that the labels endpoint works correctly. The primary labels tests
are in test_labels.py. This file exists to document the migration.
"""

import pytest


@pytest.mark.asyncio
async def test_goals_endpoint_removed(client):
    """The /api/goals endpoint has been replaced by /api/labels."""
    resp = await client.get("/api/goals")
    # The endpoint no longer exists, so it should return 404 or 405
    assert resp.status_code in (404, 405, 422)


@pytest.mark.asyncio
async def test_labels_endpoint_exists(client):
    """The new /api/labels endpoint should respond with 200."""
    resp = await client.get("/api/labels")
    assert resp.status_code == 200
    assert "labels" in resp.json()
