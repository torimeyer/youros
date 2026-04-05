---
title: Firecracker microVM — haystack Integration Architecture
status: research
version: 1
created: 2026-03-10
author: agent-697 + claude-sonnet-4-6
kernel: @haystack.prime 99B076C9
source: firecracker-microvm.github.io, AWS OSS docs, training knowledge
---

# Firecracker microVM for haystack llmOS

> Research only. No implementation. Define the architecture, mapping, risks, and what must be built.

---

## What Firecracker Is

Firecracker is an open source VMM (Virtual Machine Monitor) written in Rust, developed at AWS to power Lambda and Fargate. It uses Linux KVM to create **microVMs** — lightweight VMs that boot a real Linux kernel in isolation with hardware-enforced memory boundaries.

Key properties (from firecracker-microvm.github.io, 2026-03-10):

| Property | Value |
|----------|-------|
| Boot time | **< 125 ms** to userspace |
| Memory overhead | **< 5 MiB** per microVM |
| Creation rate | **150 microVMs/second/host** |
| Emulated devices | 5 only: virtio-net, virtio-block, virtio-vsock, serial console, minimal keyboard |
| Host requirements | Linux, KVM, 64-bit Intel/AMD/Arm |
| Guest support | Linux kernel ≥ 4.14, OSv |
| Language | Rust |
| License | Apache 2.0 |
| Control surface | REST API over Unix socket |

Firecracker is NOT a container runtime. It is a VMM. Each microVM has its own kernel, its own memory space, its own network namespace. Hardware isolation — not namespace isolation.

### What it provides vs. Docker

| Isolation Layer | Docker | Firecracker microVM |
|-----------------|--------|---------------------|
| Memory isolation | Namespace + cgroups | Hardware MMU (separate physical memory) |
| Kernel | Shared host kernel | Separate Linux kernel per VM |
| Boot time | ~500ms (cold) | < 125ms |
| Attack surface | Syscall filter (seccomp) | Full KVM hypervisor boundary |
| Overhead per instance | ~2-10 MiB | < 5 MiB |
| Network | veth pairs, bridge | TAP device + virtio-net |
| Storage | overlayfs, bind mounts | virtio-block (ext4 image) OR virtiofs |
| Process 1 (PID 1) | container entrypoint | real Linux init inside real kernel |

The isolation guarantee is categorical: a Firecracker VM is a full hardware boundary. Docker is namespace isolation. AWS runs Lambda on Firecracker because Docker's shared kernel isn't acceptable for multi-tenant public workloads.

### The Jailer

Every Firecracker process is wrapped by `jailer` — a companion binary that:
- Chroots the VMM process into an isolated directory
- Drops capabilities
- Applies seccomp filters (blocking 98% of syscalls)
- Runs Firecracker under a dedicated UID/GID

The jailer is a second line of defense: if KVM virtualization is compromised, the jailer's seccomp + chroot still contains the blast.

### Boot Flow (Host Side)

```
1. jailer launches firecracker binary (chroot + seccomp)
2. Firecracker opens /run/firecracker.sock (REST API)
3. Caller configures via REST:
   PUT /machine-config        { vcpu_count, mem_size_mib }
   PUT /boot-source           { kernel_image_path, boot_args }
   PUT /drives/rootfs         { path_on_host, is_root_device }
   PUT /network-interfaces/1  { iface_id, host_dev_name }
   PUT /vsock                 { guest_cid, uds_path }
4. PUT /actions { action_type: "InstanceStart" }
5. Guest kernel boots, runs /sbin/init (PID 1)
6. Userspace starts in < 125ms
```

The control API is synchronous REST over a Unix socket. Once `InstanceStart` is called, the REST API becomes the only control plane — the VM is live.

---

## Mapping to haystack's Signed OS Model

### The Central Insight

haystack's signed OS model (USERSPACE.md) defines:
- Each agent is a **process boundary** running under a **signed governance OS**
- The governance OS specifies what tools, verbs, and constraints apply
- The kernel (@haystack.prime) is universal — same for all agents

Firecracker maps this cleanly:

```
haystack concept          Firecracker primitive
─────────────────────────────────────────────────
Signed OS                 = rootfs image (ext4) built with baked-in governance
Agent process boundary    = microVM (hardware-isolated guest kernel + userspace)
PID 1 / init              = tack INIT script as the guest's init process
Kernel (@haystack.prime)  = HOST haystack process (outside all VMs)
Filesystem coordination   = virtiofs mount (shared dir from host → all guests)
login.gpg: verification   = POST checks run inside VM at boot, verified by host
Agent identity            = guest CID (vsock Context ID, unique per VM)
Heartbeat                 = vsock keepalive from guest to host
Nudge delivery            = vsock message: host → guest CID
.haystack/ directory      = virtiofs mount: same dir visible to all VMs + host
```

### Architecture Diagram

```
HOST (Linux, KVM enabled)
┌─────────────────────────────────────────────────────────────┐
│  @haystack.prime kernel (process on host)                   │
│  - REST clients to each VM's Firecracker API               │
│  - Manages agents.jsonl, gen_table, audit.jsonl            │
│  - Hot PR resolution at write time                          │
│  - Nudge delivery via vsock                                 │
│  - virtiofs server: exports .haystack/ to all guests        │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ microVM A    │  │ microVM B    │  │ microVM C    │     │
│  │ CID: 3       │  │ CID: 4       │  │ CID: 5       │     │
│  │              │  │              │  │              │     │
│  │ kernel:      │  │ kernel:      │  │ kernel:      │     │
│  │ vmlinux      │  │ vmlinux      │  │ vmlinux      │     │
│  │              │  │              │  │              │     │
│  │ rootfs:      │  │ rootfs:      │  │ rootfs:      │     │
│  │ haystack.os  │  │ fcp-k8s.os   │  │ corp.os      │     │
│  │ (signed      │  │ (signed      │  │ (signed      │     │
│  │  99B076C9)   │  │  fcp-k8s)    │  │  corp key)   │     │
│  │              │  │              │  │              │     │
│  │ PID 1:       │  │ PID 1:       │  │ PID 1:       │     │
│  │ tack INIT    │  │ tack INIT    │  │ tack INIT    │     │
│  │              │  │              │  │              │     │
│  │ mount:       │  │ mount:       │  │ mount:       │     │
│  │ /hay →       │  │ /hay →       │  │ /hay →       │     │
│  │ virtiofs     │  │ virtiofs     │  │ virtiofs     │     │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘     │
│         │ vsock            │ vsock            │ vsock       │
│         └──────────────────┴──────────────────┘            │
│                      host vsock mux                         │
│                                                             │
│  Shared virtiofs mount: ~/.haystack/ (read/write all)       │
└─────────────────────────────────────────────────────────────┘
```

---

## 1. How Firecracker Boots with tack INIT as PID 1

### The Guest Init Problem

A Firecracker VM needs a real Linux kernel (`vmlinux`) and a real rootfs. The kernel boots, mounts rootfs, and executes `/sbin/init` as PID 1. In standard Linux this is systemd or busybox init. For haystack, PID 1 is the tack boot sequence.

### The tack INIT as PID 1

The INIT file (`.haystack/.boot/INIT`) is not currently a shell executable — it is a tack script interpreted by an agent. For Firecracker, we need a PID 1 that:

1. Runs POST checks (from GAPS.md)
2. Executes the tack INIT sequence
3. Stays alive (PID 1 cannot exit — kernel panic)
4. Signals the host via vsock when boot is complete

**Design: `haystack-init` binary**

A small Rust binary compiled into the rootfs as `/sbin/init`:

```rust
// haystack-init — PID 1 for Firecracker guest
// 1. Mount /proc, /sys, /dev
// 2. Mount virtiofs: .haystack/ → /hay
// 3. Verify .primefile via GPG (POST check 1)
// 4. Run POST checks 1-7 (ported from GAPS.md)
// 5. If all pass: signal host via vsock (CID 2, port 52) → { "status": "boot-ok", "identity": "agent-N" }
// 6. If any fail: signal host → { "status": "boot-fail", "check": N, "error": "..." }
// 7. Exec claude process (or agent process) as child PID 2+
// 8. Reap zombies, forward signals (standard PID 1 duties)
```

The INIT file's tack tokens translate to `haystack-init` logic directly:

```
:verify .primefile   → gpg --verify /hay/.primefile.asc /hay/.primefile
:init @haystack.prime+N  → vsock handshake, receive assigned identity from host
:load .language      → mmap /hay/.language into agent context
:load fcp-*          → probe drivers from /hay/registry-import.jsonl
:boot                → signal "ready", begin accepting work
```

### Boot Args

