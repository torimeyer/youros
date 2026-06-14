"""Source library service — file storage + full-text excerpt retrieval (→1530).

Files land in SOURCES_BASE/<workspace>/<id>.<ext> with a <id>.json sidecar.
At spawn time, KNOWLEDGE-tagged sources are searched and the top 3 matching
excerpts are prepended to the agent prompt under "Reference material:".
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from services.excerpts import Excerpt, format_excerpts
from services.youros_paths import youros_home

SOURCES_BASE = youros_home() / "sources"
_DEFAULT_WORKSPACE = "default"
_EXCERPT_WINDOW = 400   # chars around each match
_MAX_EXCERPTS = 3


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_text(path: Path) -> str:
    """Return plain text from a file. Supports TXT, MD, PDF, DOCX."""
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        return path.read_text(errors="replace")

    if suffix == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            parts = []
            for page in reader.pages:
                text = page.extract_text() or ""
                parts.append(text)
            return "\n\n".join(parts)
        except Exception:
            return ""

    if suffix == ".docx":
        try:
            import docx
            doc = docx.Document(str(path))
            return "\n\n".join(p.text for p in doc.paragraphs if p.text)
        except Exception:
            return ""

    return ""


# ---------------------------------------------------------------------------
# Excerpt search (internal, returns Excerpt objects)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"\w+", text) if len(t) > 2]


def _search_excerpts(path: Path, query_terms: list[str], source: dict) -> list[Excerpt]:
    """Return up to _MAX_EXCERPTS Excerpt objects that contain any query term."""
    if not query_terms:
        return []

    text = _extract_text(path)
    if not text:
        return []

    terms_lower = [t.lower() for t in query_terms]
    found: list[tuple[int, str]] = []

    for term in terms_lower:
        start = 0
        while True:
            idx = text.lower().find(term, start)
            if idx == -1:
                break
            begin = max(0, idx - _EXCERPT_WINDOW // 2)
            end = min(len(text), idx + _EXCERPT_WINDOW // 2)
            excerpt = text[begin:end].strip()
            found.append((idx, excerpt))
            start = idx + len(term)

    if not found:
        return []

    found.sort(key=lambda x: x[0])
    deduped: list[str] = []
    last_end = -1
    for pos, excerpt in found:
        if pos > last_end:
            deduped.append(excerpt)
            last_end = pos + _EXCERPT_WINDOW
        if len(deduped) >= _MAX_EXCERPTS:
            break

    sid = source.get("id", path.stem)
    title = source.get("title", sid)
    return [
        Excerpt(
            text=exc,
            source_id=sid,
            source_title=title,
            deep_link=None,
            score=1.0,
            access_denied=False,
            provider="source_library",
        )
        for exc in deduped[:_MAX_EXCERPTS]
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _sources_dir(workspace: str = _DEFAULT_WORKSPACE) -> Path:
    d = SOURCES_BASE / workspace
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_sources(workspace: str = _DEFAULT_WORKSPACE) -> list[dict]:
    """Return all sources for the workspace, sorted newest first."""
    d = SOURCES_BASE / workspace
    if not d.exists():
        return []
    sources = []
    for meta_file in d.glob("*.json"):
        try:
            data = json.loads(meta_file.read_text())
            sources.append(data)
        except Exception:
            pass
    sources.sort(key=lambda s: s.get("upload_time", ""), reverse=True)
    return sources


def get_knowledge_excerpts_structured(
    knowledge_tags: list[str],
    user_input: str,
    workspace: str = _DEFAULT_WORKSPACE,
) -> list[Excerpt]:
    """Return typed Excerpts for knowledge sources matching any of knowledge_tags."""
    if not knowledge_tags:
        return []

    sources = list_sources(workspace)
    matching = [
        s for s in sources
        if any(t in (s.get("tags") or []) for t in knowledge_tags)
    ]
    if not matching:
        return []

    query_terms = _tokenize(user_input)[:20]
    d = _sources_dir(workspace)

    all_excerpts: list[Excerpt] = []
    for source in matching:
        sid = source.get("id", "")
        candidates = list(d.glob(f"{sid}.*"))
        content_files = [c for c in candidates if c.suffix != ".json"]
        for cf in content_files:
            excerpts = _search_excerpts(cf, query_terms, source)
            all_excerpts.extend(excerpts)
        if len(all_excerpts) >= _MAX_EXCERPTS:
            break

    if not all_excerpts:
        for source in matching[:_MAX_EXCERPTS]:
            sid = source.get("id", "")
            candidates = list(d.glob(f"{sid}.*"))
            content_files = [c for c in candidates if c.suffix != ".json"]
            for cf in content_files:
                text = _extract_text(cf)[:_EXCERPT_WINDOW].strip()
                if text:
                    all_excerpts.append(
                        Excerpt(
                            text=text,
                            source_id=sid,
                            source_title=source.get("title", sid),
                            deep_link=None,
                            score=1.0,
                            access_denied=False,
                            provider="source_library",
                        )
                    )
                    break

    return all_excerpts[:_MAX_EXCERPTS]


def get_knowledge_excerpts(
    knowledge_tags: list[str],
    user_input: str,
    workspace: str = _DEFAULT_WORKSPACE,
) -> str:
    """Find sources matching any of knowledge_tags, search for user_input terms,
    return a formatted "Reference material:" block, or "" if nothing found.

    Backwards-compatible: returns str, renders via format_excerpts.
    """
    excerpts = get_knowledge_excerpts_structured(knowledge_tags, user_input, workspace)
    if not excerpts:
        return ""
    return format_excerpts(excerpts)
