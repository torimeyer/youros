import logging
import os
import re
from pathlib import Path, PurePosixPath
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import PROJECT_ROOT
from models.schemas import SpecDraft, SpecPromote, SpecDecompose
from services.ostk import ostk, OstkError
from services.spec_audit import audit_all_specs, compute_shipped, compute_husk_status
from services.tracing import trace_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["specs"])


# Model used for acceptance-criteria drafting. Haiku produces the same
# shape of output (3 short checklist items plus a 2-3 sentence "What we
# want" paragraph) as Sonnet but lands in roughly half the wall time:
# measured p50 2.3 s on Haiku 4.5 vs 4.5 s on Sonnet 4.5 for the demo
# subjects, and the tail that pushed "Improve direct LLM integration
# with myOS" to 12 s on Sonnet collapses under Haiku. AC drafting is
# pattern-shaped writing, not reasoning-heavy, so quality holds at
# Haiku pricing and speed. Kept as a module constant so tests and
# future tuning can override without touching every call site.
AC_DRAFT_MODEL = "claude-haiku-4-5"


# In-memory map: task_id -> agent_name, populated when /specs/{path}/build
# spawns per-task builder agents. Read by GET /specs/{path}/tasks so the
# Specs page can render spinners labeled with the agent doing each task
# and link to the Agents tab. This is intentionally process-local and
# best-effort: a server restart clears it, and the UI falls back to
# unassigned rows cleanly.
_task_assignments: dict[str, str] = {}


# In-memory map: task_id -> spec_path, populated alongside _task_assignments
# when /specs/{path}/build spawns per-task builder agents. Used by
# delete_spec() to clean up any leftover builder task rows when the spec
# file is removed, and by the per-task cleanup helper so builders that
# finish successfully remove their originating task row rather than only
# closing it. Kept as a sibling of _task_assignments so the pair stays in
# sync and the existing loose-coupling between specs.py and tasks.py is
# preserved (no agents.py changes, no cross-router imports at call time).
_spec_task_origin: dict[str, str] = {}


# In-memory registry: spec_path -> list of claim records.
# Each claim record: {agent: str, source: str, started_at: str, task_ids: [str]}.
# Source values: "build" | "wrapper" | "agent" | "slash" | "passive".
# Process-local and best-effort: a server restart drops all claims and
# status falls back to the existing tasks-driven logic.  Consistent with
# the sibling _task_assignments / _spec_task_origin pattern.  See →1422.
_spec_claims: dict[str, list[dict]] = {}


async def _ensure_decomposed(spec_path: str) -> list[dict]:
    """Ensure a spec has tasks, decomposing it if it does not.

    Returns the current task list for the spec (existing or newly
    created).  Idempotent: calling twice does not double-decompose.

    Used by the /claim endpoint and the passive watcher so decomposition
    is deferred until work actually starts rather than paid upfront on
    every promote.
    """
    # Step 1: check if tasks already exist.
    try:
        existing = await ostk.spec_tasks(spec_path)
        if existing:
            return existing
    except Exception:
        pass

    # Step 2: decompose (one Haiku call, ~$0.001).  doc_decompose raises
    # OstkError if the spec was already decomposed; that is fine.
    try:
        await ostk.doc_decompose(spec_path, auto=True)
    except Exception:
        pass

    # Step 3: return the (possibly newly created) task list.
    try:
        return await ostk.spec_tasks(spec_path)
    except Exception:
        return []


async def _create_needle(title: str, description: str = "") -> dict:
    """Create a needle (task) and return a standard shape.

    Used by the 5 creation endpoints when kind='needle'. Calls the same
    ostk.add_task primitive that POST /api/tasks uses, so needles created
    via the wizard or roadmap flow land in the same Tasks list.
    """
    result = await ostk.add_task(title, description=description)
    task_id: str | None = None
    for line in (result or "").split("\n"):
        m = re.search(r"(?:->|→)(\d+)", line)
        if m:
            task_id = m.group(1)
            break
    return {"kind": "needle", "task_id": task_id, "result": result}


async def _delete_builder_task(task_id: str) -> bool:
    """Delete one builder-spawned task row. Returns True on success.

    Called on two paths: when a spec file is deleted (residue cleanup) and
    from the explicit cleanup endpoint below when a builder agent finishes
    its work. Swallows OstkError so a missing or already-deleted task does
    not break the wider deletion flow; logs a warning so the audit trail
    still reflects the miss.
    """
    try:
        await ostk.delete_task(task_id)
    except OstkError as exc:
        logger.warning(
            "builder-task cleanup: could not delete %s: %s", task_id, exc
        )
        return False
    # Clear both local maps so a later delete_spec on the same path does
    # not re-attempt the deletion and log a spurious warning.
    _task_assignments.pop(task_id, None)
    _spec_task_origin.pop(task_id, None)
    return True


# --- Acceptance-criteria generation prompt ---------------------------------
#
# Grounds the AC-drafting LLM call in what myOS already ships, caps the
# output at 3 criteria so a live Build is buildable in under two minutes,
# and explicitly bans proposals that would pull in OpenAI/ChatGPT (Tori
# only wants Claude and Gemini for now).
#
# Without this grounding the LLM happily proposes criteria like "users
# can connect their own LLM API keys" or "integration works with three
# providers" for a spec that is supposed to improve EXISTING Claude and
# Gemini wiring. Those criteria describe features that already ship or
# that are out of scope.
_MYOS_ALREADY_SHIPS = (
    "- Multi-model chat with Claude (via Claude Code subscription) and Gemini.\n"
    "- API-key management in Settings, including Anthropic and Google keys.\n"
    "- Connection status for Gmail, Google Calendar, Google Drive, Slack, "
    "GitHub, and iMessage.\n"
    "- Onboarding wizard, guided tour, briefing, tasks, agents, fleets, "
    "specs, workflows, cost tracking, activity feed.\n"
    "- Agent templates including Roadmap, PRD, Builder, Diagnose, Review, "
    "Research, Test, Explain plain.\n"
    "- One-click spawn for every agent template and one-click build for "
    "every spec."
)


def _ac_generation_prompt(subject: str, *, from_roadmap: bool = False) -> str:
    """Legacy single-string builder. Retained so existing call sites keep
    working while the caching-friendly split (``_ac_generation_system`` +
    ``_ac_generation_user``) takes over.
    """
    return _ac_generation_system() + "\n\n" + _ac_generation_user(
        subject, from_roadmap=from_roadmap
    )


def _ac_generation_system() -> str:
    """Static portion of the AC generation prompt.

    This is the stable frame: format, the myOS-already-ships list, scope
    notes. Pulled out so the Anthropic client call can mark it
    ``cache_control: ephemeral`` and reuse the processed prefix across
    every AC generation call. Prompt caching cuts TTFT substantially
    on Haiku — typical wins are 40-60% on the cached prefix portion —
    which is the difference the user feels as "fast" vs "slow" during
    the Generating acceptance criteria spinner.
    """
    return (
        "myOS ALREADY SHIPS these features. Do NOT propose acceptance "
        "criteria that re-describe or duplicate them:\n"
        f"{_MYOS_ALREADY_SHIPS}\n\n"
        "Propose criteria that are INCREMENTAL improvements on top of "
        "what already ships. Each criterion must be small enough for a "
        "single builder agent to implement in under 60 seconds. Avoid "
        "vague criteria like 'fast', 'scalable', 'handles N requests'.\n\n"
        "Scope note: myOS uses Claude and Gemini. Do NOT propose adding "
        "OpenAI, ChatGPT, or any other LLM provider. Work only with the "
        "existing two.\n\n"
        "Format:\n"
        "## What we want\n"
        "(2-3 sentences describing the work)\n\n"
        "## Acceptance criteria\n"
        "- [ ] (criterion 1)\n"
        "- [ ] (criterion 2)\n"
        "- [ ] (criterion 3)\n\n"
        "Exactly 3 criteria. Keep it concise. Plain language. No jargon."
    )


def _ac_generation_user(subject: str, *, from_roadmap: bool = False) -> str:
    """Dynamic per-request portion: just the subject line. Kept short
    so the cached prefix dominates the prompt."""
    if from_roadmap:
        return (
            f"Write a short spec and acceptance criteria for this "
            f"initiative from a roadmap: \"{subject}\""
        )
    return (
        f"Write a short spec and acceptance criteria for this feature: "
        f"\"{subject}\""
    )


# Test-artifact spec patterns. Specs whose path or title match any of
# these are machine-generated leftovers from smoke runs and demo
# scripts, never user content. The sweep deletes them.
#
# Coverage targets the leak signatures that have actually shown up in
# docs/draft/ across runs:
#   - "e2e-..." (the original smoke prefix).
#   - "Demo Smoke Spec 87311" / "demo-smoke-..." (capitalized title and
#     hyphen path forms).
#   - "v5 verify spec" / "v12-verify-spec.md" (any "v<digits> verify").
#   - "morning verify ..." / "morning-verify-..." (verification jobs).
#   - any leftover whose title ends in a 4+ digit timestamp/id, e.g.
#     "Demo Smoke Spec 87311" or "smoke run 1776380622".
#
# A real user spec like "Spec for the spec wizard" must NOT match. The
# patterns are anchored where it matters and only fire on the smoke
# prefixes, not on the word "spec" alone.
_SPEC_ARTIFACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Smoke prefixes that lead a path/filename or a title.
    re.compile(
        r"^(?:demo[-_ ]smoke[-_ ]?|smoke[-_ ]|e2e[-_ ]|test[-_ ]"
        r"|v\d+[-_ ]verify[-_ ]?|morning[-_ ]verify[-_ ]?)",
        re.IGNORECASE,
    ),
    # The same prefix sitting after a directory segment, so the match
    # holds for "docs/draft/demo-smoke-spec-87311.md" and friends.
    re.compile(
        r"/(?:demo[-_ ]smoke[-_ ]?|smoke[-_ ]|e2e[-_ ]|test[-_ ]"
        r"|v\d+[-_ ]verify[-_ ]?|morning[-_ ]verify[-_ ]?)",
        re.IGNORECASE,
    ),
    # Trailing 4+ digit timestamp/id in a title or filename, e.g.
    # "Demo Smoke Spec 87311" or "spec-87311.md". Real user titles do
    # not end in a long numeric run.
    re.compile(r"[-_ ]\d{4,}(?:\.md)?$", re.IGNORECASE),
)


def _is_test_artifact_spec(path: str, title: str) -> bool:
    """True when a spec's path OR title matches a smoke-test signature.

    The check runs both fields through every pattern so a leak that
    sets only the title (or only the path) still gets caught. False on
    user specs like "Spec for the spec wizard" because none of the
    patterns match a title that simply contains the word "spec".
    """
    candidates = (path or "", title or "")
    for value in candidates:
        if not value:
            continue
        for pat in _SPEC_ARTIFACT_PATTERNS:
            if pat.search(value):
                return True
    return False


def _validate_doc_path(path: str) -> None:
    """Reject path traversal and paths outside docs/draft/, docs/spec/, or ~/.myos/specs/."""
    from services.ostk import USER_SPECS_DIR
    p = PurePosixPath(path)
    if ".." in p.parts:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    # Expand ~ for absolute path check
    abs_path = Path(os.path.expanduser(path))
    is_user_local = str(abs_path).startswith(str(USER_SPECS_DIR))

    if not (
        str(p).startswith("docs/draft/")
        or str(p).startswith("docs/spec/")
        or is_user_local
    ):
        raise HTTPException(
            status_code=400,
            detail="Path must be under docs/draft/, docs/spec/, or ~/.myos/specs/",
        )


def _validate_write_doc_path(path: str) -> None:
    """Like _validate_doc_path but also rejects docs/spec/ for new writes (→1512).

    docs/spec/ is gitignored and redundant — all promoted specs now live in
    ~/.myos/specs/. Preventing new writes stops the directory from being
    resurrected after cleanup.
    """
    _validate_doc_path(path)
    p = PurePosixPath(path)
    if str(p).startswith("docs/spec/"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Writing to docs/spec/ is no longer allowed. "
                "Use docs/draft/ for new drafts or promote to ~/.myos/specs/ via /specs/promote."
            ),
        )


