# .language as Kernel Register — Draft

**Date**: 2026-03-24
**Status**: DRAFT
**Extends**: fcp-shared-protocol.md, unix-to-haystack.md

## Problem

Kernel primitives get built but never fully wired. `fcp/web.rs` exists but isn't a driver. `kernel/dying.rs` writes fleet state nobody reads. `kernel/nudge.rs` delivers to a void. The architecture produces orphaned capabilities because there's no registration contract.

## The Unix Precedent

Unix drivers register with the kernel: `register_chrdev(major, name, &fops)`. This creates a `/dev/` entry. Without registration, the driver is dead code. The kernel enforces the contract — unregistered drivers are invisible.

## Design: .language IS the Register

`.language` already tracks 73 verbs with tier, layer, resolution, signature, momentum. Extend it to track **all kernel capabilities**: verbs, devices, and services.

### Layer types

| Layer | Unix equivalent | Example |
|-------|----------------|---------|
| `kernel` | Built-in syscall | :boot, :shutdown, :post |
| `ceremony` | Privileged operation | :confirm, :correct |
| `user` | Userspace command | :compile, :hay, :bench |
| `device` | /dev/* driver | :fcp-rust, :fcp-web, :fcp-screen |
| `service` | Kernel module | :gen-table, :elision, :hotpr, :approval |

### Tier 0 = Infrastructure

Tier 0 entries are not user-invocable. They're internal kernel state — the equivalent of `/proc/devices` entries. Only the kernel reads and writes them.

```
# Infrastructure — tier 0, not user-invocable
:fcp-screen  | 0 | device   | 0 | 0 | 1.00 | internal | (event) → (render) | display driver
:fcp-rust    | 0 | device   | 0 | 0 | 0.00 | .ostk/drivers/fcp-rust.sock | (query) → (result) | rust intelligence
:fcp-web     | 0 | device   | 0 | 0 | 0.00 | .ostk/drivers/web.sock | (url) → (markdown) | web reading
:gen-table   | 0 | service  | 0 | 0 | 1.00 | internal | (path) → (gen) | generation tracking
:elision     | 0 | service  | 0 | 0 | 1.00 | internal | (path,hwm) → (304|content) | read optimization
:hotpr       | 0 | service  | 0 | 0 | 1.00 | internal | (conflict) → (merge) | conflict resolution
:approval    | 0 | service  | 0 | 0 | 1.00 | internal | (tool) → (decision) | tool approval
:nudge       | 0 | service  | 0 | 0 | 0.00 | .ostk/nudges/ | (agent,msg) → () | inter-agent messaging
:dying       | 0 | service  | 0 | 0 | 0.00 | .ostk/fleet/ | () → (state) | context pressure notification
```

### Momentum as health signal

- `1.00` = alive and recently used
- `0.00` = registered but not available (socket down, no consumer)
- Decays over time (same half-life mechanism as verbs)
- Boot sets initial momentum based on health check
- Runtime updates on each successful call

### Boot lifecycle

```
1. Parse HUMANFILE → DRIVER directives
2. Parse .language → existing device/service entries
3. For each DRIVER directive not in .language:
   → Add tier-0 device entry with momentum 0.00
4. For each device entry:
   → Spawn if needed (check PID, start process)
   → Health check (connect to socket, send ping)
   → Set momentum = 1.00 if alive, 0.00 if dead
5. For each service entry:
   → Verify internal module (gen_table file exists, etc.)
   → Set momentum = 1.00 if functional
6. POST reads .language tier-0 entries:
   → "devices: 2/3 alive (fcp-rust: down)"
   → "services: 7/7 ok"
```

### Runtime dispatch

```rust
// Tool dispatch checks the register
fn should_route_to_driver(tool_name: &str, language: &[LanguageEntry]) -> Option<String> {
    language.iter()
        .find(|e| e.layer == "device" && e.verb == tool_name && e.momentum > 0.0)
        .map(|e| e.resolution.clone())
}

// Kernel services check before producing output
fn should_write_fleet_state(language: &[LanguageEntry]) -> bool {
    language.iter()
        .any(|e| e.verb == "dying" && e.momentum > 0.0)
}
```

### Shutdown compiles the register

The existing shutdown compilation already saves `.language`. Extend it to:
1. Read runtime device/service health
2. Update momentum for all tier-0 entries
3. Write the full table (verbs + devices + services)

## What this eliminates

| Current | Replaced by |
|---------|------------|
| `.ostk/fleet/agent/state.yml` | `:dying` service entry with momentum |
| `.ostk/drivers/*.pid` scan in bootloader | `:fcp-*` device entries with socket resolution |
| `read_humanfile_drivers()` at boot | DRIVER directives → device entries in .language |
| Scattered `if socket_exists()` checks | `momentum > 0.0` on the device entry |
| Dead `pop_nudges()` calls | `:nudge` service entry with momentum 0 = skip |

## Implementation

1. Extend `LanguageEntry` in `language.rs`:
   - Add `device` and `service` as valid layers
   - Tier 0 entries filtered from user-facing commands

2. Boot registers devices/services in `.language`
   - `boot/drivers.rs` writes device entries after spawn
   - Health check sets momentum

3. POST reads tier-0 entries for status display

4. Tool dispatch in `agent_loop.rs` checks device registration

5. Kernel services (dying, nudge) check service registration before writing

## Compounds

- `:fcp-web` as a registered device replaces the hardcoded `fcp/web.rs` function call
- Fleet state becomes a service that only runs when registered
- The `.language` file becomes the kernel's `/proc/devices` — the complete runtime truth table
