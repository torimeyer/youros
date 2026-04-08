from pathlib import Path

from dotenv import load_dotenv

# Load .env before any router imports so that environment variables (like
# GOOGLE_CLIENT_ID) are available when modules read them at import time.
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routers import tasks, ideas, dashboard, settings, agents, chat, status, projects, transcripts, costs, auth, onboarding, search, threads, secrets, activity, docs, adventures, files, beautify, drive

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


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "myos-api"}


# Serve the built frontend in production mode. When the app/dist directory
# exists (created by npm run build), serve it as static files. This lets
# the whole app run from a single server process.
_dist = Path(__file__).resolve().parent.parent / "app" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
