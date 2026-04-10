from pathlib import Path

from dotenv import load_dotenv

# Load .env before any router imports so that environment variables (like
# GOOGLE_CLIENT_ID) are available when modules read them at import time.
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routers import tasks, ideas, dashboard, settings, agents, chat, status, projects, transcripts, costs, auth, onboarding, search, threads, secrets, activity, docs, adventures, files, beautify, drive, notifications, upgrade, sync, calendar, gmail, gmail_reply, meeting_prep, workspace, briefing, workflows, shares, export, task_suggestions as task_suggestions_router, recurring_tasks as recurring_tasks_router, agent_patterns, enterprise, agentfiles, indexing, knowledge, predictions, growth

app = FastAPI(title="myOS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router, prefix="/api")
app.include_router(ideas.router, prefix="/api")
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
app.include_router(docs.router, prefix="/api")
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
app.include_router(task_suggestions_router.router, prefix="/api")
app.include_router(recurring_tasks_router.router, prefix="/api")
app.include_router(agent_patterns.router, prefix="/api")
app.include_router(agentfiles.router, prefix="/api")
app.include_router(indexing.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(predictions.router, prefix="/api")
app.include_router(growth.router, prefix="/api")


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


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "myos-api"}


# Serve the built frontend in production mode. When the app/dist directory
# exists (created by npm run build), serve it as static files. This lets
# the whole app run from a single server process.
_dist = Path(__file__).resolve().parent.parent / "app" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