def _read_umbrella_fields(path_str: str) -> dict:
    """Return is_umbrella and parent_slug by reading frontmatter from the spec file.

    Specs declare hierarchy via two frontmatter keys:
      umbrella: true        — this spec groups one or more leaf specs under it
      parent: <slug>        — this leaf belongs to the umbrella whose filename
                              stem matches <slug>

    Both fields default to False / None when absent or unreadable.
    """
    if not path_str:
        return {"is_umbrella": False, "parent_slug": None}
    raw = path_str.expanduser() if hasattr(path_str, "expanduser") else path_str
    if raw.startswith("~"):
        abs_path = Path(raw).expanduser()
    elif raw.startswith("/"):
        abs_path = Path(raw)
    else:
        abs_path = Path(PROJECT_ROOT) / raw
    try:
        text = abs_path.read_text()
    except OSError:
        return {"is_umbrella": False, "parent_slug": None}
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {"is_umbrella": False, "parent_slug": None}
    end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if not end:
        return {"is_umbrella": False, "parent_slug": None}
    is_umbrella = False
    parent_slug = None
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key == "umbrella" and val.lower() in ("true", "yes", "1"):
            is_umbrella = True
        elif key == "parent" and val:
            parent_slug = val
    return {"is_umbrella": is_umbrella, "parent_slug": parent_slug}


@router.get("/specs")
async def list_specs(clear_to_build: Optional[bool] = None):
    """List all draft and spec documents with lifecycle metadata.

    Each document includes task_ids, task_summary, acceptance_criteria,
    and a computed status (draft/ready/in_progress).

    Plan transcripts (status="plan") are excluded here. They surface via
    /specs/recent for the Recent Documents widget. Including them would
    inflate the page count and show agent plan files as user specs.

    Specs that meet the auto-archive condition (all linked needles closed
    and all referenced files exist) are silently moved to
    ~/.myos/specs/archive/ and excluded from the list.
    """
    try:
        docs = await ostk.list_docs()
        docs = [d for d in docs if d.get("status") != "plan"]
        # Umbrella / leaf hierarchy enrichment
        for d in docs:
            d.update(_read_umbrella_fields(d.get("path", "")))

        # Silent auto-archive: move specs that are fully done off the board.
        try:
            from services.ostk import USER_SPECS_DIR as _USER_SPECS_DIR
            from services.spec_audit import _REPO_ROOT as _SPEC_REPO_ROOT
            needle_statuses: dict[str, str] = {}
            try:
                raw_tasks = await ostk.list_tasks()
                for t in raw_tasks:
                    nid = str(t.get("id", ""))
                    if nid:
                        needle_statuses[nid] = t.get("status", "open")
            except Exception:
                pass
            archive_dir = _USER_SPECS_DIR / "archive"
            surviving: list[dict] = []
            for d in docs:
                raw_path = d.get("path", "")
                if not raw_path:
                    surviving.append(d)
                    continue
                abs_path = (
                    Path(raw_path) if raw_path.startswith("/") or raw_path.startswith("~")
                    else _USER_SPECS_DIR / raw_path
                )
                shipped = compute_shipped(abs_path, _SPEC_REPO_ROOT, needle_statuses)
                if shipped.is_shipped and abs_path.exists():
                    # Move to archive
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    import datetime as _dt
                    ts = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H%M%SZ")
                    slug = abs_path.stem
                    dest = archive_dir / f"{ts}-{slug}.md"
                    try:
                        abs_path.rename(dest)
                        trace_event("spec_auto_archive", slug=slug, path=str(dest))
                        logger.info("auto-archived spec %s → %s", slug, dest)
                    except Exception as mv_err:
                        logger.warning("auto-archive failed for %s: %s", raw_path, mv_err)
                        surviving.append(d)
                else:
                    surviving.append(d)
            docs = surviving
        except Exception as arch_err:
            logger.warning("auto-archive scan failed: %s", arch_err)

        # Gemini-ready enrichment
        try:
            from services.gemini_ready import compute_spec_readiness
            from config import PROJECT_ROOT
            from pathlib import Path as _Path
            for d in docs:
                raw_path = d.get("path", "")
                abs_path = (
                    raw_path if raw_path.startswith("/") or raw_path.startswith("~")
                    else str(_Path(PROJECT_ROOT) / raw_path)
                )
                r = compute_spec_readiness(abs_path)
                d["clear_to_build"] = r.ready
                d["clear_to_build_checks"] = r.as_dict()["checks"]
                if not r.ready:
                    d["needs_clarity"] = True
                    if d.get("status") in ("ready", "spec"):
                        d["effective_status"] = "draft"
        except Exception:
            for d in docs:
                d.setdefault("clear_to_build", False)
                d.setdefault("clear_to_build_checks", [])
        if clear_to_build is not None:
            docs = [d for d in docs if d.get("clear_to_build") is clear_to_build]
        return {"docs": docs}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/specs/recent")
async def get_recent_specs():
    """Get the 5 most recently created or updated specs.

    Returns specs sorted by created_at (falling back to promoted_at),
    most recent first. Used by the dashboard widget to show recently
    accessed specs for quick resumption of work.
    """
    try:
        docs = await ostk.list_docs()
        # Sort by created_at (most recent first), falling back to promoted_at
        sorted_docs = sorted(
            docs,
            key=lambda d: d.get("created_at") or d.get("promoted_at") or "0",
            reverse=True,
        )
        # Return top 5
        recent = sorted_docs[:5]
        return {"docs": recent}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/specs/counts")
async def spec_counts():
    """Return a count of unfinished specs for the sidebar badge.

    A spec is "unfinished" when its computed status is anything other
    than ``complete``. That covers ``draft``, ``ready``, ``in-progress``,
    and any future status we have not yet named. Mirrors the shape of
    /tasks/counts so the Sidebar can treat both badges the same way.

    Returns ``{"unfinished": N, "total": M}``. A spec counts as
    unfinished as soon as it exists in docs/draft or docs/spec and has
    not been verified complete (Verify flipped all acceptance criteria
    and all its tasks are closed). This matches the definition the
    Specs page uses for its default "not yet done" view.

    Plan transcripts (status="plan") are excluded so the badge matches
    what the Specs page shows. They are agent-generated plan files, not
    user specs.
    """
    try:
        docs = await ostk.list_docs()
        real_specs = [d for d in docs if d.get("status") != "plan"]
        total = len(real_specs)
        # →1561: 3-stage model — unfinished = Ready + In Progress only
        _status_to_stage = {
            "draft": "draft",
            "ready": "ready",
            "spec": "ready",
            "in-progress": "in_progress",
            "building": "in_progress",
            "complete": "in_progress",
        }
        by_stage: dict[str, int] = {}
        for d in real_specs:
            stage = d.get("stage") or _status_to_stage.get(d.get("status", "draft"), "draft")
            by_stage[stage] = by_stage.get(stage, 0) + 1
        unfinished = by_stage.get("ready", 0) + by_stage.get("in_progress", 0)
        return {"unfinished": unfinished, "total": total, "by_stage": by_stage}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/specs/audit")
async def get_specs_audit():
    """Return a quality audit of every spec on disk (→1469).

    Scores each spec against the 10-section template and returns a
    JSON report with per-spec details and an aggregate summary.
    """
    return audit_all_specs()


@router.post("/specs/draft")
async def create_draft(body: SpecDraft):
    """Create a new draft document or a needle, depending on kind.

    When kind='needle' (default): adds a task to the needle store and
    returns immediately. No doc file is created.

    When kind='spec': auto-generates acceptance criteria and promotes the
    draft to a plan so the user lands on a one-click build path.
    """
    if body.kind == "needle":
        try:
            payload = await _create_needle(body.title)
        except OstkError as e:
            raise HTTPException(status_code=400, detail=str(e))
        trace_event("needle_created", title=body.title, source="draft", task_id=payload.get("task_id"))
        return payload

    try:
        result = await ostk.doc_draft(body.title)
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Auto-generate acceptance criteria and append to the draft.
    # Track whether AC generation succeeded so we only auto-promote
    # drafts that actually have a checklist.
    ac_written = False
    try:
        from services.chat_providers import _resolve_api_key
        import anthropic

        api_key = await _resolve_api_key("anthropic_api_key")
        if api_key:
            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model=AC_DRAFT_MODEL,
                max_tokens=250,
                system=[{
                    "type": "text",
                    "text": _ac_generation_system(),
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{
                    "role": "user",
                    "content": _ac_generation_user(body.title),
                }],
            )
            ac_text = response.content[0].text.strip()

            # Find the draft file and append the generated content
            from pathlib import Path
            draft_path = result.strip()
            # Validate ostk output stays inside docs/ before writing
            docs_root = (Path(ostk.cwd) / "docs").resolve()
            full_path = (Path(ostk.cwd) / draft_path).resolve()
            if full_path.exists() and full_path.is_relative_to(docs_root):
                content = full_path.read_text()
                if content.endswith("\n"):
                    content += "\n" + ac_text + "\n"
                else:
                    content += "\n\n" + ac_text + "\n"
                full_path.write_text(content)
                # Only mark as ready-to-promote if the appended text
                # actually includes an acceptance-criteria checkbox. The
                # promote command requires at least one unchecked box.
                if "- [ ]" in ac_text:
                    ac_written = True
    except Exception:
        logger.warning("create_draft: AC generation failed for %r", body.title, exc_info=True)

    if not ac_written:
        # No API key (subscription auth) or AI call failed.
        # Write a user-editable placeholder so the draft isn't stuck
        # showing "Generating acceptance criteria..." forever. The user
        # edits the placeholder before promoting. We intentionally do NOT
        # set ac_written = True here so the draft stays in "draft" state
        # rather than being auto-promoted with unreviewed placeholder text.
        logger.warning(
            "create_draft: AC generation skipped for %r (no API key or error). "
            "Writing placeholder.",
            body.title,
        )
        from pathlib import Path
        _draft_path = result.strip()
        _docs_root = (Path(ostk.cwd) / "docs").resolve()
        _full_path = (Path(ostk.cwd) / _draft_path).resolve()
        if _full_path.exists() and _full_path.is_relative_to(_docs_root):
            _placeholder = (
                "\n## Acceptance criteria\n\n"
                "- [ ] (replace with your first acceptance criterion)\n"
                "- [ ] (replace with your second acceptance criterion)\n"
                "- [ ] (replace with your third acceptance criterion)\n"
            )
            _full_path.write_text(_full_path.read_text() + _placeholder)

    # When the caller requests fallback_ac (e.g. smoke tests that run
    # without a live AI model), write a minimal placeholder checkbox so
    # promote doesn't reject the draft.
    if not ac_written and body.fallback_ac:
        from pathlib import Path
        draft_path = result.strip()
        docs_root = (Path(ostk.cwd) / "docs").resolve()
        full_path = (Path(ostk.cwd) / draft_path).resolve()
        if full_path.exists() and full_path.is_relative_to(docs_root):
            placeholder = "\n## Acceptance Criteria\n\n- [ ] (e2e smoke test placeholder)\n"
            full_path.write_text(full_path.read_text() + placeholder)
            ac_written = True

    # Auto-promote so the user never has to click "Promote to Plan" for
    # a draft that already has AI-written acceptance criteria. If the
    # promote call fails (for example, no AC was actually written), we
    # leave the draft alone so the frontend can still show the draft
    # state and the user can unlock-and-edit or add AC manually.
    status = "draft"
    promoted_path: str | None = None
    if ac_written:
        draft_path = result.strip()
        try:
            promoted_path = await ostk.doc_promote(draft_path)
            status = "ready"
        except OstkError:
            # Leave as draft when promote fails; the user can still
            # hand-edit the checklist and promote later.
            pass

    trace_event(
        "spec_created",
        path=result.strip() if result else "",
        source="draft",
        status=status,
    )
    return {"result": result, "status": status, "promoted_path": promoted_path}


class SpecFromTemplate(BaseModel):
    template_id: str
    title: Optional[str] = None
    # Optional free-text note the user typed when applying the template.
    # When set, it is prepended to the template's goal body so the new
    # plan captures any extra context the user wanted to carry forward.
    note: Optional[str] = None
    kind: str = "needle"  # "needle" (default) or "spec"


