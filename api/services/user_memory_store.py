"""Per-user MEMORY.md store.

Provides read/write access to ~/.myos/users/default/MEMORY.md.
The file is plain markdown with two optional top-level sections::

    # Preferences
    - Use plain language. No jargon.

    # Facts
    - I'm a product manager, not an engineer.

Each bullet may have an HTML comment with a UTC timestamp for provenance:
    - Some preference <!-- added 2026-05-17T01:23Z -->

Writes use fcntl.flock for process-level safety on concurrent tab writes.
Reads use an mtime-based cache so disk is not hit on every chat turn.
"""

from __future__ import annotations

import fcntl
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

_MEMORY_PATH = Path.home() / ".myos" / "users" / "default" / "MEMORY.md"
_WARN_SIZE_BYTES = 50 * 1024  # 50 KB

# Module-level mtime cache. Protected by a threading.Lock because uvicorn
# may service multiple concurrent requests in a single process.
_cache_lock = threading.Lock()
_cached_mtime: float = -1.0
_cached_content: str = ""

# Single-slot undo: the file content immediately before the last remove_bullet call.
_undo_snapshot: str | None = None


# ── public API ────────────────────────────────────────────────────────────────


def read() -> str:
    """Return the full contents of the user memory file.

    Returns an empty string when the file does not exist (silent, no log spam).
    Cache is invalidated on mtime change.
    """
    global _cached_mtime, _cached_content
    path = _memory_path()
    with _cache_lock:
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            _cached_mtime = -1.0
            _cached_content = ""
            return ""
        if mtime == _cached_mtime:
            return _cached_content
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return ""
        _cached_mtime = mtime
        _cached_content = content
        return content


def append_bullet(section: str, text: str) -> None:
    """Append a bullet under *section* (e.g. "Preferences" or "Facts").

    Creates the file and directory structure on first write. Uses fcntl.flock
    for an exclusive write lock. Appends an HTML comment with UTC timestamp.
    Logs a warning when the file exceeds the 50 KB soft threshold.
    """
    path = _memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    bullet = f"- {text} <!-- added {ts} -->"
    heading = f"# {section}"

    # Open in append+read mode so the file is created if absent.
    with open(path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            content = fh.read()
            content = _ensure_section(content, section)
            content = _insert_bullet(content, heading, bullet)
            fh.seek(0)
            fh.truncate()
            fh.write(content)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)

    with _cache_lock:
        _reset_cache()

    size = path.stat().st_size
    if size >= _WARN_SIZE_BYTES:
        _log.warning(
            "user MEMORY.md is %d bytes (>= 50 KB). "
            "Consider editing it in Settings to trim old entries.",
            size,
        )


def remove_bullet(query: str) -> "str | list[str] | None":
    """Find and remove a bullet matching *query* from the memory file.

    Fuzzy-matches using case-insensitive substring check. Returns:
      - The removed bullet text (str) on a single match.
      - A list of candidate bullet texts (list[str]) on multiple matches.
      - None if no bullet matches.

    On single-match removal, saves a snapshot of the pre-removal content
    into ``_undo_snapshot`` so ``restore_undo()`` can reverse it.
    """
    global _undo_snapshot

    path = _memory_path()
    if not path.exists():
        return None

    with open(path, "r+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            content = fh.read()
            bullets = _extract_bullets(content)
            q_lower = query.lower()
            # Fuzzy: any keyword in query (≥3 chars) appears in bullet text
            matches = [b for b in bullets if _fuzzy_match(q_lower, b.lower())]

            if not matches:
                return None
            if len(matches) > 1:
                return [_strip_provenance(b) for b in matches]

            # Single match: remove it and save undo snapshot.
            _undo_snapshot = content
            new_content = _remove_line_containing(content, matches[0])
            fh.seek(0)
            fh.truncate()
            fh.write(new_content)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)

    with _cache_lock:
        _reset_cache()

    return _strip_provenance(matches[0])


def restore_undo() -> bool:
    """Restore the file to the snapshot saved by the last ``remove_bullet`` call.

    Returns True on success, False if no snapshot exists.
    """
    global _undo_snapshot
    if _undo_snapshot is None:
        return False

    snapshot = _undo_snapshot
    _undo_snapshot = None
    replace_all(snapshot)
    return True


def replace_all(text: str) -> None:
    """Replace the entire memory file with *text*.

    Creates parent directories if they do not exist. Uses fcntl.flock.
    """
    path = _memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(text)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)

    with _cache_lock:
        _reset_cache()


# ── private helpers ───────────────────────────────────────────────────────────


def _extract_bullets(content: str) -> list[str]:
    """Return all bullet lines (starting with '- ') from *content*."""
    return [line.strip() for line in content.splitlines() if line.lstrip().startswith("- ")]


def _fuzzy_match(query: str, bullet_lower: str) -> bool:
    """Return True if *query* overlaps *bullet_lower* by substring or keyword."""
    if query in bullet_lower:
        return True
    # Keyword overlap: any query word ≥3 chars that appears in the bullet
    keywords = [w for w in query.split() if len(w) >= 3]
    return any(kw in bullet_lower for kw in keywords)


def _strip_provenance(bullet: str) -> str:
    """Remove the '<!-- added ... -->' comment from a bullet line."""
    import re
    return re.sub(r"\s*<!--.*?-->", "", bullet).strip()


def _remove_line_containing(content: str, bullet: str) -> str:
    """Return *content* with the line containing *bullet* removed."""
    bullet_text = _strip_provenance(bullet)
    lines = content.splitlines(keepends=True)
    result = []
    for line in lines:
        stripped = _strip_provenance(line.rstrip())
        if stripped == bullet_text:
            continue
        result.append(line)
    return "".join(result)


def _memory_path() -> Path:
    return _MEMORY_PATH


def _reset_cache() -> None:
    """Reset mtime cache. Caller must hold _cache_lock."""
    global _cached_mtime, _cached_content
    _cached_mtime = -1.0
    _cached_content = ""


def _ensure_section(content: str, section: str) -> str:
    """Return *content* guaranteed to contain a *# section* heading."""
    heading = f"# {section}"
    if heading in content:
        return content
    if content and not content.endswith("\n"):
        content += "\n"
    content += f"\n{heading}\n"
    return content


def _insert_bullet(content: str, heading: str, bullet: str) -> str:
    """Insert *bullet* on the line immediately after *heading*."""
    lines = content.splitlines(keepends=True)
    result: list[str] = []
    inserted = False
    for line in lines:
        result.append(line)
        if not inserted and line.rstrip() == heading:
            result.append(bullet + "\n")
            inserted = True
    if not inserted:
        result.append(f"\n{heading}\n{bullet}\n")
    return "".join(result)
