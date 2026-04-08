"""Break an idea into the full list of tasks needed to ship it.

Given an idea title and description, ask Claude to produce a concrete
list of tasks. If the idea is too vague to break down confidently, return
a clarifying question instead of guessing.

Strategy:
1. Build a focused Claude prompt that asks for strict JSON output with
   either a clarifying question or a task list.
2. Parse the JSON with one retry on malformed output. Fall back to a
   single task built from the original idea if Claude keeps returning
   garbage.
3. Cap task count between 1 and 15.
4. Cache by the hash of (title, description, extra_context) for the
   lifetime of the process so repeat calls do not burn extra Claude
   credits.

Public surface:
- ``IdeaBreakdownResult`` / ``BreakdownTask`` Pydantic models.
- ``break_down_idea`` async function.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import List, Optional

import anthropic
from pydantic import BaseModel, Field

from services.chat_providers import _resolve_api_key

logger = logging.getLogger(__name__)

# Same model the chat pathway uses.
_MODEL = "claude-sonnet-4-20250514"

# Keep breakdowns small enough to fit a sensible amount of tasks.
_MAX_TASKS = 15
_MIN_TASKS = 1

_VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}

# Process-lifetime cache. Maps hash -> IdeaBreakdownResult.
_cache: dict[str, "IdeaBreakdownResult"] = {}

# Counters for tests to assert that the cache is honored.
_counters = {"claude_calls": 0}


class BreakdownTask(BaseModel):
    title: str
    description: str = ""
    priority: str = "P2"
    order: int = 0


class IdeaBreakdownResult(BaseModel):
    needs_clarification: bool = False
    question: Optional[str] = None
    tasks: List[BreakdownTask] = Field(default_factory=list)


def _fingerprint(title: str, description: str, extra_context: Optional[str]) -> str:
    raw = f"{title}\n{description}\n{extra_context or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_prompt(
    title: str,
    description: str,
    extra_context: Optional[str],
    breakdown_hint: Optional[str] = None,
) -> tuple[str, str]:
    """Return (system, user) prompts for the breakdown classifier.

    The system prompt steers Claude toward either asking one clarifying
    question or returning a concrete task list. No em-dashes, plain
    language.
    """
    hint_line = ""
    if breakdown_hint and breakdown_hint.strip():
        hint_line = (
            f"Context about this idea type: {breakdown_hint.strip()}\n\n"
        )

    system = (
        "You break a user's idea into the concrete list of tasks needed to "
        "ship it. Each task should be small enough to finish in one sitting. "
        "If the idea is too vague to break down well, do not guess. Ask one "
        "clarifying question instead. Plain language. No jargon. Never use "
        "em-dashes.\n\n"
        f"{hint_line}"
        "You must respond with ONLY valid JSON matching this exact shape:\n"
        "{\n"
        '  "needs_clarification": boolean,\n'
        '  "question": string or null,\n'
        '  "tasks": [\n'
        "    {\n"
        '      "title": string,\n'
        '      "description": string,\n'
        '      "priority": "P0" or "P1" or "P2" or "P3",\n'
        '      "order": integer starting at 0\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- If needs_clarification is true, set question to a single short "
        "question in plain language and leave tasks as an empty list.\n"
        "- If needs_clarification is false, set question to null and return "
        "between 1 and 15 tasks.\n"
        "- Each task title is short (a few words). Each description is 1 to "
        "3 sentences in plain language.\n"
        "- order starts at 0 and increases.\n"
        "- Default priority is P2. Use P0 for things that block everything "
        "else, P1 for important, P2 for normal, P3 for nice to have.\n"
        "- Return ONLY the JSON object. No prose before or after."
    )

    user_parts = [
        f"Idea title: {title.strip()}",
        f"Idea description: {(description or '').strip() or '(none)'}",
    ]
    if extra_context and extra_context.strip():
        user_parts.append(
            f"Answer to the last clarifying question: {extra_context.strip()}"
        )
    user_parts.append(
        "Break this into the full list of tasks needed to ship it, or ask "
        "one clarifying question if you cannot."
    )
    user = "\n\n".join(user_parts)
    return system, user


def _extract_json_block(raw: str) -> Optional[dict]:
    """Pull the first JSON object out of Claude's response.

    Tolerates leading or trailing prose by grabbing the first ``{`` and its
    matching closing brace. Returns a parsed dict or None.
    """
    if not raw:
        return None
    text = raw.strip()
    # Fast path: the whole response is JSON.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    # Slow path: find the first balanced object.
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    return None
                return None
    return None


def _coerce_priority(value: object) -> str:
    s = str(value or "").strip().upper()
    if s in _VALID_PRIORITIES:
        return s
    return "P2"


def _coerce_result(parsed: dict) -> Optional[IdeaBreakdownResult]:
    """Turn a raw dict from Claude into an IdeaBreakdownResult.

    Returns None if the dict cannot be coerced at all.
    """
    if not isinstance(parsed, dict):
        return None

    needs_clarification = bool(parsed.get("needs_clarification", False))
    question_raw = parsed.get("question")
    question = str(question_raw).strip() if question_raw else None

    raw_tasks = parsed.get("tasks")
    tasks: list[BreakdownTask] = []
    if isinstance(raw_tasks, list):
        for idx, t in enumerate(raw_tasks):
            if not isinstance(t, dict):
                continue
            title = str(t.get("title") or "").strip()
            if not title:
                continue
            description = str(t.get("description") or "").strip()
            priority = _coerce_priority(t.get("priority"))
            order_raw = t.get("order", idx)
            try:
                order = int(order_raw)
            except (TypeError, ValueError):
                order = idx
            tasks.append(
                BreakdownTask(
                    title=title,
                    description=description,
                    priority=priority,
                    order=order,
                )
            )

    # Cap at _MAX_TASKS.
    if len(tasks) > _MAX_TASKS:
        tasks = tasks[:_MAX_TASKS]

    # Re-number order so it is always contiguous.
    for i, t in enumerate(tasks):
        t.order = i

    if needs_clarification:
        # Claude asked a question but also returned tasks. Trust the
        # question and drop the tasks.
        if not question:
            return None
        return IdeaBreakdownResult(
            needs_clarification=True,
            question=question,
            tasks=[],
        )

    if not tasks:
        # No tasks and no question. Ask one so we do not guess.
        return IdeaBreakdownResult(
            needs_clarification=True,
            question=(
                "I need a little more info to break this down. What is the "
                "main goal and who is it for?"
            ),
            tasks=[],
        )

    return IdeaBreakdownResult(
        needs_clarification=False,
        question=None,
        tasks=tasks,
    )


def _fallback_single_task(title: str, description: str) -> IdeaBreakdownResult:
    """Last-resort result when Claude keeps returning garbage."""
    clean_title = (title or "idea").strip() or "idea"
    clean_desc = (description or "").strip()
    return IdeaBreakdownResult(
        needs_clarification=False,
        question=None,
        tasks=[
            BreakdownTask(
                title=clean_title[:120],
                description=clean_desc or "From a captured idea.",
                priority="P2",
                order=0,
            )
        ],
    )


async def _call_claude(system: str, user: str) -> str:
    """Run one Claude request. Returns raw text or empty string on error."""
    api_key = await _resolve_api_key("anthropic_api_key")
    if not api_key:
        return ""
    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=_MODEL,
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": user}],
            ),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        logger.info("idea breakdown timed out")
        return ""
    except anthropic.APIError as exc:
        logger.warning("idea breakdown Claude error: %s", exc)
        return ""
    except Exception as exc:
        logger.warning("idea breakdown unexpected error: %s", exc)
        return ""

    _counters["claude_calls"] += 1
    if not response.content:
        return ""
    for block in response.content:
        if getattr(block, "type", "") == "text":
            return getattr(block, "text", "") or ""
    return ""


async def break_down_idea(
    title: str,
    description: str = "",
    extra_context: Optional[str] = None,
    breakdown_hint: Optional[str] = None,
) -> IdeaBreakdownResult:
    """Break an idea into the tasks needed to ship it.

    Returns an IdeaBreakdownResult. If Claude thinks the idea is too vague
    it returns needs_clarification=True with a single question. Otherwise
    it returns between 1 and 15 tasks.

    breakdown_hint is an optional sentence that tells Claude what kind of
    idea this is (e.g. from a template). It is injected into the system
    prompt to improve task generation.
    """
    title = (title or "").strip()
    description = (description or "").strip()
    cache_key = _fingerprint(title, description, extra_context)

    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    if not title and not description:
        result = IdeaBreakdownResult(
            needs_clarification=True,
            question="What is the idea? Share a short description.",
            tasks=[],
        )
        _cache[cache_key] = result
        return result

    system, user = _build_prompt(title, description, extra_context, breakdown_hint)

    # First attempt.
    raw = await _call_claude(system, user)
    parsed = _extract_json_block(raw)
    result = _coerce_result(parsed) if parsed else None

    # One retry on malformed output.
    if result is None:
        raw = await _call_claude(system, user)
        parsed = _extract_json_block(raw)
        result = _coerce_result(parsed) if parsed else None

    if result is None:
        result = _fallback_single_task(title, description)

    _cache[cache_key] = result
    return result


def _reset_cache_for_tests() -> None:
    """Clear the process cache. Only intended for tests."""
    _cache.clear()
    _counters["claude_calls"] = 0
