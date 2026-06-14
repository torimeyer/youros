"""File upload endpoints for agent templates (→1070).

Attached files are stored at ~/.youros/agent_uploads/{template_id}/ and their
names are tracked in the template record under the "attached_files" key.
When an agent from that template is spawned, the file content is injected as
a context block at the top of the prompt.

Supported file types: .txt, .md, .pdf, .docx
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from services import recent_deletes
from services.agent_templates_store import agent_templates_store
from services.youros_paths import youros_home

router = APIRouter(tags=["agents"])

AGENT_UPLOADS_DIR = youros_home() / "agent_uploads"

# Max file size: 5 MB
MAX_FILE_BYTES = 5 * 1024 * 1024

_ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}


def _safe_filename(name: str) -> str:
    """Sanitize an uploaded filename to prevent path traversal."""
    stem = Path(name).stem
    suffix = Path(name).suffix.lower()
    stem_clean = re.sub(r"[^a-zA-Z0-9._-]", "_", stem)[:80]
    return f"{stem_clean}{suffix}"


def _template_upload_dir(template_id: str) -> Path:
    d = AGENT_UPLOADS_DIR / template_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _extract_text(path: Path) -> str:
    """Extract plain text from a file. Raises HTTPException on unsupported type."""
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        try:
            return path.read_text(errors="replace")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not read file: {exc}")

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            raise HTTPException(status_code=500, detail="PDF support is not available on this server.")
        try:
            reader = PdfReader(str(path))
            return "\n\n".join(p.extract_text() or "" for p in reader.pages[:50]).strip()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not read PDF: {exc}")

    if suffix == ".docx":
        try:
            from docx import Document  # type: ignore
        except ImportError:
            raise HTTPException(status_code=500, detail="Word document support is not available on this server.")
        try:
            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs).strip()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not read Word document: {exc}")

    raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}. Allowed: txt, md, pdf, docx")


def read_attached_file_content(template_id: str, filename: str) -> str:
    """Return text content of an attached file. Empty string if file is missing."""
    path = AGENT_UPLOADS_DIR / template_id / filename
    if not path.exists():
        return ""
    try:
        return _extract_text(path)
    except Exception:
        return ""


def build_files_context(template_id: str, attached_files: list[str]) -> str:
    """Build a context block with content of all attached files for prompt injection."""
    if not attached_files:
        return ""
    parts: list[str] = []
    for filename in attached_files:
        content = read_attached_file_content(template_id, filename)
        if content.strip():
            parts.append(f"### Attached file: {filename}\n\n{content.strip()}")
    if not parts:
        return ""
    joined = "\n\n---\n\n".join(parts)
    return f"## Context files attached to this template\n\n{joined}"


@router.get("/agents/templates/{template_id}/uploads")
async def list_template_uploads(template_id: str):
    """List files attached to a template."""
    tpl = agent_templates_store.get_by_id(template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    files = tpl.get("attached_files", [])
    return {"template_id": template_id, "files": files}


@router.post("/agents/templates/{template_id}/upload")
async def upload_template_file(template_id: str, file: UploadFile = File(...)):
    """Upload and attach a file to a template.

    Stores the file at ~/.youros/agent_uploads/{template_id}/{filename} and
    adds the filename to the template's attached_files list.
    """
    tpl = agent_templates_store.get_by_id(template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")

    if tpl.get("source") == "builtin":
        raise HTTPException(status_code=400, detail="Cannot attach files to built-in templates")

    original_name = file.filename or "upload"
    suffix = Path(original_name).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
        )

    content = await file.read()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 5 MB.")
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    filename = _safe_filename(original_name)
    upload_dir = _template_upload_dir(template_id)
    dest = upload_dir / filename

    # Handle name collision by appending a counter
    if dest.exists():
        counter = 1
        base_stem = Path(filename).stem
        while dest.exists():
            dest = upload_dir / f"{base_stem}_{counter}{suffix}"
            counter += 1
        filename = dest.name

    dest.write_bytes(content)

    # Register filename in template record
    attached = list(tpl.get("attached_files") or [])
    if filename not in attached:
        attached.append(filename)
    agent_templates_store.update_attached_files(template_id, attached)

    return {"filename": filename, "size": len(content), "template_id": template_id}


@router.delete("/agents/templates/{template_id}/upload/{filename}")
async def delete_template_file(template_id: str, filename: str):
    """Remove an attached file from a template."""
    tpl = agent_templates_store.get_by_id(template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")

    safe = _safe_filename(filename)
    path = AGENT_UPLOADS_DIR / template_id / safe

    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    path.unlink(missing_ok=True)
    recent_deletes.record_id(f"template-upload:{template_id}:{safe}")

    attached = [f for f in (tpl.get("attached_files") or []) if f != safe]
    agent_templates_store.update_attached_files(template_id, attached)

    return {"deleted": safe, "template_id": template_id}