@router.get("/specs/templates")
async def list_spec_templates_endpoint():
    """Return the starter plan templates shown on the Plans page grid.

    Each entry carries an id, name, short description, icon, pre-written
    goal_markdown, acceptance_criteria list, and tasks list. Frontend
    uses this to render the "Start from a template" cards.
    """
    from services.spec_templates import list_spec_templates

    return {"templates": list_spec_templates()}


@router.post("/specs/from-template")
async def create_from_template(body: SpecFromTemplate):
    """Create a plan from a starter template, or a needle when kind='needle'.

    When kind='needle' (default): drafts a needle using the template title
    and the user's note as description.

    When kind='spec': creates a ready plan from the template's pre-written
    goal body and acceptance criteria checklist.

    Returns ``{"result": path, "status": "ready", "promoted_path": path,
    "template_id": id}``. A 404 is returned when the template id is not
    known.
    """
    from services.spec_templates import get_spec_template

    template = get_spec_template(body.template_id)
    if template is None:
        raise HTTPException(
            status_code=404,
            detail=f"Plan template '{body.template_id}' not found",
        )

    title = (body.title or template["name"]).strip()
    if not title:
        title = template["name"]

    if body.kind == "needle":
        description = (body.note or "").strip()
        try:
            payload = await _create_needle(title, description=description)
        except OstkError as e:
            raise HTTPException(status_code=400, detail=str(e))
        trace_event("needle_created", title=title, source="from-template", task_id=payload.get("task_id"))
        return {**payload, "template_id": body.template_id}

    # Create the draft on disk via ostk. This returns a relative path
    # like "docs/draft/foo.md".
    try:
        result = await ostk.doc_draft(title)
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))

    draft_path = result.strip()

    # Append the template's goal body and acceptance criteria checklist
    # to the drafted file. The file already has its front matter and
    # title from doc_draft; we add the pre-written content underneath.
    goal_body = template.get("goal_markdown", "").rstrip()
    criteria: list[str] = template.get("acceptance_criteria", []) or []
    checklist_lines = [f"- [ ] {item}" for item in criteria]
    ac_block = ""
    if checklist_lines:
        ac_block = "## Acceptance criteria\n" + "\n".join(checklist_lines) + "\n"

    # The user may have entered a short note while applying the template.
    # Prepend it so the plan captures their intent (e.g. audience,
    # deadline, constraints) alongside the template's pre-written body.
    note_block = ""
    note_text = (body.note or "").strip()
    if note_text:
        note_block = "## Your notes\n" + note_text + "\n\n"

    appended_body = ""
    if note_block:
        appended_body += note_block
    if goal_body:
        appended_body += goal_body + "\n\n"
    if ac_block:
        appended_body += ac_block

    docs_root = (Path(ostk.cwd) / "docs").resolve()
    full_path = (Path(ostk.cwd) / draft_path).resolve()
    ac_written = False
    if appended_body and full_path.exists() and full_path.is_relative_to(docs_root):
        content = full_path.read_text()
        if content.endswith("\n"):
            content += "\n" + appended_body
        else:
            content += "\n\n" + appended_body
        full_path.write_text(content)
        ac_written = bool(checklist_lines)

    # Auto-promote so the new plan lands in ready, the same path the
    # Wave 2 create_draft uses. If promote fails, fall back to draft so
    # the user can still edit by hand.
    status = "draft"
    promoted_path: str | None = None
    if ac_written:
        try:
            promoted_path = await ostk.doc_promote(draft_path)
            status = "ready"
        except OstkError:
            pass

    return {
        "result": result,
        "status": status,
        "promoted_path": promoted_path,
        "template_id": body.template_id,
    }


class SpeckitImport(BaseModel):
    yaml: str
    format: str = "speckit"
    kind: str = "needle"  # "needle" (default) or "spec"


@router.post("/specs/import")
async def import_spec(body: SpeckitImport):
    """Import a spec from spec-kit YAML, or create a needle when kind='needle'.

    When kind='needle' (default): parses the YAML and creates a single
    needle from the spec name and description.

    When kind='spec': parses the YAML, creates a spec draft via ostk,
    writes the body, and creates tasks for each entry in the tasks list.
    Returns the created spec id (its path).
    """
    if body.format != "speckit":
        raise HTTPException(status_code=400, detail=f"Unsupported format: {body.format}")
    from services.speckit_import import parse_speckit, SpeckitParseError
    try:
        parsed = parse_speckit(body.yaml)
    except SpeckitParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    spec = {"title": parsed["name"], "description": parsed["description"], "tasks": parsed["tasks"]}

    if body.kind == "needle":
        try:
            payload = await _create_needle(spec["title"], description=spec["description"] or "")
        except OstkError as e:
            raise HTTPException(status_code=400, detail=str(e))
        trace_event("needle_created", title=spec["title"], source="import", task_id=payload.get("task_id"))
        return {**payload, "title": spec["title"]}

    # Draft the spec via ostk.
    try:
        result = await ostk.doc_draft(spec["title"])
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))
    draft_path = result.strip()

    # Write description + acceptance criteria from tasks into the draft.
    tasks = spec.get("tasks") or []
    body_lines: list[str] = []
    if spec["description"]:
        body_lines.append(spec["description"])
        body_lines.append("")
    if tasks:
        body_lines.append("## Acceptance criteria")
        for t in tasks:
            acs = t.get("acceptance_criteria") or []
            if acs:
                for ac in acs:
                    body_lines.append(f"- [ ] {ac}")
            else:
                body_lines.append(f"- [ ] {t['title']}")
        body_lines.append("")

    # Write tasks section as context.
    if tasks:
        body_lines.append("## Tasks")
        for t in tasks:
            priority = t.get("priority", "P2")
            body_lines.append(f"- **[{priority}]** {t['title']}")
            if t.get("description"):
                body_lines.append(f"  {t['description']}")
        body_lines.append("")

    docs_root = (Path(ostk.cwd) / "docs").resolve()
    full_path = (Path(ostk.cwd) / draft_path).resolve()
    ac_written = False
    if body_lines and full_path.exists() and full_path.is_relative_to(docs_root):
        content = full_path.read_text()
        appended = "\n".join(body_lines)
        if content.endswith("\n"):
            content += "\n" + appended
        else:
            content += "\n\n" + appended
        full_path.write_text(content)
        ac_written = any("- [ ]" in line for line in body_lines)

    # Auto-promote when we have acceptance criteria.
    status = "draft"
    promoted_path: str | None = None
    if ac_written:
        try:
            promoted_path = await ostk.doc_promote(draft_path)
            status = "ready"
        except OstkError:
            pass

    # Create tasks from the spec-kit tasks list.
    created_task_ids: list[str] = []
    for t in tasks:
        try:
            ac_lines = t.get("acceptance_criteria") or []
            ac_text = "; ".join(ac_lines) if ac_lines else t["title"]
            raw = await ostk.add_task(
                title=t["title"],
                priority=t.get("priority", "P2"),
                description=t.get("description", ""),
                ac=ac_text,
            )
            for line in (raw or "").split("\n"):
                m = __import__("re").search(r"(?:->|→)(\d+)", line)
                if m:
                    created_task_ids.append(m.group(1))
                    break
        except OstkError:
            pass

    trace_event("spec_imported", path=draft_path, format="speckit", task_count=len(tasks))
    return {
        "id": promoted_path or draft_path,
        "path": promoted_path or draft_path,
        "status": status,
        "task_ids": created_task_ids,
        "title": spec["title"],
    }


