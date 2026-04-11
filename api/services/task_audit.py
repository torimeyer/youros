"""Tasks audit service.

Walks every open task, asks an LLM to classify each one against the real
codebase, and returns a verdict the user can review before anything is
closed. The service itself never auto-closes anything. Closing happens
when the user clicks Approve in the review UI.

REVIEW ONLY INVARIANT: The audit never auto closes anything. Every
proposed outcome requires Tori to approve it one by one in the review
list. No code path in this module may call ostk.close_task. Closing
lives exclusively in the router's /approve endpoint, which only runs
when the user clicks the Close button for one specific task.

Five verdicts are possible:

- ``completed``: the feature already exists in the codebase.
- ``duplicate``: another task covers the same work.
- ``archived``: the task is code work but no longer relevant.
- ``skipped_irl``: the task is not code work at all (e.g. "buy groceries").
- ``review``: unsure, or the task was touched in the last day.

The classifier boundary is :func:`_classify_with_claude` which mirrors the
pattern used by :mod:`services.label_suggester`. Tests mock that function
at the module level so no real network calls ever run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import anthropic

from config import PROJECT_ROOT
from services.chat_providers import _resolve_api_key
from services.ostk import ostk, OstkError


logger = logging.getLogger("myos.task_audit")


# Standing invariant for the whole Tasks audit feature. Referenced from
# the judge function so a future reader cannot miss it. If you ever find
# yourself wanting to close a task from inside this service, stop and
# read this line again. The answer is always no. Tori approves every
# close by hand in the review list.
AUDIT_REVIEW_ONLY_INVARIANT = (
    "The audit never auto closes anything. Every proposed outcome "
    "requires Tori to approve it one by one in the review list."
)


# Same default model used by the label classifier so we do not pin a
# separate version here. When Anthropic bumps the model, the rest of the
# codebase moves with us.
_CLASSIFIER_MODEL = "claude-sonnet-4-20250514"

# How recently a task must have been touched to auto route it to the
# review list without asking the classifier. 24 hours per the feature
# spec so fresh work is never accidentally marked done.
_RECENT_WINDOW = timedelta(hours=24)

# Directories we scan for codebase evidence when building the prompt. The
# grep is best-effort and heavily truncated so the classifier prompt stays
# well under the model context window.
_SCAN_ROOTS = (
    "api",
    "app/src",
    "agents",
)

# Test file globs scanned in a second pass so the classifier sees test
# coverage as its own evidence block. Tests are the strongest completion
# signal the audit can find: a passing regression test class is a signed
# commitment that the underlying bug was caught and locked in.
_TEST_SCAN_ROOTS = (
    "api/tests",
    "app/src",
)

# Python and TypeScript regex patterns we use to detect test or describe
# declarations that contain a task keyword. The patterns are intentionally
# permissive so both pytest class-style tests and vitest describe/it
# blocks get picked up.
_TEST_DECLARATION_PATTERNS = (
    # pytest: class TestFooBarRegression:
    r"class\s+Test[A-Za-z0-9_]*",
    # pytest: def test_foo_bar(...):
    r"def\s+test_[A-Za-z0-9_]*",
    # vitest: describe('foo bar', ...
    r"describe\s*\(\s*['\"][^'\"]+['\"]",
    # vitest: it('foo bar', ...   /   test('foo bar', ...
    r"(?:it|test)\s*\(\s*['\"][^'\"]+['\"]",
)

# Maximum number of grep hits we forward to the classifier per root. Keeps
# the prompt small even for tasks whose keywords happen to appear
# everywhere in the codebase.
_MAX_HITS_PER_ROOT = 6

# Maximum number of test evidence hits we forward per keyword. Kept tight
# so the classifier prompt stays small even when a keyword is popular in
# the test suite.
_MAX_TEST_HITS_PER_KEYWORD = 6

# Maximum length of a single grep hit we forward. Long lines get truncated
# so one minified bundle line cannot blow past the token budget.
_MAX_HIT_LENGTH = 200

# Allowed verdicts. Kept as a set for O(1) membership checks.
_ALLOWED_VERDICTS = {
    "completed",
    "duplicate",
    "archived",
    "skipped_irl",
    "review",
}


# In-process job state. The feature spec explicitly says "no persistence
# needed". A server restart wipes any in-flight job and the user starts
# over. That is fine because the audit is cheap to re-run.
_jobs: dict[str, dict[str, Any]] = {}


def _new_job_state(total: int) -> dict[str, Any]:
    return {
        "status": "running",
        "checked": 0,
        "total": total,
        "results": {
            "closed": [],
            "review": [],
            "skipped_irl": 0,
            "errors": [],
        },
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def start_audit_job() -> str:
    """Kick off a new audit job in the background. Returns a job id."""
    job_id = str(uuid.uuid4())
    # Seed the state synchronously so an immediate GET after POST always
    # finds the job, even before the background task has a chance to run.
    _jobs[job_id] = _new_job_state(total=0)
    asyncio.create_task(run_audit_job(job_id))
    return job_id


def get_audit_status(job_id: str) -> Optional[dict[str, Any]]:
    """Return a copy of a job's state or None when the id is unknown."""
    state = _jobs.get(job_id)
    if state is None:
        return None
    # Shallow copy so callers can safely mutate their own view without
    # racing the background writer.
    return {
        "status": state["status"],
        "checked": state["checked"],
        "total": state["total"],
        "results": {
            "closed": list(state["results"]["closed"]),
            "review": list(state["results"]["review"]),
            "skipped_irl": state["results"]["skipped_irl"],
            "errors": list(state["results"]["errors"]),
        },
    }


