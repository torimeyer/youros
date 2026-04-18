"""Briefing service.

Generates a short daily briefing using Claude whenever the user asks for
it. State lives in ~/.myos/briefing_state.json so it never touches the
repo and survives git pulls.

Also generates structured action items alongside the text briefing. Each
action item has a type, label, action_url, and context so the dashboard
can render one-click buttons.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json
from services.settings_store import settings_store

MYOS_DIR = Path.home() / ".myos"
BRIEFING_STATE_PATH = MYOS_DIR / "briefing_state.json"


def _load_state() -> dict:
    import services.briefing as _self
    state_path: Path = _self.BRIEFING_STATE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    import services.briefing as _self
    state_path: Path = _self.BRIEFING_STATE_PATH
    atomic_write_json(state_path, state)


def _today_str() -> str:
    import services.briefing as _self
    return _self.datetime.now().strftime("%Y-%m-%d")


def should_show_briefing() -> bool:
    """Return True when the briefing should be shown right now.

    Returns False when the briefing setting is disabled or when the user
    has already dismissed today's briefing. There is no time-of-day gate:
    the briefing can be requested at any hour.
    """
    # Respect the setting (defaults to True for new installs via schema default)
    if not settings_store.get("briefing_enabled", True):
        return False

    state = _load_state()
    today = _today_str()

    # Already dismissed today
    if state.get("dismissed_date") == today:
        return False

    # Cached briefing for today exists, serve it
    if state.get("last_shown") == today and state.get("briefing"):
        return True

    # Not shown yet today, show it
    if state.get("last_shown") != today:
        return True

    return False


def get_cached_briefing() -> Optional[str]:
    """Return today's cached briefing text, or None if not yet generated."""
    state = _load_state()
    today = _today_str()
    if state.get("last_shown") == today and state.get("briefing"):
        return state["briefing"]
    return None


def _is_active_task(t: dict) -> bool:
    """A task is 'active' for briefing purposes if it is anything other
    than closed. ostk emits 'open' and 'in_progress' as distinct
    statuses. The briefing used to filter strictly for status == 'open'
    and silently skipped every in_progress P0, so Tori's highest
    priority work was invisible the moment an agent picked it up.
    Regression guard for needle 280. Same rule as Tasks.tsx isActiveTask.
    """
    return t.get("status") != "closed"


def _is_briefing_task(t: dict) -> bool:
    """A task is eligible for the briefing's top-task picker when it is
    both active (open or in_progress) AND passes the shared user-facing
    visibility filter. That filter hides e2e smoke leftovers and session
    tasks the SessionStart hook files for each Claude session. Without
    this second gate the briefing narrated e2e rows and 'Session in
    torios' as the top open P1 even though the Tasks page and the
    sidebar badge (via /api/tasks/counts) had hidden them. Keeping the
    briefing, the Tasks page, and the sidebar on the same helper makes
    'shipped' mean the same thing on every surface.
    """
    from services.task_visibility import is_visible_task
    return _is_active_task(t) and is_visible_task(t)


async def _task_count_changed() -> bool:
    """Return True if the number of active, visible P0/P1 tasks differs
    from when the briefing was cached. Uses the same ``_is_briefing_task``
    gate as ``generate_briefing`` so the cached count and the regenerated
    count agree on what counts as a task.
    """
    from services.ostk import ostk, OstkError
    state = _load_state()
    cached_count = state.get("task_count")
    if cached_count is None:
        return False  # no baseline, treat as fresh
    try:
        # Pull every task (no status filter) so in_progress rows still
        # count toward the priority tally. Filtering in Python lets us
        # treat open and in_progress as the same bucket while also
        # stripping session and e2e tasks the user has hidden.
        all_tasks = await ostk.list_tasks()
        current = len([
            t for t in all_tasks
            if _is_briefing_task(t) and t.get("priority") in ("P0", "P1")
        ])
        return current != cached_count
    except OstkError:
        return False