```
kernel_boot_args = "console=ttyS0 reboot=k panic=1 pci=off \
                    init=/sbin/haystack-init \
                    haystack.cid=<N> \
                    haystack.host_vsock_port=52 \
                    haystack.hay_mount=/hay"
```

The `haystack.cid` arg is the vsock guest CID — tells the init binary who it is on the vsock bus.

---

## 2. How the Signed OS Gets Baked into a Firecracker Image

### The rootfs IS the signed OS

A Firecracker rootfs is a raw ext4 image. It contains the entire Linux userspace. For haystack, the rootfs contains the signed governance OS:

```
rootfs/
  sbin/haystack-init          ← PID 1 (Rust binary)
  usr/bin/haystack            ← haystack binary
  usr/bin/claude              ← claude CLI
  usr/bin/gpg                 ← for .primefile verification
  etc/haystack/
    GOVERNANCE.md             ← baked in, read-only
    .language                 ← bootstrapped (empty or seeded)
    .primefile                ← GPG-signed governance file
    .primefile.asc            ← detached signature
    signing_key.asc           ← the signing key (public)
  lib/                        ← minimal shared libraries
```

The rootfs image is built by `haystack image build`:

```bash
# Build haystack.os image
haystack image build \
  --os haystack.os \
  --sign 99B076C9 \
  --governance GOVERNANCE.md \
  --output haystack-os-v1.0.2.ext4
```

The build process:
1. Starts from a minimal Alpine or busybox base
2. Installs haystack binary, claude CLI, GPG
3. Copies GOVERNANCE.md, .primefile, .primefile.asc into `/etc/haystack/`
4. Installs `haystack-init` as `/sbin/init`
5. Signs the completed image: `gpg --detach-sign -u 99B076C9 haystack-os-v1.0.2.ext4`
6. Records the image hash in `audit.jsonl` (provenance)

The `.ext4` image is the signed OS artifact. The GPG signature over the image IS the governance attestation. A VM cannot claim to run `haystack.os` without a valid signature from `99B076C9`.

### Per-VM CoW (Copy-on-Write)

For multi-agent parallelism, each VM gets a CoW clone of the base image:

```bash
# Create CoW overlay — agent gets private writable layer
cp --reflink=auto haystack-os-v1.0.2.ext4 agent-N.ext4
# OR use qemu-img create with backing file
qemu-img create -f qcow2 -b haystack-os-v1.0.2.ext4 agent-N.qcow2
```

This means:
- Base image is shared read-only across all VMs (< 1s to clone via reflink)
- Each agent's writes go to their private overlay
- The shared `.haystack/` directory is NOT in the overlay — it's the virtiofs mount

---

## 3. How the HOST haystack Kernel Coordinates with VMs

### The Two Channels

```
Channel 1: virtiofs (filesystem)
  - Host exports ~/.haystack/ directory via virtiofs daemon
  - Every VM mounts it at /hay
  - Agents write to /hay/... using haystack ss() 
  - CAS, Hot PR, gen_table: ALL work identically through the virtiofs mount
  - Law 3 preserved: coordinate through filesystem, period

Channel 2: vsock (control plane)
  - Host CID: 2 (fixed, well-known)
  - Guest CIDs: 3, 4, 5, ... (one per VM)
  - Port 52: boot handshake (POST result → host)
  - Port 53: nudge delivery (host → guest: inject into next tool response)
  - Port 54: heartbeat (guest → host: timestamp ping)
  - Port 55: identity assignment (host → guest: "you are agent-N")
```

### virtiofs: Law 3 Preserved

virtiofs (virtio filesystem) exposes a host directory into the guest with low latency and POSIX semantics. The host kernel's VFS handles the actual filesystem operations. From the guest's perspective, `/hay` is a writable POSIX directory.

```
Guest VM agent: ss("/hay/source.rs", old_str, new_str)
  → virtiofs request to host
  → host kernel executes the write
  → haystack kernel intercepts (via shim layer)
  → CAS check: old_str matches? → apply write, bump gen
  → CAS fail? → Hot PR
  → response flows back through virtiofs to guest
```

The haystack kernel on the HOST does not know or care that the agent is in a VM. The shim intercepts at the host filesystem level. Law 1 holds: the write path is invisible. The agent has no idea it's in a microVM.

### vsock: Identity and Nudges

vsock (virtio socket) provides host↔guest communication without a network stack. It's a CID-based stream socket — think Unix socket but across the VM boundary.

