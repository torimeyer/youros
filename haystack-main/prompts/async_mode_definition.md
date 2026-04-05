# ASYNC MODE — Kernel Signal Processing Spec

## What ASYNC MODE Is

**Kernel signal processing mode where signals are processed by dependency, not temporal order.**

```
ASYNC MODE = operational state where:
- Signals may arrive out of temporal order
- Clock synchronization is not guaranteed
- Kernel queues signals by dependency
- Processing order: determined by :depends, not arrival time
- Result: Non-blocking, parallel execution when safe
```

---

## When ASYNC MODE Activates

**Signal:** `:clock skewed :operator async`

**Means:**
- Signal timestamps may be skewed or out of order
- Do not assume sequential causality
- Parse each signal independently
- Kernel resolves dependencies, not timing

**Common scenarios:**
- Network latency
- Concurrent message queues
- Distributed operators
- Multi-threaded execution
- Operator timezone differences

---

## How ASYNC MODE Changes Behavior

### Normal Mode (Sync)
```
Operator: :signal A
Kernel:   (waits)
Kernel:   [processes A]
Kernel:   (waits)
Operator: :signal B (depends on A)
Kernel:   [processes B]
```

**Requirement:** Sequential ordering
**Problem:** Blocking, slow, sensitive to timing

---

### ASYNC MODE (Active)
```
Operator: :signal A
Operator: :signal C      ← arrives before B
Operator: :signal B :depends A

Kernel:   (does NOT wait for timing)
Kernel:   [queues all signals]
Kernel:   [processes by dependency: A → B || C]
```

**Requirement:** Explicit dependencies (`:depends` tokens)
**Result:** Fast, non-blocking, parallel safe

---

## ASYNC MODE Rules

### Rule 1: Independent Signals Execute Parallel

```
:task foo           ← can execute immediately
:task bar           ← can execute immediately (no dependency on foo)

Kernel runs both in parallel.
No synchronization needed.
```

### Rule 2: Dependent Signals Queue

```
:task A
:task B :depends A

Kernel sees dependency.
Executes A first.
Queues B until A complete.
Then executes B.
```

### Rule 3: Out-of-Order Arrival Is OK

```
:task B :depends A
:task A              ← arrives after B

Kernel reorders by dependency.
Executes A (no unmet dependencies).
Then B (dependency satisfied).
Arrival order irrelevant.
```

### Rule 4: Audit Trail Records Both Timings

```
Signal:       [arrival_timestamp]     (may be out of order)
Queue entry:  [queue_order]           (canonical order)
Execution:    [execution_timestamp]   (real time)

Audit shows:
  - Temporal arrival (for diagnostics)
  - Dependency order (for correctness)
  - Execution time (for perf analysis)
```

---

## What ASYNC MODE Enables

### Performance
```
Without ASYNC: Sequential blocking
  Time: signal₁ + signal₂ + signal₃ = T₁ + T₂ + T₃

With ASYNC: Parallel when safe
  Time: max(T₁, T₂, T₃)
  Speedup: N× for N independent signals
```

### Resilience
```
Without ASYNC: One delayed signal blocks everything
With ASYNC: Queue ahead, execute independently
Result: Fault tolerance to timing skew
```

### Scalability
```
Without ASYNC: N signals = N round trips
With ASYNC: N signals = 1 batch, kernel processes
Result: Higher throughput, lower latency
```

---

## ASYNC MODE in Practice

### Example: Operator sends 3 signals

```
Operator (asynchronous):
  :task compile
  :task test :depends compile
  :task deploy :depends test

Kernel processes:
  Queue: [compile, test(:depends compile), deploy(:depends test)]
  Exec:  compile → test → deploy
         (in order per dependency, not by arrival)
```

### Example: Signals arrive out of order

```
Operator sends (actual arrival):
  :task deploy :depends test
  :task compile
  :task test :depends compile

Kernel reorders:
  Queue: [compile, test(:depends compile), deploy(:depends test)]
  Exec:  compile → test → deploy
         (order reconstructed from dependencies)
```

---

## ASYNC MODE Rules (Constraints)

**ASYNC MODE is NOT:**
- ✗ Ignore signals
- ✗ Drop out-of-order messages
- ✗ Execute without dependencies
- ✗ Lose timing information

**ASYNC MODE IS:**
- ✓ Queue signals independently
- ✓ Process by dependency, not arrival
- ✓ Execute in parallel when safe
- ✓ Record all timing (arrival + queue + execution)

---

## Implementation Pattern

```python
class AsyncKernel:
    def receive_signal(signal, arrival_time):
        """Queue signal with timestamp."""
        queue.append({
            signal: signal,
            arrival_time: arrival_time,      # when it arrived
            queue_order: len(queue),         # canonical order
            dependencies: parse_depends(signal),
            executed: False
        })

    def process_queue():
        """Process by dependency, not timing."""
        while queue not empty:
            # Find all signals with satisfied dependencies
            ready = [s for s in queue
                    if all(dep in executed for dep in s.dependencies)]

            if ready:
                # Execute all ready signals in parallel
                for s in ready:
                    execute(s)
                    s.executed = True
                    s.execution_time = now()
                    audit_trail.log(s)
            else:
                # Wait for dependencies or new signals
                wait_for_input()
```

---

## ASYNC MODE in Audit Trail

Each signal records:
```json
{
  "signal": ":task X",
  "arrival_time": "2026-03-10T03:00:00Z",
  "queue_order": 3,
  "dependencies": ["Y"],
  "execution_time": "2026-03-10T03:00:05Z",
  "result": "success"
}
```

Audit trail shows:
- **Temporal order** (for debugging skew)
- **Logical order** (for correctness verification)
- **Execution order** (for performance analysis)

---

## Why ASYNC MODE Matters for Operators

**Without ASYNC:**
```
Operator waits for kernel response
Kernel waits for next operator signal
Both idle, sequential, slow
```

**With ASYNC:**
```
Operator queues multiple signals
Kernel processes independently
Both work in parallel
System is non-blocking
```

**Result:**
```
Operator productivity increases
Kernel throughput increases
System becomes responsive to timing skew
```

---

## Summary

**ASYNC MODE = dependency-ordered signal processing**

```
Signals arrive asynchronously (timing uncertain)
Kernel queues and orders by dependency (not timing)
Execution: parallel when safe, sequential when dependent
Audit: records both temporal and logical order
Result: high throughput, resilient to timing skew, correct ordering
```

**Signal to activate:**
```
:clock skewed :operator async
```

**Kernel behavior:**
```
Parse signals independently
Process by :depends, not timing
Execute in parallel when safe
Record all timing information
```

---

## For Operators

If you send:
```
:task deploy :depends test
:task compile
:task test :depends compile
```

The kernel will:
1. Queue all three signals immediately (async)
2. Reorder by dependency (compile → test → deploy)
3. Execute in order (respecting dependency chain)
4. Return results as each completes

You don't need perfect timing. The kernel handles the async reordering.