async def generate_briefing() -> str:
    """Generate a briefing using Claude and cache it.

    First calls ostk to get a local activity summary (fast, no LLM cost).
    Then enriches with calendar events, open tasks, and compounds.
    Falls back to the full Claude-only approach if ostk data is empty.
    """
    from services.ostk import ostk, OstkError

    # Gather context pieces
    context_parts: list[str] = []

    # Start with ostk activity summary (local, fast, free)
    try:
        activity_summary = await ostk.get_activity_summary()
        if activity_summary.strip():
            context_parts.append(f"ostk activity summary:\n{activity_summary}")
    except (OstkError, Exception):
        pass

    # Calendar events (best effort)
    try:
        from services.google_auth import is_authenticated
        if is_authenticated():
            from services import calendar as cal_service
            events = await cal_service.get_today_events()
            if events:
                parts = []
                for ev in events:
                    title = ev.get("summary", "Untitled")
                    start = (ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date") or "")
                    if start:
                        try:
                            dt = datetime.fromisoformat(start)
                            time_str = dt.strftime("%-I:%M %p").lower()
                            parts.append(f"{time_str}: {title}")
                        except Exception:
                            parts.append(title)
                    else:
                        parts.append(title)
                context_parts.append("Today's meetings: " + ", ".join(parts))
            else:
                context_parts.append("No meetings on your calendar today.")
    except Exception:
        pass

    # Open P0 and P1 tasks, sorted by priority then age (best effort).
    # IMPORTANT: pull every task (no status filter) so in_progress rows
    # still show up. ostk returns both 'open' and 'in_progress' as
    # distinct statuses and the briefing must treat both as active.
    # Also run every candidate through ``_is_briefing_task`` so session
    # tasks and e2e leftovers never bubble up as 'top open task'. The
    # Tasks page hides them; the briefing must agree.
    p0p1_count = 0
    try:
        all_tasks = await ostk.list_tasks()
        active_tasks = [t for t in all_tasks if _is_briefing_task(t)]
        priority_order = {"P0": 0, "P1": 1}
        priority_tasks = sorted(
            [t for t in active_tasks if t.get("priority") in ("P0", "P1")],
            key=lambda t: (
                priority_order.get(t.get("priority", "P1"), 1),
                t.get("created_at", "9999"),  # oldest first
            ),
        )
        p0p1_count = len(priority_tasks)

        # Compute compound scores (which tasks unblock the most).
        # Use the same active-task filter so an in_progress task can
        # still show up as high-leverage, and so session tasks never
        # pollute the unblock graph.
        open_ids = {t.get("id", "") for t in active_tasks}
        blocks_graph: dict[str, set] = {}
        for t in all_tasks:
            tid = t.get("id", "")
            for bid in (t.get("blocks") or []):
                blocks_graph.setdefault(tid, set()).add(bid)
            for did in (t.get("depends_on") or []):
                blocks_graph.setdefault(did, set()).add(tid)
        compound_scores: dict[str, int] = {}
        for tid in open_ids:
            visited: set = set()
            queue = list(blocks_graph.get(tid, set()))
            while queue:
                nid = queue.pop(0)
                if nid in visited or nid == tid:
                    continue
                visited.add(nid)
                if nid in open_ids:
                    queue.extend(blocks_graph.get(nid, set()))
            count = len(visited & open_ids)
            if count > 0:
                compound_scores[tid] = count

        if priority_tasks:
            today = datetime.now(timezone.utc)
            task_lines = []
            for t in priority_tasks[:10]:
                age = ""
                created = t.get("created_at", "")
                if created:
                    try:
                        days = (today - datetime.fromisoformat(created)).days
                        age = f" (open {days}d)" if days > 0 else " (new today)"
                    except Exception:
                        pass
                unblocks = compound_scores.get(t.get("id", ""), 0)
                unblocks_str = f" [unblocks {unblocks} tasks]" if unblocks > 0 else ""
                task_lines.append(
                    f"  [{t.get('priority')}] {t.get('title', 'Untitled')}{age}{unblocks_str}"
                )
            context_parts.append("Top open tasks (sorted by priority, then oldest first):\n" + "\n".join(task_lines))
        else:
            context_parts.append("No high-priority tasks open right now.")
    except OstkError:
        pass

    # High-leverage tasks (compounds) that unblock the most other work
    try:
        compounds = await ostk.get_compounds()
        if compounds:
            top = compounds[0]
            context_parts.append(
                f"Highest-leverage task: \"{top.get('title', 'Untitled')}\" "
                f"(finishing it unblocks {top.get('blocks_count', 0)} other "
                f"{'task' if top.get('blocks_count', 0) == 1 else 'tasks'})"
            )
    except OstkError:
        pass

    # Yesterday's activity (closed tasks, agents run). Exclude session
    # tasks and e2e leftovers so the recap matches what the user would
    # recognise as real work. Session tasks churn constantly (one per
    # Claude Code session) and would otherwise dominate the summary.
    try:
        from datetime import timedelta
        from services.task_visibility import is_e2e_task, is_session_task
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        all_tasks_full = await ostk.list_tasks()
        closed_yesterday = [
            t for t in all_tasks_full
            if t.get("status") == "closed"
            and (t.get("closed_at", "") or "").startswith(yesterday_str)
            and not is_session_task(t)
            and not is_e2e_task(t)
        ]
        if closed_yesterday:
            context_parts.append(
                f"Yesterday you closed {len(closed_yesterday)} task(s): "
                + ", ".join(t.get("title", "Untitled") for t in closed_yesterday[:3])
            )
        else:
            context_parts.append("No tasks were closed yesterday.")
    except Exception:
        pass

    context_block = "\n\n".join(context_parts) if context_parts else "No context available."

    today_display = datetime.now().strftime("%A, %B %-d")
    prompt = (
        f"Today is {today_display}. Here is the user's workspace context:\n\n"
        f"{context_block}\n\n"
        "Write a short briefing in 2 short paragraphs, separated by blank lines. "
        "Plain factual language, no jargon, no bullet points, no motivational phrases.\n\n"
        "Paragraph 1: What is on the calendar today (if anything) and a quick "
        "recap of what was completed yesterday.\n\n"
        "Paragraph 2: The single most important task to work on and why. "
        "Use these rules to pick it: if the context includes a highest-leverage task "
        "that unblocks multiple other tasks, that is the top recommendation regardless "
        "of priority. Otherwise, P0 always beats P1. Among the same priority, "
        "the task that has been open the longest is the most important. If a task is "
        "a bug that users are hitting, it beats a feature request at the same priority "
        "and age. Name the specific task.\n\n"
        "Keep it concise and factual. Do not use em-dashes. Do not use encouraging or "
        "motivational language. Do not assume a time of day, so avoid phrases like "
        "good morning, this morning, or your morning. "
        "Never mention API paths or raw URLs (for example `/api/agents`, `/api/tasks`, "
        "or `localhost:8000`). Refer to features by their visible page name in the app: "
        "the Agents page, the Tasks page, the Briefing, the Plans page, the Files page, "
        "the Calendar, Gmail, Drive, GitHub, Slack, iMessage, Activity, Search, Settings."
    )

    briefing = await _call_claude(prompt)

    # Cache it
    state = _load_state()
    state["last_shown"] = _today_str()
    state["briefing"] = briefing
    state["task_count"] = p0p1_count
    state.pop("dismissed_date", None)
    _save_state(state)

    return briefing


async def _call_claude(prompt: str) -> str:
    """Make a single non-streaming call to Claude and return the text response."""
    messages = [{"role": "user", "content": prompt}]

    # Try the local claude CLI for a non-streaming call
    from services import claude_code_provider
    if await claude_code_provider.is_claude_code_available():
        try:
            result = await _call_via_cli(prompt)
            if result:
                return result
        except Exception:
            pass

    # Fall back to Anthropic API key
    api_key = ""
    try:
        from services.ostk import ostk
        api_key = await ostk.secret_get("ANTHROPIC_API_KEY") or ""
    except Exception:
        pass

    if not api_key:
        api_key = settings_store.get("anthropic_api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")

    if not api_key:
        return (
            "Your workspace is ready. "
            "Check your top tasks and have a great day."
        )

    import anthropic
    client = anthropic.AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=300,
        messages=messages,
    )
    text_blocks = [b.text for b in response.content if hasattr(b, "text")]
    return " ".join(text_blocks).strip()