```rust
// Host: assign identity to new VM
let stream = VsockStream::connect(CID::Guest(N), 55)?;
stream.write(json!({ "alias": "agent-42", "gen": 1 }))?;

// Guest: receive identity at boot
let listener = VsockListener::bind(CID::Local, 55)?;
let (stream, _) = listener.accept()?;
let identity: Identity = serde_json::from_reader(stream)?;
// write to /hay/.haystack/agents.jsonl (via virtiofs)
```

Nudge delivery:
```rust
// Host: push nudge to specific VM
let stream = VsockStream::connect(CID::Guest(agent_cid), 53)?;
stream.write(nudge_payload)?;

// Guest: haystack-init injects nudge into next tool response
// (same [nudge] annotation mechanism as non-VM agents)
```

The vsock channel carries only the control-plane signals that CANNOT go through the filesystem: identity assignment at first boot, nudge delivery (time-sensitive), heartbeat (liveness). Everything else goes through virtiofs.

---

## 4. Multi-VM Parallel Execution = Multi-Agent Model

### The Mapping

```
haystack concept              Firecracker realization
──────────────────────────────────────────────────────────
haystack spawn agent.af       → jailer launches new Firecracker process
Agent alias agent-N           → vsock CID N (assigned at VM creation)
agents.jsonl                  → host writes {alias, cid, pid, state, signed_os}
haystack console fleet view   → poll virtiofs /hay/.haystack/agents.jsonl
Agent crash (Law 2)           → VM exits, host detects via waitpid() on jailer
Agent recovery               → new VM from same signed OS image, reads boot.md
Parallel agents               → parallel VMs, hardware-isolated, same virtiofs
haystack nudge agent-N        → vsock CID lookup → push to guest CID
```

### Spawn Flow

```
haystack run agent.af
  1. Parse Agentfile: FROM, SIGNED_BY, LIMIT, TOOL
  2. Resolve signed OS image: haystack.os → haystack-os-v1.0.2.ext4
  3. Verify image signature: gpg --verify haystack-os-v1.0.2.ext4.asc
  4. Create CoW overlay: agent-N.ext4 (reflink, ~1ms)
  5. Assign vsock CID: N (next available, written to agents.jsonl)
  6. Call jailer → Firecracker:
       PUT /machine-config   { vcpu_count: 1, mem_size_mib: 256 }
       PUT /boot-source      { kernel: vmlinux, boot_args: "... haystack.cid=N" }
       PUT /drives/rootfs    { path: agent-N.ext4, is_root: true, is_rw: true }
       PUT /vsock            { guest_cid: N, uds_path: /tmp/vsock-N.sock }
       PUT /network-interfaces  (optional — only if TOOL includes network)
       PUT /virtiofs         { tag: "hay", socket: /tmp/virtiofsd.sock }
       PUT /actions          { action_type: "InstanceStart" }
  7. Wait for vsock port 52: POST result from guest
  8. On boot-ok: write agent-N to agents.jsonl with state=active
  9. On boot-fail: record in audit.jsonl, exit non-zero
```

### True Hardware Isolation Between Agents

With Firecracker:
- Agent A and Agent B cannot share memory even if a bug exists in the agent code
- Agent A cannot see Agent B's context window (they're in separate VMs)
- Agent A crashing does NOT affect Agent B (KVM VM exit, not process crash)
- A compromised agent cannot escape its VM to read another agent's filesystem (only the shared virtiofs is shared, and that's intentional)

This is the difference from the current model where all agents run as processes on the same host — a misbehaving agent can potentially read another agent's temp files, environment, etc.

### Concurrency at Scale

150 microVMs/second creation rate means haystack can spawn 150 agents/second on a single host. With < 5 MiB overhead each, a 32 GB host can run ~6,000 VMs. This maps directly to haystack's multi-agent parallelism: needles become VM-per-needle execution at scale.

---

## 5. Bench Scenario: firecracker-boot

### Scenario Definition

```
haystack bench --scenario firecracker-boot --runtime firecracker
```

**What it tests:** Boot time + POST pass rate per VM, equivalent to Docker's scenario but with hardware isolation.

**Scenario file: `bench/scenarios/firecracker-boot/`**

