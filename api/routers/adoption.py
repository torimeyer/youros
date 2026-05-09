"""Adoption page data endpoint.

Returns a personalised "what's working / what to try next" snapshot:
  - top 3 skills the user ran this week (by agent-name keyword match)
  - 1-2 recommendations based on a static adjacency map
  - a short week summary (agent runs completed, top open task)

No vanity metrics. No "hours saved". Plain-language field names.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

_SESSION_TASK_RE = re.compile(r"^Session in ", re.IGNORECASE)

from fastapi import APIRouter

from config import OSTK_DIR
from services.agent_templates_store import BUILTIN_AGENT_TEMPLATES
from services.ostk import OstkError, ostk, read_audit_entries

router = APIRouter(tags=["adoption"])

# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------

_TEMPLATE_BY_ID: dict[str, dict] = {t["id"]: t for t in BUILTIN_AGENT_TEMPLATES}

# Keyword → template-id lookup (first match wins, order matters).
_NAME_PATTERNS: list[tuple[str, str]] = [
    ("diagnose",         "builtin-diagnose"),
    ("debug",            "builtin-eng-debug-helper"),
    ("bug-find",         "builtin-eng-bug-finder"),
    ("bug_find",         "builtin-eng-bug-finder"),
    ("refactor",         "builtin-eng-refactor-plan"),
    ("write-test",       "builtin-eng-write-tests"),
    ("write_test",       "builtin-eng-write-tests"),
    ("test",             "builtin-test"),
    ("brainstorm",       "builtin-brainstorm"),
    ("ideate",           "builtin-brainstorm"),
    ("research",         "builtin-research"),
    ("review",           "builtin-review"),
    ("prd",              "builtin-pm-prd"),
    ("roadmap",          "builtin-pm-roadmap"),
    ("competitive",      "builtin-pm-competitive-scan"),
    ("stakeholder",      "builtin-pm-stakeholder-update"),
    ("launch",           "builtin-pm-launch-checklist"),
    ("interview",        "builtin-pm-customer-interviews"),
    ("blog",             "builtin-writer-blog-post"),
    ("social-post",      "builtin-writer-social-post"),
    ("social_post",      "builtin-writer-social-post"),
    ("headline",         "builtin-writer-headlines"),
    ("proofread",        "builtin-writer-proofreader"),
    ("name-gen",         "builtin-writer-name-generator"),
    ("prospect",         "builtin-sales-prospect-research"),
    ("outreach",         "builtin-sales-cold-outreach"),
    ("call-prep",        "builtin-sales-call-prep"),
    ("call_prep",        "builtin-sales-call-prep"),
    ("follow-up",        "builtin-sales-follow-up"),
    ("follow_up",        "builtin-sales-follow-up"),
    ("objection",        "builtin-sales-objection-handling"),
    ("meal",             "builtin-home-meal-planner"),
    ("grocery",          "builtin-home-grocery-list"),
    ("trip",             "builtin-home-trip-planner"),
    ("gift",             "builtin-home-gift-finder"),
    ("homework",         "builtin-home-homework-helper"),
    ("study",            "builtin-student-study-guide"),
    ("essay",            "builtin-student-essay-outline"),
    ("flash-card",       "builtin-student-flash-cards"),
    ("flash_card",       "builtin-student-flash-cards"),
    ("citation",         "builtin-student-citation-helper"),
    ("concept",          "builtin-student-concept-explainer"),
    ("explain",          "builtin-explain-plain"),
    ("builder",          "builtin-builder"),
    ("build",            "builtin-builder"),
]

# Skill adjacency map for V1 recommendations (skill → what to try next).
_SKILL_NEIGHBORS: dict[str, list[str]] = {
    "builtin-brainstorm":               ["builtin-research", "builtin-pm-prd", "builtin-builder"],
    "builtin-builder":                  ["builtin-review", "builtin-test", "builtin-diagnose", "builtin-brainstorm"],
    "builtin-diagnose":                 ["builtin-builder", "builtin-test", "builtin-eng-debug-helper"],
    "builtin-research":                 ["builtin-pm-prd", "builtin-pm-competitive-scan", "builtin-pm-roadmap", "builtin-brainstorm"],
    "builtin-review":                   ["builtin-builder", "builtin-test"],
    "builtin-test":                     ["builtin-builder", "builtin-diagnose"],
    "builtin-pm-prd":                   ["builtin-pm-stakeholder-update", "builtin-pm-roadmap", "builtin-pm-launch-checklist", "builtin-brainstorm"],
    "builtin-pm-competitive-scan":      ["builtin-research", "builtin-pm-prd"],
    "builtin-pm-roadmap":               ["builtin-pm-prd", "builtin-pm-stakeholder-update"],
    "builtin-pm-stakeholder-update":    ["builtin-pm-prd", "builtin-pm-roadmap"],
    "builtin-pm-launch-checklist":      ["builtin-pm-prd", "builtin-pm-stakeholder-update"],
    "builtin-pm-customer-interviews":   ["builtin-pm-prd", "builtin-research"],
    "builtin-eng-write-tests":          ["builtin-test", "builtin-diagnose"],
    "builtin-eng-bug-finder":           ["builtin-diagnose", "builtin-builder"],
    "builtin-eng-debug-helper":         ["builtin-diagnose", "builtin-eng-bug-finder"],
    "builtin-eng-refactor-plan":        ["builtin-builder", "builtin-review"],
    "builtin-writer-blog-post":         ["builtin-writer-social-post", "builtin-writer-headlines"],
    "builtin-writer-social-post":       ["builtin-writer-blog-post", "builtin-writer-headlines"],
    "builtin-writer-proofreader":       ["builtin-writer-blog-post", "builtin-writer-social-post"],
    "builtin-writer-headlines":         ["builtin-writer-blog-post", "builtin-writer-social-post"],
    "builtin-sales-prospect-research":  ["builtin-sales-cold-outreach", "builtin-sales-call-prep"],
    "builtin-sales-cold-outreach":      ["builtin-sales-prospect-research", "builtin-sales-follow-up"],
    "builtin-sales-call-prep":          ["builtin-sales-prospect-research", "builtin-sales-follow-up"],
    "builtin-sales-follow-up":          ["builtin-sales-call-prep", "builtin-sales-cold-outreach"],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _template_id_from_name(name: str) -> Optional[str]:
    """Return the best-matching built-in template ID for an agent name."""
    lower = name.lower()
    for keyword, template_id in _NAME_PATTERNS:
        if keyword in lower:
            return template_id
    return None


def _build_cooccurrence(entries: list[dict]) -> dict[str, dict[str, int]]:
    """Build a pairwise co-occurrence map from agent.spawned events in the last 90 days.

    Returns {template_id: {neighbor_template_id: count}} where count is the number
    of ISO weeks in which both templates were used.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    week_to_tids: dict[str, set[str]] = {}
    for entry in entries:
        if entry.get("event") != "agent.spawned":
            continue
        ts = entry.get("timestamp", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if dt < cutoff:
            continue
        tid = entry.get("template_id") or _template_id_from_name(entry.get("name", ""))
        if not tid:
            continue
        week_key = dt.strftime("%G-W%V")
        week_to_tids.setdefault(week_key, set()).add(tid)

    cooccurrence: dict[str, dict[str, int]] = {}
    for tids in week_to_tids.values():
        tids_list = sorted(tids)
        for i, a in enumerate(tids_list):
            for b in tids_list[i + 1:]:
                cooccurrence.setdefault(a, {}).setdefault(b, 0)
                cooccurrence[a][b] += 1
                cooccurrence.setdefault(b, {}).setdefault(a, 0)
                cooccurrence[b][a] += 1
    return cooccurrence


def _within_window(ts_str: str, since: datetime) -> bool:
    if not ts_str:
        return False
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt >= since
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/adoption/whats-working")
async def get_whats_working():
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    audit_path = OSTK_DIR / "audit.jsonl"
    entries = read_audit_entries(audit_path)

    template_counts: dict[str, int] = {}
    prev_week_counts: dict[str, int] = {}
    agent_runs_completed = 0
    agent_runs_failed = 0

    for entry in entries:
        ts = entry.get("timestamp", "")
        in_this_week = _within_window(ts, week_ago)
        in_prev_week = not in_this_week and _within_window(ts, two_weeks_ago)
        if not (in_this_week or in_prev_week):
            continue
        ev = entry.get("event", "")
        if ev == "agent.spawned":
            tid = entry.get("template_id") or _template_id_from_name(entry.get("name", ""))
            if tid:
                if in_this_week:
                    template_counts[tid] = template_counts.get(tid, 0) + 1
                else:
                    prev_week_counts[tid] = prev_week_counts.get(tid, 0) + 1
        elif ev in ("agent.completed", "agent.failed") and in_this_week:
            agent_runs_completed += 1
            if ev == "agent.failed":
                agent_runs_failed += 1

    sorted_skills = sorted(template_counts.items(), key=lambda x: x[1], reverse=True)
    top_skills = []
    for tid, count in sorted_skills[:3]:
        tmpl = _TEMPLATE_BY_ID.get(tid)
        if tmpl:
            top_skills.append({
                "id": tid,
                "name": tmpl["name"],
                "uses_this_week": count,
                "prev_week_uses": prev_week_counts.get(tid, 0),
            })

    used_ids = {s["id"] for s in top_skills}

    # Use real co-occurrence data when there are at least 3 distinct weeks of history;
    # fall back to the static adjacency map for new users.
    cooccurrence = _build_cooccurrence(entries)
    distinct_weeks = len({
        datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).strftime("%G-W%V")
        for e in entries
        if e.get("event") == "agent.spawned" and e.get("timestamp")
    }) if entries else 0
    use_cooccurrence = distinct_weeks >= 3

    candidate_recs: dict[str, int] = {}
    for tid in used_ids:
        if use_cooccurrence:
            for neighbor, count in cooccurrence.get(tid, {}).items():
                if neighbor not in used_ids:
                    candidate_recs[neighbor] = candidate_recs.get(neighbor, 0) + count
        else:
            for neighbor in _SKILL_NEIGHBORS.get(tid, []):
                if neighbor not in used_ids:
                    candidate_recs[neighbor] = candidate_recs.get(neighbor, 0) + 1

    sorted_recs = sorted(candidate_recs.items(), key=lambda x: x[1], reverse=True)
    recommendations = []
    for rid, _ in sorted_recs[:2]:
        tmpl = _TEMPLATE_BY_ID.get(rid)
        if not tmpl:
            continue
        if use_cooccurrence:
            trigger_names = [
                _TEMPLATE_BY_ID[uid]["name"]
                for uid in used_ids
                if rid in cooccurrence.get(uid, {})
            ]
        else:
            trigger_names = [
                _TEMPLATE_BY_ID[uid]["name"]
                for uid in used_ids
                if rid in _SKILL_NEIGHBORS.get(uid, [])
            ]
        why = f"you've been using {trigger_names[0]}" if trigger_names else "based on your activity"
        recommendations.append({"id": rid, "name": tmpl["name"], "why": why})

    top_spec_or_task: Optional[str] = None
    try:
        all_tasks = await ostk.list_tasks()
        _priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        open_tasks = [
            t for t in all_tasks
            if t.get("status") != "closed"
            and not _SESSION_TASK_RE.match(t.get("title") or "")
        ]
        if open_tasks:
            best = min(open_tasks, key=lambda t: _priority_rank.get(t.get("priority") or "", 4))
            top_spec_or_task = best.get("title")
    except OstkError:
        pass

    return {
        "top_skills": top_skills,
        "recommendations": recommendations,
        "this_week": {
            "agent_runs_completed": agent_runs_completed,
            "agent_runs_failed": agent_runs_failed,
            "top_spec_or_task": top_spec_or_task,
        },
    }