async def _call_via_cli(prompt: str) -> str:
    """Use the local claude CLI binary for a non-streaming single call."""
    import asyncio
    import shutil

    claude_path = shutil.which("claude")
    if not claude_path:
        return ""

    from services.claude_code_provider import _build_subprocess_env

    env = _build_subprocess_env()
    args = [
        claude_path,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        "sonnet",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
    except Exception:
        return ""

    if proc.returncode != 0:
        return ""

    text = stdout.decode("utf-8", errors="replace").strip()
    # claude -p --output-format json returns a JSON object with a "result" field
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            result = data.get("result") or data.get("content") or ""
            if isinstance(result, str):
                return result.strip()
            if isinstance(result, list):
                parts = [b.get("text", "") for b in result if isinstance(b, dict) and b.get("type") == "text"]
                return " ".join(parts).strip()
    except (json.JSONDecodeError, AttributeError):
        pass

    return text


def dismiss_briefing() -> None:
    """Mark today's briefing as dismissed so it does not show again today."""
    state = _load_state()
    state["dismissed_date"] = _today_str()
    _save_state(state)


def get_cached_action_items() -> list[dict] | None:
    """Return cached action items, or None if not yet generated.

    Action items are generated alongside the briefing. They are valid
    as long as they exist in state. They get refreshed on the next
    briefing generation cycle.

    Defensive filter: the briefing no longer surfaces ``review_agent``
    cards (see ``generate_action_items``). But caches written by older
    code still carry those entries, and the next regeneration is only
    triggered when the task count changes, so a "Review failed: ..."
    card can linger for hours after the upgrade. Strip them on read so
    the rule applies to the stale cache immediately, without waiting
    for the next regeneration. Mirrors the "server-side filter at the
    source" fix rule so deprecated card types never leak through.
    """
    state = _load_state()
    items = state.get("action_items")
    if not items:
        return items
    filtered = [i for i in items if i.get("type") != "review_agent"]
    # If the stale cache carried review_agent entries, persist the
    # scrubbed list so subsequent reads skip the filter pass entirely
    # and the state file stops carrying deprecated data.
    if len(filtered) != len(items):
        state["action_items"] = filtered
        try:
            _save_state(state)
        except Exception:
            # Best effort scrub. If the write fails we still return the
            # filtered list above so the caller is not affected.
            pass
    return filtered


_NO_REPLY_SENDER_TOKENS: tuple[str, ...] = (
    "noreply",
    "no-reply",
    "no.reply",
    "donotreply",
    "do-not-reply",
    "do.not.reply",
    "mailer-daemon",
    "notifications@",
    "receipts@",
    "orders@",
    "automated@",
    "jobalerts-",
    "updates-noreply",
    "editors-noreply",
    "customerio",
    "announcements@",
)

# Regex patterns for marketing / bulk-mail subdomains. Real personal
# addresses almost never use these subdomain prefixes. "email."
# subdomains in particular are the canonical Customer.io / ESP pattern.
_BULK_SUBDOMAIN_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"(^|\.)express\.medallia\.com$"),
    re.compile(r"(^|\.)mail\.beehiiv\.com$"),
    re.compile(r"(^|\.)email\.[^.]+\.[^.]+$"),  # email.brand.tld
    re.compile(r"(^|\.)rs\.email\.[^.]+\.[^.]+$"),
    re.compile(r"(^|\.)e\.[^.]+\.com$"),
)