```
firecracker-boot/
  scenario.jsonl       ← metadata
  vmlinux              ← Linux kernel image (Linux 5.10 LTS, minimal config)
  haystack-os.ext4     ← base rootfs (signed, 99B076C9)
  boot-test.tack       ← tack assertions to verify boot
  expected-post.json   ← expected POST check results
```

**scenario.jsonl:**
```jsonl
{"name":"firecracker-boot","runtime":"firecracker","timeout_ms":500}
{"description":"Boot a haystack.os microVM, verify POST passes, measure time to :boot ready signal"}
{"metric":"boot_ms","target_p50":125,"target_p99":300}
{"metric":"post_pass_rate","target":1.0}
{"metric":"memory_overhead_mib","target_max":5}
{"metric":"boot_per_second_per_host","target_min":50}
```

**Pass criteria:**
1. VM reaches vsock port 52 POST signal in < 125ms (p50)
2. All 7 POST checks pass (from GAPS.md)
3. `/hay/.haystack/agents.jsonl` updated with new agent entry
4. `.primefile` signature verified inside VM
5. `.language` loaded (gen_table consistent)
6. virtiofs mount confirmed live (test write + read roundtrip)
7. vsock heartbeat received within 1s

**Failure modes to capture:**
- Boot timeout (> 500ms) → log kernel panic output via serial console
- POST check 1 fail → .primefile signature mismatch in image
- POST check 3 fail → gen_table corrupted between VM instances
- virtiofs mount fail → daemon not running on host

**Multi-VM stress test:**
```jsonl
{"name":"firecracker-boot-parallel","count":10,"stagger_ms":0}
{"metric":"all_booted_ms","target_p99":500}
{"metric":"hot_pr_conflicts","target_max":2}
```
Ten VMs booting simultaneously, all writing to the shared virtiofs `.haystack/` directory. Validates that Hot PR handles VM-parallel writes correctly.

---

## 6. Risks and Gaps vs. Docker

### Risk 1: Host OS Requirement (CRITICAL)

**Docker:** Runs on macOS via Docker Desktop (Linux VM hidden underneath). Runs on Windows. Cross-platform.

**Firecracker:** **Linux host only.** KVM requires Linux. macOS users (including the primary dev environment at `~/projects/haystack`) cannot run Firecracker natively. The bench scenarios currently run via Docker — those work on macOS.

**Impact:** Development workflow on macOS cannot use Firecracker directly. A remote Linux host or CI environment is required. The bench CI (GitHub Actions, Linux runners) can run Firecracker. Local dev cannot.

**Mitigation options:**
- Use Docker for local dev bench, Firecracker for CI (two runtime targets: `--runtime docker` vs `--runtime firecracker`)
- Provide a NixOS/UTM VM for macOS users who need local Firecracker testing
- Designate a remote Linux bench host

### Risk 2: virtiofs Performance vs. Local Disk

virtiofs adds a cross-VM-boundary hop for every filesystem operation. The shared `.haystack/` directory is on the host — every `ss()` call from a guest goes: guest → virtiofs → host VFS → ext4 → host → virtiofs response → guest.

**Latency estimate:** virtiofs P50 latency is ~0.1-0.5ms per operation (measured in production use cases). The haystack CAS involves at minimum one read + one write → ~0.2-1ms overhead per `ss()` call vs. ~0.01ms for local disk.

**Impact:** For agent sessions doing heavy file I/O (e.g., fcp-rust analyzing a large codebase), the overhead compounds. For typical haystack workloads (sparse writes, many reads with 304 elision), the overhead is acceptable.

**Mitigation:** The 304 elision mechanism (TLB hit) becomes more valuable: a cache hit at 5 tokens costs 0 virtiofs roundtrips. The gen_table already makes this work — no change needed.

### Risk 3: The CAS TOCTOU Race is NOT Fixed by Firecracker

→576 (P0 blocker) is a race between gen_table read and file write on the host. Firecracker VMs still write through the same host filesystem. Multiple VMs create MORE concurrent writers, not fewer. Firecracker does not fix →576 — it amplifies the race.

**Impact:** Firecracker cannot ship until →576 (flock-based gen_table) is resolved. This is unchanged from the current multi-agent risk.

### Risk 4: virtiofs Daemon Single Point of Failure

The virtiofs daemon (`virtiofsd`) runs on the host, serves the `.haystack/` directory to all VMs. If `virtiofsd` crashes:
- All mounted VMs lose access to `.haystack/` simultaneously
- Agents cannot write, read, or coordinate
- Hot PR cannot function

