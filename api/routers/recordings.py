"""Terminal recording endpoints.

POST /recordings/start          - start a new recording session
POST /recordings/{id}/command   - run a command (captured to the cast file)
POST /recordings/{id}/stop      - finalize the recording
GET  /recordings                - list all recordings
GET  /recordings/{id}/cast      - download the .cast file for playback
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import services.recordings as rec_svc

router = APIRouter(tags=["recordings"])


class StartRecordingRequest(BaseModel):
    label: Optional[str] = ""
    width: Optional[int] = 80
    height: Optional[int] = 24


class RunCommandRequest(BaseModel):
    cmd: str


@router.post("/recordings/start")
def start_recording(body: StartRecordingRequest):
    """Start a new terminal recording. Returns the recording ID."""
    return rec_svc.start_recording(
        label=body.label or "",
        width=body.width or 80,
        height=body.height or 24,
    )


@router.post("/recordings/{rec_id}/command")
async def run_command(rec_id: str, body: RunCommandRequest):
    """Run a shell command and append its output to the recording."""
    if not body.cmd or not body.cmd.strip():
        raise HTTPException(status_code=400, detail="Command cannot be empty")
    try:
        return await rec_svc.run_command(rec_id, body.cmd.strip())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/recordings/{rec_id}/stop")
def stop_recording(rec_id: str):
    """Stop and finalize a recording."""
    try:
        return rec_svc.stop_recording(rec_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/recordings")
def list_recordings():
    """List all terminal recordings."""
    return {"recordings": rec_svc.list_recordings()}


@router.get("/recordings/{rec_id}/cast")
def get_cast(rec_id: str):
    """Download a recording's .cast file (asciicast v2)."""
    path = rec_svc.get_cast_path(rec_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(
        str(path),
        media_type="application/json",
        filename=f"{rec_id}.cast",
    )