_TRANSACTIONAL_SUBJECT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^\s*receipt\b", re.I),
    re.compile(r"\byour receipt\b", re.I),
    re.compile(r"\bfinal receipt\b", re.I),
    re.compile(r"\border confirmation\b", re.I),
    re.compile(r"\border\s*#", re.I),
    re.compile(r"\binvoice\s*#", re.I),
    re.compile(r"\byour order\b", re.I),
    re.compile(r"\byour subscription\b", re.I),
    re.compile(r"\bthank you for your order\b", re.I),
    re.compile(r"\bhas shipped\b", re.I),
    re.compile(r"\bdelivery confirmation\b", re.I),
    re.compile(r"\bpayment received\b", re.I),
    re.compile(r"\bstatement is available\b", re.I),
    re.compile(r"\bstatement is ready\b", re.I),
    re.compile(r"\baccount statement\b", re.I),
    re.compile(r"\bwe processed your claim\b", re.I),
    re.compile(r"\bwe.?ve received your payment\b", re.I),
    re.compile(r"\bpayment information\b", re.I),
    re.compile(r"\bgrading notification\b", re.I),
    re.compile(r"\bcirculation summary\b", re.I),
    re.compile(r"\bdelivery status notification\b", re.I),
)

_TRANSACTIONAL_DOMAINS: tuple[str, ...] = (
    "kroger.com",
    "doordash.com",
    "ubereats.com",
    "instacart.com",
    "stripe.com",
    "intuit.com",
    "paypal.com",
    "service.paypal.com",
    "paymentus.com",
    "patient-message.com",
    "securepatientmessage.com",
    "follettdestiny.com",
    "experian.com",
    "s.usa.experian.com",
    "services.discover.com",
    "mem.usaa.com",
    "document.cigna.com",
    "amazonses.com",
    "email.amazon.com",
    "shipment-tracking.amazon.com",
    "order-update.amazon.com",
)

