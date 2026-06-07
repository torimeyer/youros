"""FastAPI router for the declarative rule-config layer (→1288, wave 4/7).

Surfaces the merged config of `.claude/hooks/lib/default-rules.json` (in-repo
defaults + schema) overlaid by `~/.youros/rules.json` (per-user, not in repo).
The frontend `/settings/rules` page uses these endpoints to toggle rules,
edit their parameters, and view recent firings without hand-editing JSON.

Atomic write pattern for `~/.youros/rules.json`:
  1. Read current file (or {} if missing).
  2. Merge new fields into rules.<key>.
  3. Write to .tmp sibling, then os.rename → atomic on POSIX.

Reading is fail-soft. Writes raise HTTP 500 on filesystem errors so the
frontend can surface the failure rather than silently dropping the change.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
#
# These are module-level so tests can monkeypatch them onto a tmp_path
# without having to mock os.path.expanduser globally.

# Repo root: api/routers/rules.py → api/routers → api → repo
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_RULES_PATH = _REPO_ROOT / ".claude" / "hooks" / "lib" / "default-rules.json"
USER_RULES_PATH = Path.home() / ".youros" / "rules.json"
RULE_FIRES_LOG = Path.home() / ".claude" / "logs" / "rule-fires.jsonl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    """Load a JSON file. Missing → {}. Malformed → {}."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, IsADirectoryError):
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def _deep_merge(a: dict, b: dict) -> dict:
    """Recursively merge b into a copy of a. b's leaves win on conflict."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_defaults() -> dict:
    """Read the in-repo default-rules.json. Returns the full payload."""
    return _load_json(DEFAULT_RULES_PATH)


def _load_user() -> dict:
    """Read ~/.youros/rules.json. Returns the full payload (or {})."""
    return _load_json(USER_RULES_PATH)


def _atomic_write(path: Path, payload: dict) -> None:
    """Write `payload` to `path` atomically via temp + rename.

    Creates parent dirs if needed. Raises OSError on filesystem failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(payload, indent=2, sort_keys=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, path)


def _build_row(
    key: str,
    default_block: dict,
    user_block: Optional[dict],
) -> dict:
    """Build one rule row in the GET-response shape.

    `default_block` is the raw block from default-rules.json (includes `_meta`).
    `user_block` is the same-key block from rules.json (may be None or {}).
    """
    # Pull out _meta separately so it does not end up duplicated in `params`.
    meta = default_block.get("_meta", {}) or {}

    # Effective merged values (defaults overlaid by user).
    merged = _deep_merge(default_block, user_block or {})
    merged_params = {k: v for k, v in merged.items() if k not in ("enabled", "_meta")}

    default_enabled = bool(default_block.get("enabled", False))
    user_enabled = None
    if isinstance(user_block, dict) and "enabled" in user_block:
        user_enabled = bool(user_block["enabled"])

    enabled = user_enabled if user_enabled is not None else default_enabled

    # `source` indicates whether the user file has any override for this key
    # (enabled OR any param). Useful for the UI's "reset to default" button.
    source = "user" if isinstance(user_block, dict) and len(user_block) > 0 else "default"

    return {
        "key": key,
        "enabled": enabled,
        "params": merged_params,
        "meta": meta,
        "default_enabled": default_enabled,
        "source": source,
    }


def _build_full_response() -> list[dict]:
    """Build the GET /api/rules response from defaults + user overrides."""
    defaults = _load_defaults()
    user = _load_user()

    default_rules = defaults.get("rules", {}) or {}
    user_rules = user.get("rules", {}) or {}

    rows = []
    for key, default_block in default_rules.items():
        if not isinstance(default_block, dict):
            continue
        user_block = user_rules.get(key)
        rows.append(_build_row(key, default_block, user_block if isinstance(user_block, dict) else None))
    return rows


def _build_single_row(key: str) -> dict:
    """Build a single rule row (used by PUT to return the updated row)."""
    defaults = _load_defaults()
    user = _load_user()
    default_block = (defaults.get("rules", {}) or {}).get(key)
    if not isinstance(default_block, dict):
        raise HTTPException(status_code=404, detail=f"Unknown rule key: {key}")
    user_block = (user.get("rules", {}) or {}).get(key)
    return _build_row(key, default_block, user_block if isinstance(user_block, dict) else None)


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class RuleUpdate(BaseModel):
    enabled: Optional[bool] = None
    params: Optional[dict[str, Any]] = None


