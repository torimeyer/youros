# Time primitive

A yourOS primitive (v1.0). Every long-running operation publishes its start, progress, and finish through Time; consumers ask Time for ETA and current status.

## Purpose

Give every long-running operation in yourOS a single, observable lifecycle. Time records when an op started, what step it's on, and (using rolling-median history) when it's likely to finish. Other surfaces (chat preamble, Agents panel pill, release banner) read from Time.

## Contract

Module: `services.time_primitive` · Version: v1.0 · Status: active.

```python
def start(op_id: str, op_kind: str, hint_sec: int | None = None) -> None
def progress(op_id: str, pct: float, current_step: str | None = None) -> None
def finish(op_id: str, status: Literal["completed", "failed", "cancelled"]) -> None
def status(op_id: str) -> TimeStatus | None
def estimate(op_kind: str) -> int | None
def all_running() -> list[TimeStatus]

@dataclass
class TimeStatus:
    op_id: str
    op_kind: str
    started_at: float
    elapsed_sec: float
    eta_sec: int | None
    progress_pct: float
    current_step: str | None
    status: Literal["running", "completed", "failed", "cancelled"]
```

HTTP surface:

- `GET /api/time/status/{op_id}` → TimeStatus or 404
- `GET /api/time/estimate/{op_kind}` → `{"estimate_sec": int | null}`
- `GET /api/time/running` → `{"running": [TimeStatus, ...]}`

## Events emitted

Each state-changing call (`start`, `progress`, `finish`) writes a row into `time_runs` in `~/.myos/primitives.db`. Audit-level introspection is mechanical: the table is the audit.

## Versioning history

- **v1.0** (2026-05-16): initial release. SQLite-backed history. Rolling-median ETA over the last 30 completed runs of the same `op_kind` in the last 14 days. 2-second floor, 2-hour cap on outliers (lifted from `agent_duration_stats.py`).

## Worked examples

```python
from services import time_primitive as time

# Start an op with no estimate (let the median figure it out)
time.start("smoke-2026-05-16-001", op_kind="smoke_gate")

# Mid-run progress
time.progress("smoke-2026-05-16-001", pct=0.45, current_step="phase 4: live HTTP")

# Done
time.finish("smoke-2026-05-16-001", status="completed")

# Anywhere else, ask: how long does a smoke usually take?
secs = time.estimate("smoke_gate")  # e.g. 920

# Or: what's currently running?
for st in time.all_running():
    print(st.op_id, st.eta_sec)
```

## What this primitive is NOT

- **Not a job scheduler.** Time does not start work; it only observes work that other code is doing.
- **Not a retry mechanism.** Time records `failed`; it does not re-spawn or retry.
- **Not the chat preamble itself.** Chat's "~Nm" prefix calls Time's `estimate()`; Time does not own the chat UI.
- **Not a cross-user store.** Each laptop's `~/.myos/primitives.db` is personal.
