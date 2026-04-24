"""Automation output helpers.

Every automation run (fleet, solo agent, workflow) produces a persistent
``.md`` file in ``~/.myos/files/`` so it lands in Recent Documents on
the Files tab, and auto-creates follow-up tasks from any next-steps
block in the output.

This is the single choke point. Every completion path (``/agents/{name}/complete``,
fleet demo force-complete supervisor, ``services.workflows.run_workflow``
terminal branch) calls :func:`write_output` with the run's final text.
Next-step bullets become real tasks via :func:`auto_create_tasks`, which
also runs the shared ``schedule_auto_labels`` hook so every task path
stays consistent.

Skip rules are intentional:

* Summaries shorter than :data:`_MIN_SUMMARY_CHARS` are acks like "Done",
  not real artifacts. They never land.
* Agent names that match the shared infra-noise regex (``demo-smoke-*``,
  ``build-NNN``, ``fix-*``, etc) never land. Those are ops runs.
* Duplicate filenames in the same minute get a numeric suffix so two
  runs of the same automation stay distinct on the Files tab.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


# Where user-facing automation outputs land. The Files tab's
# ``/docs/recent`` endpoint already scans this directory, so anything
# written here shows up automatically.
def __getattr__(name):  # PEP 562: resolve MYOS_FILES_DIR at call-time
    if name == "MYOS_FILES_DIR":
        from services.files_dir import get_files_dir
        return get_files_dir()
    raise AttributeError(name)

# Minimum characters (after strip) that qualify as a real output. Under
# this bar we treat the run as an acknowledgment, not an artifact.
_MIN_SUMMARY_CHARS = 50


# ---------------------------------------------------------------------------
# Slug + timestamp helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Return a filesystem-safe slug from an automation name."""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (name or "").strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "automation"


