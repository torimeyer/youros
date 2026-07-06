"""Auto-match user chat messages to agent templates.

The goal: when a user types a message in chat, pick the best-fitting
template (if any) so myOS can apply the template's behavior automatically.
The user never has to explicitly say "saa" or "diagnose".

Strategy (in order, first match wins):
1. Explicit invocation: the message starts with the template name or one of
   its triggers, word-boundary aware. Preserves saa/diagnose/explain-plain behavior.
2. Keyword match: the message contains ALL of the template's required
   keywords (if any are defined). Cheap and deterministic.
3. AI classifier: if nothing matched deterministically, ask Claude to pick
   the best template from name+description pairs. Response is validated
   against the list of template names and cached by message hash.

Design choices:
- The matcher never raises on classifier errors. If Claude fails, the
  matcher returns None and chat falls back to normal behavior.
- The classifier is only called when at least one candidate template has
  no keyword match. This avoids calling Claude on common greetings.
- Results from the classifier are cached in an in-memory dict keyed by
  (message_hash, template_name_set) for the lifetime of the process.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from typing import Any, Optional

import anthropic

# Built-in templates. These mirror the saa/diagnose/explain-plain commands that used
# to live inside the chat system prompt. They are injected alongside the
# user's custom templates so the matcher can find them.
BUILT_IN_TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "saa",
        "description": (
            "Spawn background agents to do work in parallel. Use when the user "
            "wants multiple pieces of work done at once, wants to run a long task "
            "in the background, or asks to 'spawn', 'delegate', or 'run in parallel'."
        ),
        "triggers": ["saa", "spawn agents", "spawn an agent", "spawn agent"],
        "keywords": [],
        "prompt": (
            "SAA MODE: You must plan in 2 to 3 bullet points, then use the "
            "spawn_agent tool to create one or more background agents to do the "
            "actual work. Do NOT do the work inline. Each agent's prompt must "
            "include clear instructions, tests to write, and a verification step. "
            "If the task splits naturally, spawn multiple agents in parallel. "
            "After spawning, briefly tell the user what each agent is working on."
        ),
    },
    {
        "name": "diagnose",
        "description": (
            "Find the root cause of a bug or broken behavior, fix it, and write "
            "a regression test. Use when the user asks why something is broken, "
            "failing, hanging, or not working."
        ),
        "triggers": ["diagnose", "why is", "why does", "why isn't", "why won't"],
        "keywords": [],
        "prompt": (
            "DIAGNOSE MODE: Find the root cause of the problem. Read the actual "
            "code before making any claims. Never assume. Once you have the root "
            "cause, fix it and write a regression test so it cannot happen again. "
            "Report the root cause, the fix, and the test in plain language."
        ),
    },
    {
        "name": "explain-plain",
        "aliases": ["elit"],
        "description": (
            "Plain-language explanation of anything. No jargon, no omissions. "
            "Covers every relevant point and uses analogies for technical concepts. "
            "Use when the user wants a non-technical explanation of something."
        ),
        "triggers": [
            "explain-plain",
            "explain plain",
            "elit",
            "explain like i'm",
            "eli5",
            "in plain english",
        ],
        "keywords": [],
        "prompt": (
            "EXPLAIN-PLAIN MODE: Explain the subject in plain language so "
            "someone with no background in the field can follow it. No jargon. "
            "Cover every relevant point. Do not skip material for brevity. Use "
            "analogies for technical or abstract concepts. No code. No "
            "em-dashes. Start with a one-paragraph summary, then go deeper in "
            "clearly labeled sections."
        ),
    },
    {
        "name": "idea",
        "description": (
            "The user shared an idea, project concept, or initiative they want to "
            "pursue. Use when the user describes something they want to build, launch, "
            "plan, create, or ship. NOT for specific bug reports or questions."
        ),
        "triggers": ["break this into tasks", "break into tasks", "plan this out"],
        "keywords": [],
        "prompt": (
            "IDEA MODE: The user just shared an idea. First, briefly acknowledge it "
            "(one sentence). Then ask: 'Want me to break this into tasks?' If they "
            "say yes, use the idea breakdown service to create a full task list. "
            "If the idea is too vague, ask one clarifying question first."
        ),
    },
]

# In-memory cache of classifier results. Keyed by (message_hash,
# template_signature). Values are either a template name (str) or the
# literal string "NONE" to cache negative results.
_CLASSIFIER_CACHE: dict[tuple[str, str], str] = {}

# Model for the classifier. Same model the chat flow uses so we do not need
# another API key or config entry.
_CLASSIFIER_MODEL = "claude-sonnet-4-6"


def _word_boundary_starts_with(text: str, phrase: str) -> bool:
    """Return True if text starts with phrase as a whole word or phrase.

    Case-insensitive. Allows punctuation after the phrase (colon, comma,
    etc) so "saa: fix this" matches "saa".
    """
    text = text.strip().lower()
    phrase = phrase.strip().lower()
    if not phrase:
        return False
    pattern = r"^" + re.escape(phrase) + r"(?:\b|[:,;.!?\s]|$)"
    return re.search(pattern, text) is not None


def _all_keywords_present(text: str, keywords: list[str]) -> bool:
    """Return True if every keyword appears as a word in text (case-insensitive)."""
    if not keywords:
        return False
    lowered = text.lower()
    for kw in keywords:
        kw = kw.strip().lower()
        if not kw:
            continue
        if not re.search(r"\b" + re.escape(kw) + r"\b", lowered):
            return False
    return True


def _template_signature(templates: list[dict[str, Any]]) -> str:
    """Stable signature for a set of templates, for cache keying."""
    names = sorted(str(t.get("name", "")) for t in templates)
    return "|".join(names)


def _message_hash(message: str) -> str:
    return hashlib.sha256(message.strip().lower().encode("utf-8")).hexdigest()[:16]


def _inject_user_aliases(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inject user-set aliases into the templates list.

    User aliases from ~/.youros/template_aliases.json are added to the
    matching template's aliases list so the explicit-invocation layer
    can pick them up. User aliases take priority over built-in aliases
    on collision.
    """
    try:
        from services.agent_templates_store import agent_templates_store
        user_aliases = agent_templates_store.get_all_user_aliases()
    except Exception:
        return templates

    if not user_aliases:
        return templates

    # Build a reverse map: template_id -> [alias1, alias2, ...]
    id_to_aliases: dict[str, list[str]] = {}
    for alias, tid in user_aliases.items():
        id_to_aliases.setdefault(tid, []).append(alias)

    # Also build name-to-aliases for templates that have no id field
    # (the chat matcher built-ins use "name" not "id").
    # Include template aliases so "saa" (which is an alias of Builder)
    # resolves to "builtin-builder".
    name_to_tid: dict[str, str] = {}
    try:
        from services.agent_templates_store import BUILTIN_AGENT_TEMPLATES
        for t in BUILTIN_AGENT_TEMPLATES:
            name_to_tid[t["name"].lower()] = t["id"]
            for a in t.get("aliases", []):
                name_to_tid[a.lower()] = t["id"]
    except Exception:
        pass

    for t in templates:
        tid = t.get("id", "")
        if not tid:
            # Try to resolve via name
            tid = name_to_tid.get(t.get("name", "").lower(), "")
        if tid and tid in id_to_aliases:
            existing = list(t.get("aliases", []) or [])
            for ua in id_to_aliases[tid]:
                if ua.lower() not in {a.lower() for a in existing}:
                    existing.append(ua)
            t["aliases"] = existing

    return templates


