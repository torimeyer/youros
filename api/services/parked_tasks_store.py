"""Parked tasks store for mychat background work (→1399).

Tracks tasks parked via chat_schedule_wakeup / chat_monitor tools.
Persists to ~/.myos/parked_tasks.json so backend restarts resume timers.
Uses contextvars to thread tab_id + ws_send through execute_tool without
changing its signature.
"""

import asyncio
import contextvars
import json
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

_PARKED_FILE = Path.home() / ".myos" / "parked_tasks.json"

# Context vars set by agent_anthropic before each tool-execution batch.
# asyncio.gather inherits these from the task that called gather, so all
# parallel tool coroutines see the same values.
_ctx_tab_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "parked_ctx_tab_id", default=""
)
_ctx_ws_send: contextvars.ContextVar[Optional[Callable]] = contextvars.ContextVar(
    "parked_ctx_ws_send", default=None
)


@dataclass
class ParkedTask:
    id: str
    tab_id: str
    label: str
    kind: str               # "schedule" | "monitor"
    wakeup_at: Optional[float]   # unix ts; None for monitor
    command: Optional[str]       # monitor: shell command to run
    pattern: Optional[str]       # monitor: stdout grep pattern
    timeout_s: float             # monitor: max wait; default 900
    created_at: float
    cancelled: bool = False


class ParkedTasksStore:
    def __init__(self) -> None:
        self._tasks: dict[str, ParkedTask] = {}
        self._bg: set[asyncio.Task] = set()
        self._load()

    # ---- context ----

    def set_context(self, tab_id: str, ws_send: Callable) -> None:
        """Call from agent_anthropic before the tool-execution loop."""
        _ctx_tab_id.set(tab_id)
        _ctx_ws_send.set(ws_send)

    def ctx_tab_id(self) -> str:
        return _ctx_tab_id.get()

    def ctx_ws_send(self) -> Optional[Callable]:
        return _ctx_ws_send.get()

    # ---- CRUD ----

    def park(self, task: ParkedTask) -> None:
        self._tasks[task.id] = task
        self._persist()

    def cancel(self, task_id: str) -> Optional[ParkedTask]:
        t = self._tasks.get(task_id)
        if not t or t.cancelled:
            return None
        t.cancelled = True
        self._persist()
        return t

    def get_active(self) -> list[ParkedTask]:
        return [t for t in self._tasks.values() if not t.cancelled]

    def get(self, task_id: str) -> Optional[ParkedTask]:
        return self._tasks.get(task_id)

    # ---- background timers ----

    def schedule_wakeup_timer(self, task: ParkedTask, ws_send: Callable) -> None:
        delay = max(0.0, (task.wakeup_at or 0) - time.time())
        bg = asyncio.create_task(self._fire_wakeup(delay, task, ws_send))
        self._bg.add(bg)
        bg.add_done_callback(self._bg.discard)

    def schedule_monitor_timer(self, task: ParkedTask, ws_send: Callable) -> None:
        bg = asyncio.create_task(self._run_monitor(task, ws_send))
        self._bg.add(bg)
        bg.add_done_callback(self._bg.discard)

    async def _fire_wakeup(
        self, delay: float, task: ParkedTask, ws_send: Callable
    ) -> None:
        await asyncio.sleep(delay)
        t = self._tasks.get(task.id)
        if not t or t.cancelled:
            return
        t.cancelled = True
        self._persist()
        resume = f"I'm back — the '{task.label}' timer fired. Ready to continue."
        await self._emit_resume(ws_send, task, resume)

    async def _run_monitor(self, task: ParkedTask, ws_send: Callable) -> None:
        """Run the command and watch stdout for the pattern (or timeout)."""
        deadline = time.time() + task.timeout_s
        matched_line: Optional[str] = None
        try:
            proc = await asyncio.create_subprocess_shell(
                task.command or "true",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                t = self._tasks.get(task.id)
                if not t or t.cancelled:
                    proc.kill()
                    return
                try:
                    raw = await asyncio.wait_for(
                        proc.stdout.readline(),  # type: ignore[union-attr]
                        timeout=min(remaining, 5.0),
                    )
                except asyncio.TimeoutError:
                    continue
                if not raw:
                    break
                line = raw.decode(errors="replace").rstrip()
                if task.pattern and task.pattern in line:
                    matched_line = line
                    break
            try:
                proc.kill()
            except Exception:
                pass
        except Exception:
            pass
        t = self._tasks.get(task.id)
        if not t or t.cancelled:
            return
        t.cancelled = True
        self._persist()
        if matched_line is not None:
            resume = f"Monitor matched '{task.pattern}' in command output. Ready to continue."
        else:
            resume = f"Monitor for '{task.label}' timed out after {int(task.timeout_s)}s. Ready to continue."
        await self._emit_resume(ws_send, task, resume)

    @staticmethod
    async def _emit_resume(
        ws_send: Callable, task: ParkedTask, resume_text: str
    ) -> None:
        try:
            await ws_send({
                "type": "task_resumed",
                "data": {
                    "id": task.id,
                    "label": task.label,
                    "message": resume_text,
                },
                "tab_id": task.tab_id,
            })
            # Inject the resume text as a chat message so the user sees it.
            await ws_send({
                "type": "token",
                "data": resume_text,
                "tab_id": task.tab_id,
            })
            await ws_send({"type": "done", "tab_id": task.tab_id})
        except Exception:
            pass

    # ---- persistence ----

    def _persist(self) -> None:
        try:
            _PARKED_FILE.parent.mkdir(parents=True, exist_ok=True)
            active = [asdict(t) for t in self._tasks.values() if not t.cancelled]
            _PARKED_FILE.write_text(json.dumps(active, indent=2))
        except Exception:
            pass

    def _load(self) -> None:
        try:
            if _PARKED_FILE.exists():
                for item in json.loads(_PARKED_FILE.read_text()):
                    t = ParkedTask(**item)
                    if not t.cancelled:
                        self._tasks[t.id] = t
        except Exception:
            pass


parked_tasks_store = ParkedTasksStore()