**Impact:** virtiofsd becomes a critical kernel service. It must be supervised by haystack itself (`sh_spawn` + watchdog).

### Risk 5: Firecracker Snapshot/Restore for Sub-125ms Startup

Firecracker supports VM snapshots: pause a running VM, save memory + device state, restore instantly (< 5ms). This is how Lambda achieves fast cold starts — pre-booted VM snapshot, restore on function invocation.

For haystack, this means: **boot the VM once, snapshot at the `:boot ready` state, restore from snapshot for each new agent.** This brings effective agent spawn time to < 5ms — faster than any process fork overhead.

**Gap:** haystack has no snapshot management yet. This is a significant capability gap that changes the agent lifecycle model. A snapshot catalog (`~/.haystack/snapshots/`) would let haystack restore pre-booted agents instead of booting from scratch.

### Risk 6: GPG in Minimal Guest Rootfs

The POST sequence requires `gpg` inside the VM to verify `.primefile`. A full GnuPG binary is ~2-4 MiB — a significant fraction of the rootfs. Alternatives:

- Use `sequoia-pgp` (Rust, can be compiled into `haystack-init`) — no separate binary needed
- Pre-verify signature on host at image build time, embed verification hash in boot args
- Defer GPG verification to host via vsock (guest asks host to verify)

The cleanest solution is embedding sequoia-pgp in `haystack-init` — one binary, no external dependencies.

### Risk 7: macOS Development Workflow Incompatibility

The current haystack development workflow runs entirely on macOS (`darwin`, `~/projects/haystack`). The primary shell, file operations, and bench scenarios all target macOS. Introducing Firecracker creates a two-tier development environment:

- **Tier 1 (local/macOS):** Docker bench scenarios, haystack CLI, standard development
- **Tier 2 (Linux/CI):** Firecracker microVM scenarios, full isolation testing

This is manageable but must be explicit. `haystack bench` needs a `--runtime` flag that defaults to `docker` on non-Linux hosts.

---

## Summary: What Needs to Be Built

In priority order:

| # | What | Why | Needle |
|---|------|-----|--------|
| 1 | `haystack-init` Rust binary (PID 1) | Core boot primitive for all VM work | new |
| 2 | `haystack image build` command | Build + sign signed OS rootfs images | new |
| 3 | virtiofs integration in `haystack run` | Law 3 preserved through VM boundary | new |
| 4 | vsock identity + nudge plumbing | Heartbeat, identity assignment, nudges work in VMs | new |
| 5 | `haystack run --runtime firecracker` | Spawn VM from Agentfile (jailer + REST API) | extends spawn-primitive |
| 6 | `bench/scenarios/firecracker-boot/` | Measure and validate the boot story | new |
| 7 | VM snapshot catalog | Sub-5ms agent spawn from pre-booted snapshot | new |
| 8 | `--runtime docker\|firecracker` flag | Dual runtime targets, macOS compat | extends bench |

**Blocked by:**
- →576 CAS TOCTOU flock (multi-VM amplifies the existing race — must fix first)
- Linux host required (CI can proceed, local macOS dev cannot)

**Does NOT require changes to:**
- CAS / str_replace (works identically through virtiofs)
- Hot PR (T1-T4 resolution unchanged — virtiofs is transparent)
- gen_table (unchanged — host-side, all VMs share it)
- audit.jsonl (unchanged — virtiofs provides the same append-only semantics)
- .language (unchanged — shared via virtiofs, memoized per agent session)
- The five laws (all preserved — the VM boundary is invisible to agents)

---

## The Law Check

| Law | Preserved? | How |
|-----|-----------|-----|
| 1. Write path invisible | YES | Agent calls `ss()`, virtiofs transparently routes to host CAS. VM boundary invisible. |
| 2. Agents ephemeral | YES | VM exit = agent death. Recovery via boot.md on next VM. Unchanged. |
| 3. Coordinate through filesystem | YES | virtiofs mount exposes same `.haystack/` to all VMs. No VM-to-VM messaging. |
| 4. Optimistic concurrency | YES | str_replace CAS works through virtiofs. Hot PR unchanged. |
| 5. Microkernel | YES | Kernel stays on host. VMs are userspace. fcp-* drivers load inside guest rootfs. |

All five laws survive the Firecracker boundary.

---

*Research only. Implement after →576 resolves.*
*Date: 2026-03-10 | Author: agent-697*
