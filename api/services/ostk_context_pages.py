"""Async service wrapper around ostk 7.7.7 tiered context pages (->3114).

Supports listing hot/warm/cold context pages, storing pages, toggling eviction
protection (pin/unpin), and promoting warm/cold pages back to hot (restore).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import re
from typing import Any

from services.ostk import get_effective_root

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT_SECONDS = 8.0

_SUMMARY_RE = re.compile(
    r"([\d,]+)\s+pages?,\s*([\d,]+)\s+tokens?\s+total(?:\s*\(([\d,]+)\s+hot\))?",
    re.IGNORECASE,
)

_PAGE_LINE_RE = re.compile(
    r"^(?P<star>\*\s*)?(?P<tier>[a-zA-Z][a-zA-Z0-9_-]*)\s+(?P<tokens>[\d,]+)\s*tok(?:ens?)?\s+(?P<rest>.+)$"
)


def _unavailable_payload() -> dict[str, Any]:
    return {
        "available": False,
        "pages": [],
        "total_pages": 0,
        "total_tokens": 0,
        "hot_tokens": 0,
    }


def _empty_available_payload() -> dict[str, Any]:
    return {
        "available": True,
        "pages": [],
        "total_pages": 0,
        "total_tokens": 0,
        "hot_tokens": 0,
    }


def _validate_page_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("Context page name must be a string.")
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Context page name must not be empty.")
    if cleaned.startswith("-"):
        raise ValueError("Context page name must not start with '-'.")
    if any(ch.isspace() or ord(ch) < 32 for ch in cleaned):
        raise ValueError("Context page name must not contain whitespace or control characters.")
    return cleaned


def _validate_owner(owner: str | None) -> str | None:
    if owner is None:
        return None
    if not isinstance(owner, str):
        raise ValueError("Owner must be a string.")
    cleaned = owner.strip()
    if not cleaned:
        return None
    if cleaned.startswith("-"):
        raise ValueError("Owner must not start with '-'.")
    return cleaned


def _parse_int(raw: Any, default: int = 0) -> int:
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        cleaned = raw.replace(",", "").strip()
        if cleaned.isdigit():
            return int(cleaned)
    return default


def _normalize_json_pages(
    raw_pages: list[Any],
    *,
    available: bool = True,
    total_pages: Any = None,
    total_tokens: Any = None,
    hot_tokens: Any = None,
) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    for item in raw_pages:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        tier = str(item.get("tier") or item.get("state") or "cold").strip().lower()
        tokens = _parse_int(item.get("tokens"), 0)
        raw_owner = item.get("owner")
        owner = str(raw_owner).strip().lstrip("@") if raw_owner else None
        if owner == "":
            owner = None
        pinned = bool(item.get("pinned", False))
        pages.append(
            {
                "name": name,
                "tier": tier,
                "tokens": tokens,
                "owner": owner,
                "pinned": pinned,
            }
        )

    computed_total_pages = len(pages)
    computed_total_tokens = sum(p["tokens"] for p in pages)
    computed_hot_tokens = sum(p["tokens"] for p in pages if p["tier"] == "hot")

    return {
        "available": available,
        "pages": pages,
        "total_pages": _parse_int(total_pages, computed_total_pages)
        if total_pages is not None
        else computed_total_pages,
        "total_tokens": _parse_int(total_tokens, computed_total_tokens)
        if total_tokens is not None
        else computed_total_tokens,
        "hot_tokens": _parse_int(hot_tokens, computed_hot_tokens)
        if hot_tokens is not None
        else computed_hot_tokens,
    }


def _parse_page_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    lower = stripped.lower()
    if (
        lower.startswith("memory: no pages")
        or lower.startswith("no context pages")
        or _SUMMARY_RE.match(stripped)
    ):
        return None

    m = _PAGE_LINE_RE.match(stripped)
    if not m:
        return None

    pinned = bool(m.group("star"))
    tier = m.group("tier").strip().lower()
    tokens = _parse_int(m.group("tokens"), 0)
    rest_tokens = m.group("rest").split()
    if not rest_tokens:
        return None

    owner: str | None = None
    name_candidates: list[str] = []

    for idx, tok in enumerate(rest_tokens):
        low_tok = tok.lower()
        if low_tok in {"[pinned]", "(pinned)", "*"}:
            pinned = True
            continue
        if idx > 0 and low_tok == "pinned":
            pinned = True
            continue
        if tok.startswith("@") and len(tok) > 1:
            owner = tok[1:]
            continue
        if tok.endswith("*") and len(tok) > 1:
            pinned = True
            tok = tok.rstrip("*")
        name_candidates.append(tok)

    if not name_candidates:
        return None

    return {
        "name": name_candidates[0],
        "tier": tier,
        "tokens": tokens,
        "owner": owner,
        "pinned": pinned,
    }


def parse_context_pages_output(raw_text: str) -> dict[str, Any]:
    """Parse ostk context-pages output (JSON envelope, direct JSON list, or CLI text)."""
    if not isinstance(raw_text, str):
        return _empty_available_payload()

    stripped = raw_text.strip()
    if not stripped:
        return _empty_available_payload()

    if stripped[0] in {"{", "["}:
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            decoded = None

        if isinstance(decoded, list):
            return _normalize_json_pages(decoded, available=True)

        if isinstance(decoded, dict):
            ok_flag = bool(decoded.get("ok", True))
            if not ok_flag and "pages" not in decoded and "output" not in decoded:
                return _unavailable_payload()
            if isinstance(decoded.get("pages"), list):
                return _normalize_json_pages(
                    decoded["pages"],
                    available=ok_flag,
                    total_pages=decoded.get("total_pages"),
                    total_tokens=decoded.get("total_tokens"),
                    hot_tokens=decoded.get("hot_tokens"),
                )
            if isinstance(decoded.get("output"), str):
                inner = parse_context_pages_output(decoded["output"])
                inner["available"] = ok_flag
                return inner

    lower = stripped.lower()
    if "no pages stored" in lower or "no context pages" in lower:
        return _empty_available_payload()

    pages: list[dict[str, Any]] = []
    summary_match: re.Match[str] | None = None

    for raw_line in stripped.splitlines():
        s_match = _SUMMARY_RE.search(raw_line)
        if s_match:
            summary_match = s_match
            continue
        parsed_line = _parse_page_line(raw_line)
        if parsed_line is not None:
            pages.append(parsed_line)

    if summary_match is not None:
        total_pages = _parse_int(summary_match.group(1), len(pages))
        total_tokens = _parse_int(
            summary_match.group(2),
            sum(p["tokens"] for p in pages),
        )
        hot_tokens = _parse_int(
            summary_match.group(3),
            sum(p["tokens"] for p in pages if p["tier"] == "hot"),
        )
    else:
        total_pages = len(pages)
        total_tokens = sum(p["tokens"] for p in pages)
        hot_tokens = sum(p["tokens"] for p in pages if p["tier"] == "hot")

    return {
        "available": True,
        "pages": pages,
        "total_pages": total_pages,
        "total_tokens": total_tokens,
        "hot_tokens": hot_tokens,
    }


async def _run_ostk_command(
    cmd: list[str],
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    effective_cwd = cwd or get_effective_root()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(effective_cwd),
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        raise
    stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace").strip()
    stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
    return int(proc.returncode or 0), stdout_text, stderr_text


def _extract_envelope_fields(rc: int, stdout_text: str, stderr_text: str) -> tuple[bool, str, str]:
    if stdout_text:
        try:
            parsed = json.loads(stdout_text)
            if isinstance(parsed, dict):
                ok = bool(parsed.get("ok", rc == 0)) and rc == 0
                output = str(parsed.get("output") or "").strip()
                err = str(parsed.get("error") or stderr_text or "").strip()
                return ok, output, err
        except json.JSONDecodeError:
            pass
    return rc == 0, stdout_text, stderr_text


async def list_context_pages(cwd: Path | None = None) -> dict[str, Any]:
    """List ostk tiered context pages cleanly, degrading when ostk is unavailable."""
    try:
        rc, stdout_text, stderr_text = await _run_ostk_command(
            ["ostk", "context-pages", "--json"],
            cwd=cwd,
        )
    except Exception as exc:
        logger.debug("ostk_context_pages.list_degraded err=%s", exc)
        return _unavailable_payload()

    if rc != 0:
        logger.debug(
            "ostk_context_pages.list_nonzero rc=%s stderr=%s",
            rc,
            stderr_text[:200],
        )
        return _unavailable_payload()

    return parse_context_pages_output(stdout_text)


async def pin_context_page(
    name: str,
    unpin: bool = False,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Pin or unpin an ostk context page to toggle eviction protection."""
    cleaned_name = _validate_page_name(name)
    cmd = ["ostk", "context-pin", cleaned_name, *(["--unpin"] if unpin else []), "--json"]
    try:
        rc, stdout_text, stderr_text = await _run_ostk_command(cmd, cwd=cwd)
    except Exception as exc:
        logger.debug("ostk_context_pages.pin_degraded name=%s err=%s", cleaned_name, exc)
        return {
            "ok": False,
            "name": cleaned_name,
            "pinned": False,
            "unpin": bool(unpin),
            "output": "",
            "error": str(exc)[:300],
        }

    ok, output_str, err_str = _extract_envelope_fields(rc, stdout_text, stderr_text)
    result: dict[str, Any] = {
        "ok": ok,
        "name": cleaned_name,
        "pinned": (not unpin) if ok else False,
        "unpin": bool(unpin),
        "output": output_str,
    }
    if not ok and err_str:
        result["error"] = err_str[:300]
    return result


