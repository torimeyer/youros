"""Atomic file write helpers for myOS data stores.

Every JSON store under ``api/services/*_store.py`` persists user state
(tasks, settings, labels, threads, templates, chat history, etc.).
Before this helper they all called ``path.write_text(json.dumps(...))``
which is a non-atomic write: the old file is truncated first, then new
bytes are written. A SIGKILL, power loss, disk-full error, or OS crash
between those two steps leaves an empty or partial file and Tori loses
state. Replacing the direct writes with ``atomic_write_text`` writes to
a sibling ``.tmp`` file and ``os.replace``'s it into place. On POSIX
``os.replace`` is an atomic rename so the final path either points at
the old content or the new content, never a half-written file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Union


def atomic_write_text(path: Union[str, Path], content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    Creates parent dirs if missing, writes to ``<path>.tmp``, flushes +
    fsyncs the temp file, then ``os.replace``s it onto the target path.
    If any step fails the temp file is removed and the original path is
    untouched.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        # Open, write, flush, fsync. fsync forces the bytes to disk
        # before the rename so a crash right after os.replace cannot
        # resurrect the old content with a half-flushed buffer.
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # fsync can fail on some filesystems (tmpfs under test
                # fixtures for example). The rename is still safer than
                # a direct write so do not abort the save.
                pass
        os.replace(tmp, p)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_json(path: Union[str, Path], data: Any, *, indent: int = 2) -> None:
    """Serialize ``data`` and write it atomically. Convenience wrapper
    around :func:`atomic_write_text` so stores do not have to repeat
    the ``json.dumps(..., indent=2)`` incantation on every save.
    """
    atomic_write_text(path, json.dumps(data, indent=indent, ensure_ascii=False))