def remove_review_entry(job_id: str, task_id: str) -> bool:
    """Drop a task from the review list so the UI can shrink.

    Returns True when the entry was found and removed. Used by the
    /reject endpoint when the user decides to keep a task open after all.
    """
    state = _jobs.get(job_id)
    if state is None:
        return False
    review = state["results"]["review"]
    for i, entry in enumerate(review):
        if entry.get("task_id") == task_id:
            review.pop(i)
            return True
    return False


def record_closed_entry(
    job_id: str, task_id: str, reason: str, evidence: str = ""
) -> None:
    """Move a task from the review list to the closed list after approval.

    Only used by the /approve endpoint. If the task is still in the
    review list we strip it from there so the UI stops showing it.
    """
    state = _jobs.get(job_id)
    if state is None:
        return
    remove_review_entry(job_id, task_id)
    state["results"]["closed"].append(
        {"task_id": task_id, "reason": reason, "evidence": evidence}
    )


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ostk timestamp string. Returns None when unparseable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        # ostk stores ISO8601 with a trailing Z. Swap it for +00:00 so
        # fromisoformat accepts it on Python versions below 3.11.
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        return None


def _is_recently_touched(task: dict, now: datetime) -> bool:
    """Return True when the task was created or updated in the last day."""
    for key in ("updated_at", "created_at"):
        ts = _parse_timestamp(task.get(key))
        if ts is None:
            continue
        if now - ts < _RECENT_WINDOW:
            return True
    return False


def _keywords_from_title(title: str) -> list[str]:
    """Pull 1-3 short keywords from a task title for grep seeding.

    Falls back to the first word when the title has no long words.
    Everything is lowercased and stripped of punctuation so the grep is
    regex-safe.
    """
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", title or "")
    stopwords = {
        "the", "and", "for", "with", "from", "into", "this", "that",
        "add", "fix", "make", "use", "all", "new", "not", "but",
    }
    keywords: list[str] = []
    for tok in tokens:
        lower = tok.lower()
        if lower in stopwords:
            continue
        if lower not in keywords:
            keywords.append(lower)
        if len(keywords) >= 3:
            break
    if not keywords and tokens:
        keywords = [tokens[0].lower()]
    return keywords