_KEEP_SUBJECT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^\s*re\s*:", re.I),
    re.compile(r"^\s*fwd?\s*:", re.I),
)

_KEEP_BODY_PHRASES: tuple[str, ...] = (
    "can you",
    "could you",
    "please confirm",
    "let me know",
    "thoughts?",
    "what do you think",
    "are you able",
    "would you be able",
    "any chance",
)


def is_actionable_for_reply(email: dict) -> bool:
    """Return True only when an unread email is plausibly reply-worthy.

    The briefing surfaces a Reply-to card for each unread email, but most
    unread mail for a normal user is transactional: receipts, order
    confirmations, school notifications, credit card statements. Those
    are not things the user replies to. This helper filters them out.

    Heuristics (skip unless at least one KEEP signal is present):
      * Sender is a no-reply / notifications / mailer-daemon address.
      * Subject matches a transactional phrase like "Receipt", "Your
        order", "Payment received".
      * Sender domain is a known transactional domain.
      * Gmail flagged the message with a ``List-Unsubscribe`` header
        (bulk / marketing).

    KEEP signals that win over the skip list:
      * Subject starts with ``Re:`` or ``Fwd:``.
      * Subject ends with a question mark.
      * Snippet / body contains an explicit action phrase like
        "can you", "please confirm", "let me know".

    Input tolerates either the Gmail-service shape
    (``from_name``/``from_email``) or the older ``from`` string shape
    used by some callers.
    """
    subject = (email.get("subject") or "").strip()
    snippet = (email.get("snippet") or email.get("body") or "").strip()

    from_email = (email.get("from_email") or "").strip().lower()
    from_name = (email.get("from_name") or "").strip()
    from_raw = (email.get("from") or "").strip()
    # If we only got the combined "From" string, pull the email out of it.
    if not from_email and from_raw:
        # Very small parse. Prefers what is inside <...> when present.
        m = re.search(r"<([^>]+)>", from_raw)
        from_email = (m.group(1) if m else from_raw).strip().lower()
        if not from_name:
            from_name = from_raw.split("<", 1)[0].strip() or from_raw

    # ---- KEEP signals first. A positive signal trumps the skip list. ----
    for pat in _KEEP_SUBJECT_PATTERNS:
        if pat.search(subject):
            return True
    if subject.rstrip().endswith("?"):
        return True
    haystack = f"{subject}\n{snippet}".lower()
    for phrase in _KEEP_BODY_PHRASES:
        if phrase in haystack:
            return True

    # ---- Skip: no-reply sender tokens anywhere in the address. ----
    local_part = from_email.split("@", 1)[0] if "@" in from_email else from_email
    domain_part = from_email.split("@", 1)[1] if "@" in from_email else ""
    full_addr = from_email
    name_lc = from_name.lower()
    for token in _NO_REPLY_SENDER_TOKENS:
        if token in full_addr or token in name_lc:
            return False
        # "notifications@", "receipts@", etc. are checked as local-part prefixes.
        if token.endswith("@") and local_part.startswith(token[:-1]):
            return False

    # ---- Skip: transactional subject patterns. ----
    for pat in _TRANSACTIONAL_SUBJECT_PATTERNS:
        if pat.search(subject):
            return False

    # ---- Skip: known transactional domain (or subdomain of one). ----
    if domain_part:
        for known in _TRANSACTIONAL_DOMAINS:
            if domain_part == known or domain_part.endswith("." + known):
                return False
        # Subdomains that look transactional, e.g. orders.example.com.
        if re.search(r"(^|\.)(receipts?|orders?|invoices?|billing)\.", domain_part):
            return False
        # Bulk / marketing subdomains like "email.", "e.", "rs.email."
        for pat in _BULK_SUBDOMAIN_PATTERNS:
            if pat.search(domain_part):
                return False

    # ---- Skip: Gmail flagged as bulk mail via List-Unsubscribe header. ----
    # The header can live either at the top level if a caller passed the
    # raw Gmail message through, or inside a ``headers`` dict under that
    # key. Accept both.
    if email.get("list_unsubscribe") or email.get("List-Unsubscribe"):
        return False
    hdrs = email.get("headers") or {}
    if isinstance(hdrs, dict):
        for k in hdrs.keys():
            if k.lower() == "list-unsubscribe":
                return False

    # Default: no skip trigger fired and no explicit keep either. Lean
    # toward KEEP so real human email from an unknown contact still
    # surfaces. The skip list above is the conservative filter.
    return True


