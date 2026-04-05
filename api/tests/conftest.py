import sys
from pathlib import Path

import pytest
import pytest_asyncio
import httpx

# Ensure api/ is on sys.path so imports like `from main import app` work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
