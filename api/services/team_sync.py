"""Team sync service.

Federated team knowledge layer backed by a shared git repo configured in
~/.myos/team.json. Each member publishes "shared" items there; all members
can search/read it.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from models.team_schemas import TeamConfig

MYOS_DIR = Path.home() / ".myos"
TEAM_CONFIG_PATH = MYOS_DIR / "team.json"
TEAM_CACHE_PATH = MYOS_DIR / "team_cache.json"


def load_config() -> Optional[TeamConfig]:
    if not TEAM_CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(TEAM_CONFIG_PATH.read_text())
        return TeamConfig(**data)
    except Exception:
        return None


def save_config(cfg: TeamConfig) -> None:
    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    TEAM_CONFIG_PATH.write_text(json.dumps(cfg.model_dump(), indent=2))


def resolve_visibility(item: dict, category: str, cfg: TeamConfig) -> str:
    if "visibility" in item and item["visibility"] is not None:
        return item["visibility"]
    return cfg.category_defaults.get(category, "private")


def _get_cwd() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return str(Path.home())


def _load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _local_decisions() -> list:
    cwd = _get_cwd()
    return _load_jsonl(Path(cwd) / ".ostk" / "decisions.jsonl")


def _local_knowledge() -> list:
    path = MYOS_DIR / "knowledge.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _local_needles() -> list:
    cwd = _get_cwd()
    entries = _load_jsonl(Path(cwd) / ".ostk" / "needles" / "issues.jsonl")
    return [e for e in entries if e.get("status") == "open"]


def sync_outbound(cfg: TeamConfig) -> dict:
    """Write shared items to team_repo and git push."""
    team_repo = Path(cfg.team_repo)
    member_dir = team_repo / cfg.member_id
    member_dir.mkdir(parents=True, exist_ok=True)

    categories = {
        "decisions": _local_decisions(),
        "knowledge": _local_knowledge(),
        "needles": _local_needles(),
    }

    pushed_count = 0
    for category, items in categories.items():
        shared = [i for i in items if resolve_visibility(i, category, cfg) == "shared"]
        pushed_count += len(shared)
        out_path = member_dir / f"{category}.jsonl"
        out_path.write_text("\n".join(json.dumps(i) for i in shared) + ("\n" if shared else ""))

    meta = {"last_sync_at": datetime.now(timezone.utc).isoformat(), "member_id": cfg.member_id}
    (member_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    try:
        subprocess.run(["git", "add", "."], cwd=cfg.team_repo, capture_output=True, timeout=10)
        ts = datetime.now(timezone.utc).isoformat()
        result = subprocess.run(
            ["git", "commit", "-m", f"team sync {ts}"],
            cwd=cfg.team_repo, capture_output=True, timeout=10
        )
        if result.returncode == 0:
            subprocess.run(
                ["git", "push"], cwd=cfg.team_repo, capture_output=True, timeout=30
            )
    except Exception:
        pass

    return {"pushed": pushed_count}


def sync_inbound(cfg: TeamConfig) -> dict:
    """Pull from team_repo and merge teammates' items into team_cache.json."""
    pulled = False
    try:
        result = subprocess.run(
            ["git", "pull"], cwd=cfg.team_repo, capture_output=True, timeout=30
        )
        pulled = result.returncode == 0
    except Exception:
        pass

    cache: dict = {}
    team_repo = Path(cfg.team_repo)
    pulled_count = 0

    for member_dir in team_repo.iterdir():
        if not member_dir.is_dir():
            continue
        member_id = member_dir.name
        if member_id == cfg.member_id:
            continue
        if member_id.startswith("."):
            continue

        meta_path = member_dir / "meta.json"
        display_name = member_id
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                display_name = meta.get("display_name", member_id)
            except Exception:
                pass

        member_data: dict = {"display_name": display_name}
        for category in ("decisions", "knowledge", "needles"):
            items = _load_jsonl(member_dir / f"{category}.jsonl")
            member_data[category] = items
            pulled_count += len(items)

        cache[member_id] = member_data

    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    TEAM_CACHE_PATH.write_text(json.dumps(cache, indent=2))

    return {"pulled": pulled_count}


def _load_cache() -> dict:
    if not TEAM_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(TEAM_CACHE_PATH.read_text())
    except Exception:
        return {}


def _item_timestamp(item: dict) -> str:
    for key in ("ts", "timestamp", "created_at", "updated_at"):
        if key in item:
            return str(item[key])
    return ""


def search_team(query: str, category: Optional[str], cfg: TeamConfig) -> list:
    """Search all teammates' items for query string in title/content/key/value/reason."""
    cache = _load_cache()
    q = query.lower()
    results = []

    categories = [category] if category else ["decisions", "knowledge", "needles"]

    for member_id, member_data in cache.items():
        display_name = member_data.get("display_name", member_id)
        for cat in categories:
            for item in member_data.get(cat, []):
                searchable = " ".join(
                    str(item.get(f, ""))
                    for f in ("title", "content", "key", "value", "reason", "description")
                ).lower()
                if q in searchable:
                    results.append({
                        "member_id": member_id,
                        "display_name": display_name,
                        "category": cat,
                        "item": item,
                    })

    results.sort(key=lambda r: _item_timestamp(r["item"]), reverse=True)
    return results


def get_feed(cfg: TeamConfig, limit: int = 20) -> list:
    """Return all items from team cache sorted by timestamp desc."""
    cache = _load_cache()
    results = []

    for member_id, member_data in cache.items():
        display_name = member_data.get("display_name", member_id)
        for cat in ("decisions", "knowledge", "needles"):
            for item in member_data.get(cat, []):
                results.append({
                    "member_id": member_id,
                    "display_name": display_name,
                    "category": cat,
                    "item": item,
                })

    results.sort(key=lambda r: _item_timestamp(r["item"]), reverse=True)
    return results[:limit]


def get_context_for_task(task: str, cfg: TeamConfig) -> list:
    """Return top 5 team items most relevant to task by token overlap."""
    tokens = [t for t in task.lower().split() if len(t) >= 4]
    if not tokens:
        return []

    cache = _load_cache()
    scored = []

    for member_id, member_data in cache.items():
        display_name = member_data.get("display_name", member_id)
        for cat in ("decisions", "knowledge"):
            for item in member_data.get(cat, []):
                searchable = " ".join(
                    str(item.get(f, ""))
                    for f in ("title", "content", "key", "value", "reason", "description")
                ).lower()
                score = sum(1 for t in tokens if t in searchable)
                if score > 0:
                    scored.append((score, {
                        "member_id": member_id,
                        "display_name": display_name,
                        "category": cat,
                        "item": item,
                    }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:5]]
