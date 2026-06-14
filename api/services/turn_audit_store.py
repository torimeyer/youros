"""Per-turn file change audit store for the chat audit / undo trail (→1400).

Lifecycle:
  turn_id = store.begin_turn(tab_id, repo_root)
      → snapshot dirty files; record start time
  ... chat turn runs; model edits files ...
  store.end_turn(turn_id, gen_table_path, repo_root)
      → scan gen_table for new writes; build diffs; persist
  store.get_file_changes(turn_id) → list[dict]
  store.undo_file(turn_id, path)   → raises ConflictError if externally modified
  store.redo_file(turn_id, path)
  store.undo_all(turn_id)

Persistence: each turn's record is written to
  <data_dir>/turn_audit/<turn_id>.json
so records survive a tab refresh or backend restart.

The data_dir defaults to ~/.youros/; tests override via MYOS_TURN_AUDIT_DIR env var
or by passing data_dir= directly.
"""

from __future__ import annotations

import difflib
import json
import mimetypes
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from services.youros_paths import youros_home

_DEFAULT_DATA_DIR = youros_home()
DATA_DIR = _DEFAULT_DATA_DIR

# Patterns for writers that indicate a model wrote the file during a chat turn.
# The ostk kernel tags writes with "claude-code-NNNN" or "myos-api-NNNN".
_WRITER_PREFIXES = ("claude-code-", "myos-api-")

# We keep the in-memory undo stack here (not on disk) because it only matters
# while the process is alive.  disk-persisted data is the authoritative record.
_UNDO_STACKS: dict[str, dict[str, str]] = {}  # turn_id -> {path -> pre-undo content}


class ConflictError(Exception):
    """Raised when a file was externally modified after the turn that changed it."""