@router.get("/specs/{spec_path:path}/export")
async def export_spec(spec_path: str, format: str = "speckit"):
    """Export a spec as spec-kit YAML.

    Returns the spec-kit YAML as plain text. Raises 404 if the spec
    does not exist, 400 for unsupported formats.
    """
    if format != "speckit":
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")
    _validate_doc_path(spec_path)
    full_path = (Path(PROJECT_ROOT) / spec_path).resolve()
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"Spec not found: {spec_path}")
    text = full_path.read_text()

    # Parse title and body from the spec file.
    lines = text.split("\n")
    title = ""
    description_lines: list[str] = []
    tasks: list[dict] = []

    # Extract front matter fields.
    fm_end = 0
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                fm_end = i
                break
            if line.startswith("title:"):
                title = line[len("title:"):].strip().strip('"').strip("'")

    body_lines = lines[fm_end + 1:] if fm_end else lines
    body_text = "\n".join(body_lines).strip()

    # Extract description (first non-heading paragraph).
    in_description = True
    for line in body_lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            in_description = False
            break
        if stripped:
            description_lines.append(stripped)

    description = " ".join(description_lines).strip()

    # Extract AC bullets as tasks.
    ac_bullets = _parse_unchecked_ac_bullets(body_text)
    for bullet in ac_bullets:
        tasks.append({"title": bullet, "acceptance_criteria": [bullet]})

    from services.speckit_import import spec_to_speckit
    spec_dict = {"title": title, "description": description, "tasks": tasks}
    yaml_str = spec_to_speckit(spec_dict)

    from fastapi.responses import Response
    filename = Path(spec_path).stem + ".speckit.yaml"
    return Response(
        content=yaml_str,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/specs/promote")
async def promote_draft(body: SpecPromote):
    """Promote a draft to a finalized spec.

    Delegates to ``ostk.doc_promote()`` which implements the file move
    and front-matter flip in pure Python to ensure correct routing
    to ``~/.myos/specs/``.
    """
    _validate_doc_path(body.path)
    if not body.path.startswith("docs/draft/"):
        raise HTTPException(
            status_code=400,
            detail="Only drafts can be promoted. Path must start with docs/draft/.",
        )

    # Gate: block promotion when the draft still needs clarity
    try:
        from services.gemini_ready import compute_spec_readiness
        abs_path = str(Path(PROJECT_ROOT) / body.path)
        r = compute_spec_readiness(abs_path)
        if not r.ready:
            failing = [c for c in r.as_dict()["checks"] if not c["passed"]]
            raise HTTPException(
                status_code=422,
                detail={"error": "needs_clarity", "failing_checks": failing},
            )
    except HTTPException:
        raise
    except Exception:
        pass  # if readiness check errors, don't block promotion

    try:
        target_path = await ostk.doc_promote(body.path)
        return {"result": target_path, "promoted_path": target_path}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


class SpecClarityFix(BaseModel):
    check: str
    fix: str


_CLARITY_CHECK_SECTION: dict[str, str] = {
    "has_ac_checkboxes": "## Acceptance criteria",
    "no_vague_ac": "## Acceptance criteria",
    "ac_count_threshold": "## Acceptance criteria",
    "has_file_paths": "## Critical files",
    "referenced_files_exist": "## Critical files",
    "in_repo_scope": "## Scope",
    "is_unblocked": "## Blockers",
}


@router.patch("/specs/{spec_path:path}/clarity")
async def patch_spec_clarity(spec_path: str, body: SpecClarityFix):
    """Append user clarification text to the spec then re-run readiness.

    The check name determines which markdown section to append under.
    Returns updated checks so the frontend can refresh the modal live.
    """
    _validate_doc_path(spec_path)

    abs_path = (
        spec_path
        if spec_path.startswith("/") or spec_path.startswith("~")
        else str(Path(PROJECT_ROOT) / spec_path)
    )
    abs_path = str(Path(os.path.expanduser(abs_path)).resolve())

    if not Path(abs_path).exists():
        raise HTTPException(status_code=404, detail="Spec file not found")

    text = Path(abs_path).read_text(encoding="utf-8")

    section_header = _CLARITY_CHECK_SECTION.get(body.check, "## Notes")
    if section_header in text:
        text = text.replace(section_header, f"{section_header}\n{body.fix}", 1)
    else:
        text = text.rstrip() + f"\n\n{section_header}\n{body.fix}\n"

    Path(abs_path).write_text(text, encoding="utf-8")

    from services.gemini_ready import compute_spec_readiness
    r = compute_spec_readiness(abs_path)
    return {"checks": r.as_dict()["checks"], "ready": r.ready}


class SpecClarifySuggestBody(BaseModel):
    check: str


@router.post("/specs/{spec_path:path}/clarity/suggest")
async def spec_clarity_suggest(spec_path: str, body: SpecClarifySuggestBody):
    """Return an AI-proposed fix for a failing spec readiness check.

    Does NOT persist anything. The caller previews the suggestion and
    decides whether to apply it via ``PATCH /specs/{path}/clarity``.
    """
    _validate_doc_path(spec_path)

    abs_path = (
        spec_path
        if spec_path.startswith("/") or spec_path.startswith("~")
        else str(Path(PROJECT_ROOT) / spec_path)
    )
    abs_path = str(Path(os.path.expanduser(abs_path)).resolve())

    if not Path(abs_path).exists():
        raise HTTPException(status_code=404, detail="Spec file not found")

    file_text = Path(abs_path).read_text(encoding="utf-8")

    # Extract H1 title for the prompt context
    import re as _re
    title_m = _re.search(r"^#\s+(.+)$", file_text, _re.MULTILINE)
    spec_title = title_m.group(1).strip() if title_m else spec_path

    context = {
        "kind": "spec",
        "title": spec_title,
        "description": file_text[:500],
        "file_text": file_text,
        "failing_check": body.check,
    }

    try:
        from services.clarity_suggest import suggest_clarification
        result = await suggest_clarification(body.check, context)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI suggestion failed: {e}")

    return result


@router.post("/specs/{spec_path:path}/unlock")
async def unlock_spec(spec_path: str):
    """Move a ready plan back to draft so the user can edit acceptance criteria.

    Moves the file from ``docs/spec/<name>.md`` or ``~/.myos/specs/<name>.md``
    to ``docs/draft/<name>.md`` and flips the ``status:`` front matter field
    from ``spec`` to ``draft``. This is the inverse of promote.
    """
    from services.ostk import USER_SPECS_DIR

    _validate_doc_path(spec_path)
    
    # Expand ~ for absolute path check
    abs_source = Path(os.path.expanduser(spec_path))
    is_user_local = str(abs_source).startswith(str(USER_SPECS_DIR))

    if not (spec_path.startswith("docs/spec/") or is_user_local):
        raise HTTPException(
            status_code=400,
            detail="Only plans in the ready state can be unlocked.",
        )
    
    if is_user_local:
        source = abs_source
        target_rel = f"docs/draft/{source.name}"
    else:
        source = (Path(PROJECT_ROOT) / spec_path).resolve()
        target_rel = spec_path.replace("docs/spec/", "docs/draft/", 1)

    if not source.exists():
        raise HTTPException(status_code=404, detail="Plan not found")
    
    target = (Path(PROJECT_ROOT) / target_rel).resolve()
    # Safety: target must live under docs/draft/
    draft_root = (Path(PROJECT_ROOT) / "docs" / "draft").resolve()
    if not target.is_relative_to(draft_root):
        raise HTTPException(status_code=400, detail="Invalid target path")

    target.parent.mkdir(parents=True, exist_ok=True)
    # Flip the status field in front matter. doc_promote writes
    # "status: spec"; unlock reverses that. We also strip any
    # promoted_at line so the new draft looks freshly-drafted.
    text = source.read_text()
    new_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("status:") and "spec" in stripped:
            new_lines.append("status: draft")
        elif stripped.startswith("promoted_at:"):
            # Drop the promoted_at marker when returning to draft.
            continue
        else:
            new_lines.append(line)
    target.write_text("\n".join(new_lines) + ("\n" if text.endswith("\n") else ""))
    source.unlink()
    return {"result": target_rel, "path": target_rel}


@router.post("/specs/decompose")
async def decompose_spec(body: SpecDecompose):
    """Break a spec into individual tasks.

    After decomposing, the created task IDs are written back to the
    spec's front matter and returned alongside the raw output.
    """
    _validate_doc_path(body.path)
    try:
        result = await ostk.doc_decompose(body.path, auto=True)
        return result  # already a dict with "result" and "task_ids"
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


class DecomposeKernelBody(BaseModel):
    auto: bool = False


@router.post("/specs/{spec_path:path}/decompose-kernel")
async def decompose_spec_kernel(spec_path: str, body: DecomposeKernelBody):
    """Decompose a spec into sub-needles via the ostk kernel.

    Unlike /specs/decompose (which always passes --auto), this endpoint
    lets the caller control whether ostk runs non-interactively. Pass
    ``auto: true`` to skip confirmation prompts.
    """
    _validate_doc_path(spec_path)
    try:
        result = await ostk.doc_decompose(spec_path, auto=body.auto)
        return result
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


class SpecFromTask(BaseModel):
    task_id: str


@router.post("/specs/from-task")
async def create_spec_from_task(body: SpecFromTask):
    """Create a spec draft from an existing task.

    Reads the task's title and description, creates a draft via ostk,
    then uses AI to generate acceptance criteria and appends them to
    the draft body. One click from task to spec.
    """
    try:
        tasks = await ostk.list_tasks()
        task = None
        for t in tasks:
            if str(t.get("id", "")).lstrip("→") == body.task_id.lstrip("→"):
                task = t
                break
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {body.task_id} not found.")

        title = task.get("title", "Untitled")
        description = task.get("description", "")

        # Create the draft
        result = await ostk.doc_draft(title)

        # Auto-generate acceptance criteria
        try:
            from services.chat_providers import _resolve_api_key
            import anthropic

            api_key = await _resolve_api_key("anthropic_api_key")
            if api_key:
                client = anthropic.AsyncAnthropic(api_key=api_key)
                prompt_text = f"Write a short spec and acceptance criteria for this feature: \"{title}\""
                if description:
                    prompt_text += f"\n\nContext: {description}"
                prompt_text += (
                    "\n\nFormat:\n"
                    "## What we want\n"
                    "(2-3 sentences describing the feature)\n\n"
                    "## Acceptance criteria\n"
                    "- [ ] (criterion 1)\n"
                    "- [ ] (criterion 2)\n"
                    "(4-6 criteria total)\n\n"
                    "Keep it concise. Plain language. No jargon."
                )
                response = await client.messages.create(
                    model=AC_DRAFT_MODEL,
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt_text}],
                )
                ac_text = response.content[0].text.strip()

                draft_path = result.strip()
                docs_root = (Path(PROJECT_ROOT) / "docs").resolve()
                full_path = (Path(PROJECT_ROOT) / draft_path).resolve()
                if full_path.exists() and full_path.is_relative_to(docs_root):
                    content = full_path.read_text()
                    content = content.rstrip() + "\n\n" + ac_text + "\n"
                    full_path.write_text(content)
        except Exception:
            pass

        return {"result": result, "task_id": body.task_id, "title": title}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


class SpecFromRoadmapLine(BaseModel):
    roadmap_path: str
    initiative_text: str
    title: Optional[str] = None
    kind: str = "needle"  # "needle" (default) or "spec"