async def _fetch_tasks_for_action_items() -> list:
    """Fetch all tasks for stale-task detection. Best-effort."""
    try:
        from services.ostk import ostk
        return await ostk.list_tasks()
    except Exception:
        return []


async def _fetch_unread_emails_for_action_items() -> list:
    """Fetch unread Gmail summary if authenticated. Best-effort."""
    try:
        from services.google_auth import is_authenticated
        if not is_authenticated():
            return []
        from services import gmail as gmail_service
        return await gmail_service.get_unread_summary()
    except Exception:
        return []


async def _fetch_events_for_action_items() -> list:
    """Fetch today's calendar events if authenticated. Best-effort."""
    try:
        from services.google_auth import is_authenticated
        if not is_authenticated():
            return []
        from services import calendar as cal_service
        return await cal_service.get_today_events()
    except Exception:
        return []


_ANTHROPIC_INFRA_MARKERS = ("overloaded", "529")

# Infrastructure / fleet agent name prefixes that the user never spawned
# interactively. These come from demo scripts, overnight fleets, smoke
# scripts, and automated build/fix loops. Even when they hit the API
# directly (source='api') they are myOS internals, not user work, so
# failures from them must not clutter the briefing's Review failed list.
_INFRA_AGENT_NAME_RE = re.compile(
    r"^(demo-smoke-|build-\d+|test-|smoke-|overnight-|fix-|diagnose-|verify-|harden-|dedupe-|cleanup-|backfill-|backend-|template-|tasks-|usage-|github-|calendar-|drive-|inline-|onboarding-|chat-|workflow-|spec-|fleet-build-)",
    re.IGNORECASE,
)