class TurnAuditStore:
    def __init__(self, data_dir: Optional[Path] = None) -> None:
        env_dir = os.environ.get("MYOS_TURN_AUDIT_DIR")
        if data_dir is None and env_dir:
            data_dir = Path(env_dir)
        self._base = data_dir or _DEFAULT_DATA_DIR
        self._dir = self._base / "turn_audit"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def begin_turn(self, tab_id: str, repo_root: Optional[Path] = None) -> str:
        """Start tracking a new turn. Returns the new turn_id (UUID)."""
        turn_id = str(uuid.uuid4())
        record: dict[str, Any] = {
            "turn_id": turn_id,
            "tab_id": tab_id,
            "start_time": time.time(),
            "start_ts": datetime.now(timezone.utc).isoformat(),
            "end_time": None,
            "before_snapshots": {},
            "file_changes": [],
        }
        self._save(turn_id, record)

        # Snapshot currently-dirty files so we have their before content.
        if repo_root and (Path(repo_root) / ".git").exists():
            self._snapshot_dirty_files(turn_id, repo_root, record)

        return turn_id

    def _snapshot_dirty_files(
        self, turn_id: str, repo_root: Path, record: dict
    ) -> None:
        """Read and store the content of all uncommitted modified files."""
        import subprocess
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
            cwd=str(repo_root),
        )
        for line in result.stdout.splitlines():
            status = line[:2].strip()
            if status in ("M", "MM", "AM") and len(line) > 3:
                rel = line[3:].strip()
                abs_path = str(Path(repo_root) / rel)
                try:
                    content = Path(abs_path).read_text(errors="replace")
                    record["before_snapshots"][abs_path] = content
                except OSError:
                    pass
        self._save(turn_id, record)

    def _snapshot_file(self, turn_id: str, path: str, content: str) -> None:
        """Manually add a before-snapshot for a specific file. Used in tests."""
        record = self._load(turn_id)
        if record is None:
            return
        record["before_snapshots"][path] = content
        self._save(turn_id, record)

    def end_turn(
        self,
        turn_id: str,
        gen_table_path: Optional[Path] = None,
        repo_root: Optional[Path] = None,
    ) -> None:
        """Scan gen_table for files written during this turn; compute diffs."""
        record = self._load(turn_id)
        if record is None:
            return

        if gen_table_path is None:
            gen_table_path = self._base / ".ostk" / "gen_table.jsonl"

        start_time = record["start_time"]
        before_snapshots: dict[str, str] = record.get("before_snapshots", {})

        # Find all files written since this turn started.
        changed_paths = self._scan_gen_table(gen_table_path, start_time)

        file_changes = []
        for path in changed_paths:
            fc = self._build_file_change(path, before_snapshots, repo_root)
            if fc:
                file_changes.append(fc)

        record["end_time"] = time.time()
        record["file_changes"] = file_changes
        self._save(turn_id, record)

    def get_file_changes(self, turn_id: str) -> list[dict]:
        record = self._load(turn_id)
        if record is None:
            return []
        return record.get("file_changes", [])

    def get_record(self, turn_id: str) -> Optional[dict]:
        return self._load(turn_id)

    def undo_file(self, turn_id: str, path: str) -> None:
        """Restore file to its before-turn content.

        Raises ConflictError if the file was externally modified after the
        turn ended (i.e. current content != content_after stored in the record).
        """
        fc = self._find_change(turn_id, path)
        if fc is None:
            raise KeyError(f"No file change record for path {path!r} in turn {turn_id}")

        current_content: Optional[str] = None
        if not fc["is_binary"]:
            try:
                current_content = Path(path).read_text(errors="replace")
            except OSError:
                current_content = None

        # Conflict check: if file has been modified since the turn, the current
        # content won't match what we stored as content_after.
        content_after = fc.get("content_after")
        if (
            not fc["is_binary"]
            and content_after is not None
            and current_content is not None
            and current_content != content_after
        ):
            raise ConflictError(
                f"File {path!r} was modified after the turn ended. "
                "Undo aborted to avoid overwriting external changes."
            )

        # Store the post-undo content in the undo stack so redo can restore.
        if turn_id not in _UNDO_STACKS:
            _UNDO_STACKS[turn_id] = {}

        if fc["is_binary"]:
            content_before_bytes = fc.get("content_before_bytes")
            if content_before_bytes is not None:
                Path(path).write_bytes(bytes(content_before_bytes))
        else:
            content_before = fc.get("content_before", "")
            _UNDO_STACKS[turn_id][path] = content_after or ""
            Path(path).write_text(content_before, errors="replace")

    def redo_file(self, turn_id: str, path: str) -> None:
        """Re-apply the change after an undo."""
        stack = _UNDO_STACKS.get(turn_id, {})
        if path not in stack:
            raise KeyError(f"No undo history for path {path!r} in turn {turn_id}")
        Path(path).write_text(stack.pop(path), errors="replace")

    def undo_all(self, turn_id: str) -> None:
        """Undo every file change in this turn. Conflict errors are collected."""
        for fc in self.get_file_changes(turn_id):
            try:
                self.undo_file(turn_id, fc["path"])
            except ConflictError:
                # Skip conflicted files; caller can surface via file_changes endpoint
                pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_gen_table(self, gen_table_path: Path, start_time: float) -> list[str]:
        """Return unique file paths written after start_time by a model writer."""
        if not gen_table_path.exists():
            return []

        seen: dict[str, bool] = {}  # path -> True (preserve last-write order)
        try:
            for line in gen_table_path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                writer = entry.get("writer", "")
                if not any(writer.startswith(p) for p in _WRITER_PREFIXES):
                    continue
                ts_str = entry.get("timestamp", "")
                if ts_str:
                    try:
                        ts = _parse_ts(ts_str)
                        # gen_table timestamps are second-precision; start_time
                        # has sub-second precision.  Truncate to avoid dropping
                        # entries that land in the same second as begin_turn.
                        if ts < int(start_time):
                            continue
                    except Exception:
                        continue
                path = entry.get("path", "")
                if path:
                    seen[path] = True
        except OSError:
            pass

        return list(seen.keys())

    def _build_file_change(
        self,
        path: str,
        before_snapshots: dict[str, str],
        repo_root: Optional[Path],
    ) -> Optional[dict]:
        """Build a FileChange dict for the given path."""
        p = Path(path)

        # Detect binary
        mime_type, _ = mimetypes.guess_type(path)
        is_binary = _is_binary_file(p)

        if is_binary:
            size = p.stat().st_size if p.exists() else 0
            return {
                "path": path,
                "diff": "",
                "content_before": None,
                "content_after": None,
                "is_binary": True,
                "size_bytes": size,
                "mime_type": mime_type or "application/octet-stream",
            }

        try:
            content_after = p.read_text(errors="replace")
        except OSError:
            content_after = ""

        # Determine before content
        content_before: str
        if path in before_snapshots:
            content_before = before_snapshots[path]
        else:
            content_before = _git_show_head(path, repo_root) or content_after

        # Compute unified diff
        diff_lines = list(difflib.unified_diff(
            content_before.splitlines(keepends=True),
            content_after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        ))
        diff = "".join(diff_lines)

        return {
            "path": path,
            "diff": diff,
            "content_before": content_before,
            "content_after": content_after,
            "is_binary": False,
            "size_bytes": len(content_after.encode()),
            "mime_type": mime_type or "text/plain",
        }

    def _find_change(self, turn_id: str, path: str) -> Optional[dict]:
        for fc in self.get_file_changes(turn_id):
            if fc["path"] == path:
                return fc
        return None

    def _save(self, turn_id: str, record: dict) -> None:
        path = self._dir / f"{turn_id}.json"
        path.write_text(json.dumps(record, default=str))

    def _load(self, turn_id: str) -> Optional[dict]:
        path = self._dir / f"{turn_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts_str: str) -> float:
    """Parse an ISO-8601 timestamp string to a Unix epoch float."""
    ts_str = ts_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts_str)
    return dt.timestamp()


def _is_binary_file(path: Path) -> bool:
    """Heuristic: read first 8K bytes and check for null bytes."""
    if not path.exists():
        return False
    try:
        chunk = path.read_bytes()[:8192]
        return b"\x00" in chunk
    except OSError:
        return False


def _git_show_head(path: str, repo_root: Optional[Path]) -> Optional[str]:
    """Return the content of *path* at HEAD via git show.

    Returns None if git isn't available or the file isn't tracked.
    """
    if repo_root is None:
        return None
    import subprocess
    p = Path(path)
    try:
        rel = p.relative_to(repo_root)
    except ValueError:
        return None
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            capture_output=True, text=True, timeout=10,
            cwd=str(repo_root),
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    return None


# Module-level singleton (tests can create their own TurnAuditStore instances).
turn_audit_store = TurnAuditStore()