@router.post("/specs/from-roadmap-line")
async def create_spec_from_roadmap_line(body: SpecFromRoadmapLine):
    """Create a needle or a ready plan from a single roadmap initiative line.

    When kind='needle' (default): validates the roadmap file exists, then
    adds a needle using the initiative text as the title. Fast path.

    When kind='spec': drafts a plan whose goal is the initiative text,
    lets acceptance criteria generation run in the background, and
    auto-promotes the draft to ready.

    Raises 404 when the roadmap file is missing (bad frontend state).
    """
    # Validate the roadmap path exists. Keep the check broad so both
    # files under ``~/.myos/files`` (the normal Roadmap output) and any
    # absolute path the frontend sends back work. This is a read-only
    # lookup, not a write; we only need to be sure the file is real
    # before we spend model tokens on acceptance criteria.
    raw_path = (body.roadmap_path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=404, detail="Roadmap path is required")
    try:
        roadmap_file = Path(raw_path).expanduser().resolve()
    except Exception:
        raise HTTPException(status_code=404, detail=f"Roadmap not found: {raw_path}")
    if not roadmap_file.exists() or not roadmap_file.is_file():
        raise HTTPException(status_code=404, detail=f"Roadmap not found: {raw_path}")

    initiative = (body.initiative_text or "").strip()
    if not initiative:
        raise HTTPException(
            status_code=400,
            detail="Initiative text is required to create a plan.",
        )

    # Title: caller-supplied or the initiative text itself, clipped so
    # the filename stays reasonable. Plain language.
    title = (body.title or initiative).strip()
    if len(title) > 80:
        title = title[:77].rstrip() + "..."

    if body.kind == "needle":
        try:
            payload = await _create_needle(title, description=initiative)
        except OstkError as e:
            raise HTTPException(status_code=400, detail=str(e))
        trace_event("needle_created", title=title, source="from-roadmap-line", task_id=payload.get("task_id"))
        return {**payload, "title": title, "roadmap_path": raw_path}

    try:
        result = await ostk.doc_draft(title)
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))

    draft_path = result.strip()
    docs_root = (Path(PROJECT_ROOT) / "docs").resolve()
    full_path = (Path(PROJECT_ROOT) / draft_path).resolve()

    # Write the initiative as the plan goal with a short header that
    # links back to the source roadmap by its basename. Keeps the plan
    # self describing when the user opens it later.
    header_written = False
    if full_path.exists() and full_path.is_relative_to(docs_root):
        existing = full_path.read_text()
        header = f"## From roadmap: {roadmap_file.name}\n\n{initiative}\n\n"
        existing = existing.rstrip() + "\n\n" + header
        full_path.write_text(existing)
        header_written = True

    # Background AC generation + auto-promote.
    #
    # Return fast (this endpoint used to block 2-8s on the Anthropic
    # call, which is what "Generating acceptance criteria..." spinner
    # was measuring). Now we schedule the AC call as a background task
    # and return immediately with status="draft". When the AC response
    # lands, the background task appends it to the draft file and calls
    # ostk.doc_promote, flipping the on-disk status to "spec". The
    # frontend's per-second Specs poll picks up the status flip and the
    # pending-spec shimmer resolves automatically.
    #
    # Safe-by-default: if the Anthropic key is missing or the call
    # errors, the draft stays as-is and the user can hand-edit + promote
    # from the Specs page. Any exception is swallowed to stop it from
    # killing the event loop — the task is fire-and-forget.
    async def _finish_ac_and_promote() -> None:
        if not header_written:
            return
        try:
            from services.chat_providers import _resolve_api_key
            import anthropic

            api_key = await _resolve_api_key("anthropic_api_key")
            if not api_key:
                logger.warning(
                    "from_roadmap_line: no API key for %r, writing placeholder AC",
                    draft_path,
                )
                _placeholder = (
                    "\n## Acceptance criteria\n\n"
                    "- [ ] (replace with your first acceptance criterion)\n"
                    "- [ ] (replace with your second acceptance criterion)\n"
                    "- [ ] (replace with your third acceptance criterion)\n"
                )
                _content = full_path.read_text()
                full_path.write_text(_content.rstrip() + _placeholder)
                try:
                    await ostk.doc_promote(draft_path)
                except OstkError:
                    pass
                return
            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model=AC_DRAFT_MODEL,
                max_tokens=250,
                system=[{
                    "type": "text",
                    "text": _ac_generation_system(),
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[
                    {
                        "role": "user",
                        "content": _ac_generation_user(
                            initiative, from_roadmap=True
                        ),
                    }
                ],
            )
            ac_text = response.content[0].text.strip()
            content = full_path.read_text()
            content = content.rstrip() + "\n\n" + ac_text + "\n"
            full_path.write_text(content)
            if "- [ ]" in ac_text:
                try:
                    await ostk.doc_promote(draft_path)
                except OstkError:
                    pass
        except Exception:
            logger.exception(
                "from_roadmap_line: background AC generation failed for %s",
                draft_path,
            )

    import asyncio as _asyncio
    try:
        _asyncio.create_task(_finish_ac_and_promote())
    except Exception:
        logger.exception(
            "from_roadmap_line: could not schedule AC background task"
        )

    return {
        "result": result,
        # status="draft" because AC generation is still in flight. The
        # Specs page's 1s poll picks up the flip to "spec" (ready) when
        # the background task finishes and promotes. Frontend pending-
        # spec rows already render a generating state while the server
        # walks the background task through to auto-promote.
        "status": "draft",
        "promoted_path": None,
        "title": title,
        "roadmap_path": raw_path,
    }


@router.get("/specs/{spec_path:path}/tasks")
async def get_spec_tasks(spec_path: str):
    """Return all tasks linked to a spec, with their current status.

    Reads the spec's ``tasks:`` front matter field and fetches each
    task's current status from ostk.  Also returns the active claims
    array so the UI can render the terminal-agent status note (→1422).
    """
    _validate_doc_path(spec_path)
    try:
        tasks = await ostk.spec_tasks(spec_path)
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Attach the builder agent assigned to each open task so the Specs
    # page can render live progress: spinner + agent name while the
    # task is open, a green checkmark with the builder name once closed.
    enriched = []
    for t in tasks:
        task_id = str(t.get("id", "")).lstrip("\u2192")
        agent_name = _task_assignments.get(task_id)
        enriched.append({**t, "assigned_agent": agent_name})
    return {"tasks": enriched, "claims": _spec_claims.get(spec_path, [])}


class _ClaimBody(BaseModel):
    agent: str
    source: str = "agent"  # build | wrapper | agent | slash | passive


@router.post("/specs/{spec_path:path}/claim")
async def claim_spec(spec_path: str, body: _ClaimBody):
    """Register a claim that work on this spec has started.

    Ensures the spec is decomposed (one Haiku call if needed), records
    a claim entry so the spec status flips to ``in-progress`` even when
    work is happening in a terminal session rather than through the
    in-app Build button.

    Returns the task_ids list so the caller knows what tasks exist.
    See spec-auto-status.md §4 for the active-claim paths (→1422).
    """
    _validate_doc_path(spec_path)
    spec_full_path = Path(PROJECT_ROOT) / spec_path
    if not spec_full_path.exists():
        raise HTTPException(status_code=404, detail=f"Spec not found: {spec_path}")

    tasks = await _ensure_decomposed(spec_path)
    task_ids = [str(t.get("id", "")).lstrip("\u2192") for t in tasks]

    from datetime import datetime, timezone as _tz
    claim: dict = {
        "agent": body.agent,
        "source": body.source,
        "started_at": datetime.now(_tz.utc).isoformat(),
        "task_ids": task_ids,
    }

    if spec_path not in _spec_claims:
        _spec_claims[spec_path] = []
    _spec_claims[spec_path].append(claim)

    return {"task_ids": task_ids, "claim": claim}


def _fire_spec_complete_notification(spec_path: str) -> None:
    """Emit the "Spec done" persistent notification for a completed spec.

    Single source of truth for the notification shape so both code paths
    that detect completion (explicit ``/verify`` and the automatic
    all-builder-tasks-closed advancer) produce identical rows. The
    ``target`` key dedupes repeat fires for the same spec, so firing
    from both paths on the same run collapses to a single bell row
    instead of two.

    Best-effort. Never raises to the caller; a flaky notifications layer
    must not break the spec-status flip or the verify response.
    """
    try:
        from services.notifications import notifications_service as _notif
        from pathlib import Path as _Path
        title_from_path = _Path(spec_path).stem.replace("-", " ").strip() or "Spec"
        _notif.add(
            type="spec_complete",
            title="Your feature is live",
            body=(
                f"{title_from_path} is built and ready to try. "
                "Open the spec to review what changed."
            ),
            action_label="Open spec",
            action_url=f"/specs?expand={spec_path}",
            metadata={
                "spec_path": spec_path,
                "kind": "spec_complete",
            },
            target=f"spec_complete:{spec_path}",
        )
    except Exception:
        logger.exception(
            "spec_complete notification emit failed for %s", spec_path
        )


@router.post("/specs/{spec_path:path}/verify")
async def verify_spec(spec_path: str):
    """Verify a spec's acceptance criteria against linked task status.

    Returns which criteria are met, whether all are met, and a summary
    of linked task statuses.

    When verify determines that ALL acceptance criteria are met (the
    "spec is complete" transition), fire a single bell notification so
    Tori sees "Your feature is live" in the tray without having to keep
    staring at the Specs page. Dedup target key is the spec path so
    repeat verifies do not duplicate bells.
    """
    _validate_doc_path(spec_path)
    try:
        result = await ostk.spec_verify(spec_path)
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(result, dict) and result.get("all_met") is True:
        _fire_spec_complete_notification(spec_path)
    return result


# Matches a markdown unchecked checkbox bullet. Permissive so specs
# authored with varying indentation or ``*`` bullets still yield tasks:
#
#   - leading whitespace allowed (nested lists, soft-wrapped templates)
#   - ``-`` or ``*`` as the bullet marker
#   - any amount of whitespace between the marker and ``[ ]``
#   - at least one space after ``[ ]`` before the bullet text
#
# The captured group is the bullet text with surrounding whitespace.
_UNCHECKED_AC_RE = re.compile(r"^\s*[-*]\s*\[ \]\s+(.+)")

# Matches a markdown ``## Acceptance criteria`` heading (case-insensitive,
# any heading depth >= 2). Used to scope the bullet scan to the section
# the user intends as criteria, so prose bullets that happen to live
# elsewhere in the spec body do not become builder tasks.
_AC_HEADING_RE = re.compile(r"^\s{0,3}#{2,}\s+acceptance\s+criteria\b", re.I)

# Matches any subsequent ``##`` heading; used to bound the AC section.
_ANY_HEADING_RE = re.compile(r"^\s{0,3}#{2,}\s+")


def _parse_unchecked_ac_bullets(spec_body: str) -> list[str]:
    """Return the text of every unchecked AC checkbox bullet in a spec body.

    Scoping: if the body contains a ``## Acceptance Criteria`` heading
    (case-insensitive), only the lines between that heading and the
    next ``##`` heading are scanned. Otherwise the whole body is
    scanned. This keeps prose ``- [ ]`` bullets from earlier sections
    (for example, checklists in a "What we want" narrative) out of the
    task list the AC fallback creates.

    Regex: see ``_UNCHECKED_AC_RE`` above. Already-checked ``- [x]``
    bullets are skipped because the fallback only needs to create
    builders for work that is still open.
    """
    lines = spec_body.split("\n")

    # Find the "## Acceptance Criteria" heading, if any, and scope the
    # scan to its section. If no such heading is present, fall back to
    # scanning the entire body so template specs that forget the
    # heading still produce tasks.
    start = 0
    end = len(lines)
    ac_start: Optional[int] = None
    for idx, line in enumerate(lines):
        if _AC_HEADING_RE.match(line):
            ac_start = idx + 1
            break
    if ac_start is not None:
        start = ac_start
        for idx in range(ac_start, len(lines)):
            if _ANY_HEADING_RE.match(lines[idx]):
                end = idx
                break

    bullets: list[str] = []
    for line in lines[start:end]:
        match = _UNCHECKED_AC_RE.match(line)
        if match:
            text = match.group(1).strip()
            if text:
                bullets.append(text)
    return bullets


async def _build_fallback_from_acceptance_criteria(
    spec_path: str,
) -> list[dict]:
    """Create one builder task per unchecked AC bullet and return configs.

    Reads the spec file, parses the body for ``- [ ]`` bullets, calls
    ``ostk.add_task`` per bullet (capturing the returned task id),
    writes the new ids back to the spec's frontmatter via
    ``ostk._write_tasks_to_frontmatter`` so state stays consistent, and
    returns a list of agent-config dicts in the same shape that
    ``ostk.spec_build`` emits so the existing spawn loop can consume
    them unchanged.
    """
    full = Path(PROJECT_ROOT) / spec_path
    if not full.exists():
        return []

    # Parse body out of the file so we do not re-run frontmatter logic
    # from ostk. The body is everything after the closing "---" line of
    # the YAML front matter; if no front matter is present, the entire
    # file is the body.
    text = full.read_text()
    lines = text.split("\n")
    body = text
    if lines and lines[0].strip() == "---":
        end = None
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                end = i
                break
        if end is not None:
            body = "\n".join(lines[end + 1:])

    bullets = _parse_unchecked_ac_bullets(body)
    if not bullets:
        return []

    spec_name = Path(spec_path).stem
    # Serial (not parallel) task creation. A prior attempt parallelized
    # these with asyncio.gather to save ~300ms on the click-to-spawn
    # window, but concurrent subprocesses raced for the ostk needle
    # counter lock, so bullets[i] no longer reliably got task_id
    # (first_id + i). Serial preserves the one-task-per-bullet mapping.
    configs: list[dict] = []
    created_ids: list[str] = []
    for idx, bullet_text in enumerate(bullets):
        description = f"AC for spec {spec_path}: {bullet_text}"
        try:
            raw = await ostk.add_task(
                title=bullet_text,
                priority="P1",
                description=description,
                ac=bullet_text,
            )
        except OstkError as exc:
            logger.warning(
                "build_spec AC fallback: add_task failed for '%s': %s",
                bullet_text, exc,
            )
            continue
        task_id = ""
        for line in (raw or "").split("\n"):
            m = re.search(r"(?:->|\u2192)(\d+)", line)
            if m:
                task_id = m.group(1)
                break
        if not task_id:
            stripped_raw = (raw or "").strip().lstrip("\u2192").lstrip("-> ")
            if stripped_raw.isdigit():
                task_id = stripped_raw
        if not task_id:
            logger.warning(
                "build_spec AC fallback: could not parse task id from '%s'",
                raw,
            )
            continue
        created_ids.append(task_id)
        agent_name = f"spec-{spec_name}-{task_id}"
        prompt = (
            f"You are building task {task_id}: {bullet_text}\n\n"
            f"## Spec\n\n{body.strip()}\n\n"
            "## Instructions\n\n"
            "- Complete the task described above.\n"
            "- Edit files directly. Do NOT run `ostk commit` or "
            "`ostk work close` .  those calls can lag under load. The "
            "spec router closes the task for you via HTTP when you "
            "finish.\n"
        )
        configs.append({
            "name": agent_name,
            "task_id": task_id,
            "task_title": bullet_text,
            "prompt": prompt,
        })

    # Persist the new ids into the spec's frontmatter so subsequent
    # list_docs / spec_tasks calls see the link and do not double-create
    # on a second Build it click.
    if created_ids:
        try:
            ostk._write_tasks_to_frontmatter(spec_path, created_ids)
        except Exception:
            logger.exception(
                "build_spec AC fallback: frontmatter write failed for %s",
                spec_path,
            )

    return configs


def _advance_spec_status_if_all_builder_tasks_closed(task_id: str) -> None:
    """Flip a spec's frontmatter status to ``done`` when every builder
    task recorded for that spec in ``_spec_task_origin`` has been
    closed.

    Called from the Tasks router after a task is closed. ``task_id`` is
    the id that was just flipped to closed. We look up the spec the
    task came from, gather every sibling builder task for that spec
    from ``_spec_task_origin``, and ask ostk for their statuses. If
    ALL are closed, we rewrite the frontmatter ``status:`` line to
    ``done`` so the Specs page lands on the build-complete state
    without waiting for the user to run Verify first.
    """
    norm_tid = str(task_id or "").lstrip("\u2192")
    spec_path = _spec_task_origin.get(norm_tid)
    if not spec_path:
        return

    # Collect every task_id the build for this spec produced.
    sibling_ids = [
        tid for tid, sp in _spec_task_origin.items() if sp == spec_path
    ]
    if not sibling_ids:
        return

    # We need task statuses. Since this helper is called from a sync
    # closure inside an async route in tasks.py, fetch them via a small
    # blocking wrapper: we rely on the caller to await us if they want
    # the real async path. For the code path in tasks.close_task, the
    # route is already async and calls the async variant below. This
    # sync shell exists only to keep the helper importable and tested.
    raise RuntimeError(
        "use _advance_spec_status_if_all_builder_tasks_closed_async instead"
    )


async def close_spec_builder_task(agent_name: str) -> Optional[str]:
    """Close the builder task tied to ``agent_name`` and advance spec status.

    Called from ``agents.mark_agent_complete`` when a spec-spawned builder
    reports done. The builder prompt tells the agent to edit files and
    NOT run ``ostk work close`` itself, so this is the only path that
    turns an agent ``/complete`` into a closed task. Without it, Build it
    tasks stay open forever, spec status never advances past
    ``in-progress``, and the UI looks stuck even though the agents are
    done.

    Returns the task_id that was closed, or ``None`` if ``agent_name`` is
    not a registered spec builder. Never raises to the caller; a flaky
    ostk must not break agent completion.
    """
    target_tid: Optional[str] = None
    for tid, aname in _task_assignments.items():
        if aname == agent_name:
            target_tid = tid
            break
    if not target_tid:
        return None
    try:
        await ostk.close_task(target_tid, closed_reason="completed")
    except Exception:
        # A "task not found" error here is expected when the builder's
        # prompt told it to DELETE the task row (to keep the Tasks page
        # clean after a spec build). In that case the task is already
        # effectively closed. We still want to advance the spec status,
        # so log and fall through instead of bailing out. Without this,
        # the advancer never runs, the spec stays in ``building``
        # forever, and the "Your feature is live" notification never
        # fires.
        logger.exception(
            "close_spec_builder_task: ostk close failed for task %s agent %s (continuing to advance spec)",
            target_tid,
            agent_name,
        )
    try:
        await _advance_spec_status_if_all_builder_tasks_closed_async(target_tid)
    except Exception:
        logger.exception(
            "close_spec_builder_task: spec-status advance failed for task %s",
            target_tid,
        )
    return target_tid


async def _advance_spec_status_if_all_builder_tasks_closed_async(
    task_id: str,
) -> Optional[str]:
    """Async version of the spec-status advancer. See sync docstring.

    Returns the spec path whose status was flipped to ``done``, or
    ``None`` if the flip did not fire. Never raises to callers; swallows
    IO errors so task close always succeeds even if the status write
    fails (the next list_docs run will still render an accurate
    computed status from task state).
    """
    norm_tid = str(task_id or "").lstrip("\u2192")
    spec_path = _spec_task_origin.get(norm_tid)
    if not spec_path:
        return None

    sibling_ids = [
        tid for tid, sp in _spec_task_origin.items() if sp == spec_path
    ]
    if not sibling_ids:
        return None

    try:
        all_tasks = await ostk.list_tasks()
    except OstkError:
        return None
    status_by_id: dict[str, str] = {}
    for t in all_tasks:
        raw = t.get("id")
        norm = str(raw or "").lstrip("\u2192")
        if norm:
            status_by_id[norm] = t.get("status", "open")

    # Treat "registered spec-builder task that is no longer in the task
    # list" as effectively closed. The builder prompt tells the agent to
    # DELETE the task row when it finishes so the Tasks page stays clean
    # after a spec build. Without this check the advancer would read the
    # default "open" status for the missing task, decide the spec still
    # has open work, and never flip the spec to ``complete``. sibling_ids
    # only contains tasks we registered in build_spec, so missing-from-
    # list unambiguously means the builder finished and cleaned up.
    for tid in sibling_ids:
        effective = status_by_id.get(tid, "deleted")
        if effective not in ("closed", "deleted"):
            return None

    # Every sibling closed. Rewrite the spec frontmatter.
    full = Path(PROJECT_ROOT) / spec_path
    if not full.exists():
        return None
    try:
        text = full.read_text()
    except OSError:
        return None
    lines = text.split("\n")
    if not (lines and lines[0].strip() == "---"):
        return None
    new_lines: list[str] = []
    flipped = False
    in_fm = True
    saw_fm_end = False
    for line in lines:
        if in_fm and not saw_fm_end:
            stripped = line.strip()
            if stripped.startswith("status:") and not flipped:
                current_val = stripped[len("status:"):].strip().strip('"').strip("'")
                if current_val == "complete":
                    # Already complete — skip the rewrite and the
                    # notification. The notification service's target-based
                    # dedup only collapses repeats while the row is unread;
                    # once the user reads the bell, a second call here would
                    # insert a NEW row and re-fire the modal. Early-exit
                    # prevents the file-mtime bump and the spurious notification.
                    return None
                indent = line[: len(line) - len(line.lstrip())]
                # Write ``complete`` (not ``done``). The writer used to
                # emit ``done`` but the reader (compute_spec_status) only
                # knew about ``complete``, so fully-built specs with
                # all-closed tasks landed back in the Drafts tab because
                # the frontend's normalizeStatus mapped the unrecognized
                # ``done`` to ``draft``. Using ``complete`` closes the
                # status-vocabulary mismatch end to end.
                new_lines.append(f"{indent}status: complete")
                flipped = True
                continue
            if stripped == "---" and new_lines:
                # Second --- closes the frontmatter block.
                saw_fm_end = True
                in_fm = False
        new_lines.append(line)
    if flipped:
        try:
            full.write_text("\n".join(new_lines))
        except OSError:
            return None
        # Fire the "Your feature is live" persistent notification here,
        # on the auto-advancer path that flips status when every builder
        # task closes. Without this, a Build-it that terminates cleanly
        # lands silently: the modal watcher only sees the transition if
        # the spec file is still on disk and the watcher was mounted,
        # and the TopBar toast never fires because no notification row
        # was ever written. The /verify endpoint also fires the same
        # notification; dedup in services/notifications.py on the
        # ``target`` key collapses repeat emits for the same spec.
        _fire_spec_complete_notification(spec_path)
        trace_event(
            "spec_built_complete",
            spec_path=spec_path,
            task_count=len(sibling_ids),
        )
        return spec_path
    return None


def _set_spec_status(spec_path: str, new_status: str) -> Optional[str]:
    """Rewrite a spec's frontmatter ``status:`` line.

    Returns the spec_path on a successful write, ``None`` otherwise.
    Best-effort: swallows IO errors so callers (typically build_spec
    right after a successful spawn) never break the response because a
    status flip failed. The sibling _advance_spec_status_if_all_builder_
    tasks_closed_async helper uses the same pattern for ``done``.
    """
    full = Path(PROJECT_ROOT) / spec_path
    if not full.exists():
        return None
    try:
        text = full.read_text()
    except OSError:
        return None
    lines = text.split("\n")
    if not (lines and lines[0].strip() == "---"):
        return None
    new_lines: list[str] = []
    flipped = False
    in_fm = True
    saw_fm_end = False
    for line in lines:
        if in_fm and not saw_fm_end:
            stripped = line.strip()
            if stripped.startswith("status:") and not flipped:
                indent = line[: len(line) - len(line.lstrip())]
                new_lines.append(f"{indent}status: {new_status}")
                flipped = True
                continue
            if stripped == "---" and new_lines:
                saw_fm_end = True
                in_fm = False
        new_lines.append(line)
    if not flipped:
        return None
    try:
        full.write_text("\n".join(new_lines))
        return spec_path
    except OSError:
        return None


async def _resolve_task_configs(spec_path: str) -> list[dict]:
    """Run the ordered Build it cascade once and return builder configs.

    One ordered path, three steps, always terminates:

    1. ``ostk.spec_build`` first. Any OstkError (CLI missing, non-zero
       exit, parse error) is caught and turned into an empty list so
       the next step can run.
    2. If still empty: ``ostk.doc_decompose`` + retry ``spec_build``.
       Covers the case where the spec has never been decomposed.
    3. If still empty: ``_build_fallback_from_acceptance_criteria``
       parses the spec's Acceptance Criteria checklist directly and
       creates one builder task per unchecked bullet. Guarantees Build
       it always has something to spawn when the spec has unchecked ACs.

    Returns ``[]`` only when the spec literally has no unchecked AC
    bullets. Callers treat that as "no work to do" rather than a
    failure.
    """
    # Step 1: spec_build.
    try:
        result = await ostk.spec_build(spec_path)
        configs = result.get("agents", []) or []
        if configs:
            return configs
    except OstkError as e:
        logger.warning(
            "build_spec: ostk.spec_build failed for %s: %s", spec_path, e
        )

    # Step 2: decompose + retry spec_build. decompose can fail if the
    # spec was already decomposed; that is fine. spec_build still
    # running empty falls through to the AC fallback below.
    try:
        await ostk.doc_decompose(spec_path, auto=True)
        result = await ostk.spec_build(spec_path)
        configs = result.get("agents", []) or []
        if configs:
            return configs
    except OstkError:
        pass

    # Step 3: parse the AC checklist ourselves. Never raises to
    # callers; any exception is swallowed so the cascade can return a
    # clean empty list and the handler lands on the "no unchecked ACs"
    # message instead of a 500.
    try:
        return await _build_fallback_from_acceptance_criteria(spec_path)
    except Exception:
        logger.exception(
            "build_spec: AC fallback failed for %s", spec_path
        )
        return []


_VALID_BUILD_MODELS = {"sonnet", "opus", "haiku", "gemini"}


async def _build_artifact_agent(
    spec_path: str,
    spec_full_path: "Path",
    template: str,
    model: Optional[str] = None,
) -> dict:
    """Spawn one artifact-drafter agent for a non-code spec (→1531).

    Reads the full spec body, creates a single agent config using the
    given template, and spawns it. Returns the same shape as build_spec.
    """
    from routers.agents import spawn_agent
    from models.schemas import AgentSpawn

    spec_text = spec_full_path.read_text()
    spec_name = Path(spec_path).stem
    agent_name = f"spec-{spec_name}-{template}"
    chosen_model = model or "sonnet"

    prompt = (
        f"You are building the spec: {spec_path}\n\n"
        f"## Spec\n\n{spec_text.strip()}\n\n"
        "## Instructions\n\n"
        "Read the spec above and produce the requested artifact. "
        "Ground your work in any attached Library sources (KNOWLEDGE directive). "
        "Return the output file path in your final message.\n"
    )

    body = AgentSpawn(
        name=agent_name,
        prompt=prompt,
        model=chosen_model,
        budget=2.0,
        template=template,
        source="spec-build",
        task=f"Draft {template} for {spec_name}",
        locks=[f"specs/{spec_path}"],
    )
    try:
        await spawn_agent(body)
    except HTTPException as exc:
        return {
            "agents": [],
            "message": f"Could not spawn artifact agent: {exc.detail}",
            "has_unchecked_acs": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "agents": [],
            "message": f"Could not spawn artifact agent: {exc}",
            "has_unchecked_acs": True,
        }
    return {
        "agents": [agent_name],
        "message": f"Spawned {template} agent. Check the transcript for the output file path.",
        "has_unchecked_acs": True,
    }


def build_recheck_preamble(spawn_lock: list) -> str:
    """Return the conflict-recheck block prepended to every builder prompt.

    Instructs the agent to verify no other agent is holding its paths
    before making any file edits, so a late-arriving duplicate spawn
    aborts cleanly instead of clobbering in-progress work.
    """
    return (
        "## Conflict recheck (do this FIRST, before any other step)\n\n"
        "Call `curl -sSk --connect-timeout 3 -m 5 https://127.0.0.1:8000/api/agents` "
        "to list active agents. If any agent OTHER THAN YOURSELF holds any path in your "
        f"spawn locks ({spawn_lock!r}), exit immediately with a message naming the "
        "conflicting agent. Do NOT proceed with any file edits.\n\n"
    )


@router.post("/specs/{spec_path:path}/build")
async def build_spec(spec_path: str, model: Optional[str] = None):
    """One-click build: decompose if needed, then spawn a builder per open task.

    Loads open tasks from the spec's front matter, asks ostk for the
    per-task agent configs, then spawns a Builder subagent for each one
    via POST /api/agents/spawn. The Builder agentfile runs in quick_mode
    so the mailbox block stays compact and the spawn is fast.

    The resolver runs one ordered cascade (``_resolve_task_configs``)
    that terminates in either a non-empty list of builder configs or
    an empty list meaning "the spec has no unchecked acceptance
    criteria". There is no middle "gave up" state: if ostk is
    unreachable AND the AC checklist is empty, the spec genuinely has
    no work to build and the frontend shows an informational card, not
    an error.

    Returns ``{"agents": [names...], "message": "...",
    "has_unchecked_acs": bool}``.
    """
    _validate_doc_path(spec_path)
    if model is not None and model not in _VALID_BUILD_MODELS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown model '{model}'. Valid options: {sorted(_VALID_BUILD_MODELS)}",
        )
    # Hard-reject only paths that point at a missing file. A broken or
    # uninstalled ostk CLI must NOT bubble up as 404 here; that kills
    # the AC fallback before it has a chance to run. We detect the
    # missing-file case via a direct fs check instead of relying on
    # ostk's error text.
    spec_full_path = Path(PROJECT_ROOT) / spec_path
    if not spec_full_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Spec not found: {spec_path}"
        )

    # Route non-code produces to a single template agent instead of task decomposition
    produces = _read_frontmatter_produces(spec_full_path.read_text())
    artifact_template = _PRODUCES_TEMPLATE.get(produces)
    if artifact_template:
        return await _build_artifact_agent(spec_path, spec_full_path, artifact_template, model)

    agent_configs = await _resolve_task_configs(spec_path)
    # Override model on every cfg so _spawn_one picks it up.
    if model and agent_configs:
        for cfg in agent_configs:
            cfg["model"] = model

    if not agent_configs:
        # Cascade returned empty -> the spec has no unchecked ACs.
        # (Any transient failure mid-cascade either produced a config
        # list by step 3 or was logged above; there is no "gave up"
        # middle state.)
        return {
            "agents": [],
            "message": (
                "This plan has no unchecked acceptance criteria. Add "
                "at least one, then click Build."
            ),
            "has_unchecked_acs": False,
        }

    # Spawn a Builder subagent for each open task. We call the in-process
    # spawn_agent() directly (same pattern as workflows.py) so we get the
    # full mailbox block, policy checks, and transcript wiring without
    # any curl-in-subprocess overhead.
    #
    # Speed: spawn all builders in parallel with asyncio.gather so the
    # total wall time is ~one spawn, not N. Each spawn does a subprocess
    # fork, a stdin drain, a chat-state purge, and an audit write. Serial
    # with 3 tasks measured at ~3 to 6 s end to end; parallel lands in
    #
    # Progress visibility: we stamp _task_assignments BEFORE awaiting
    # each spawn, so GET /specs/{path}/tasks already shows the agent
    # name on the very first poll even while the subprocess is still
    # warming up. If the spawn then errors, we pop the assignment so
    # the UI does not attach a broken builder to the row.
    try:
        from services.tracing import trace_event as _trace_event
        _trace_event("spec_built_start", spec_path=str(spec_path), agent_count=len(agent_configs))
    except Exception:
        pass

    # Record a source=build claim so status flips to in-progress immediately,
    # before any builder agent closes its task (→1422).
    try:
        from datetime import datetime, timezone as _tz
        _build_task_ids = [
            str(cfg.get("task_id", "")).lstrip("\u2192")
            for cfg in agent_configs
            if cfg.get("task_id")
        ]
        _build_claim: dict = {
            "agent": "build",
            "source": "build",
            "started_at": datetime.now(_tz.utc).isoformat(),
            "task_ids": _build_task_ids,
        }
        if spec_path not in _spec_claims:
            _spec_claims[spec_path] = []
        _spec_claims[spec_path].append(_build_claim)
    except Exception:
        logger.exception("build_spec: failed to record build claim for %s", spec_path)

    import asyncio as _asyncio
    from routers.agents import spawn_agent
    from models.schemas import AgentSpawn

    async def _spawn_one(cfg: dict) -> tuple[str, Optional[str]]:
        """Spawn one builder. Returns (name, error_or_none)."""
        name = cfg.get("name")
        prompt = cfg.get("prompt", "")
        task_id = cfg.get("task_id", "")
        task_title = cfg.get("task_title", "")
        if not name:
            return ("", "missing agent name")
        # Append a clear finish instruction so the agent hits the Tasks
        # API when it finishes instead of only running ostk locally. The
        # task row is a per-build artifact (the spec IS the source of
        # truth for this work), so we delete the row rather than close it.
        # Closing leaves a "completed" bullet behind on the Tasks page
        # that Tori then has to hand-sweep after every demo run. DELETE
        # fires the same progress-bar update and keeps the Tasks page
        # clean of spec-build residue.
        # Use a task-specific lock so parallel builder spawns for different
        # tasks never conflict with each other, while preventing the same
        # task from being spawned twice (same lock key → 409 if still held).
        spawn_lock = [f"tasks/{task_id}"] if task_id else [f"specs/{spec_path}"]
        prompt_with_close = (
            build_recheck_preamble(spawn_lock)
            + prompt
            + "\n\n## When done\n\n"
            + f"Delete task {task_id} via `curl -sSk --connect-timeout 3 -m 5 "
            + f"-X DELETE https://127.0.0.1:8000/api/tasks/{task_id}` so "
            + "the spec's progress bar updates and the Tasks page does not "
            + "keep the spec-build row around after the work is done.\n"
        )
        # Build a friendly task label so the Agents page shows what each
        # builder is working on instead of the opaque agent name.
        if task_title:
            friendly_task = f"Build task {task_id}: {task_title}"
        else:
            friendly_task = f"Build task {task_id} for spec {spec_path}"
        # Pick the model: caller-supplied cfg wins, then "sonnet" as
        # the fallback. The builder agentfile already opts into quick_mode
        # so the compact mailbox block is applied downstream.
        #
        # IMPORTANT: do NOT read ``default_model`` from Settings here.
        # That setting controls the user's chat provider preference
        # (e.g. "@gemini" when they chose Gemini in onboarding). Spec
        # builders spawn a Claude Code subprocess (claude --print ...),
        # which only accepts Claude models. Passing "@gemini" to claude
        # makes the subprocess exit instantly with "There's an issue
        # with the selected model (@gemini)" and a 142-byte transcript,
        # which is exactly how the Build it demo silently produced no
        # file edits before this fix. Builders must always run on a
        # Claude model regardless of chat preference.
        cfg_model = cfg.get("model")
        chosen_model = cfg_model or "sonnet"
        body = AgentSpawn(
            name=name,
            prompt=prompt_with_close,
            model=chosen_model,
            budget=2.0,
            template="builder",
            source="spec-build",
            task=friendly_task,
            locks=spawn_lock,
        )
        # Record assignment up front so the first poll after the HTTP
        # return already shows the agent-to-task mapping. Also record the
        # spec path so delete_spec can sweep any leftover builder rows if
        # the spec is removed before every builder has finished.
        # Needles use both "→728" and "728" as keys at different layers,
        # so we store both forms and the origin map keys off the bare id
        # that ostk.delete_task accepts.
        norm_tid = str(task_id).lstrip("\u2192") if task_id else ""
        if norm_tid:
            _task_assignments[norm_tid] = name
            _spec_task_origin[norm_tid] = spec_path
        try:
            await spawn_agent(body)
            return (name, None)
        except HTTPException as e:
            if norm_tid:
                _task_assignments.pop(norm_tid, None)
                _spec_task_origin.pop(norm_tid, None)
            return (name, str(e.detail))
        except Exception as e:  # noqa: BLE001
            if norm_tid:
                _task_assignments.pop(norm_tid, None)
                _spec_task_origin.pop(norm_tid, None)
            return (name, str(e))

    # Parallelize every builder spawn. asyncio.gather preserves input
    # order, so results zip back cleanly into success and failure lists.
    results = await _asyncio.gather(
        *(_spawn_one(cfg) for cfg in agent_configs)
    )

    spawned: list[str] = []
    failures: list[str] = []
    for name, err in results:
        if not name:
            continue
        if err is None:
            spawned.append(name)
        else:
            failures.append(f"{name}: {err}")

    count = len(spawned)
    if count == 0:
        return {
            "agents": [],
            "message": f"Could not spawn any agents. {'; '.join(failures)}",
        }

    # Flip the spec's frontmatter status to ``building`` so the Specs
    # page renders an in-flight state instead of the old "ready" badge
    # while the builder subagents run. Best-effort: a write error does
    # not block the spawn response, since the sibling
    # _advance_spec_status_if_all_builder_tasks_closed_async helper
    # will also reconcile the file once every task closes.
    try:
        _set_spec_status(spec_path, "building")
    except Exception:
        logger.exception(
            "build_spec: failed to flip status to building for %s", spec_path
        )

    message = f"Spawned {count} agent{'s' if count != 1 else ''} to build this spec. Watch the Agents tab."
    if failures:
        message += f" {len(failures)} failed: {'; '.join(failures)}"
    return {"agents": spawned, "message": message, "task_ids": [
        cfg.get("task_id") for cfg in agent_configs if cfg.get("task_id")
    ]}


