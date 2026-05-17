# Time primitive

Version: 1.0  
Status: active  
Module: `api/services/time_primitive.py`  
Routes: `GET /api/time/status/{op_id}`, `GET /api/time/estimate/{op_kind}`, `GET /api/time/running`

---

## Purpose

The Time primitive tracks how long things are taking and predicts how long they will take. Any operation that runs for more than a second or two — spawning an agent, running a smoke test, cutting a release — can register with Time. Time watches the clock, records history, and surfaces a "~2m left" estimate so you are never staring at a blank spinner.

Time is the only place in myOS that does this. Other services that used to compute their own estimates (like the agent panel) now ask Time instead.

---

## Contract

### Inputs and outputs

```python
from services.time_primitive import start, progress, finish, status, estimate, all_running, TimeStatus

# Tell Time an operation has started.
start(op_id: str, op_kind: str, hint_sec: int | None = None) -> None
# op_id   — unique ID for this specific run (e.g. the agent name or a UUID)
# op_kind — category of work (e.g. "agent_spawn", "smoke_gate", "release_cut")
# hint_sec — your best upfront guess in seconds; None means "I don't know"

# Report how far along the operation is (0.0 to 1.0).
progress(op_id: str, pct: float, current_step: str | None = None) -> None

# Tell Time the operation is done.
finish(op_id: str, status: Literal["completed", "failed", "cancelled"]) -> None

# Ask about a specific running operation.
status(op_id: str) -> TimeStatus | None

# Ask how long this kind of operation usually takes, based on history.
estimate(op_kind: str) -> int | None  # seconds, or None if no history yet

# Get all currently running operations.
all_running() -> list[TimeStatus]
```

### TimeStatus fields

| Field | Type | Meaning |
|---|---|---|
| `op_id` | str | Unique run identifier |
| `op_kind` | str | Category of work |
| `started_at` | float | Unix timestamp when `start()` was called |
| `elapsed_sec` | float | Seconds since start |
| `progress_pct` | float | 0.0–1.0 |
| `current_step` | str or None | Human-readable step label |
| `eta_sec` | int or None | Seconds remaining (None = estimating) |
| `status` | str | `"running"`, `"completed"`, `"failed"`, `"cancelled"` |

### HTTP endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/time/status/{op_id}` | TimeStatus JSON or 404 |
| GET | `/api/time/estimate/{op_kind}` | `{"op_kind": ..., "median_sec": ...}` |
| GET | `/api/time/running` | Array of TimeStatus JSON |

---

## Events emitted

Every `start`, `progress`, and `finish` call writes a row to `~/.myos/primitives.db` → table `time_runs`. No separate event bus. Callers that want to observe changes poll `/api/time/running` or subscribe via the frontend `useTimeStatus` hook.

---

## Versioning history

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-05 | Initial release. `start / progress / finish / status / estimate / all_running`. SQLite history store, rolling median ETA. |

---

## Worked examples

### Wrap an agent spawn

```python
from services.time_primitive import start, finish

agent_id = "my-agent-abc123"
start(agent_id, "agent_spawn")
try:
    run_agent(agent_id)
    finish(agent_id, "completed")
except Exception:
    finish(agent_id, "failed")
```

### Ask how long agents usually take

```python
from services.time_primitive import estimate

median_sec = estimate("agent_spawn")
if median_sec:
    print(f"Agents usually finish in about {median_sec // 60}m")
else:
    print("Not enough history yet — estimating…")
```

### Fetch all active operations via HTTP

```
GET /api/time/running

[
  {
    "op_id": "release-3.18.2",
    "op_kind": "release_cut",
    "elapsed_sec": 45.3,
    "progress_pct": 0.4,
    "eta_sec": 67,
    "status": "running"
  }
]
```

---

## What this primitive is NOT

- **Not a scheduler.** Time does not trigger anything. It only observes and reports.
- **Not a real-time push system.** It does not push updates via websocket; callers poll or use the frontend hook.
- **Not cross-user.** History lives on your laptop in `~/.myos/primitives.db`. Nothing is shared across machines.
- **Not a replacement for logs.** It tracks elapsed time and ETA, not detailed step-by-step output. For that, read the transcript.
- **Not a billing meter.** It measures wall-clock time, not token counts or compute cost.
