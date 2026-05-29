"""AI-powered clarification suggestions for failing readiness checks (→1565).

Calls Claude to propose a concrete fix for a specific failing check. Does not
persist anything — callers decide whether to apply the suggestion.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Prompt templates — one per check name.
# Each template receives .format(**context) where context has:
#   kind        "task" | "spec"
#   title       str
#   description str
#   file_text   str (spec body, empty for tasks)
#   failing_check str
# ---------------------------------------------------------------------------

_PROMPTS: dict[str, str] = {
    "outcome_concrete": """\
You are helping a software developer improve a work item description so it passes a clarity check.

The clarity check "outcome_concrete" fails when the title or description contains vague language
(TBD, ?, should we, TODO, maybe, discuss, figure out, we'll see, decide what) or when the title
is empty / too generic (e.g. "update X" or "fix X" with nothing after the noun).

{kind_label} to improve:
Title: {title}
Description:
{description}

Write a brief concrete replacement for the description (2–5 sentences) that:
- States exactly what will be built or changed
- Removes all vague language
- Keeps any technical details already present

Return JSON with two keys:
  "proposed_fix": the replacement description text (plain text, no markdown fences)
  "rationale": one sentence explaining what was vague and why the new text is clearer
""",

    "in_repo_scope": """\
You are helping a software developer fix a work item that a clarity check flagged as potentially
out of scope for the current repository.

The clarity check "in_repo_scope" fails when the title starts with an upstream/external marker
or when the description mentions "upstream ostk", "different repo", "external repo", or similar.

{kind_label} to improve:
Title: {title}
Description:
{description}

Rewrite the description (2–4 sentences) to make it clear this work happens inside the current
repository. If the work genuinely is upstream, acknowledge that and suggest a scoped in-repo
sub-task instead.

Return JSON with two keys:
  "proposed_fix": the replacement description text (plain text, no markdown fences)
  "rationale": one sentence explaining what triggered the scope flag and how the fix resolves it
""",

    "is_unblocked": """\
You are helping a software developer address an open blocker on a task.

The clarity check "is_unblocked" fails when the task has at least one unresolved blocker.

{kind_label} to improve:
Title: {title}
Description:
{description}

Suggest a short addition to the description (1–3 sentences) that either:
(a) explains how to resolve the blocker so the task can proceed, or
(b) proposes splitting the blocked part into a separate follow-up task.

Return JSON with two keys:
  "proposed_fix": the text to append to the description (plain text, no markdown fences)
  "rationale": one sentence explaining the suggested approach
""",

    "has_ac_checkboxes": """\
You are helping a software developer add acceptance-criteria checkboxes to a spec document.

The clarity check "has_ac_checkboxes" fails when the spec has no unchecked "- [ ]" lines.

Spec title: {title}
Spec body (excerpt):
{file_text_excerpt}

Write 2–5 concrete acceptance-criteria lines in the format:
  - [ ] <criterion>

Each criterion should be a specific, verifiable outcome.

Return JSON with two keys:
  "proposed_fix": the acceptance-criteria block (plain text, one line per criterion)
  "rationale": one sentence explaining what criteria are most important for this spec
""",

    "no_vague_ac": """\
You are helping a software developer make acceptance criteria more concrete.

The clarity check "no_vague_ac" fails when AC lines contain vague language
(TBD, ?, should we, TODO, maybe, discuss, figure out, we'll see, decide what).

Spec title: {title}
Acceptance criteria with vague language:
{description}

Rewrite each vague AC line to be specific and verifiable. Keep the "- [ ]" format.

Return JSON with two keys:
  "proposed_fix": the rewritten AC lines (plain text, one line per criterion)
  "rationale": one sentence explaining what vague tokens were found and how the fix is better
""",

    "has_file_paths": """\
You are helping a software developer add concrete file references to a spec document.

The clarity check "has_file_paths" fails when the spec body contains no explicit file paths
(e.g. foo.py, bar.tsx) that resolve to real files in the repo.

Spec title: {title}
Spec body (excerpt):
{file_text_excerpt}

Suggest 2–4 file paths (one per line) that this spec likely needs to touch, based on the
title and body content. Use relative paths like api/routers/tasks.py or app/src/pages/Tasks.tsx.

Return JSON with two keys:
  "proposed_fix": a short paragraph or list of file paths to add to the spec's Critical Files section
  "rationale": one sentence explaining which files are most relevant to this spec
""",

    "referenced_files_exist": """\
You are helping a software developer fix file references in a spec document.

The clarity check "referenced_files_exist" fails when fewer than 50% of named file paths in the
spec body can be found in the repository. This usually means paths are mis-typed or point to
files that were moved.

Spec title: {title}
Spec body (excerpt):
{file_text_excerpt}

Suggest corrected or alternative file paths. Focus on the most likely renames or typos.

Return JSON with two keys:
  "proposed_fix": the corrected or replacement file path text (plain text)
  "rationale": one sentence explaining which paths look wrong and what might be the correct names
""",
}

_FALLBACK_PROMPT = """\
You are helping a software developer improve a work item that failed a readiness check.

Check that failed: {failing_check}
{kind_label}:
Title: {title}
Description:
{description}

Suggest a concrete improvement to the description that would make this check pass.

Return JSON with two keys:
  "proposed_fix": the suggested text (plain text, no markdown fences)
  "rationale": one sentence explaining why this change helps
"""


# ---------------------------------------------------------------------------
# Service function
# ---------------------------------------------------------------------------

async def suggest_clarification(
    check_name: str,
    context: dict[str, Any],
    model: str = "claude-sonnet-4-5",
) -> dict[str, str]:
    """Return {proposed_fix, rationale} for a failing readiness check.

    context keys:
      kind          "task" | "spec"
      title         str
      description   str
      file_text     str (full spec body; empty for tasks)
      failing_check str (same as check_name, kept for prompt interpolation)
    """
    kind = context.get("kind", "task")
    title = context.get("title", "")
    description = context.get("description", "")
    file_text = context.get("file_text", "")

    kind_label = "Task" if kind == "task" else "Spec"
    file_text_excerpt = file_text[:2000] if file_text else description[:2000]

    template = _PROMPTS.get(check_name, _FALLBACK_PROMPT)
    prompt = template.format(
        kind=kind,
        kind_label=kind_label,
        title=title,
        description=description,
        file_text=file_text,
        file_text_excerpt=file_text_excerpt,
        failing_check=check_name,
    )

    # Route through the shared AI backend so suggestions work on a Claude
    # subscription (CLI) OR an API key, instead of hard-requiring an Anthropic
    # key. Matches the wizard_suggest path and degrades with a clear, vendor
    # neutral message when no backend is connected.
    from services.ai_backend import get_ai_client
    client = await get_ai_client()
    if client is None:
        raise ValueError(
            "No AI backend is connected. Connect Claude or add an API key in Settings to use AI suggestions."
        )

    response = await client.messages.create(
        model=model,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", "") == "text":
            text_parts.append(getattr(block, "text", ""))
    raw = "".join(text_parts).strip()

    return _parse_suggestion(raw, check_name)


def _parse_suggestion(raw: str, check_name: str) -> dict[str, str]:
    """Extract {proposed_fix, rationale} from raw model output."""
    import json
    import re

    # Strip markdown code fences if present
    clean = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    clean = re.sub(r"\s*```$", "", clean, flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict) and "proposed_fix" in parsed:
            return {
                "proposed_fix": str(parsed.get("proposed_fix", "")),
                "rationale": str(parsed.get("rationale", "")),
            }
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: return raw text as the proposed fix
    return {
        "proposed_fix": raw,
        "rationale": f"AI suggestion for check '{check_name}' (raw output — JSON parse failed)",
    }