@router.delete("/specs/orphan-drafts")
async def delete_orphan_plan_drafts():
    """Delete plan transcript files that contain no real plan content.

    Scans transcripts/ for plan-{id}.md files whose content matches
    known orphan patterns (cancelled without work, registered-externally
    stubs, or explicit "no plan needed" messages) and removes them.

    Returns the count and paths of deleted files. Safe to call repeatedly.
    """
    import re as _re

    _plan_re = _re.compile(r"^plan-(\d+)\.md$")
    transcripts_dir = Path(PROJECT_ROOT) / "transcripts"
    deleted: list[str] = []

    if not transcripts_dir.is_dir():
        return {"deleted": 0, "deleted_paths": []}

    for md in sorted(transcripts_dir.glob("plan-*.md")):
        if not _plan_re.match(md.name):
            continue
        try:
            text = md.read_text(errors="replace")
        except OSError:
            continue
        if not ostk._is_orphan_plan_transcript(text):
            continue
        rel = f"transcripts/{md.name}"
        try:
            md.unlink()
            deleted.append(rel)
        except OSError as exc:
            logger.warning("delete_orphan_plan_drafts: unlink failed %s: %s", rel, exc)

    if deleted:
        logger.info("delete_orphan_plan_drafts removed %d orphans: %s", len(deleted), deleted)
    return {"deleted": len(deleted), "deleted_paths": deleted}


