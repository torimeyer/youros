"""Tests for the Guess Who answerer router."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_llm_response(answer: str) -> MagicMock:
    text_block = SimpleNamespace(type="text", text=json.dumps({"answer": answer}))
    response = MagicMock()
    response.content = [text_block]
    response.usage = SimpleNamespace(input_tokens=10, output_tokens=5)
    return response


def _mock_client(answer: str):
    inst = MagicMock()
    inst.messages.create = AsyncMock(return_value=_make_llm_response(answer))
    return inst


APPLE = {
    "item_name": "Apple",
    "item_attributes": {"fruit": True, "sweet": True, "red": True, "round": True, "crunchy": True},
    "question": "Is it a fruit?",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["yes", "no", "invalid"])
async def test_llm_answer_passes_through(client, answer):
    with patch("routers.guesswho.get_ai_client", new=AsyncMock(return_value=_mock_client(answer))):
        resp = await client.post("/api/guesswho/answer", json=APPLE)
    assert resp.status_code == 200
    assert resp.json()["answer"] == answer


@pytest.mark.asyncio
async def test_llm_malformed_json_is_salvaged(client):
    inst = MagicMock()
    bad = MagicMock()
    bad.content = [SimpleNamespace(type="text", text="Yes, definitely!")]
    inst.messages.create = AsyncMock(return_value=bad)
    with patch("routers.guesswho.get_ai_client", new=AsyncMock(return_value=inst)):
        resp = await client.post("/api/guesswho/answer", json=APPLE)
    assert resp.json()["answer"] == "yes"


@pytest.mark.asyncio
async def test_fallback_matches_known_attribute_when_no_backend(client):
    # No AI backend → keyword fallback. "Is it sweet?" maps to sweet=True.
    body = {**APPLE, "question": "Is it sweet?"}
    with patch("routers.guesswho.get_ai_client", new=AsyncMock(return_value=None)):
        resp = await client.post("/api/guesswho/answer", json=body)
    assert resp.json()["answer"] == "yes"

    body_no = {**APPLE, "question": "Is it green?"}
    with patch("routers.guesswho.get_ai_client", new=AsyncMock(return_value=None)):
        resp = await client.post("/api/guesswho/answer", json=body_no)
    assert resp.json()["answer"] == "no"


@pytest.mark.asyncio
async def test_fallback_rejects_non_yes_no_question(client):
    body = {**APPLE, "question": "What is it?"}
    with patch("routers.guesswho.get_ai_client", new=AsyncMock(return_value=None)):
        resp = await client.post("/api/guesswho/answer", json=body)
    assert resp.json()["answer"] == "invalid"