class RuleReset(BaseModel):
    key: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/rules")
async def get_rules() -> list[dict]:
    """Return the merged default + user config as a list of rule rows."""
    return _build_full_response()


@router.put("/rules/{key}")
async def put_rule(key: str, body: RuleUpdate) -> dict:
    """Merge the given enabled/params into ~/.youros/rules.json.rules.<key>.

    Rejects unknown keys (not present in default-rules.json) with 400.
    Writes atomically. Returns the updated row in the same shape as GET.
    """
    defaults = _load_defaults()
    if not isinstance((defaults.get("rules", {}) or {}).get(key), dict):
        raise HTTPException(status_code=400, detail=f"Unknown rule key: {key}")

    if body.enabled is None and body.params is None:
        # Nothing to update — return the current row unchanged.
        return _build_single_row(key)

    user = _load_user()
    if "rules" not in user or not isinstance(user.get("rules"), dict):
        user["rules"] = {}
    if "schema_version" not in user:
        user["schema_version"] = 1

    existing = user["rules"].get(key)
    if not isinstance(existing, dict):
        existing = {}

    update_block: dict[str, Any] = {}
    if body.enabled is not None:
        update_block["enabled"] = bool(body.enabled)
    if body.params is not None:
        # params is a flat dict of param-name → value; merge into the block
        # alongside `enabled`. Stored shape mirrors default-rules.json.
        update_block.update(body.params)

    merged_block = _deep_merge(existing, update_block)
    user["rules"][key] = merged_block

    try:
        _atomic_write(USER_RULES_PATH, user)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write rules.json: {exc}") from exc

    return _build_single_row(key)


@router.post("/rules/reset")
async def reset_rules(body: RuleReset) -> list[dict]:
    """Reset rule overrides.

    With `{"key": "<k>"}`: removes just that key from rules.json.rules.
    With `{}` (or `{"key": null}`): replaces the file with an empty config.

    Returns the full GET-shaped list so the UI can re-render without a follow-up
    request.
    """
    if body.key is None:
        # Empty all user overrides.
        empty = {"schema_version": 1, "rules": {}}
        try:
            _atomic_write(USER_RULES_PATH, empty)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to write rules.json: {exc}") from exc
        return _build_full_response()

    key = body.key
    defaults = _load_defaults()
    if not isinstance((defaults.get("rules", {}) or {}).get(key), dict):
        raise HTTPException(status_code=400, detail=f"Unknown rule key: {key}")

    user = _load_user()
    rules = user.get("rules")
    if isinstance(rules, dict) and key in rules:
        del rules[key]
        user["rules"] = rules
        if "schema_version" not in user:
            user["schema_version"] = 1
        try:
            _atomic_write(USER_RULES_PATH, user)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to write rules.json: {exc}") from exc

    return _build_full_response()


# ---------------------------------------------------------------------------
# Rule firings log
# ---------------------------------------------------------------------------


def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp. Returns None on failure."""
    if not ts:
        return None
    try:
        # datetime.fromisoformat handles "2026-05-13T16:21:09+00:00" since 3.11.
        # Accept the trailing-Z form too by normalizing.
        normalized = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


@router.get("/rules/fires")
async def get_rule_fires(
    rule: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    since: Optional[str] = Query(default=None),
) -> list[dict]:
    """Return the most recent rule firings.

    Reads ~/.claude/logs/rule-fires.jsonl and returns up to `limit` rows
    sorted by `ts` descending. Filters by rule key and `since` timestamp
    when provided. Returns [] if the log file is missing.

    Tolerates malformed JSON lines silently (skips them) so a single bad
    write does not blank the whole log.
    """
    if not RULE_FIRES_LOG.exists():
        return []

    since_dt = _parse_iso(since) if since else None

    rows: list[dict] = []
    try:
        with open(RULE_FIRES_LOG, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict):
                    continue
                if rule is not None and entry.get("rule") != rule:
                    continue
                if since_dt is not None:
                    entry_dt = _parse_iso(entry.get("ts", ""))
                    if entry_dt is None or entry_dt < since_dt:
                        continue
                rows.append(entry)
    except OSError:
        return []

    # Sort by ts desc. Rows with missing/unparseable ts sink to the bottom.
    def _sort_key(e: dict):
        dt = _parse_iso(e.get("ts", ""))
        return dt or datetime.min.replace(tzinfo=timezone.utc)

    rows.sort(key=_sort_key, reverse=True)
    return rows[:limit]