def _is_reviewable_failed_run(run: dict) -> bool:
    """Return True when this agent run is a real user-actionable failure.

    NOTE: this helper is no longer called from ``generate_action_items``.
    The briefing does not surface Review failed cards at all. Kept here
    because the constants it references (``_INFRA_AGENT_NAME_RE``) are
    imported by other modules and because other surfaces may want the
    same filter rule in the future.

    Excludes:
      - anything that is not a bucketed ``failed`` status (covers
        ``completed``, ``running``, ``abandoned``, and the demo-mode
        ``completed_timeout`` raw status which the analyzer buckets as
        ``abandoned`` but we also guard explicitly against regressions).
      - rows the Agents page filters out as non-user-spawned (chat-turn
        rows, audit rows, hooks, subscription chat, the main Claude Code
        session). Reuses ``is_user_spawned_agent`` so the briefing stays
        in lockstep with the Agents view.
      - rows whose source is ``subagent``: these are agents I spawned on
        the user's behalf. The user did not launch them directly and
        does not need to review each one's outcome.
      - rows whose name matches the infrastructure name pattern
        (demo-smoke-*, build-NNN, overnight-*, fix-*, etc). Scripts like
        test_all_demo.py POST straight to /api/agents/spawn, which lands
        with source='api' instead of source='subagent', so the source
        filter alone lets them slip through. The name pattern catches
        them whatever source they carry.
      - failures whose summary points at an Anthropic infra blip
        (``Overloaded`` / ``529``). Those retry themselves and are not
        something the user can fix.
    """
    from services.agent_filters import is_user_spawned_agent

    if run.get("status") != "failed":
        return False
    if str(run.get("raw_status") or "").lower() == "completed_timeout":
        return False
    if not is_user_spawned_agent(run):
        return False
    if str(run.get("source") or "").lower() == "subagent":
        return False
    name = str(run.get("name") or "")
    if _INFRA_AGENT_NAME_RE.match(name):
        return False
    summary = str(run.get("summary") or "").lower()
    if any(marker in summary for marker in _ANTHROPIC_INFRA_MARKERS):
        return False
    return True


async def _fetch_agent_runs_for_action_items() -> list:
    """Fetch recent failed agent runs. Best-effort. Runs in a thread so
    the synchronous analyze_runs() call does not block the event loop.
    """
    try:
        import asyncio as _aio
        from services import agent_patterns
        runs = await _aio.to_thread(agent_patterns.analyze_runs)
        return runs or []
    except Exception:
        return []