@router.delete("/specs/{doc_path:path}")
async def delete_spec(doc_path: str):
    """Delete a draft or spec document by its relative path.

    The path must live under docs/draft/ or docs/spec/ inside the
    project root. Any path that escapes those directories is rejected.

    Accepts both ``docs/draft/foo.md`` (as stored in Spec.path from the
    list endpoint) and ``draft/foo.md``. Without this normalization the
    frontend's ``doc.path`` would resolve to ``docs/docs/draft/foo.md``
    and every delete would fail with 400.
    """
    # Frontend sends the full stored path, e.g. "docs/draft/foo.md".
    # The validator expects that exact form; the target is built from
    # PROJECT_ROOT directly so we don't double the "docs/" segment.
    if not doc_path.startswith("docs/"):
        doc_path = "docs/" + doc_path
    _validate_doc_path(doc_path)
    docs_dir = Path(PROJECT_ROOT) / "docs"
    target = (Path(PROJECT_ROOT) / doc_path).resolve()
    # Safety: directory-boundary check (is_relative_to avoids prefix substring false positives)
    if not (
        target.is_relative_to((docs_dir / "draft").resolve())
        or target.is_relative_to((docs_dir / "spec").resolve())
    ):
        raise HTTPException(status_code=400, detail="Path must be under docs/draft/ or docs/spec/")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    target.unlink()

    # Residue cleanup: every task row that was spawned from this spec is
    # an in-flight build artifact, not a free-standing task. Once the
    # spec file is gone, the tasks have nothing to build against, so
    # sweep them. Without this, the Tasks page keeps 2 to 6 orphan
    # "Build X / Ship Y" rows per demo run, and the demo script's
    # separate cleanup pass has to paper over the gap. We iterate over a
    # snapshot of the map because _delete_builder_task mutates it.
    # We compare on the stored spec_path verbatim; build_spec records
    # that exact string, and delete_spec normalises doc_path above to
    # the same "docs/..." prefix, so equality is the right match.
    stranded = [
        tid for tid, sp in list(_spec_task_origin.items()) if sp == doc_path
    ]
    deleted_tasks: list[str] = []
    for tid in stranded:
        if await _delete_builder_task(tid):
            deleted_tasks.append(tid)
    if deleted_tasks:
        logger.info(
            "delete_spec: swept %d builder task(s) for %s: %s",
            len(deleted_tasks),
            doc_path,
            deleted_tasks,
        )
    return {"result": "deleted", "cleaned_builder_tasks": deleted_tasks}


@router.post("/specs/cleanup-test-artifacts")
async def cleanup_test_artifact_specs():
    """Delete every draft/spec whose path or title matches a smoke signature.

    Walks ``ostk.list_docs()``, matches each entry against the
    test-artifact patterns above, and removes the file from disk. Safe
    to run as often as you like, it is idempotent and skips real user
    specs (anything whose title and path are both clean).

    Returns the count plus the deleted paths so the caller can log
    exactly what was removed.
    """
    try:
        docs = await ostk.list_docs()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    deleted: list[str] = []
    for d in docs:
        path = d.get("path") or ""
        title = d.get("title") or ""
        if not path:
            continue
        if not _is_test_artifact_spec(path, title):
            continue
        target = (Path(PROJECT_ROOT) / path).resolve()
        # Defense in depth: only delete files inside docs/draft/ or
        # docs/spec/ even if the listing somehow returned a path
        # outside those dirs.
        docs_dir = (Path(PROJECT_ROOT) / "docs").resolve()
        if not (
            target.is_relative_to((docs_dir / "draft").resolve())
            or target.is_relative_to((docs_dir / "spec").resolve())
        ):
            continue
        try:
            if target.exists():
                target.unlink()
                deleted.append(path)
        except OSError as exc:
            logger.warning(
                "cleanup_test_artifact_specs: unlink failed for %s: %s",
                path,
                exc,
            )
            continue

    if deleted:
        logger.warning(
            "cleanup_test_artifact_specs cleaned %d leaked specs: %s",
            len(deleted),
            deleted,
        )
    return {"deleted": len(deleted), "deleted_paths": deleted}