def _timestamp(now: Optional[datetime] = None) -> str:
    """Return an ISO-ish timestamp safe for filenames (colons replaced)."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H-%M")


def _is_infra_noise(name: str) -> bool:
    """True when the automation name matches the shared infra-noise pattern.

    Reuses ``services.briefing._INFRA_AGENT_NAME_RE`` so the Files tab,
    the Review failed list, and this module all agree on which runs are
    machine-generated noise. Lazy import keeps this module free of a
    circular dependency at load time.
    """
    try:
        from services.briefing import _INFRA_AGENT_NAME_RE

        return bool(_INFRA_AGENT_NAME_RE.match(name or ""))
    except Exception:
        fallback = re.compile(
            r"^(demo-smoke-|build-\d+|test-|smoke-|overnight-|fix-|diagnose-|"
            r"verify-|harden-|dedupe-|cleanup-|backfill-|backend-|template-|"
            r"tasks-|usage-|github-|calendar-|drive-|inline-|onboarding-|"
            r"chat-|workflow-|spec-|fleet-build-|automation-to-)",
            re.IGNORECASE,
        )
        return bool(fallback.match(name or ""))


def _unique_path(base: Path) -> Path:
    """Return ``base`` or a ``base-<n>`` variant that does not yet exist.

    Same-minute reruns of the same automation would otherwise overwrite
    each other because the filename only carries minute resolution. The
    numeric suffix keeps both runs visible on the Files tab.
    """
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix or ".md"
    parent = base.parent
    for i in range(2, 100):
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    # Pathological fallback: append seconds. Should never hit in practice.
    seconds = datetime.now(timezone.utc).strftime("%S")
    return parent / f"{stem}-{seconds}{suffix}"


# ---------------------------------------------------------------------------
# Next-step parsing
# ---------------------------------------------------------------------------


_TODO_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?TODO[:\-]\s*(.+)$", re.IGNORECASE)
_CHECKBOX_LINE_RE = re.compile(r"^\s*[-*]\s*\[\s*\]\s+(.+)$")
_BULLET_LINE_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(.+)$")
_NEXT_STEPS_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s*(next\s*steps?|follow[- ]?ups?|action\s*items?|todos?)\s*$",
    re.IGNORECASE,
)

# Roadmap-specific headings. A roadmap artifact organizes work under
# headings like "Milestones", "Phases", "Initiatives", "Deliverables",
# "Goals", "Epics", or quarter labels (Q1 2026, Q2, etc). The chat
# "create tasks from this roadmap" flow uses these to harvest bullets
# when the author never wrote a dedicated "Next steps" section.
_ROADMAP_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s*("
    r"milestones?|phases?|initiatives?|deliverables?|"
    r"goals?|epics?|features?|workstreams?|"
    r"q[1-4](?:\s+20\d{2})?"
    r")\s*$",
    re.IGNORECASE,
)


def parse_next_steps(content: str) -> list[str]:
    """Pull actionable follow-up bullets out of an automation's output.

    Recognises three sources:

    1. Any line starting with ``TODO:`` or ``- TODO:`` anywhere in the
       body.
    2. Any ``- [ ]`` / ``* [ ]`` unchecked checkbox anywhere in the body.
    3. Bullet lines inside a dedicated ``Next steps`` / ``Follow-ups`` /
       ``Action items`` / ``TODOs`` section (markdown heading).

    Returns a deduplicated list preserving first-seen order. Each item is
    a short plain-language sentence stripped of markdown syntax.
    """
    if not content:
        return []

    items: list[str] = []
    seen: set[str] = set()

    def _push(raw: str) -> None:
        clean = raw.strip().strip("*_`").rstrip(".").strip()
        # Clip overly long lines so one multi-paragraph TODO does not
        # become one giant task title. Tasks do better with short titles.
        if len(clean) > 200:
            clean = clean[:197].rstrip() + "..."
        if not clean:
            return
        key = clean.lower()
        if key in seen:
            return
        seen.add(key)
        items.append(clean)

    lines = content.splitlines()

    # Pass 1: scan every line for TODO: / - [ ] regardless of section.
    for line in lines:
        m = _TODO_LINE_RE.match(line)
        if m:
            _push(m.group(1))
            continue
        m = _CHECKBOX_LINE_RE.match(line)
        if m:
            _push(m.group(1))

    # Pass 2: scan dedicated "Next steps" / "Follow-ups" sections. Start
    # collecting bullets after the matching heading and stop at the next
    # heading or blank-then-non-bullet run.
    in_section = False
    blank_seen = False
    for line in lines:
        if _NEXT_STEPS_HEADING_RE.match(line):
            in_section = True
            blank_seen = False
            continue
        if not in_section:
            continue
        if line.strip().startswith("#"):
            # A new heading closes the section.
            in_section = False
            continue
        if not line.strip():
            blank_seen = True
            continue
        bullet_m = _BULLET_LINE_RE.match(line)
        if bullet_m:
            _push(bullet_m.group(1))
            blank_seen = False
        else:
            # Non-bullet content after a blank line ends the section.
            if blank_seen:
                in_section = False

    return items


def parse_roadmap_items(content: str) -> list[str]:
    """Pull actionable items out of a roadmap ``.md`` for task creation.

    Runs the regular :func:`parse_next_steps` pass first so any
    ``Next steps`` / ``Follow-ups`` / ``TODO`` / ``- [ ]`` bullets land
    like they do for every other automation output. Then scans for
    roadmap-specific section headings (``Milestones``, ``Phases``,
    ``Initiatives``, ``Q1 2026``, ...) and collects the bullets under
    each one.

    Returns a deduplicated list preserving first-seen order. Bullets
    from the generic next-step pass come first, then roadmap-section
    bullets, so the most explicit author-signaled items win.
    """
    if not content:
        return []

    items: list[str] = []
    seen: set[str] = set()

    def _push(raw: str) -> None:
        clean = raw.strip().strip("*_`").rstrip(".").strip()
        if len(clean) > 200:
            clean = clean[:197].rstrip() + "..."
        if not clean:
            return
        key = clean.lower()
        if key in seen:
            return
        seen.add(key)
        items.append(clean)

    # Start from whatever the generic next-step parser found. Those
    # items are already deduped and normalized.
    for item in parse_next_steps(content):
        _push(item)

    # Now walk the document looking for roadmap-specific headings and
    # grab every bullet line that lives under one. Nested sub-headings
    # (``### Q1 initiatives``) keep collecting; only a non-roadmap
    # heading closes the section.
    lines = content.splitlines()
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            if _ROADMAP_HEADING_RE.match(line):
                in_section = True
                continue
            # Hitting "Next steps" or similar is handled separately, but
            # it still ends any roadmap section we were collecting.
            in_section = False
            continue
        if not in_section:
            continue
        m = _BULLET_LINE_RE.match(line)
        if m:
            _push(m.group(1))

    return items


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------


def write_output(
    automation_kind: str,
    name: str,
    content: str,
    next_steps: Optional[Iterable[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
    files_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Persist an automation's final output to the Files directory.

    ``automation_kind`` is a short label like ``"agent"``, ``"fleet"``, or
    ``"workflow"``. ``name`` is the automation's identifier (agent name,
    fleet id + role, workflow name). ``content`` is the final summary /
    artifact text. ``next_steps`` is an optional pre-parsed list; when
    omitted the function parses ``content`` itself via
    :func:`parse_next_steps`.

    Returns the written Path, or None when the run was skipped (short
    summary, infra-noise name, or filesystem error).
    """
    if not content:
        return None
    body = content.strip()
    if len(body) < _MIN_SUMMARY_CHARS:
        return None
    # Skip infra-noise names (demo-smoke-*, build-NNN, etc). Real fleet
    # members have ``fleet_id`` stamped on their metadata (surfaced
    # through the metadata dict here) so they bypass the regex even
    # though their names start with ``fleet-build-``. Same exemption as
    # routers.agents._save_agent_output_to_files.
    is_fleet_member = bool(metadata and metadata.get("fleet_id"))
    if _is_infra_noise(name) and not is_fleet_member:
        return None

    target_dir = Path(files_dir) if files_dir is not None else MYOS_FILES_DIR

    if next_steps is None:
        next_steps = parse_next_steps(body)
    next_steps_list = [s for s in (next_steps or []) if s]

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("automation_outputs: cannot create %s: %s", target_dir, exc)
        return None

    now = datetime.now(timezone.utc)
    slug = _slugify(name)
    filename = f"{slug}-{_timestamp(now)}.md"
    target = _unique_path(target_dir / filename)

    heading_words = [w for w in slug.split("-") if w]
    heading = " ".join(w.capitalize() for w in heading_words) or "Automation Output"

    meta = dict(metadata or {})
    meta.setdefault("source", name)
    meta.setdefault("kind", f"{automation_kind}-output")
    meta.setdefault("generated_at", now.isoformat())
    meta.setdefault("summary_length", len(body))

    front_matter_lines = ["---"]
    for key, value in meta.items():
        front_matter_lines.append(f"{key}: {value}")
    front_matter_lines.append("---")
    front_matter = "\n".join(front_matter_lines) + "\n\n"

    sections = [front_matter, f"# {heading}\n\n", body.rstrip() + "\n"]

    # Only emit a rendered "Next steps" section when the body does not
    # already carry one. Prevents double-render when the caller baked a
    # Next steps block into its content text.
    already_has_section = bool(
        re.search(r"(?mi)^##\s*next\s*steps", body)
    )
    if next_steps_list and not already_has_section:
        sections.append("\n## Next steps\n\n")
        for item in next_steps_list:
            sections.append(f"- [ ] {item}\n")

    try:
        target.write_text("".join(sections))
    except OSError as exc:
        logger.warning("automation_outputs: write failed for %s: %s", target, exc)
        return None
    return target


