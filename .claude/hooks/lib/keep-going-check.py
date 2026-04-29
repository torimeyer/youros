#!/usr/bin/env python3
"""
keep-going-check.py
Reads TASKS_JSON and AGENTS_JSON env vars, emits a FIRE-NOW directive
if open tasks exist and no user-spawned agents are running.
"""
import json, os, sys

tasks_raw  = os.environ.get("TASKS_JSON", "")
agents_raw = os.environ.get("AGENTS_JSON", "")

if not tasks_raw or not agents_raw:
    sys.exit(0)

try:
    tasks_data  = json.loads(tasks_raw)
    agents_data = json.loads(agents_raw)
except Exception:
    sys.exit(0)

EXCLUDE_SOURCES = {"ack-bot", "heartbeat-bot", "e2e-smoke"}
running_agents = 0
for a in agents_data.get("agents", []) or []:
    if a.get("status") != "running":
        continue
    src = a.get("source")
    if src not in ("claude-code", "task-bridge") or src in EXCLUDE_SOURCES:
        continue
    name = a.get("name") or ""
    if not name or name.startswith("claude-code-"):
        continue
    running_agents += 1

open_tasks      = []
in_progress_cnt = 0
for t in tasks_data.get("tasks", []) or []:
    s = (t.get("status") or "").lower()
    if s == "in_progress":
        in_progress_cnt += 1
    elif s == "open":
        open_tasks.append(t)

if running_agents > 0 or in_progress_cnt > 0 or not open_tasks:
    sys.exit(0)

print()
print("PENDING-TASK FIRE CHECK — DO NOT ASK PERMISSION:")
print(f"  {len(open_tasks)} open task(s). No agents running. No task in_progress.")
print("  This is your cue to act, not to ask. Pick the highest-priority task and saa it now.")
print()
top = sorted(open_tasks, key=lambda t: (t.get("priority") or "P9"))[:3]
for t in top:
    tid   = t.get("id") or "?"
    title = (t.get("title") or "").strip()[:90]
    pri   = t.get("priority") or "?"
    print(f"  [{pri}] {tid}: {title}")
print()
print("  Rule: ostk decides when to go. You are torios. Fire the next task now.")