def merge_with_built_ins(custom_templates: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return built-in templates plus any custom ones from settings.

    Custom templates with the same name as a built-in override the built-in.
    This lets power users tweak saa/diagnose/explain-plain behavior without losing
    auto-matching. User aliases from the Settings page are injected so the
    explicit-invocation layer can match them.
    """
    custom = list(custom_templates or [])
    custom_names = {str(t.get("name", "")).lower() for t in custom if isinstance(t, dict)}
    merged: list[dict[str, Any]] = []
    for t in BUILT_IN_TEMPLATES:
        if t["name"].lower() not in custom_names:
            merged.append(dict(t))
    for t in custom:
        if isinstance(t, dict) and t.get("name"):
            merged.append(dict(t))
    return _inject_user_aliases(merged)


async def _run_classifier(
    message: str,
    candidates: list[dict[str, Any]],
    api_key: str,
) -> Optional[str]:
    """Ask Claude to pick the best template name, or NONE. Returns the name or None."""
    if not api_key or not candidates:
        return None

    lines = []
    for t in candidates:
        name = t.get("name", "")
        desc = (t.get("description") or "").strip()
        if name:
            lines.append(f"- {name}: {desc}")
    if not lines:
        return None

    # Cap the prompt. Very long messages get truncated to the first 500 chars
    # which is plenty for intent classification.
    snippet = message.strip()
    if len(snippet) > 500:
        snippet = snippet[:500] + "..."

    system = (
        "You are a fast intent classifier. The user will give you a message "
        "and a list of available templates. Respond with ONLY the template "
        "name that best matches the user's intent, or the word NONE if no "
        "template fits. Do not explain. Do not add punctuation. Just the "
        "template name or NONE."
    )
    user = (
        f"Message: {snippet}\n\n"
        f"Templates:\n" + "\n".join(lines) + "\n\n"
        f"Which template name best matches this message? Respond with just "
        f"the name, or NONE."
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        # Cap the classifier at 5 seconds. A slow or stalled Anthropic API
        # would otherwise block the caller (stream_anthropic / agent_anthropic)
        # before backend_active is sent, which fires the frontend 30s timer.
        response = await asyncio.wait_for(
            client.messages.create(
                model=_CLASSIFIER_MODEL,
                max_tokens=32,
                system=system,
                messages=[{"role": "user", "content": user}],
            ),
            timeout=5.0,
        )
        # Extract text from the first content block.
        if not response.content:
            return None
        raw = ""
        for block in response.content:
            if getattr(block, "type", "") == "text":
                raw = block.text.strip()
                break
        if not raw:
            return None
        # Strip possible trailing punctuation or quotes.
        cleaned = raw.strip().strip(".!?\"'` ")
        return cleaned or None
    except Exception:
        return None


async def match_template(
    user_message: str,
    templates: list[dict[str, Any]],
    api_key: Optional[str] = None,
    enable_classifier: bool = True,
) -> Optional[dict[str, Any]]:
    """Return the best-matching template for a user message, or None.

    ``templates`` should be the full list of candidates, including built-in
    entries. Call :func:`merge_with_built_ins` first if you only have the
    custom list from settings.

    ``api_key`` is used for the Claude classifier. If omitted, the matcher
    reads it from the ``ANTHROPIC_API_KEY`` env var. If the key is missing,
    only the deterministic match layers run.

    ``enable_classifier`` lets callers turn off the AI fallback. Useful in
    tests and in cases where the cost of an extra Claude call is not worth
    the marginal recall improvement.
    """
    if not user_message or not user_message.strip():
        return None
    if not templates:
        return None

    text = user_message.strip()

    # Layer 1: explicit invocation. Starts with name, any alias, or any
    # trigger phrase. Name and aliases are always implicit triggers so
    # templates can declare user-facing shortcuts (e.g. elit -> explain-plain)
    # in the ``aliases`` list without duplicating them in ``triggers``.
    for tpl in templates:
        name = str(tpl.get("name", ""))
        aliases: list[str] = [str(a) for a in (tpl.get("aliases", []) or [])]
        triggers: list[str] = list(tpl.get("triggers", []) or [])
        all_triggers = [name] + aliases + triggers
        for trig in all_triggers:
            if trig and _word_boundary_starts_with(text, trig):
                return _with_reason(tpl, "explicit")

    # Layer 2: keyword match. Only templates that declare keywords are
    # considered. First template with all its keywords present wins.
    for tpl in templates:
        keywords: list[str] = list(tpl.get("keywords", []) or [])
        if keywords and _all_keywords_present(text, keywords):
            return _with_reason(tpl, "keyword")

    # Layer 3: AI classifier. Cached by (message hash, template set).
    if not enable_classifier:
        return None
    cache_key = (_message_hash(text), _template_signature(templates))
    cached = _CLASSIFIER_CACHE.get(cache_key)
    if cached is not None:
        if cached == "NONE":
            return None
        for tpl in templates:
            if str(tpl.get("name", "")) == cached:
                return _with_reason(tpl, "ai_cached")
        return None

    # Resolve API key if not provided.
    if api_key is None:
        from services.ostk_secrets import get_anthropic_key
        api_key = await get_anthropic_key()
    if not api_key:
        # No classifier available. Cache NONE so we do not try again this
        # process lifetime for the same message.
        _CLASSIFIER_CACHE[cache_key] = "NONE"
        return None

    predicted = await _run_classifier(text, templates, api_key)
    if not predicted or predicted.upper() == "NONE":
        _CLASSIFIER_CACHE[cache_key] = "NONE"
        return None

    # Validate: must be an exact match to one of the template names.
    for tpl in templates:
        if str(tpl.get("name", "")) == predicted:
            _CLASSIFIER_CACHE[cache_key] = predicted
            return _with_reason(tpl, "ai")

    # Classifier returned something we don't know about. Don't fuzzy match.
    _CLASSIFIER_CACHE[cache_key] = "NONE"
    return None


def _with_reason(tpl: dict[str, Any], reason: str) -> dict[str, Any]:
    """Shallow copy the template and stamp the match reason on it."""
    out = dict(tpl)
    out["_match_reason"] = reason
    return out


def clear_cache() -> None:
    """Clear the classifier cache. Mainly for tests."""
    _CLASSIFIER_CACHE.clear()
