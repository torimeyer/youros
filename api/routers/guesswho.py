"""Guess Who answerer.

The browser game runs the *asking* side (the computer narrows down the user's
secret with information-gain questions over structured attributes). This router
handles the other direction: the user types a free-text question about the
computer's hidden food, and we answer strictly yes / no / invalid.

Primary path uses the shared AI backend so any phrasing is understood. When no
backend is configured we fall back to keyword-matching the question against the
item's known attributes (degraded, but the game still works offline).
"""

import json
import logging
import re
from typing import Literal

import anthropic
from fastapi import APIRouter
from pydantic import BaseModel

from services.ai_backend import get_ai_client

router = APIRouter(tags=["guesswho"])
logger = logging.getLogger(__name__)

Answer = Literal["yes", "no", "invalid"]


class AnswerRequest(BaseModel):
    item_name: str
    item_attributes: dict[str, bool] = {}
    question: str


class AnswerResponse(BaseModel):
    answer: Answer


_SYSTEM = (
    "You are the answerer in a children's Guess Who game. The secret item is "
    "'{name}'. Its known true attributes are: {attrs}. The player will ask a "
    "question. Answer ONLY about the secret item.\n"
    "Reply with STRICT JSON and nothing else: {{\"answer\": \"yes\"}}, "
    "{{\"answer\": \"no\"}}, or {{\"answer\": \"invalid\"}}.\n"
    "Use \"invalid\" ONLY when the question cannot be answered yes or no (for "
    "example 'what is it?' or 'name a color'). For any reasonable yes/no "
    "question, answer yes or no using common knowledge about the item and its "
    "attributes. Interpret friendly, kid-style phrasing generously."
)

# Keyword fallback synonyms for the shipped Food edition attributes.
_ATTR_SYNONYMS: dict[str, tuple[str, ...]] = {
    "fruit": ("fruit",),
    "vegetable": ("vegetable", "veggie", "veggies"),
    "sweet": ("sweet",),
    "drink": ("drink", "drank", "sip"),
    "dairy": ("dairy", "milk", "cheese"),
    "meat": ("meat", "animal"),
    "hot": ("hot", "warm", "cooked"),
    "red": ("red",),
    "green": ("green",),
    "round": ("round", "circle", "circular"),
    "dessert": ("dessert", "treat", "pudding"),
    "breakfast": ("breakfast",),
    "crunchy": ("crunchy", "crunch", "crispy"),
    "fromWater": ("water", "ocean", "sea", "fish", "river"),
}

_WH_WORDS = ("what", "which", "who", "whom", "whose", "when", "where", "why", "how", "name", "tell", "describe", "list")
_YN_STARTS = (
    "is", "are", "am", "was", "were", "do", "does", "did", "can", "could",
    "will", "would", "should", "has", "have", "had", "may", "might",
)


def _fallback_answer(req: AnswerRequest) -> Answer:
    """Answer without an LLM by matching the question to known attributes."""
    q = req.question.strip().lower()
    if not q:
        return "invalid"
    first = re.findall(r"[a-z']+", q)
    first_word = first[0] if first else ""
    # Wh-questions are not yes/no.
    if first_word in _WH_WORDS:
        return "invalid"
    looks_yn = first_word in _YN_STARTS or q.endswith("?")
    if not looks_yn:
        return "invalid"
    for attr, syns in _ATTR_SYNONYMS.items():
        if any(s in q for s in syns):
            return "yes" if req.item_attributes.get(attr) else "no"
    # Looks like a yes/no question but we can't map it to a known attribute.
    return "no"


async def _llm_answer(req: AnswerRequest) -> Answer:
    client = await get_ai_client()
    if client is None:
        return _fallback_answer(req)

    true_attrs = [k for k, v in req.item_attributes.items() if v] or ["(none provided)"]
    system = _SYSTEM.format(name=req.item_name, attrs=", ".join(true_attrs))
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=20,
            system=system,
            messages=[{"role": "user", "content": req.question}],
        )
    except anthropic.APIError as exc:
        logger.error("Guess Who LLM error: %s", exc)
        return _fallback_answer(req)

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    try:
        data = json.loads(text)
        ans = str(data.get("answer", "")).lower()
    except (json.JSONDecodeError, AttributeError):
        # Lenient salvage if the model didn't return clean JSON.
        low = text.lower()
        ans = "invalid" if "invalid" in low else "yes" if "yes" in low else "no" if "no" in low else ""

    if ans in ("yes", "no", "invalid"):
        return ans  # type: ignore[return-value]
    return _fallback_answer(req)


@router.post("/guesswho/answer", response_model=AnswerResponse)
async def answer(body: AnswerRequest) -> AnswerResponse:
    return AnswerResponse(answer=await _llm_answer(body))
