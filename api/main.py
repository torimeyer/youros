from pathlib import Path

from dotenv import load_dotenv

# Load .env before any router imports so that environment variables (like
# GOOGLE_CLIENT_ID) are available when modules read them at import time.
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from services.request_trace import TraceMiddleware
from services.loopback_guard import LoopbackGuardMiddleware
from services.security_headers import SecurityHeadersMiddleware

from routers import tasks, dashboard, settings, agents, chat, status, projects, transcripts, costs, auth, onboarding, search, threads, secrets, activity, specs, adventures, files, beautify, drive, notifications, upgrade, sync, calendar, gmail, gmail_reply, meeting_prep, workspace, briefing, workflows, shares, export, exports, task_suggestions as task_suggestions_router, recurring_tasks as recurring_tasks_router, agent_patterns, enterprise, agentfiles, indexing, knowledge, predictions, growth, task_audit, slack, github, project_import, push, decisions, team_dashboard, sessions, imessage, dogwalk, prototypes, models as models_router, probes, trace, pdf, diagrams, documents

app = FastAPI(title="myOS API")

import os as _os

# Restrict CORS to localhost origins so malicious webpages cannot make
# credentialed requests to the local API while the backend is running.
# In production/enterprise deployments set CORS_ALLOWED_ORIGINS to a
# comma-separated list of additional allowed origins.
_CORS_EXTRA = [o.strip() for o in _os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
_CORS_ORIGINS = [
    "https://localhost:3010",
    "https://127.0.0.1:3010",
    "http://localhost:3010",
    "http://127.0.0.1:3010",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://localhost:8000",
    "https://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
] + _CORS_EXTRA

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TraceMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# Defense-in-depth: reject non-loopback clients unless the operator opts
# in with ALLOW_NON_LOOPBACK=1. See .ostk/plans/security-privacy-review.md
# item G5. Registered AFTER SecurityHeadersMiddleware so the 401 response
# still carries the baseline security headers.
app.add_middleware(LoopbackGuardMiddleware)

app.include_router(tasks.router, prefix="/api")
app.include_router(task_audit.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(chat.router)
app.include_router(status.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(transcripts.router, prefix="/api")
app.include_router(costs.router, prefix="/api")
app.include_router(auth.router)
app.include_router(onboarding.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(threads.router, prefix="/api")
app.include_router(secrets.router, prefix="/api")
app.include_router(activity.router, prefix="/api")
app.include_router(trace.router, prefix="/api")
app.include_router(specs.router, prefix="/api")
app.include_router(specs._compat, prefix="/api")
app.include_router(adventures.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(beautify.router, prefix="/api")
app.include_router(drive.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(upgrade.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
app.include_router(calendar.router, prefix="/api")
app.include_router(gmail.router, prefix="/api")
app.include_router(gmail_reply.router, prefix="/api")
app.include_router(meeting_prep.router, prefix="/api")
app.include_router(workspace.router, prefix="/api")
app.include_router(briefing.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")
app.include_router(enterprise.router, prefix="/api")
app.include_router(shares.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(exports.router, prefix="/api")
app.include_router(task_suggestions_router.router, prefix="/api")
app.include_router(recurring_tasks_router.router, prefix="/api")
app.include_router(agent_patterns.router, prefix="/api")
app.include_router(agentfiles.router, prefix="/api")
app.include_router(indexing.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(predictions.router, prefix="/api")
app.include_router(growth.router, prefix="/api")
app.include_router(slack.router, prefix="/api")
app.include_router(github.router, prefix="/api")
app.include_router(project_import.router, prefix="/api")
app.include_router(push.router, prefix="/api")
app.include_router(decisions.router, prefix="/api")
app.include_router(team_dashboard.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(imessage.router, prefix="/api")
app.include_router(dogwalk.router, prefix="/api")
app.include_router(prototypes.router, prefix="/api")
app.include_router(models_router.router, prefix="/api")
app.include_router(probes.router, prefix="/api")
app.include_router(pdf.router, prefix="/api")
app.include_router(diagrams.router, prefix="/api")
app.include_router(documents.router, prefix="/api")


@app.on_event("startup")
async def fix_audit_watermark():
    """Auto-fix audit.jsonl watermark if it drifted from the actual file size."""
    import os
    ostk_dir = Path(__file__).resolve().parent.parent / ".ostk"
    audit_file = ostk_dir / "audit.jsonl"
    size_file = ostk_dir / "audit.size"
    if audit_file.exists() and size_file.exists():
        actual = os.path.getsize(audit_file)
        try:
            expected = int(size_file.read_text().strip())
        except (ValueError, OSError):
            expected = actual
        if actual != expected:
            size_file.write_text(str(actual))


@app.on_event("startup")
async def schedule_upgrade_check():
    """After a short delay, check for available updates and fire a notification.

    We wait 10 seconds so the server is fully up before doing network I/O.
    Only creates a notification if one with type 'upgrade' is not already unread.
    """
    import asyncio

    async def _check():
        await asyncio.sleep(10)
        try:
            from services.upgrade_check import check_all
            from services.notifications import notifications_service

            # Skip if an unread upgrade notification already exists
            if notifications_service.has_unread_of_type("upgrade"):
                return

            result = await check_all()
            myos = result.get("myos", {})
            ostk = result.get("ostk", {})

            behind_parts = []
            if myos.get("behind"):
                count = myos.get("commits_behind", 0)
                word = "commit" if count == 1 else "commits"
                behind_parts.append(f"myOS has {count} new {word}.")
            if ostk.get("behind"):
                latest_v = ostk.get("latest", "")
                current_v = ostk.get("current", "")
                behind_parts.append(
                    f"ostk {latest_v} is out (you have {current_v})."
                )

            if behind_parts:
                body = " ".join(behind_parts)
                notifications_service.add(
                    type="upgrade",
                    title="Update available",
                    body=body,
                    action_label="Update now",
                    action_url="/settings/upgrade",
                )
        except Exception:
            pass

    import asyncio
    asyncio.create_task(_check())


@app.on_event("startup")
async def schedule_label_backfill():
    """After a short delay, run auto-labeling on any open tasks that have no labels.

    We wait 5 seconds so the server is fully up and the ostk process is ready.
    This ensures fresh installs and upgrades never accumulate unlabeled tasks.
    """
    import asyncio

    async def _backfill():
        await asyncio.sleep(5)
        try:
            from services.ostk import ostk, OstkError
            from services.task_labels_store import task_labels_store
            from services.task_labeling import apply_auto_labels

            tasks = await ostk.list_tasks(status="open")
            all_assignments = task_labels_store.get_all_assignments()
            unlabeled = [t for t in tasks if not all_assignments.get(t.get("id", ""))]
            for task in unlabeled:
                task_id = task.get("id", "")
                title = task.get("title", "")
                if not task_id or not title:
                    continue
                try:
                    await asyncio.wait_for(
                        apply_auto_labels(task_id, title, ""),
                        timeout=10.0,
                    )
                except Exception:
                    pass
        except Exception:
            pass

    asyncio.create_task(_backfill())


@app.on_event("startup")
async def schedule_settings_sync_pull():
    """After a short delay, if sync is configured, pull from the remote.

    This means opening myOS on a new machine automatically pulls in the
    settings from the user's other device. We wait 15 seconds so the
    server is fully up before doing any git network I/O.
    """
    import asyncio

    async def _pull():
        await asyncio.sleep(15)
        try:
            from services import settings_sync
            from services.notifications import notifications_service

            if not settings_sync.is_configured():
                return

            result = settings_sync.pull()
            if result.get("files"):
                notifications_service.add(
                    type="sync",
                    title="Settings synced",
                    body="Settings synced from your other device.",
                    action_label="View Settings",
                    action_url="/settings",
                )
        except Exception:
            pass

    asyncio.create_task(_pull())


@app.on_event("startup")
async def schedule_gmail_unread_notification():
    """After a short delay, check for unread Gmail and fire a notification.

    Only fires if Gmail is authenticated and there are unread messages.
    Skips if an unread gmail notification already exists.
    """
    import asyncio

    async def _check():
        await asyncio.sleep(12)
        try:
            from services.google_auth import is_authenticated
            from services.notifications import notifications_service
            from services import gmail as gmail_service

            if not is_authenticated():
                return

            if notifications_service.has_unread_of_type("gmail"):
                return

            messages = await gmail_service.get_unread_summary()
            count = len(messages)
            if count > 0:
                word = "email" if count == 1 else "emails"
                notifications_service.add(
                    type="gmail",
                    title="Unread email",
                    body=f"You have {count} unread {word}.",
                    action_label="Open Gmail",
                    action_url="/gmail",
                )
        except Exception:
            pass

    asyncio.create_task(_check())


@app.on_event("startup")
async def schedule_overdue_task_check():
    """After a short delay, check for overdue tasks and fire a notification.

    A task is overdue when it has a due date in the past and is still open.
    Only fires once per day by checking for an existing unread overdue notification.
    """
    import asyncio

    async def _check():
        await asyncio.sleep(20)
        try:
            from datetime import date
            from services.ostk import ostk as _ostk
            from services.notifications import notifications_service

            if notifications_service.has_unread_of_type("task_overdue"):
                return

            tasks = await _ostk.list_tasks(status="open")
            today = date.today().isoformat()
            overdue = [
                t for t in tasks
                if t.get("due") and t["due"] < today
            ]
            if overdue:
                count = len(overdue)
                word = "task" if count == 1 else "tasks"
                notifications_service.add(
                    type="task_overdue",
                    title=f"{count} overdue {word}",
                    body=f"You have {count} {word} past their due date.",
                    action_label="View tasks",
                    action_url="/tasks",
                    metadata={"count": count},
                )
        except Exception:
            pass

    asyncio.create_task(_check())


@app.on_event("startup")
async def prewarm_claude_cli():
    """Pre-warm the local ``claude`` program so the first chat is fast.

    The first ``claude -p`` invocation after a cold boot takes 7 to 9
    seconds before the first token arrives because the Node.js runtime
    and CLI plugin loader have not been mapped into memory yet. Running a
    tiny prompt here at backend startup pays that cost in the background
    so the user never sees it. Measured benefit: first real chat drops
    from 7 to 9 seconds down to 3 to 5 seconds for the same prompt.
    """
    import asyncio
    from services import claude_code_provider

    async def _warm():
        await asyncio.sleep(2)  # let higher-priority startup tasks run first
        try:
            await claude_code_provider.prewarm_cli()
        except Exception:
            pass

    asyncio.create_task(_warm())


@app.on_event("startup")
async def pregenerate_briefing():
    """Pre-generate today's briefing in the background on startup.

    This way the briefing is cached and ready before anyone opens the
    dashboard, avoiding the 5-10 second wait on first load.
    """
    import asyncio
    from services.briefing import get_cached_briefing, generate_briefing

    async def _gen():
        await asyncio.sleep(3)  # let other startup tasks finish first
        if get_cached_briefing():
            return  # already cached for today
        try:
            await generate_briefing()
        except Exception:
            pass

    asyncio.create_task(_gen())


@app.on_event("startup")
async def prewarm_savings():
    """Pre-warm the ostk savings cache on startup.

    The savings endpoint shells out to ostk os metrics which takes 2-5
    seconds on the first call. Warming it here (both the token_metrics
    layer cache and the router-level _savings_cache) means the Cost
    Tracking page loads instantly on first visit and after restarts.
    """
    import asyncio
    from services import token_metrics

    async def _warm():
        await asyncio.sleep(2)
        try:
            # Warm the token_metrics layer first (shells out to ostk binary).
            token_metrics.get_ostk_savings()
            # Then warm the router cache for the "all" period so the first
            # HTTP request to /api/costs/savings?period=all returns in <10 ms.
            from routers.costs import _compute_savings_for_period, _savings_cache
            import time as _time
            result = _compute_savings_for_period(None)
            _savings_cache["all"] = (result, _time.monotonic() + 300.0)
        except Exception:
            pass

    asyncio.create_task(_warm())


@app.on_event("startup")
async def schedule_session_task_reaper():
    """Close auto-filed session tasks whose Claude Code session has ended.

    A Claude Code session can exit without firing its SessionEnd hook
    (force quit, crash, reboot). Without a backstop those auto-filed
    rows pile up on the Tasks page forever. This sweep checks every
    open session-task row and closes it when the linked transcript is
    stale and no live process claims the same session_id.
    """
    import asyncio

    from services.session_task_reaper import run_forever

    asyncio.create_task(run_forever())


@app.on_event("startup")
async def backfill_chat_ack_bots():
    """Start an ack bot for every running subagent after a restart.

    A backend restart wipes the in-memory ack bot registry but
    ``agent_metadata`` rehydrates from disk. Without this backfill
    every subagent that was mid-flight when the server bounced would
    lose its ack bot and Tori's inline chat would fall back to
    "picked up on next mailbox check" wording for the rest of the
    run. The small asyncio.sleep mirrors the other startup hooks and
    gives the router imports time to settle before we iterate.
    """
    import asyncio

    async def _run():
        await asyncio.sleep(1)
        try:
            from services import chat_ack_bot
            chat_ack_bot.start_for_running_agents()
        except Exception:
            pass

    asyncio.create_task(_run())


@app.on_event("startup")
async def sweep_stale_backend_sessions():
    """Retire leftover backend session rows from previous uvicorn workers.

    Every hot reload spawns a new worker that opens a new ostk session
    folder. The old worker's folder is left behind with a recent
    events.jsonl mtime, so the sidebar keeps counting it as a live
    session. This sweep back-dates those older folders once at startup
    so only the current worker's session shows up.
    """
    try:
        from routers.sessions import cleanup_stale_backend_sessions
        cleanup_stale_backend_sessions()
    except Exception:
        pass


@app.on_event("startup")
async def schedule_agent_reconciliation():
    """Reconcile agent state every 5 minutes.

    Scans all running agent_metadata entries. For each, checks if a live
    process exists or if a recent heartbeat was received. If neither is
    true, marks the agent as stopped. This catches agents that died
    without calling /complete (crashed, OOM, force-quit terminal).
    """
    import asyncio

    from routers.agents import _reconcile_loop

    asyncio.create_task(_reconcile_loop())


@app.on_event("startup")
async def schedule_recurring_task_spawner():
    """Spawn any due recurring tasks on boot, then repeat every 30 minutes.

    A recurring task is a rule the user set up in Settings that should
    regenerate a task on a schedule (e.g. "weekly status update every
    Friday"). This loop checks those rules, creates fresh tasks for the
    ones that are due, and records when each rule fired so it does not
    double-fire in the same day.
    """
    import asyncio

    async def _loop():
        # Give the server a moment to settle before touching ostk.
        await asyncio.sleep(8)
        while True:
            try:
                from services.recurring_tasks import spawn_due_tasks
                await spawn_due_tasks()
            except Exception:
                pass
            # Check again in 30 minutes.
            await asyncio.sleep(30 * 60)

    asyncio.create_task(_loop())


@app.on_event("startup")
async def schedule_test_artifact_sweep():
    """Run a periodic sweep that deletes any leaked test-artifact tasks.

    Layer 3 of defense in depth. Layer 1 rejects bad titles at create
    time and Layer 2 hides them at display time. This sweep catches
    anything that slips past both layers within 5 minutes by calling
    the same /api/tasks/cleanup-test-artifacts endpoint that runs on
    demand. Set ``MYOS_NO_SWEEP=1`` to disable (used by tests).

    The first sweep is delayed 30 seconds so initial migrations and
    other startup tasks finish first. After that it runs every 300
    seconds. When it finds anything, it logs a WARNING with the
    titles so the source of the leak can be traced back to whichever
    code path bypassed the create-time rejection.
    """
    import asyncio
    import logging
    import os

    if os.environ.get("MYOS_NO_SWEEP") == "1":
        return

    logger = logging.getLogger(__name__)

    async def _loop():
        # Wait 30 seconds before first sweep so the rest of startup
        # can settle without contention on the audit log.
        await asyncio.sleep(30)
        while True:
            try:
                from services.ostk import ostk, write_audit_entry
                from routers.tasks import (
                    _reject_bad_title,
                    _is_test_label_name,
                )
                from services.labels_store import labels_store
                from services.task_labels_store import task_labels_store

                # Pre-compute leaked titles so the warning log can name
                # them, then call the existing cleanup endpoint via the
                # router function to avoid duplicating delete logic.
                leaked_titles: list[str] = []
                try:
                    open_tasks = await ostk.list_tasks(status="open")
                    all_labels = labels_store.list_labels()
                    bad_label_ids = {
                        label["id"] for label in all_labels
                        if _is_test_label_name(label.get("name", ""))
                    }
                    all_assignments = task_labels_store.get_all_assignments()
                    for t in open_tasks:
                        title = t.get("title") or ""
                        task_id = t.get("id") or ""
                        if not task_id:
                            continue
                        matched_by_title = (
                            _reject_bad_title(title) is not None
                        )
                        assigned = set(all_assignments.get(task_id, []))
                        matched_by_label = bool(assigned & bad_label_ids)
                        if matched_by_title or matched_by_label:
                            leaked_titles.append(title)
                except Exception:
                    leaked_titles = []

                from routers.tasks import cleanup_test_artifacts
                result = await cleanup_test_artifacts()
                deleted = int(result.get("deleted", 0)) if result else 0

                if deleted > 0:
                    logger.warning(
                        "test_artifact_sweep cleaned %d leaked tasks: %s",
                        deleted,
                        leaked_titles,
                    )
                    logger.warning(
                        "investigate why _reject_bad_title missed these."
                    )

                try:
                    write_audit_entry({
                        "event_type": "test_artifact_sweep",
                        "deleted": deleted,
                        "leaked_titles": leaked_titles,
                    })
                except Exception:
                    pass
            except Exception:
                pass
            # Sweep again in 5 minutes.
            await asyncio.sleep(300)

    asyncio.create_task(_loop())


@app.on_event("startup")
async def schedule_test_artifact_spec_sweep():
    """Run a periodic sweep that deletes any leaked test-artifact specs.

    Mirrors ``schedule_test_artifact_sweep`` for tasks. Drafts and specs
    created by smoke runs (``Demo Smoke Spec 87311``, ``v5 verify spec``,
    ``e2e-...``, ``morning-verify-...``) sometimes survive their own
    teardown when a backend reload drops the delete request mid-flight.
    Without this loop they pile up in ``docs/draft/`` forever. The sweep
    runs every 5 minutes after a 45 second startup delay. Set
    ``MYOS_NO_SWEEP=1`` to disable (used by tests).
    """
    import asyncio
    import logging
    import os

    if os.environ.get("MYOS_NO_SWEEP") == "1":
        return

    logger = logging.getLogger(__name__)

    async def _loop():
        # Stagger the first run after the task sweep so they do not
        # contend for the audit log on a cold start.
        await asyncio.sleep(45)
        while True:
            try:
                from routers.specs import cleanup_test_artifact_specs
                from services.ostk import write_audit_entry

                result = await cleanup_test_artifact_specs()
                deleted = int(result.get("deleted", 0)) if result else 0

                if deleted > 0:
                    logger.warning(
                        "test_artifact_spec_sweep cleaned %d leaked specs: %s",
                        deleted,
                        result.get("deleted_paths", []),
                    )

                try:
                    write_audit_entry({
                        "event_type": "test_artifact_spec_sweep",
                        "deleted": deleted,
                        "deleted_paths": result.get("deleted_paths", []),
                    })
                except Exception:
                    pass
            except Exception:
                pass
            # Sweep again in 5 minutes.
            await asyncio.sleep(300)

    asyncio.create_task(_loop())


@app.on_event("startup")
async def install_signal_shutdown_hook():
    """Install a SIGTERM/SIGINT handler that notifies chat WebSockets.

    Why a signal handler and not the FastAPI ``shutdown`` event:
    uvicorn's graceful shutdown sequence closes active WebSockets with
    a ``1012 service restart`` close frame BEFORE the lifespan
    shutdown event fires. By the time ``on_event("shutdown")`` runs,
    the sockets are already gone. Installing our own SIGTERM/SIGINT
    handler on the running asyncio loop lets us push a friendly
    ``{"type": "error", "data": "backend restarting"}`` frame first,
    while the sockets are still open. The default uvicorn shutdown
    then runs afterwards. Safe: if signal install fails (Windows,
    unusual event loop), we silently skip and fall back to the UI's
    dropped-connection banner, which matches pre-fix behavior.
    """
    import asyncio
    import signal

    async def _notify_and_continue():
        try:
            from routers.chat import notify_active_websockets_of_shutdown
            await notify_active_websockets_of_shutdown(
                "Backend is restarting, please retry in a moment."
            )
        except Exception:
            pass

    loop = asyncio.get_running_loop()

    def _handler(signum):
        # Schedule the notifier and then re-raise the default signal
        # behavior so uvicorn's own shutdown still proceeds.
        asyncio.create_task(_notify_and_continue())
        # Remove our handler so a second signal hits the default.
        try:
            loop.remove_signal_handler(signum)
        except Exception:
            pass
        # Re-deliver to the default handler by raising it through
        # python's signal module.
        import os
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: _handler(s))
        except (NotImplementedError, RuntimeError):
            # add_signal_handler is unavailable on Windows and some
            # embedded loops. Skip silently in those cases.
            pass


@app.on_event("shutdown")
async def notify_chat_clients_on_shutdown():
    """Fallback notifier. Uvicorn closes WebSockets before this event
    fires, so this is a best-effort for the case where shutdown comes
    through a path that does not deliver SIGTERM (e.g. some test
    harnesses call the lifespan shutdown directly). The signal handler
    installed at startup is the primary mechanism.
    """
    try:
        from routers.chat import notify_active_websockets_of_shutdown
        await notify_active_websockets_of_shutdown(
            "Backend is restarting, please retry in a moment."
        )
    except Exception:
        pass


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "myos-api"}


# Serve the built frontend in production mode. When the app/dist directory
# exists (created by npm run build), serve it as static files. This lets
# the whole app run from a single server process.
_dist = Path(__file__).resolve().parent.parent / "app" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