async def restore_context_page(
    name: str,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Promote a warm or cold ostk context page back to hot."""
    cleaned_name = _validate_page_name(name)
    cmd = ["ostk", "context-restore", cleaned_name, "--json"]
    try:
        rc, stdout_text, stderr_text = await _run_ostk_command(cmd, cwd=cwd)
    except Exception as exc:
        logger.debug("ostk_context_pages.restore_degraded name=%s err=%s", cleaned_name, exc)
        return {
            "ok": False,
            "name": cleaned_name,
            "tier": None,
            "output": "",
            "error": str(exc)[:300],
        }

    ok, output_str, err_str = _extract_envelope_fields(rc, stdout_text, stderr_text)
    result: dict[str, Any] = {
        "ok": ok,
        "name": cleaned_name,
        "tier": "hot" if ok else None,
        "output": output_str,
    }
    if not ok and err_str:
        result["error"] = err_str[:300]
    return result


async def store_context_page(
    name: str,
    content: str,
    owner: str | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Store new content as an ostk context page."""
    cleaned_name = _validate_page_name(name)
    if not isinstance(content, str):
        raise ValueError("Context page content must be a string.")
    cleaned_owner = _validate_owner(owner)
    cmd = [
        "ostk",
        "context-store",
        cleaned_name,
        content,
        *(["--owner", cleaned_owner] if cleaned_owner else []),
        "--json",
    ]
    try:
        rc, stdout_text, stderr_text = await _run_ostk_command(cmd, cwd=cwd)
    except Exception as exc:
        logger.debug("ostk_context_pages.store_degraded name=%s err=%s", cleaned_name, exc)
        return {
            "ok": False,
            "name": cleaned_name,
            "owner": cleaned_owner,
            "output": "",
            "error": str(exc)[:300],
        }

    ok, output_str, err_str = _extract_envelope_fields(rc, stdout_text, stderr_text)
    result: dict[str, Any] = {
        "ok": ok,
        "name": cleaned_name,
        "owner": cleaned_owner,
        "output": output_str,
    }
    if not ok and err_str:
        result["error"] = err_str[:300]
    return result


__all__ = [
    "list_context_pages",
    "parse_context_pages_output",
    "pin_context_page",
    "restore_context_page",
    "store_context_page",
]
