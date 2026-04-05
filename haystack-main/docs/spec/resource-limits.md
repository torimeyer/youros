---
status: spec
author: orchestrator
created: 2026-03-07
implements: []
---

# Resource Limits — P004

> Mish defaults tuned for multi-agent coordination. ostk inherits these.

## Mish Limits (v0.4.23)

| Resource | Default | Rationale |
|----------|---------|-----------|
| `max_sessions` | 12 | 7 agents + main session + headroom |
| `max_processes` | 50 | Each agent may spawn subprocesses |
| `PTY chunk_size` | 4096 bytes | Match macOS PTY buffer, fewer write cycles |
| `PTY backpressure_retries` | 3000 (~30s) | Long prompts to agents need drain time |
| `spool_bytes` | 4MB per process | Agents produce verbose output |
| `max_spool_bytes_total` | 200MB | 50 processes × 4MB |
| `handoff_timeout` | 900s | claude -p calls can take 30-60s |

## System Considerations (macOS Apple Silicon)

| Resource | System Limit | Mish Usage |
|----------|-------------|------------|
| PTY buffer | ~4KB kernel buffer | Chunk writes to match |
| Open file descriptors | 256 default (ulimit -n) | 2 fds per PTY × 50 = 100 fds |
| Processes | ~2000 (kern.maxproc) | 50 managed + OS overhead |
| Memory | 16-64GB typical | 200MB spool + agent RSS |

## Future: Configurable via mish.toml

```toml
[server]
max_sessions = 12
max_processes = 50
max_spool_bytes_total = 209715200

[squasher]
spool_bytes = 4194304

[pty]
chunk_size = 4096
backpressure_retries = 3000

[timeouts]
handoff_sec = 900
```

Not yet implemented — currently hardcoded defaults. See BUG-008.

## Acceptance Criteria

- [x] Limits raised to support 7+ concurrent agents (v0.4.23)
- [ ] Limits configurable via ~/.config/mish/mish.toml (BUG-008)
- [ ] `ostk top` shows resource usage vs limits
- [ ] Backpressure warning in digest when >80% of any limit