# ---------------------------------------------------------------------------
# Task auto-creation
# ---------------------------------------------------------------------------


def _plain_language_description(item: str, source_name: str, automation_kind: str) -> str:
    """Return a plain-language description explaining where the task came from.

    Used by :func:`auto_create_tasks` so every generated task tells Tori
    which automation run produced it. No jargon. No shorthand. The
    automation kind is phrased in everyday terms ("team run" for fleet,
    "agent" for single agent, "recipe" for workflow).
    """
    kind_label_map = {
        "agent": "agent run",
        "fleet": "team run",
        "workflow": "automation run",
    }
    kind_label = kind_label_map.get(automation_kind, "automation run")
    return (
        f"Follow-up from the {kind_label} called '{source_name}'. "
        f"The automation suggested this next step: {item}"
    )


def _normalize_title(title: str) -> str:
    """Return a lowercased, whitespace-collapsed, punctuation-trimmed key.

    Two next-step bullets with trivial formatting differences (trailing
    period, extra spaces, different case) map to the same key so the
    deduper treats them as the same task. Conservative normalization:
    only collapse whitespace, lowercase, and strip a trailing period.
    Never strip interior punctuation because real task titles can legally
    differ by a word like "and" or "the".
    """
    if not title:
        return ""
    key = re.sub(r"\s+", " ", title.strip().lower()).rstrip(".")
    return key


