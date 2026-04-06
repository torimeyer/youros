from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routers import tasks, ideas, dashboard, settings, agents, chat, status, projects, transcripts, costs, auth, onboarding

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


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "myos-api"}


# Serve the built frontend in production mode. When the app/dist directory
# exists (created by npm run build), serve it as static files. This lets
# the whole app run from a single server process.
_dist = Path(__file__).resolve().parent.parent / "app" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