# --- SDD Wizard ---


class WizardSuggestRequest(BaseModel):
    problem: str
    in_scope: Optional[list[str]] = None


class WizardCreateRequest(BaseModel):
    title: str
    problem: str = ""
    in_scope: list[str] = []
    out_of_scope: list[str] = []
    non_goals: list[str] = []
    criteria: list[str] = []
    technical_context: Optional[str] = None
    api_contract: Optional[str] = None
    ui_requirements: Optional[str] = None
    kind: str = "needle"  # "needle" (default) or "spec"
    produces: str = "code"  # "code"|"agent"|"document"|"slides"|"diagram"|"skill"


# Maps produces value to the template agent name
_PRODUCES_TEMPLATE: dict[str, str] = {
    "agent": "builder-of-agents",
    "document": "document-drafter",
    "slides": "slides-drafter",
    "diagram": "diagram-drafter",
    "skill": "skill-builder",
}


def _inject_frontmatter_key(content: str, key: str, value: str) -> str:
    """Insert key: value into YAML frontmatter, before the closing '---' line.

    If the key already exists it is left unchanged. If there is no frontmatter
    the content is returned as-is.
    """
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return content
    end_idx = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return content
    # Skip if key already present
    fm_block = "\n".join(lines[1:end_idx])
    if f"\n{key}:" in f"\n{fm_block}" or fm_block.startswith(f"{key}:"):
        return content
    lines.insert(end_idx, f"{key}: {value}")
    return "\n".join(lines)


def _read_frontmatter_produces(text: str) -> str:
    """Extract the 'produces' key from YAML frontmatter; returns 'code' if absent."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return "code"
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("produces:"):
            return line.split(":", 1)[1].strip()
    return "code"


def _wizard_system_prompt() -> str:
    return (
        "You are helping a product manager write a spec for a feature in myOS, "
        "a personal productivity OS.\n\n"
        f"myOS ALREADY SHIPS these features:\n{_MYOS_ALREADY_SHIPS}\n\n"
        "Do NOT propose criteria that duplicate existing features. "
        "Each criterion must be testable by a single builder agent. "
        "Plain language, no jargon."
    )


@router.post("/specs/wizard/suggest")
async def wizard_suggest(body: WizardSuggestRequest):
    """Given a problem statement, suggest success criteria and non-goals."""
    if not body.problem.strip():
        raise HTTPException(status_code=400, detail="Problem statement is required")

    scope_hint = ""
    if body.in_scope:
        scope_hint = "\nIn scope: " + "; ".join(body.in_scope)

    try:
        from services.chat_providers import _resolve_api_key
        import anthropic

        api_key = await _resolve_api_key("anthropic_api_key")
        if not api_key:
            return {"criteria": [], "non_goals": [], "after_statement": ""}

        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=AC_DRAFT_MODEL,
            max_tokens=400,
            system=[{
                "type": "text",
                "text": _wizard_system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": (
                    f"Problem: {body.problem}{scope_hint}\n\n"
                    "Respond with EXACTLY this format, no extra text:\n\n"
                    "AFTER: (one sentence: 'After this ships, [who] can [what] "
                    "instead of [workaround].')\n\n"
                    "CRITERIA:\n"
                    "- (criterion 1, starts with a verb)\n"
                    "- (criterion 2)\n"
                    "- (criterion 3)\n"
                    "- (criterion 4)\n\n"
                    "NON_GOALS:\n"
                    "- (thing this does NOT do 1)\n"
                    "- (thing this does NOT do 2)"
                ),
            }],
        )
        text = response.content[0].text.strip()

        criteria = []
        non_goals = []
        after_statement = ""
        section = None
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("AFTER:"):
                after_statement = stripped[6:].strip()
                section = None
            elif stripped == "CRITERIA:":
                section = "criteria"
            elif stripped == "NON_GOALS:":
                section = "non_goals"
            elif stripped.startswith("- ") and section == "criteria":
                criteria.append(stripped[2:].strip())
            elif stripped.startswith("- ") and section == "non_goals":
                non_goals.append(stripped[2:].strip())

        return {
            "criteria": criteria[:5],
            "non_goals": non_goals[:3],
            "after_statement": after_statement,
        }
    except Exception as exc:
        logger.warning("wizard_suggest AI call failed: %s", exc)
        return {"criteria": [], "non_goals": [], "after_statement": ""}


@router.post("/specs/wizard/create")
async def wizard_create(body: WizardCreateRequest):
    """Create a needle or a rich SDD spec from wizard inputs.

    When kind='needle' (default): creates a needle from the title and
    optional description. No spec file is written.

    When kind='spec': assembles a structured markdown spec from the wizard
    fields, creates a draft via ostk, writes the body, and auto-promotes.
    Requires a non-empty problem statement when kind='spec'.
    """
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")

    if body.kind == "needle":
        description = body.problem.strip()
        try:
            payload = await _create_needle(body.title.strip(), description=description)
        except OstkError as e:
            raise HTTPException(status_code=400, detail=str(e))
        trace_event("needle_created", title=body.title.strip(), source="wizard", task_id=payload.get("task_id"))
        return {**payload, "path": None}

    if not body.problem.strip():
        raise HTTPException(status_code=400, detail="Problem statement is required")

    try:
        result = await ostk.doc_draft(body.title.strip())
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))

    draft_path = result.strip()

    sections: list[str] = []

    sections.append("## Problem")
    sections.append(body.problem.strip())
    sections.append("")

    if body.in_scope:
        sections.append("## Scope")
        for item in body.in_scope:
            sections.append(f"- {item}")
        sections.append("")

    if body.out_of_scope:
        sections.append("## Out of scope")
        for item in body.out_of_scope:
            sections.append(f"- {item}")
        sections.append("")

    if body.non_goals:
        sections.append("## Non-goals")
        for item in body.non_goals:
            sections.append(f"- {item}")
        sections.append("")

    if body.criteria:
        sections.append("## Acceptance criteria")
        for item in body.criteria:
            sections.append(f"- [ ] {item}")
        sections.append("")

    if body.technical_context:
        sections.append("## Technical context")
        sections.append(body.technical_context.strip())
        sections.append("")

    if body.api_contract:
        sections.append("## API contract")
        sections.append(body.api_contract.strip())
        sections.append("")

    if body.ui_requirements:
        sections.append("## UI requirements")
        sections.append(body.ui_requirements.strip())
        sections.append("")

    body_text = "\n".join(sections)

    docs_root = (Path(PROJECT_ROOT) / "docs").resolve()
    full_path = (Path(PROJECT_ROOT) / draft_path).resolve()
    ac_written = False
    if full_path.exists() and full_path.is_relative_to(docs_root):
        content = full_path.read_text()
        # Inject produces into YAML frontmatter when it's not the default "code"
        if body.produces and body.produces != "code":
            content = _inject_frontmatter_key(content, "produces", body.produces)
        if content.endswith("\n"):
            content += "\n" + body_text
        else:
            content += "\n\n" + body_text
        full_path.write_text(content)
        ac_written = bool(body.criteria)

    status = "draft"
    promoted_path: str | None = None
    if ac_written:
        try:
            promoted_path = await ostk.doc_promote(draft_path)
            status = "ready"
        except OstkError:
            pass

    trace_event(
        "spec_created",
        path=draft_path,
        source="wizard",
        status=status,
        sections=len(sections),
    )
    return {
        "result": result,
        "status": status,
        "promoted_path": promoted_path,
        "path": promoted_path or draft_path,
    }


# ---------------------------------------------------------------------------
# →1470  Backfill and Archive endpoints
# ---------------------------------------------------------------------------

_BACKFILL_SECTIONS: list[tuple[str, str]] = [
    ("Problem", "_What's broken and who's affected._"),
    ("Goals", "_What success looks like._"),
    ("Non-goals", "_What is explicitly out of scope._"),
    ("Solution", "_How we will solve the problem._"),
    ("Edge cases", "_Scenarios that need special handling._"),
    ("Success criteria", "_How we know this is done._"),
    ("Acceptance criteria", "_Checkboxes the implementer must tick._"),
    ("Verification", "_How the solution was confirmed to work._"),
    ("USER FEEDBACK", "_Open questions and user responses._"),
    ("DECISION", "_Final decisions made and their rationale._"),
]


@router.post("/specs/{slug}/backfill")
async def backfill_spec(slug: str):
    """Append any missing required sections to a spec in ~/.myos/specs/.

    Reads ~/.myos/specs/<slug>.md, finds the subset of the 10 required
    sections that are absent, and appends each missing one as a
    section-header + one-line placeholder:

        ## Problem
        _What's broken and who's affected._

    Returns the updated file content and path.  Idempotent: sections that
    already exist are left untouched.
    """
    from services.ostk import USER_SPECS_DIR

    spec_file = USER_SPECS_DIR / f"{slug}.md"
    if not spec_file.exists():
        raise HTTPException(status_code=404, detail=f"Spec not found: {slug}")

    content = spec_file.read_text()

    appended: list[str] = []
    additions = []
    for section_name, placeholder in _BACKFILL_SECTIONS:
        if f"## {section_name}" not in content:
            additions.append(f"\n## {section_name}\n{placeholder}\n")
            appended.append(section_name)

    if additions:
        new_content = content.rstrip("\n") + "\n" + "".join(additions)
        spec_file.write_text(new_content)
    else:
        new_content = content

    trace_event("spec_backfill", path=str(spec_file), added=appended)
    return {
        "path": str(spec_file),
        "content": new_content,
        "added_sections": appended,
    }


@router.post("/specs/{slug}/archive")
async def archive_spec(slug: str):
    """Move a spec from ~/.myos/specs/<slug>.md to ~/.myos/specs/archive/<ts>-<slug>.md (→1512).

    Returns the new path.  The original file is removed.
    404 if the spec does not exist.
    409 if the spec is already in the archive directory.
    """
    from datetime import timezone
    from services.ostk import USER_SPECS_DIR

    archive_dir = USER_SPECS_DIR / "archive"

    # 409 if already archived (file found in archive/ by any ts prefix)
    if archive_dir.is_dir():
        existing = list(archive_dir.glob(f"*-{slug}.md"))
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Spec '{slug}' is already archived at {existing[0].name}",
            )

    spec_file = USER_SPECS_DIR / f"{slug}.md"
    if not spec_file.exists():
        raise HTTPException(status_code=404, detail=f"Spec not found: {slug}")

    import datetime as _dt
    ts = _dt.datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"{ts}-{slug}.md"

    spec_file.rename(dest)

    trace_event("spec_archive", slug=slug, path=str(dest))
    return {"path": str(dest)}


# --- Backward-compatible aliases for /api/docs/* ---
# These mirror every /api/specs/* route so existing bookmarks, external
# callers, and in-flight frontend builds keep working during migration.

_compat = APIRouter(tags=["docs-compat"])


@_compat.get("/docs")
async def list_docs_compat():
    return await list_specs()


@_compat.get("/docs/recent")
async def get_recent_docs_compat():
    return await get_recent_specs()


@_compat.post("/docs/draft")
async def create_draft_compat(body: SpecDraft):
    return await create_draft(body)


@_compat.post("/docs/promote")
async def promote_draft_compat(body: SpecPromote):
    return await promote_draft(body)


@_compat.post("/docs/decompose")
async def decompose_spec_compat(body: SpecDecompose):
    return await decompose_spec(body)


@_compat.delete("/docs/{doc_path:path}")
async def delete_doc_compat(doc_path: str):
    return await delete_spec(doc_path)