def _run_grep(keyword: str, root: str) -> list[str]:
    """Run one bounded grep rooted at ``root``. Returns up to N matching
    ``path:line`` strings. Never raises.
    """
    root_path = Path(PROJECT_ROOT) / root
    if not root_path.exists():
        return []
    try:
        result = subprocess.run(
            [
                "grep",
                "-rIn",
                "--exclude-dir=node_modules",
                "--exclude-dir=dist",
                "--exclude-dir=.venv",
                "--exclude-dir=__pycache__",
                keyword,
                str(root_path),
            ],
            capture_output=True,
            text=True,
            timeout=4.0,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    hits: list[str] = []
    for line in (result.stdout or "").splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        if len(trimmed) > _MAX_HIT_LENGTH:
            trimmed = trimmed[:_MAX_HIT_LENGTH] + "..."
        hits.append(trimmed)
        if len(hits) >= _MAX_HITS_PER_ROOT:
            break
    return hits


def _gather_evidence(title: str) -> str:
    """Build a small block of grep evidence for a task title.

    The block is fed into the classifier prompt so the LLM can see real
    code references instead of guessing from the title alone.
    """
    keywords = _keywords_from_title(title)
    if not keywords:
        return "(no keywords extracted from title)"
    chunks: list[str] = []
    for root in _SCAN_ROOTS:
        for keyword in keywords:
            hits = _run_grep(keyword, root)
            if hits:
                chunks.append(f"# grep '{keyword}' in {root}")
                chunks.extend(hits)
    if not chunks:
        return "(no grep hits for the title keywords)"
    return "\n".join(chunks)


def _run_test_grep(keyword: str, root: str) -> list[str]:
    """Run a bounded grep limited to Python or TS test files.

    Returns up to ``_MAX_TEST_HITS_PER_KEYWORD`` ``path:line`` strings
    whose lines look like a test declaration (class Test..., def test_...,
    describe(..., it(...) and contain the keyword. Never raises.
    """
    root_path = Path(PROJECT_ROOT) / root
    if not root_path.exists():
        return []
    try:
        result = subprocess.run(
            [
                "grep",
                "-rIn",
                "--include=*test*.py",
                "--include=*.test.ts",
                "--include=*.test.tsx",
                "--include=*.spec.ts",
                "--include=*.spec.tsx",
                "--exclude-dir=node_modules",
                "--exclude-dir=dist",
                "--exclude-dir=.venv",
                "--exclude-dir=__pycache__",
                "-i",
                keyword,
                str(root_path),
            ],
            capture_output=True,
            text=True,
            timeout=4.0,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    hits: list[str] = []
    for line in (result.stdout or "").splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        # Only keep lines that look like a test declaration so we do not
        # flood the prompt with unrelated matches inside test bodies.
        if not any(re.search(p, trimmed) for p in _TEST_DECLARATION_PATTERNS):
            continue
        if len(trimmed) > _MAX_HIT_LENGTH:
            trimmed = trimmed[:_MAX_HIT_LENGTH] + "..."
        hits.append(trimmed)
        if len(hits) >= _MAX_TEST_HITS_PER_KEYWORD:
            break
    return hits


def _gather_test_evidence(title: str) -> tuple[str, bool]:
    """Build a grep evidence block scoped to test files.

    Returns ``(block, has_regression_class)``. The block is a rendered
    string ready to drop into the classifier prompt. ``has_regression_class``
    is True when any matching declaration is a ``TestSomethingRegression``
    class or a describe/it/test block whose name contains the substring
    "regression" plus at least one task keyword. That pattern is our
    gold standard for "this bug is locked in with a passing test".
    """
    keywords = _keywords_from_title(title)
    if not keywords:
        return "(no keywords extracted from title)", False

    chunks: list[str] = []
    has_regression_class = False
    lower_keywords = [k.lower() for k in keywords]

    for root in _TEST_SCAN_ROOTS:
        for keyword in keywords:
            hits = _run_test_grep(keyword, root)
            if not hits:
                continue
            chunks.append(f"# test grep '{keyword}' in {root}")
            chunks.extend(hits)
            for hit in hits:
                hit_lower = hit.lower()
                if "regression" not in hit_lower:
                    continue
                # Only count it if at least one of the task keywords also
                # appears in the same line, so an unrelated regression
                # test elsewhere in the repo does not flip the flag.
                if any(k in hit_lower for k in lower_keywords):
                    has_regression_class = True

    if not chunks:
        return "(no test file hits for the title keywords)", False
    return "\n".join(chunks), has_regression_class


def _build_prompt(
    task: dict,
    evidence: str,
    test_evidence: str,
    has_regression_class: bool,
    other_titles: list[str],
) -> tuple[str, str]:
    """Build the (system, user) classifier prompt."""
    system = (
        "You are a task auditor. You receive one open task, a slice of "
        "the project's codebase that matches its keywords, a slice of "
        "test file declarations that match its keywords, and a short "
        "list of other task titles. Decide whether the task is already "
        "done, is a duplicate, is no longer relevant, is a real-life "
        "chore that belongs outside the tracker, or needs a human to "
        "look at it.\n\n"
        "Your verdict is a RECOMMENDATION. The user reviews every task "
        "by hand before anything is closed. Do not hedge just because "
        "you are unsure, a confident verdict makes review faster. "
        "Nothing you return will auto close a task, so give your best "
        "call and explain it in one short sentence.\n\n"
        "You MUST respond with a single JSON object on one line and "
        "nothing else. The shape is exactly:\n"
        '{"verdict": "completed|duplicate|archived|skipped_irl|review", '
        '"evidence": "one short sentence", '
        '"duplicate_of": "task id or null"}\n\n'
        "Rules:\n"
        "- Test file evidence is the strongest completion signal. A "
        "regression test class or describe block whose name matches the "
        "task keywords means the bug was caught and locked in with a "
        "passing test. If such evidence exists, the verdict should be "
        "completed unless there is direct contradictory evidence.\n"
        "- Use completed when the evidence names a real file path or "
        "code symbol that clearly implements the task, or when a matching "
        "regression test class exists.\n"
        "- Use duplicate only when another task in the titles list "
        "describes the same work. Put that task id in duplicate_of.\n"
        "- Use archived only when the task references a removed or "
        "rewritten subsystem.\n"
        "- Use skipped_irl when the task is a chore like 'buy groceries', "
        "'call mom', 'book flights', or any non code errand.\n"
        "- Use review only when you are truly unsure.\n"
        "- Never include markdown, explanations, or extra keys."
    )

    title = task.get("title", "") or ""
    description = (task.get("description") or "").strip() or "(none)"
    others_block = (
        "\n".join(f"- {line}" for line in other_titles[:30])
        or "(no other tasks)"
    )
    regression_note = (
        "A matching regression test class or describe block was found. "
        "Treat this as the strongest possible completion signal."
        if has_regression_class
        else "No matching regression test class was found."
    )

    user = (
        f"Task title: {title}\n"
        f"Task description: {description}\n\n"
        f"Codebase evidence from grep:\n{evidence}\n\n"
        f"Test file evidence:\n{test_evidence}\n"
        f"Regression signal: {regression_note}\n\n"
        f"Other open task titles:\n{others_block}\n\n"
        "Respond with only the JSON object."
    )
    return system, user


async def _classify_with_claude(
    task: dict,
    evidence: str,
    test_evidence: str,
    has_regression_class: bool,
    other_titles: list[str],
) -> str:
    """Call Anthropic and return the raw text reply.

    Returns an empty string when no API key is configured or the call
    fails. Tests patch this function at the module level so no real
    network traffic is ever generated.
    """
    api_key = await _resolve_api_key("anthropic_api_key")
    if not api_key:
        return ""
    client = anthropic.AsyncAnthropic(api_key=api_key)
    system, user = _build_prompt(
        task, evidence, test_evidence, has_regression_class, other_titles
    )
    try:
        response = await client.messages.create(
            model=_CLASSIFIER_MODEL,
            max_tokens=256,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:
        logger.warning("task_audit classifier error: %s", exc)
        return ""
    if not response.content:
        return ""
    for block in response.content:
        if getattr(block, "type", "") == "text":
            return getattr(block, "text", "") or ""
    return ""


def _parse_verdict(raw: str) -> dict[str, Any]:
    """Parse the classifier JSON reply.

    Returns a dict with keys ``verdict``, ``evidence``, ``duplicate_of``.
    Any malformed or missing fields degrade to a review verdict with a
    plain language evidence note, so the UI shows the user something they
    can act on rather than crashing.
    """
    fallback = {
        "verdict": "review",
        "evidence": "classifier output could not be parsed",
        "duplicate_of": None,
    }
    if not raw:
        return fallback
    text = raw.strip()
    # The model sometimes wraps its reply in a code fence despite the
    # instructions. Strip a simple fenced block when present.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return fallback
    if not isinstance(data, dict):
        return fallback
    verdict = data.get("verdict")
    if verdict not in _ALLOWED_VERDICTS:
        return fallback
    evidence = data.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        evidence = "(no evidence provided)"
    duplicate_of = data.get("duplicate_of")
    if not isinstance(duplicate_of, str) or not duplicate_of.strip():
        duplicate_of = None
    return {
        "verdict": verdict,
        "evidence": evidence.strip(),
        "duplicate_of": duplicate_of,
    }


def _plain_reason_label(verdict: str, duplicate_of: Optional[str]) -> str:
    """Return a short plain language label for a review entry."""
    if verdict == "completed":
        return "This looks already done"
    if verdict == "duplicate":
        if duplicate_of:
            return f"This looks like a duplicate of {duplicate_of}"
        return "This looks like a duplicate of another task"
    if verdict == "archived":
        return "This may no longer be relevant"
    if verdict == "skipped_irl":
        return "This looks like a real-life chore"
    return "Unsure, please confirm"


async def run_audit_job(job_id: str) -> None:
    """Walk every open task and classify it. Never closes anything.

    The caller starts this on a background asyncio task. The job state
    is mutated in-place in the ``_jobs`` dict so polling GETs can see
    live progress.

    Invariant (see :data:`AUDIT_REVIEW_ONLY_INVARIANT`): the audit
    never auto closes anything. Every proposed outcome requires Tori to
    approve it one by one in the review list. This function proposes
    verdicts into the review list. It must never call ostk.close_task.
    Closing lives only in the /approve endpoint, which the user drives
    one task at a time from the review UI.
    """
    # Keep the invariant live at runtime so it shows up in logs and stack
    # traces. A future reader skimming this function will see it without
    # chasing imports.
    logger.debug("task_audit invariant: %s", AUDIT_REVIEW_ONLY_INVARIANT)

    state = _jobs.get(job_id)
    if state is None:
        return

    try:
        tasks = await ostk.list_tasks(status="open")
    except OstkError as exc:
        state["status"] = "failed"
        state["results"]["errors"].append(
            {"task_id": "", "message": f"Could not load tasks: {exc}"}
        )
        return

    state["total"] = len(tasks)
    all_titles = [
        f"{t.get('id', '')}: {t.get('title', '')}"
        for t in tasks
        if t.get("id") and t.get("title")
    ]
    now = datetime.now(timezone.utc)

    for task in tasks:
        task_id = task.get("id", "") or ""
        try:
            if _is_recently_touched(task, now):
                state["results"]["review"].append({
                    "task_id": task_id,
                    "reason_guess": "recently_touched",
                    "reason_label": "You just touched this, please confirm",
                    "evidence": "Task was created or updated in the last day.",
                    "confidence": "low",
                    "title": task.get("title", ""),
                })
                continue

            # Build evidence and other titles for the prompt.
            other_titles = [
                line for line in all_titles if not line.startswith(f"{task_id}:")
            ]
            title = task.get("title", "")
            evidence = _gather_evidence(title)
            test_evidence, has_regression_class = _gather_test_evidence(title)
            raw = await _classify_with_claude(
                task,
                evidence,
                test_evidence,
                has_regression_class,
                other_titles,
            )
            verdict_data = _parse_verdict(raw)
            verdict = verdict_data["verdict"]

            if verdict == "skipped_irl":
                state["results"]["skipped_irl"] += 1
                continue

            if verdict in {"completed", "duplicate", "archived"}:
                # The service never auto closes. The UI asks the user to
                # approve each proposed close. Proposals live in review
                # so the human stays in the loop.
                state["results"]["review"].append({
                    "task_id": task_id,
                    "reason_guess": verdict,
                    "reason_label": _plain_reason_label(
                        verdict, verdict_data.get("duplicate_of")
                    ),
                    "evidence": verdict_data["evidence"],
                    "duplicate_of": verdict_data.get("duplicate_of"),
                    "confidence": "medium",
                    "title": task.get("title", ""),
                })
                continue

            # review verdict or any unknown bucket lands here.
            state["results"]["review"].append({
                "task_id": task_id,
                "reason_guess": "review",
                "reason_label": _plain_reason_label("review", None),
                "evidence": verdict_data["evidence"],
                "confidence": "low",
                "title": task.get("title", ""),
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("task_audit: error on task %s", task_id)
            state["results"]["errors"].append({
                "task_id": task_id,
                "message": str(exc),
            })
        finally:
            state["checked"] += 1

    state["status"] = "done"


def _reset_state_for_tests() -> None:
    """Clear the jobs dict. Only meant to be called from the test suite."""
    _jobs.clear()