async def _open_task_title_keys() -> set[str]:
    """Return the set of normalized titles for every currently-open task.

    Best-effort. On any failure the deduper falls back to an empty set so
    across-run dedup becomes a no-op (we still dedupe within the run).
    Never raises; the shared auto-task pass must not break because ostk
    hiccuped while listing.
    """
    try:
        from services.ostk import ostk

        rows = await ostk.list_tasks(status="open")
    except Exception as exc:
        logger.info(
            "automation_outputs: open-task listing failed, dedup skipped: %s", exc
        )
        return set()
    keys: set[str] = set()
    for row in rows or []:
        title = str(row.get("title") or "")
        key = _normalize_title(title)
        if key:
            keys.add(key)
    return keys


async def auto_create_tasks(
    next_steps: Iterable[str],
    source_name: str,
    automation_kind: str = "automation",
    priority: str = "P2",
) -> list[dict[str, Any]]:
    """Create one task per next-step item. Returns list of ``{id, title}``.

    Dedup rules:

    * **Within a run**: if the same title appears twice in ``next_steps``
      (after normalization), only the first copy becomes a task.
    * **Across runs**: before creating a task, list the currently open
      tasks and skip any whose normalized title already matches. This
      prevents fleet completion storms from injecting five identical
      "Re-run Agent" tasks when every role contributes the same bullet.

    Each task:

    * Gets a plain-language description tying it back to the source run.
    * Runs through ``schedule_auto_labels`` so labels land on it like any
      other task creation path (regression guard: every path must label).
    * Skips ostk's recent-delete resurrection guard by simply swallowing
      failures. A retry storm from an automation must never re-inject a
      task the user just deleted.

    Errors on a single task never block the rest; the function always
    returns the list of tasks it actually created.
    """
    items = [s.strip() for s in (next_steps or []) if s and s.strip()]
    if not items:
        return []

    from services.ostk import ostk, OstkError
    from services.task_labeling import extract_task_id, schedule_auto_labels

    # Across-run dedup: collect the set of normalized open-task titles
    # once, up front. We mutate this set as we create new tasks so the
    # second duplicate inside this same batch hits the same gate without
    # a second round trip to ostk.
    existing_keys = await _open_task_title_keys()

    # Within-run dedup: track titles we've already queued so the same
    # bullet appearing three times in one artifact still only creates
    # one task.
    seen_in_run: set[str] = set()

    created: list[dict[str, Any]] = []
    for item in items:
        title = item if len(item) <= 120 else item[:117].rstrip() + "..."
        key = _normalize_title(title)
        if not key:
            continue
        if key in seen_in_run:
            logger.info(
                "automation_outputs: skip duplicate-in-run task '%s'", title
            )
            continue
        if key in existing_keys:
            logger.info(
                "automation_outputs: skip duplicate-of-open task '%s'", title
            )
            seen_in_run.add(key)
            continue
        description = _plain_language_description(item, source_name, automation_kind)
        try:
            add_result = await ostk.add_task(title, priority, description=description)
        except OstkError as exc:
            logger.info(
                "automation_outputs: skip task for '%s': %s", title, exc
            )
            continue
        except Exception as exc:
            logger.warning(
                "automation_outputs: unexpected error creating task '%s': %s",
                title, exc,
            )
            continue

        task_id = extract_task_id(add_result)
        # Always schedule labels, even when task_id is None. The helper
        # itself no-ops on a missing id, and keeping the call here means
        # every automation-created task goes through the same label hook.
        schedule_auto_labels(task_id, title, description)
        created.append({"id": task_id, "title": title, "description": description})
        seen_in_run.add(key)
        existing_keys.add(key)

    return created


def auto_create_tasks_sync(
    next_steps: Iterable[str],
    source_name: str,
    automation_kind: str = "automation",
    priority: str = "P2",
) -> list[dict[str, Any]]:
    """Fire-and-forget wrapper that works from synchronous completion paths.

    ``_schedule_demo_force_complete`` is an async task, and the agent
    ``/complete`` route is async, so both can await :func:`auto_create_tasks`
    directly. This helper is kept for sync callers that want to schedule
    the task-creation pass without awaiting it (returns immediately).
    """
    items = [s.strip() for s in (next_steps or []) if s and s.strip()]
    if not items:
        return []
    try:
        asyncio.create_task(
            auto_create_tasks(items, source_name, automation_kind, priority)
        )
    except RuntimeError:
        # No running loop. Nothing to do; caller is outside async context.
        logger.info(
            "automation_outputs: no event loop for task auto-creation of %d items",
            len(items),
        )
    return [{"title": i} for i in items]
