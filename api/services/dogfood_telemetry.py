"""14-day dogfood fleet vs raw terminal telemetry service (Phase 5, task ->3097).

Computes objective supervision metrics across the active repository and optional
local repositories so operators can measure how much engineering work runs
through tracked yourOS fleet agents vs untracked raw terminal sessions.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import config
from services.agent_filters import is_main_session
from services.youros_paths import youros_home

FREEZE_TARGET_DAYS: int = 14
BACKEND_SESSION_PREFIX: str = "myos-api-"
_TAIL_BYTES: int = 8192
_HEAD_BYTES: int = 16384

_ORCHESTRATED_SOURCES: frozenset[str] = frozenset(
    {
        "ui",
        "api",
        "task-bridge",
        "spec-build",
        "handoff",
        "claude-code",
        "gemini-cli",
        "workflow",
        "schedule",
        "recurring",
        "auto-spawn",
        "blocked-by",
        "recovery",
        "saa",
        "fleet",
        "agent",
    }
)

_NON_FLEET_SOURCES: frozenset[str] = frozenset(
    {
        "chat",
        "audit",
        "hook",
        "daemon",
        "untracked",
        "raw-terminal",
        "manual",
    }
)


def _parse_ts(raw: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string or epoch timestamp into a UTC datetime."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _supervised_ratio(tracked_spawns: int, untracked_sessions: int) -> float:
    return round(tracked_spawns / max(1, tracked_spawns + untracked_sessions), 3)


def _is_backend_self_session(session_id: str) -> bool:
    return session_id == "myos-api" or session_id.startswith(BACKEND_SESSION_PREFIX)


def _is_tracked_fleet_run(name: str, meta: Mapping[str, Any]) -> bool:
    """Return True if an agent record represents a tracked yourOS fleet run."""
    if _is_backend_self_session(name):
        return False
    if is_main_session({"name": name, **meta}):
        return False
    if meta.get("hook_preregister"):
        return False
    if meta.get("model") == "claude-code-subscription":
        return False

    source = str(meta.get("source") or "").strip().lower()
    if source in _NON_FLEET_SOURCES:
        return False

    if source in _ORCHESTRATED_SOURCES:
        return True
    if meta.get("pid") or meta.get("worktree_path"):
        return True
    return False


def _classify_runtime(name: str, meta: Mapping[str, Any]) -> str:
    """Classify a tracked run into 'claude' or 'gemini'."""
    for raw in (meta.get("runtime"), meta.get("provider")):
        if isinstance(raw, str) and raw.strip():
            lowered = raw.strip().lower()
            if "gemini" in lowered or "google" in lowered:
                return "gemini"
            if "claude" in lowered or "anthropic" in lowered:
                return "claude"

    model = str(meta.get("model") or "").strip().lower()
    if "gemini" in model:
        return "gemini"
    if any(tok in model for tok in ("claude", "sonnet", "opus", "haiku")):
        return "claude"

    source = str(meta.get("source") or "").strip().lower()
    if "gemini" in source or name.lower().startswith("gemini"):
        return "gemini"
    return "claude"


def _agent_primary_ts(meta: Mapping[str, Any]) -> Optional[datetime]:
    for field in (
        "spawned_at",
        "registered_at",
        "created_at",
        "started_at",
        "completed_at",
        "last_heartbeat_at",
    ):
        ts = _parse_ts(meta.get(field))
        if ts is not None:
            return ts
    return None


def _default_repo_roots() -> list[Path]:
    """Return deduplicated repository roots (active repo plus configured local repos)."""
    candidates: list[Path] = [Path(config.PROJECT_ROOT)]
    try:
        from services import settings_store

        raw_settings = settings_store.settings_store.load()
        if isinstance(raw_settings, dict):
            for single_key in ("active_repo_path", "active_repo"):
                val = raw_settings.get(single_key)
                if isinstance(val, str) and val.strip():
                    p = Path(val).expanduser()
                    if p.is_dir():
                        candidates.append(p)
            for list_key in ("recent_repos", "repo_roots"):
                items = raw_settings.get(list_key)
                if isinstance(items, list):
                    for item in items:
                        path_str = (
                            item.get("path")
                            if isinstance(item, dict)
                            else item
                            if isinstance(item, str)
                            else None
                        )
                        if isinstance(path_str, str) and path_str.strip():
                            p = Path(path_str).expanduser()
                            if p.is_dir():
                                candidates.append(p)
    except Exception:
        pass

    seen: set[Path] = set()
    resolved_list: list[Path] = []
    for cand in candidates:
        try:
            resolved = cand.expanduser().resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            resolved_list.append(resolved)
    return resolved_list


def _default_handoffs_log_path() -> Path:
    return youros_home() / "memory" / "handoffs.jsonl"


def _load_repo_agent_records(
    repo: Path,
    include_live_metadata: Optional[bool],
) -> dict[str, dict[str, Any]]:
    """Load persisted .ostk/agent_state.json rows and merge matching live metadata."""
    records: dict[str, dict[str, Any]] = {}
    state_file = repo / ".ostk" / "agent_state.json"
    if state_file.is_file():
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8", errors="replace"))
            if isinstance(raw, dict):
                maybe_nested = raw.get("agents")
                source_map = (
                    maybe_nested
                    if isinstance(maybe_nested, dict) and set(raw.keys()) <= {"agents", "version", "updated_at"}
                    else raw
                )
                for name, meta in source_map.items():
                    if isinstance(name, str) and isinstance(meta, dict):
                        records[name] = dict(meta)
        except (OSError, ValueError, TypeError):
            pass

    if include_live_metadata is False:
        return records

    try:
        from routers.agents import AGENT_STATE_PATH, agent_metadata

        try:
            live_default_repo = Path(AGENT_STATE_PATH).resolve().parent.parent
        except OSError:
            live_default_repo = None

        for name, meta in list(agent_metadata.items()):
            if not isinstance(name, str) or not isinstance(meta, dict):
                continue
            explicit_repo = meta.get("repo_path")
            if isinstance(explicit_repo, str) and explicit_repo.strip():
                try:
                    belongs = Path(explicit_repo).expanduser().resolve() == repo
                except OSError:
                    belongs = False
            else:
                belongs = live_default_repo is not None and live_default_repo == repo

            if belongs:
                merged = dict(records.get(name, {}))
                merged.update(meta)
                records[name] = merged
    except Exception:
        pass

    return records


def _tail_text(path: Path, nbytes: int = _TAIL_BYTES) -> str:
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    if size <= 0:
        return ""
    read_len = min(size, nbytes)
    try:
        with open(path, "rb") as fh:
            fh.seek(-read_len, os.SEEK_END)
            chunk = fh.read(read_len).decode("utf-8", errors="replace")
    except OSError:
        return ""
    if read_len < size:
        nl = chunk.find("\n")
        if nl != -1:
            return chunk[nl + 1 :]
    return chunk


def _head_and_tail_text(path: Path) -> str:
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    if size <= 0:
        return ""
    try:
        with open(path, "rb") as fh:
            if size <= _HEAD_BYTES + _TAIL_BYTES:
                return fh.read().decode("utf-8", errors="replace")
            head = fh.read(_HEAD_BYTES).decode("utf-8", errors="replace")
            fh.seek(-_TAIL_BYTES, os.SEEK_END)
            tail = fh.read(_TAIL_BYTES).decode("utf-8", errors="replace")
            return head + "\n" + tail
    except OSError:
        return ""


def _session_last_timestamp(session_dir: Path) -> Optional[datetime]:
    """Extract the most recent timestamp for an .ostk/sessions/<id> directory."""
    events_file = session_dir / "events.jsonl"
    if events_file.is_file():
        tail = _tail_text(events_file)
        for line in reversed(tail.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                ev = json.loads(stripped)
            except (ValueError, TypeError):
                continue
            if isinstance(ev, dict):
                ts = _parse_ts(ev.get("ts") or ev.get("timestamp"))
                if ts is not None:
                    return ts
        try:
            return datetime.fromtimestamp(events_file.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return None

    try:
        return datetime.fromtimestamp(session_dir.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _build_agent_url_regex(tracked_names: set[str]) -> Optional[re.Pattern[str]]:
    if not tracked_names:
        return None
    escaped = sorted((re.escape(n) for n in tracked_names if n), key=len, reverse=True)
    if not escaped:
        return None
    alternation = "|".join(escaped)
    return re.compile(
        rf"(?:/api/agents/(?:{alternation})(?=[/\"'\s?]|$)|\"name\"\s*:\s*\"(?:{alternation})\")"
    )


def _session_matches_tracked_agent(
    session_id: str,
    session_dir: Path,
    tracked_names: set[str],
    tracked_session_ids: set[str],
    tracked_pids: set[str],
    agent_ref_pattern: Optional[re.Pattern[str]],
) -> bool:
    if session_id in tracked_names or session_id in tracked_session_ids:
        return True

    for pid in tracked_pids:
        if session_id == f"p{pid}" or session_id.endswith(f"-p{pid}"):
            return True

    if agent_ref_pattern is not None:
        events_file = session_dir / "events.jsonl"
        if events_file.is_file():
            sample = _head_and_tail_text(events_file)
            if sample and agent_ref_pattern.search(sample):
                return True

    return False


def _day_bucket_key(ts: datetime, start_date: date, end_date: date) -> str:
    d = ts.date()
    if d < start_date:
        d = start_date
    elif d > end_date:
        d = end_date
    return d.isoformat()


def _iter_jsonl_dicts(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except (ValueError, TypeError):
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError:
        return []
    return rows


def compute_dogfood_telemetry(
    window_days: int = 14,
    repo_roots: Optional[Sequence[Path | str]] = None,
    *,
    now: Optional[datetime] = None,
    include_live_metadata: Optional[bool] = None,
    handoffs_log_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Compute objective dogfood fleet vs raw terminal metrics over window_days."""
    days = max(1, int(window_days))
    ref_now = (
        now.astimezone(timezone.utc)
        if now is not None and now.tzinfo is not None
        else now.replace(tzinfo=timezone.utc)
        if now is not None
        else datetime.now(timezone.utc)
    )
    cutoff = ref_now - timedelta(days=days)
    end_date = ref_now.date()
    start_date = end_date - timedelta(days=days - 1)

    explicit_repos = repo_roots is not None
    raw_roots = [Path(p) for p in repo_roots] if repo_roots is not None else _default_repo_roots()
    resolved_repos: list[Path] = []
    seen_repos: set[Path] = set()
    for root in raw_roots:
        try:
            resolved = root.expanduser().resolve()
        except OSError:
            continue
        if resolved not in seen_repos:
            seen_repos.add(resolved)
            resolved_repos.append(resolved)

    # Disambiguate repo display keys if two roots share the same directory basename.
    basename_counts: dict[str, int] = {}
    for repo in resolved_repos:
        basename_counts[repo.name] = basename_counts.get(repo.name, 0) + 1
    repo_key_for: dict[Path, str] = {
        repo: (repo.name if basename_counts.get(repo.name, 0) == 1 else str(repo))
        for repo in resolved_repos
    }

    daily_buckets: dict[str, dict[str, int]] = {}
    for offset in range(days):
        d_str = (start_date + timedelta(days=offset)).isoformat()
        daily_buckets[d_str] = {
            "tracked_spawns": 0,
            "untracked_terminal_sessions": 0,
            "handoffs_count": 0,
            "verified_merges": 0,
        }

    total_tracked = 0
    total_handoffs = 0
    total_merges = 0
    total_untracked = 0
    total_by_runtime: dict[str, int] = {"claude": 0, "gemini": 0}
    by_repo: dict[str, dict[str, Any]] = {}
    repo_seen_handoffs: dict[Path, set[tuple[str, str]]] = {r: set() for r in resolved_repos}

    for repo in resolved_repos:
        r_tracked = 0
        r_handoffs = 0
        r_merges = 0
        r_untracked = 0
        r_by_runtime: dict[str, int] = {"claude": 0, "gemini": 0}
        seen_handoff_pairs = repo_seen_handoffs[repo]

        records = _load_repo_agent_records(repo, include_live_metadata=include_live_metadata)

        tracked_names: set[str] = set()
        tracked_session_ids: set[str] = set()
        tracked_pids: set[str] = set()

        for name, meta in records.items():
            is_fleet = _is_tracked_fleet_run(name, meta)
            if is_fleet:
                tracked_names.add(name)
                for sid_field in (
                    "session_id",
                    "ostk_session",
                    "ostk_session_id",
                    "originating_session_id",
                ):
                    sid_val = meta.get(sid_field)
                    if isinstance(sid_val, str) and sid_val.strip():
                        tracked_session_ids.add(sid_val.strip())
                pid_val = meta.get("pid")
                if pid_val is not None and not isinstance(pid_val, bool):
                    pid_str = str(pid_val).strip()
                    if pid_str:
                        tracked_pids.add(pid_str)

            run_ts = _agent_primary_ts(meta)
            if is_fleet and run_ts is not None and run_ts >= cutoff:
                r_tracked += 1
                runtime_key = _classify_runtime(name, meta)
                r_by_runtime[runtime_key] = r_by_runtime.get(runtime_key, 0) + 1
                b_key = _day_bucket_key(run_ts, start_date, end_date)
                daily_buckets[b_key]["tracked_spawns"] += 1

                hc_raw = meta.get("handoff_count")
                from_agent = str(
                    meta.get("handed_off_from") or meta.get("handoff_from") or ""
                ).strip()
                source_lower = str(meta.get("source") or "").strip().lower()
                agent_handoffs = 0
                if isinstance(hc_raw, int) and not isinstance(hc_raw, bool) and hc_raw > 0:
                    agent_handoffs = hc_raw
                elif from_agent or source_lower == "handoff":
                    agent_handoffs = 1

                if agent_handoffs > 0:
                    r_handoffs += agent_handoffs
                    daily_buckets[b_key]["handoffs_count"] += agent_handoffs
                    seen_handoff_pairs.add((name, from_agent))

            merged_ts = _parse_ts(meta.get("merged_at"))
            if merged_ts is not None and merged_ts >= cutoff:
                r_merges += 1
                m_key = _day_bucket_key(merged_ts, start_date, end_date)
                daily_buckets[m_key]["verified_merges"] += 1

        # Scan <repo>/.ostk/audit.jsonl for verb == "handoff" or handoff events.
        for audit_row in _iter_jsonl_dicts(repo / ".ostk" / "audit.jsonl"):
            verb = str(audit_row.get("verb") or "").strip().lower()
            ev = str(audit_row.get("event") or "").strip().lower()
            if verb != "handoff" and ev not in ("handoff", "agent.handoff"):
                continue
            a_ts = _parse_ts(
                audit_row.get("timestamp")
                or audit_row.get("ts")
                or audit_row.get("created_at")
            )
            if a_ts is None or a_ts < cutoff:
                continue
            to_ag = str(
                audit_row.get("to_agent")
                or audit_row.get("target_agent")
                or audit_row.get("name")
                or audit_row.get("agent")
                or ""
            ).strip()
            from_ag = str(
                audit_row.get("from_agent")
                or audit_row.get("source_agent")
                or audit_row.get("handed_off_from")
                or ""
            ).strip()
            pair = (to_ag, from_ag)
            if (to_ag or from_ag) and pair in seen_handoff_pairs:
                continue
            if to_ag or from_ag:
                seen_handoff_pairs.add(pair)
            r_handoffs += 1
            a_key = _day_bucket_key(a_ts, start_date, end_date)
            daily_buckets[a_key]["handoffs_count"] += 1

        # Scan <repo>/.ostk/sessions/* for untracked raw terminal sessions.
        sessions_dir = repo / ".ostk" / "sessions"
        if sessions_dir.is_dir():
            agent_ref_re = _build_agent_url_regex(tracked_names)
            try:
                entries = sorted(sessions_dir.iterdir(), key=lambda p: p.name)
            except OSError:
                entries = []
            for entry in entries:
                if not entry.is_dir():
                    continue
                sid = entry.name
                if _is_backend_self_session(sid):
                    continue
                s_ts = _session_last_timestamp(entry)
                if s_ts is None or s_ts < cutoff:
                    continue
                if _session_matches_tracked_agent(
                    sid,
                    entry,
                    tracked_names,
                    tracked_session_ids,
                    tracked_pids,
                    agent_ref_re,
                ):
                    continue
                r_untracked += 1
                s_key = _day_bucket_key(s_ts, start_date, end_date)
                daily_buckets[s_key]["untracked_terminal_sessions"] += 1

        r_key = repo_key_for[repo]
        by_repo[r_key] = {
            "name": repo.name,
            "path": str(repo),
            "tracked_spawns": r_tracked,
            "handoffs_count": r_handoffs,
            "verified_merges": r_merges,
            "untracked_terminal_sessions": r_untracked,
            "supervised_ratio": _supervised_ratio(r_tracked, r_untracked),
            "by_runtime": dict(r_by_runtime),
        }

        total_tracked += r_tracked
        total_handoffs += r_handoffs
        total_merges += r_merges
        total_untracked += r_untracked
        total_by_runtime["claude"] += r_by_runtime["claude"]
        total_by_runtime["gemini"] += r_by_runtime["gemini"]

    # Scan durable ~/.youros/memory/handoffs.jsonl entries within window_days.
    effective_handoffs_path = (
        Path(handoffs_log_path)
        if handoffs_log_path is not None
        else _default_handoffs_log_path()
    )
    if resolved_repos and effective_handoffs_path.is_file():
        try:
            default_project_resolved = Path(config.PROJECT_ROOT).expanduser().resolve()
        except OSError:
            default_project_resolved = None

        for h_row in _iter_jsonl_dicts(effective_handoffs_path):
            h_ts = _parse_ts(
                h_row.get("timestamp")
                or h_row.get("ts")
                or h_row.get("created_at")
                or h_row.get("handed_off_at")
            )
            if h_ts is None or h_ts < cutoff:
                continue

            raw_repo_str = (
                h_row.get("repo_path")
                or h_row.get("repo")
                or h_row.get("project_root")
            )
            target_repo: Optional[Path] = None
            if isinstance(raw_repo_str, str) and raw_repo_str.strip():
                try:
                    cand_repo = Path(raw_repo_str).expanduser().resolve()
                except OSError:
                    cand_repo = None
                if cand_repo in seen_repos:
                    target_repo = cand_repo
            elif handoffs_log_path is not None or not explicit_repos:
                if default_project_resolved in seen_repos:
                    target_repo = default_project_resolved
                else:
                    target_repo = resolved_repos[0]

            if target_repo is None:
                continue

            to_ag = str(
                h_row.get("to_agent")
                or h_row.get("target_agent")
                or h_row.get("name")
                or h_row.get("agent")
                or ""
            ).strip()
            from_ag = str(
                h_row.get("from_agent")
                or h_row.get("source_agent")
                or h_row.get("handed_off_from")
                or ""
            ).strip()
            pair = (to_ag, from_ag)
            seen_pairs = repo_seen_handoffs[target_repo]
            if (to_ag or from_ag) and pair in seen_pairs:
                continue
            if to_ag or from_ag:
                seen_pairs.add(pair)

            r_key = repo_key_for[target_repo]
            by_repo[r_key]["handoffs_count"] += 1
            total_handoffs += 1
            h_key = _day_bucket_key(h_ts, start_date, end_date)
            daily_buckets[h_key]["handoffs_count"] += 1

    daily_list: list[dict[str, Any]] = []
    for d_str, counts in daily_buckets.items():
        daily_list.append(
            {
                "date": d_str,
                "tracked_spawns": counts["tracked_spawns"],
                "untracked_terminal_sessions": counts["untracked_terminal_sessions"],
                "handoffs_count": counts["handoffs_count"],
                "verified_merges": counts["verified_merges"],
                "supervised_ratio": _supervised_ratio(
                    counts["tracked_spawns"],
                    counts["untracked_terminal_sessions"],
                ),
            }
        )

    return {
        "window_days": days,
        "freeze_target_days": FREEZE_TARGET_DAYS,
        "computed_at": ref_now.isoformat(),
        "tracked_spawns": total_tracked,
        "handoffs_count": total_handoffs,
        "verified_merges": total_merges,
        "untracked_terminal_sessions": total_untracked,
        "supervised_ratio": _supervised_ratio(total_tracked, total_untracked),
        "by_runtime": total_by_runtime,
        "by_repo": by_repo,
        "daily": daily_list,
    }