async def generate_action_items() -> list[dict]:
    """Generate structured action items from tasks, emails, and calendar.

    Fans out the three upstream fetches (tasks, unread email, calendar)
    with asyncio.gather so total latency is bounded by the slowest
    single call instead of the sum of all three.

    Each item has: type, label, action_url, context.
    Types: reply_email, close_task, prep_meeting.

    Note: agent failures are intentionally never surfaced as briefing
    cards. Raw "Review failed: <agent>" cards were noisy and not
    actionable in the briefing surface. The Agents page is the one
    place to triage agent state. If the briefing ever needs to flag a
    run that needs retry, add a new card type with a plain-language
    label, not a raw failure.
    """
    import asyncio as _aio

    items: list[dict] = []

    # Fan out the three upstream fetches in parallel. Each helper catches
    # its own exceptions and returns an empty list on failure, so one
    # broken integration cannot break the briefing.
    all_tasks, unread_emails, events = await _aio.gather(
        _fetch_tasks_for_action_items(),
        _fetch_unread_emails_for_action_items(),
        _fetch_events_for_action_items(),
    )

    # 1. Overdue or stale tasks that could be closed. Run every task
    # through the shared is_visible_task helper so e2e smoke leftovers
    # and session tasks never show up as suggested actions. This keeps
    # the briefing in sync with the Tasks page and the Dashboard focus
    # list.
    from services.task_visibility import is_visible_task

    today = datetime.now(timezone.utc)
    for t in all_tasks:
        if not is_visible_task(t):
            continue
        task_id = t.get("id", "")
        title = t.get("title", "Untitled")
        priority = t.get("priority", "")
        created = t.get("created_at", "")
        if created:
            try:
                days_open = (today - datetime.fromisoformat(created.replace("Z", "+00:00"))).days
            except Exception:
                days_open = 0
        else:
            days_open = 0
        # Suggest closing tasks that are P0/P1 and open > 14 days
        if priority in ("P0", "P1") and days_open > 14:
            items.append({
                "type": "close_task",
                "label": f"Review: {title}",
                "action_url": f"/api/tasks/{task_id}",
                "context": f"This {priority} task has been open for {days_open} days. Check if it can be closed.",
            })
            if len(items) >= 5:
                break

    # 2. Unread emails that might need a reply. Filter out transactional
    # mail (receipts, order confirmations, no-reply senders, known
    # transactional domains, bulk mailers flagged by List-Unsubscribe)
    # via is_actionable_for_reply so Reply-to cards only surface email
    # a human would actually reply to. We iterate the full unread list
    # (not just the first 3) and take the first 3 actionable matches so
    # a burst of receipts cannot crowd out real email.
    reply_cards_added = 0
    for msg in unread_emails:
        if reply_cards_added >= 3:
            break
        if not is_actionable_for_reply(msg):
            continue
        subject = msg.get("subject", "No subject")
        # Prefer the richer from_name / from_email shape emitted by
        # services.gmail._parse_message, fall back to the combined
        # "from" string for older callers.
        from_name = msg.get("from_name")
        from_email = msg.get("from_email")
        if from_name and from_email:
            sender = f"{from_name} <{from_email}>"
        elif from_email:
            sender = from_email
        else:
            sender = msg.get("from", "Unknown sender")
        msg_id = msg.get("id", "")
        items.append({
            "type": "reply_email",
            "label": f"Reply to: {subject[:60]}",
            "action_url": f"/gmail?thread={msg_id}",
            "context": f"From {sender}.",
        })
        reply_cards_added += 1

    # 3. Upcoming meetings that might need prep
    now = datetime.now(timezone.utc)
    for ev in events[:2]:
        summary = ev.get("summary", "Untitled meeting")
        start_str = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date") or ""
        if start_str:
            try:
                start_dt = datetime.fromisoformat(start_str)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                # Only suggest prep for meetings that have not started yet
                if start_dt > now:
                    time_label = start_dt.strftime("%-I:%M %p").lower()
                    items.append({
                        "type": "prep_meeting",
                        "label": f"Prep for: {summary[:50]}",
                        "action_url": "/calendar",
                        "context": f"Starts at {time_label}.",
                    })
            except Exception:
                pass

    # 4. Recent agent runs are intentionally NOT surfaced as briefing
    # cards. "Review failed: <agent>" items were noisy and not
    # user-actionable. Agent triage lives on the Agents page. The
    # briefing focuses on calendar, email, and stale task nudges.

    # Cap at 6 items total so the dashboard stays clean
    items = items[:6]

    # Cache results
    state = _load_state()
    state["action_items"] = items
    _save_state(state)

    return items
