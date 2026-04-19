import logging
import os
import re
from pathlib import Path, PurePosixPath
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import PROJECT_ROOT
from models.schemas import SpecDraft, SpecPromote, SpecDecompose
from services.ostk import ostk, OstkError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["specs"])


# In-memory map: task_id -> agent_name, populated when /specs/{path}/build
# spawns per-task builder agents. Read by GET /specs/{path}/tasks so the
# Specs page can render spinners labeled with the agent doing each task
# and link to the Agents tab. This is intentionally process-local and
# best-effort: a server restart clears it, and the UI falls back to
# unassigned rows cleanly.
_task_assignments: dict[str, str] = {}


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
    """Build the prompt that asks Claude to draft a spec body plus 3 AC.

    ``subject`` is either the spec title (create_draft) or the roadmap
    initiative (from_roadmap_line). Both callers want the same shape, so
    they share one prompt. Set ``from_roadmap=True`` for slightly different
    framing ("this initiative from a roadmap" vs "this feature").
    """
    intro = (
        f"Write a short spec and acceptance criteria for this initiative "
        f"from a roadmap: \"{subject}\""
        if from_roadmap
        else f"Write a short spec and acceptance criteria for this feature: \"{subject}\""
    )
    return (
        f"{intro}\n\n"
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
    """Reject path traversal and paths outside docs/draft/ or docs/spec/."""
    p = PurePosixPath(path)
    if ".." in p.parts:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    if not (str(p).startswith("docs/draft/") or str(p).startswith("docs/spec/")):
        raise HTTPException(status_code=400, detail="Path must be under docs/draft/ or docs/spec/")


@router.get("/specs")
async def list_specs():
    """List all draft and spec documents with lifecycle metadata.

    Each document includes task_ids, task_summary, acceptance_criteria,
    and a computed status (draft/ready/in-progress/complete).
    """
    try:
        docs = await ostk.list_docs()
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
    """
    try:
        docs = await ostk.list_docs()
        total = len(docs)
        unfinished = sum(
            1 for d in docs if d.get("status") != "complete"
        )
        return {"unfinished": unfinished, "total": total}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/specs/draft")
async def create_draft(body: SpecDraft):
    """Create a new draft document, auto-generate acceptance criteria, then promote to a plan.

    After ostk creates the draft file, we use AI to generate acceptance
    criteria from the title and append them to the draft body. Because
    the AI always writes a complete checklist, we immediately promote
    the draft to a plan (ready state) so the user lands on a one-click
    build path. If the AI call fails (no acceptance criteria written),
    we leave the document as a draft so the user can hand-edit and
    promote it themselves.
    """
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
                model="claude-sonnet-4-5-20250929",
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": _ac_generation_prompt(body.title),
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
        pass  # If AI generation fails, the draft is still created without AC

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

    return {"result": result, "status": status, "promoted_path": promoted_path}


class SpecFromTemplate(BaseModel):
    template_id: str
    title: Optional[str] = None
    # Optional free-text note the user typed when applying the template.
    # When set, it is prepended to the template's goal body so the new
    # plan captures any extra context the user wanted to carry forward.
    note: Optional[str] = None


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
    """Create a ready plan from a starter template.

    Looks up the template by id, drafts a new doc with the template's
    title, writes the template's goal body plus the pre-written
    acceptance criteria checklist into the draft file, then promotes it
    so the user lands on a ready plan. The decompose step runs later
    when the user clicks Build it (same path as Wave 2 create_draft).

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


@router.post("/specs/promote")
async def promote_draft(body: SpecPromote):
    """Promote a draft to a finalized spec."""
    _validate_doc_path(body.path)
    try:
        result = await ostk.doc_promote(body.path)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/specs/{spec_path:path}/unlock")
async def unlock_spec(spec_path: str):
    """Move a ready plan back to draft so the user can edit acceptance criteria.

    Moves the file from ``docs/spec/<name>.md`` to ``docs/draft/<name>.md``
    and flips the ``status:`` front matter field from ``spec`` to
    ``draft``. This is the inverse of promote. Keeps the plan's task
    links and body intact so the user only has to change the checklist
    and re-promote.
    """
    _validate_doc_path(spec_path)
    if not spec_path.startswith("docs/spec/"):
        raise HTTPException(
            status_code=400,
            detail="Only plans in the ready state can be unlocked.",
        )
    source = (Path(PROJECT_ROOT) / spec_path).resolve()
    if not source.exists():
        raise HTTPException(status_code=404, detail="Plan not found")
    target_rel = spec_path.replace("docs/spec/", "docs/draft/", 1)
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
        result = await ostk.doc_decompose(body.path)
        return result  # already a dict with "result" and "task_ids"
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
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=500,
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


@router.post("/specs/from-roadmap-line")
async def create_spec_from_roadmap_line(body: SpecFromRoadmapLine):
    """Create a ready plan from a single line of a generated roadmap.

    The Files-page roadmap preview renders a plus button next to each
    initiative. Clicking it posts the initiative text and the roadmap
    file path here. The endpoint drafts a new plan whose goal is the
    initiative text, lets acceptance criteria generation run, and
    auto-promotes the draft to ready. Returns the new spec shape so the
    frontend can route the user straight to it.

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
    ac_written = False
    if full_path.exists() and full_path.is_relative_to(docs_root):
        existing = full_path.read_text()
        header = f"## From roadmap: {roadmap_file.name}\n\n{initiative}\n\n"
        existing = existing.rstrip() + "\n\n" + header
        full_path.write_text(existing)

        # Auto-generate acceptance criteria. Same model call as
        # create_draft so the plan lands in the same shape Wave 2 built.
        try:
            from services.chat_providers import _resolve_api_key
            import anthropic

            api_key = await _resolve_api_key("anthropic_api_key")
            if api_key:
                client = anthropic.AsyncAnthropic(api_key=api_key)
                response = await client.messages.create(
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=500,
                    messages=[
                        {
                            "role": "user",
                            "content": _ac_generation_prompt(
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
                    ac_written = True
        except Exception:
            # Fail soft: the user still gets a draft with the initiative
            # body even when the AC call errors. They can hand edit and
            # promote from the Specs page.
            pass

    status = "draft"
    promoted_path: Optional[str] = None
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
        "title": title,
        "roadmap_path": raw_path,
    }


@router.get("/specs/{spec_path:path}/tasks")
async def get_spec_tasks(spec_path: str):
    """Return all tasks linked to a spec, with their current status.

    Reads the spec's ``tasks:`` front matter field and fetches each
    task's current status from ostk.
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
    return {"tasks": enriched}


@router.post("/specs/{spec_path:path}/verify")
async def verify_spec(spec_path: str):
    """Verify a spec's acceptance criteria against linked task status.

    Returns which criteria are met, whether all are met, and a summary
    of linked task statuses.

    When verify determines that ALL acceptance criteria are met (the
    "spec is complete" transition), fire a single bell notification so
    Tori sees "Spec done" in the tray without having to keep staring
    at the Specs page. Dedup target key is the spec path so repeat
    verifies do not duplicate bells.
    """
    _validate_doc_path(spec_path)
    try:
        result = await ostk.spec_verify(spec_path)
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        if isinstance(result, dict) and result.get("all_met") is True:
            from services.notifications import notifications_service as _notif
            from pathlib import Path as _Path
            title_from_path = _Path(spec_path).stem.replace("-", " ").strip() or "Spec"
            _notif.add(
                type="spec_complete",
                title="Spec done",
                body=(
                    f"All acceptance criteria passed for {title_from_path}. "
                    "Open the spec to review the build."
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
        # Notification is best-effort. Never break verify.
        pass
    return result


@router.post("/specs/{spec_path:path}/build")
async def build_spec(spec_path: str):
    """One-click build: decompose if needed, then spawn a builder per open task.

    Loads open tasks from the spec's front matter, asks ostk for the
    per-task agent configs, then spawns a Builder subagent for each one
    via POST /api/agents/spawn. The Builder agentfile runs in quick_mode
    so the mailbox block stays compact and the spawn is fast.

    If the plan has never been decomposed (no linked task IDs), this
    endpoint runs ``doc_decompose`` first so the user only ever has to
    click one button. The decompose-then-build flow returns the same
    shape as a plain build call.

    Returns ``{"agents": [names...], "message": "..."}``. When the plan
    still has no open tasks after a decompose attempt (for example,
    every task was already closed), returns an empty agent list with a
    helpful message instead of spawning anything.
    """
    _validate_doc_path(spec_path)
    try:
        result = await ostk.spec_build(spec_path)
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))

    agent_configs = result.get("agents", []) or []

    # One-click path: if this plan has no agents to build (meaning no
    # open tasks), try running decompose once so the user does not have
    # to click "Break into Tasks" first. If decompose still produces
    # nothing, fall through to the empty-message response.
    if not agent_configs:
        try:
            await ostk.doc_decompose(spec_path)
            result = await ostk.spec_build(spec_path)
            agent_configs = result.get("agents", []) or []
        except OstkError:
            # Decompose can fail if the spec was already decomposed; in
            # that case spec_build still returned no agents, so the
            # empty-message branch below handles it cleanly.
            pass

    if not agent_configs:
        return {
            "agents": [],
            "message": (
                "This plan has no open tasks to build. Every task may "
                "already be closed."
            ),
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
    # well under a second. Same pattern fleet_spawn uses
    # (api/routers/agents.py fleet_spawn_agents gather).
    #
    # Progress visibility: we stamp _task_assignments BEFORE awaiting
    # each spawn, so GET /specs/{path}/tasks already shows the agent
    # name on the very first poll even while the subprocess is still
    # warming up. If the spawn then errors, we pop the assignment so
    # the UI does not attach a broken builder to the row.
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
        # Append a clear close instruction so the agent hits the Tasks
        # API when it finishes instead of only running ostk locally.
        prompt_with_close = (
            prompt
            + "\n\n## When done\n\n"
            + f"Close task {task_id} via `curl -sSk --connect-timeout 3 -m 5 "
            + f"-X POST https://127.0.0.1:8000/api/tasks/{task_id}/close` so "
            + "the spec's progress bar updates and the Tasks page reflects "
            + "the completion immediately.\n"
        )
        # Build a friendly task label so the Agents page shows what each
        # builder is working on instead of the opaque agent name.
        if task_title:
            friendly_task = f"Build task {task_id}: {task_title}"
        else:
            friendly_task = f"Build task {task_id} for spec {spec_path}"
        body = AgentSpawn(
            name=name,
            prompt=prompt_with_close,
            model="sonnet",
            budget=2.0,
            template="builder",
            source="spec-build",
            task=friendly_task,
            # Demo budget: cap each Builder at 90 seconds so a fan-out
            # spec build (one Builder per task) never hangs the demo
            # past the wall-clock window. Builder agentfile already opts
            # into quick_mode for the compact mailbox block; demo_mode
            # adds the Haiku coercion and force-complete supervisor.
            demo_mode=True,
        )
        # Record assignment up front so the first poll after the HTTP
        # return already shows the agent-to-task mapping.
        norm_tid = str(task_id).lstrip("\u2192") if task_id else ""
        if norm_tid:
            _task_assignments[norm_tid] = name
        try:
            await spawn_agent(body)
            return (name, None)
        except HTTPException as e:
            if norm_tid:
                _task_assignments.pop(norm_tid, None)
            return (name, str(e.detail))
        except Exception as e:  # noqa: BLE001
            if norm_tid:
                _task_assignments.pop(norm_tid, None)
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
    message = f"Spawned {count} agent{'s' if count != 1 else ''} to build this spec. Watch the Agents tab."
    if failures:
        message += f" {len(failures)} failed: {'; '.join(failures)}"
    return {"agents": spawned, "message": message}


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
    return {"result": "deleted"}


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
