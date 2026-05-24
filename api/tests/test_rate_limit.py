"""Rate-limiter unit tests.

Exercise the limiter directly with a synthetic Request so we can run
many calls in a tight loop without hitting the async TestClient.
"""

import pytest
from fastapi import HTTPException

from services.rate_limit import LIMITS, _reset_all, rate_limit_check


class _FakeClient:
    def __init__(self, host: str):
        self.host = host


class _FakeURL:
    scheme = "http"


class _FakeRequest:
    def __init__(self, host: str = "1.2.3.4"):
        self.client = _FakeClient(host)
        self.headers: dict = {}
        self.url = _FakeURL()


@pytest.fixture(autouse=True)
def _enable_limiter(monkeypatch):
    monkeypatch.delenv("MYOS_DISABLE_RATE_LIMIT", raising=False)
    _reset_all()
    yield
    _reset_all()


def test_spawn_limit_blocks_twenty_first():
    req = _FakeRequest()
    limit, _ = LIMITS["agents.spawn"]
    assert limit == 20
    for _ in range(limit):
        rate_limit_check(req, "agents.spawn")
    with pytest.raises(HTTPException) as exc:
        rate_limit_check(req, "agents.spawn")
    assert exc.value.status_code == 429
    assert exc.value.detail["bucket"] == "agents.spawn"
    assert "Retry-After" in exc.value.headers


def test_chat_limit_blocks_at_cap():
    req = _FakeRequest()
    limit, _ = LIMITS["chat.send"]
    assert limit == 60
    for _ in range(limit):
        rate_limit_check(req, "chat.send")
    with pytest.raises(HTTPException) as exc:
        rate_limit_check(req, "chat.send")
    assert exc.value.status_code == 429


def test_separate_ips_do_not_interfere():
    a = _FakeRequest(host="10.0.0.1")
    b = _FakeRequest(host="10.0.0.2")
    limit, _ = LIMITS["agents.spawn"]
    for _ in range(limit):
        rate_limit_check(a, "agents.spawn")
    # b should not be blocked by a's usage.
    rate_limit_check(b, "agents.spawn")


def test_disable_env_var_bypasses(monkeypatch):
    monkeypatch.setenv("MYOS_DISABLE_RATE_LIMIT", "1")
    req = _FakeRequest()
    # Exceed the limit. Must not raise.
    for _ in range(LIMITS["agents.spawn"][0] + 5):
        rate_limit_check(req, "agents.spawn")
