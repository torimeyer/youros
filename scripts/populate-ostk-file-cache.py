#!/usr/bin/env python3
"""
populate-ostk-file-cache.py

Populates .ostk/file_cache.jsonl with Anthropic Files API file_ids.

PREREQUISITES
-------------
- ANTHROPIC_API_KEY env var set (or subscription OAuth — test first)
- Files to upload must be >= 512 bytes (proxy min_size_threshold)

USAGE
-----
    python3 scripts/populate-ostk-file-cache.py [FILE1 FILE2 ...]

If no files are given, uses the built-in high-value defaults.

IDEMPOTENT
----------
If a file's SHA-256 is already in the cache with the same content, it is
skipped (no re-upload). If the content changed (new SHA-256), it re-uploads
and updates the entry.

NOTE
----
As of 2026-05-14, this requires an Anthropic API key. Subscription OAuth
auth has NOT been tested against POST /v1/files. See:
docs/ostk-cache-mechanism-4-research.md
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---- config ----------------------------------------------------------------

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/files"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_BETA = "files-api-2025-04-14"

MIN_SIZE_BYTES = 512  # proxy min_size_threshold default

# Default candidate files (highest savings value from →1335 retro)
HOME = Path.home()
REPO_ROOT = Path(__file__).resolve().parent.parent
# Claude Code encodes a project's path into ~/.claude/projects/ by replacing
# every "/" with "-". Derive the slug from this repo's path so the defaults
# work for any user/checkout instead of a hardcoded personal path.
_PROJECT_SLUG = str(REPO_ROOT).replace("/", "-")
DEFAULT_FILES = [
    HOME / f".claude/projects/{_PROJECT_SLUG}/memory/MEMORY.md",
    REPO_ROOT.parent / "CLAUDE.md",
    REPO_ROOT / "CLAUDE.md",
]

# .ostk/ directory — relative to this script's repo root or explicit OSTK_DIR
OSTK_DIR = Path(os.environ.get("OSTK_DIR", Path(__file__).parent.parent / ".ostk"))
CACHE_FILE = OSTK_DIR / "file_cache.jsonl"

# ---- helpers ----------------------------------------------------------------

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_cache() -> dict:
    """Load existing cache; returns dict keyed by sha256."""
    entries = {}
    if CACHE_FILE.exists():
        for line in CACHE_FILE.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("sha256"):
                    entries[entry["sha256"]] = entry
            except json.JSONDecodeError:
                pass
    return entries


def save_cache(entries: dict) -> None:
    OSTK_DIR.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e) for e in entries.values()]
    CACHE_FILE.write_text("\n".join(lines) + "\n" if lines else "")


def upload_file(path: Path, content: bytes) -> str:
    """Upload file to Anthropic Files API. Returns file_id."""
    try:
        import urllib.request, urllib.error
        import mimetypes

        mime = mimetypes.guess_type(str(path))[0] or "text/plain"
        boundary = "----PythonFormBoundary7MA4YWxkTrZu0gW"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            ANTHROPIC_API_URL,
            data=body,
            method="POST",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": ANTHROPIC_VERSION,
                "anthropic-beta": ANTHROPIC_BETA,
                "content-type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result["id"]
    except Exception as e:
        raise RuntimeError(f"Upload failed for {path}: {e}") from e


# ---- main -------------------------------------------------------------------

def main(file_paths: list[Path]) -> None:
    if not ANTHROPIC_API_KEY:
        print(
            "ERROR: ANTHROPIC_API_KEY not set.\n"
            "On Claude subscription, test whether OAuth works first.\n"
            "See docs/ostk-cache-mechanism-4-research.md",
            file=sys.stderr,
        )
        sys.exit(1)

    cache = load_cache()
    written = 0
    skipped = 0
    failed = 0

    for path in file_paths:
        path = Path(path).expanduser().resolve()
        if not path.exists():
            print(f"  SKIP (not found): {path}")
            skipped += 1
            continue

        content = path.read_bytes()
        size = len(content)

        if size < MIN_SIZE_BYTES:
            print(f"  SKIP (too small, {size}B < {MIN_SIZE_BYTES}B): {path.name}")
            skipped += 1
            continue

        digest = sha256_hex(content)

        if digest in cache and not cache[digest].get("stale", False):
            print(f"  SKIP (already cached, sha256={digest[:12]}): {path.name}")
            skipped += 1
            continue

        print(f"  UPLOAD: {path.name} ({size:,} bytes) ...", end=" ", flush=True)
        try:
            file_id = upload_file(path, content)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            cache[digest] = {
                "path": str(path),
                "file_id": file_id,
                "uploaded_at": now,
                "size": size,
                "last_seen_gen": 0,
                "stale": False,
                "sha256": digest,
            }
            print(f"OK ({file_id})")
            written += 1
        except RuntimeError as e:
            print(f"FAILED: {e}")
            failed += 1

    save_cache(cache)
    print(
        f"\nDone. written={written} skipped={skipped} failed={failed}"
        f"\nCache: {CACHE_FILE}"
    )
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    paths = [Path(p) for p in sys.argv[1:]] if len(sys.argv) > 1 else DEFAULT_FILES
    main(paths)
