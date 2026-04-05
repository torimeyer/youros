---
status: spec
author: round-table
created: 2026-03-08
discussion: transcripts/discussions/installation-shim/
participants: [systems-engineer, developer-experience, security-architect]
rounds: 2
implements: []
---

# Installation & Shim Layer

> One binary. PATH prefix. Byte-for-byte passthrough. The shim is invisible because it is behaviorally indistinguishable from the real tool.

## Install Flow

### Global Install (once)

```
curl -fsSL https://ostk.dev/install | sh
```

This drops a single statically linked binary at `~/.ostk/bin/ostk` and adds it to PATH. No daemon, no symlinks, no system-level changes. The binary is inert until used.

`brew install ostk` is a convenience path. The curl script is canonical because it works on Linux CI runners where Homebrew does not exist.

There is no `ostk install` command. The word "install" after the binary exists is confusing. Docker does not have `docker install`. You install Docker once, then you use it.

### Per-Project Setup (optional)

```
cd my-project
ostk init
```

Creates `.ostk/` in the project root (analogous to `.git/`). This directory holds the Agentfile, workspace config, and runtime state. `ostk init` also writes a `.envrc` for direnv integration.

`ostk init` is optional. Running `ostk run` in a directory without `.ostk/` works with sensible defaults, the same way `docker run ubuntu` works without a Dockerfile. The init ceremony is for committing configuration to the repo. Requiring it on day one is adoption poison.

### Running an Agent

```
ostk run agent.yaml
  1. Read agent.yaml for project root, resource limits
  2. Hash project root -> find or start daemon on Unix socket
  3. Connect to daemon, receive agent alias
  4. Build shim directory (bash->mish, cat->ss, ...)
  5. Export OSTK_SOCKET, OSTK_AGENT, prepend PATH
  6. exec the agent process in that environment
```

No chroot. No namespaces. No LD_PRELOAD. Just environment variables and PATH -- the two primitives every Unix process already respects.

## Shim Mechanism: PATH Prefix

Symlinks break. A symlink at `/usr/local/bin/bash` pointing to `mish` means every script on the system runs through the kernel. Shell functions only work in interactive shells and are invisible to subprocesses. LD_PRELOAD is fragile, platform-dependent, and breaks signed binaries on macOS.

PATH prefix is the proven pattern. direnv, nix-shell, virtualenv, rbenv, and pyenv all use it. The mechanism:

1. Create a directory of shims at `~/.ostk/shims/` containing thin executables named `bash`, `cat`, `sed`, etc.
2. Each shim delegates to the real binary through the coordination layer, byte-for-byte passthrough otherwise.
3. `ostk run` prepends `~/.ostk/shims/` to PATH.
4. Subprocesses inherit PATH. Works in bash, zsh, fish. Works in non-interactive shells. Works in `system()` calls from any language.

The shim is invisible because it is genuinely indistinguishable from the real tool at the interface level. The moment an agent can detect the shim -- through timing, through unexpected output, through a missing flag -- the shim has failed. Byte-for-byte passthrough is a correctness requirement, not an optimization.

## The Three Env Vars (The "Container")

The agent's isolation boundary is three environment variables. No namespaces, no cgroups, no mount points.

| Variable | Purpose |
|----------|---------|
| `PATH` | Shim directory prepended; routes tool calls through coordination |
| `OSTK_SOCKET` | Unix socket path to the project daemon |
| `OSTK_AGENT` | Kernel-assigned agent identity alias |

The agent enters the "container" by having these vars set. It exits by unsetting them. That is the full boundary. Sub-stacks map naturally: `ostk stack create` starts a new daemon on a new socket; agents in that stack get a different OSTK_SOCKET.

## Per-Project Isolation

Multiple ostk projects on one machine means multiple daemon instances, each with its own Unix socket:

```
$XDG_RUNTIME_DIR/ostk/<project-hash>/daemon.sock
```

OSTK_SOCKET tells shims which daemon to talk to. No kernel namespaces needed. No cgroups. No sandbox-exec. The isolation boundary is environment variables, not OS primitives.

The threat model is accidental collision, not malicious escape. OCC through the filesystem handles write interference. If sandboxing is needed later, it layers on top -- run `ostk run` inside a nix develop shell or a Docker container. The PATH-prefix approach composes with any outer isolation.

## direnv Integration

Do not build a custom shell hook. direnv already solves per-directory environment scoping:

- `ostk init` writes a `.envrc` that calls `ostk activate`
- direnv handles load/unload as you `cd` in and out
- Supports bash, zsh, fish, elvish
- Handles nesting of multiple projects

For CI and non-interactive contexts where direnv does not run: `eval $(ostk env)` exports PATH and OSTK_SOCKET. Same pattern as `docker env` and `ssh-agent`.

## DMA Bypass: Detect, Don't Prevent

An agent that calls `/usr/bin/cat` directly bypasses all coordination. This is the DMA bypass the llmOS spec defines. The shim is a tollbooth on a road with no fence.

Detection via stat-on-access: on the next `ss` read, compare the file's mtime against the gen counter from the last `ss` write. If the mtime is newer, someone went around the kernel. Response:

- Log the bypass event
- Flag it in the digest: `[bypass] src/main.rs modified outside ss`
- No false positives (mtime vs gen counter comparison is deterministic)
- Near-zero runtime cost (lazy check, not continuous polling)

Prevention (blocking raw filesystem access) requires OS-level sandboxing, which is a separate architectural layer.

## The Shim as Audit Plane, Not Security Boundary

The shim provides three things:

1. **Audit log.** Every file read and write routed through `ss` is a structured event: who, what file, what operation, gen counter before and after. Foundation for `audit trace` across agents.

2. **Policy gate.** Hold destructive tool calls (file deletion, `git push`, network access) pending human approval. The shim becomes an admission controller.

3. **Token accounting.** Per-agent usage tracking through the shim layer.

These are valuable. They are not sandboxing. The shim does not provide isolation. Treating it as a security boundary creates false confidence.

## OS-Level Sandboxing: Workspace, Not Per-Agent

macOS `sandbox-exec` and Linux seccomp/AppArmor solve the workspace boundary problem:

- One sandbox profile per workspace
- Restrict filesystem access to the workspace root
- Deny network except allowed endpoints
- Limit process spawning

Per-agent OS sandboxing is architecturally wrong -- agents within a workspace need shared filesystem access by design. The llmOS spec coordinates through the filesystem. Agents MUST read shared files to coordinate. The real threat is write interference, which Hot PR already handles.

The layering: OS sandbox enforces workspace boundaries. Shim enforces per-agent policy within the workspace. Neither alone is sufficient.

## CI/CD

### GitHub Actions

```yaml
- uses: ostk-dev/setup@v1  # drops binary, prepends PATH
- run: ostk run --headless agent.yaml
```

`--headless` means: apply the shim layer, run the agent to completion, exit with the agent's exit code. No persistent daemon. No cleanup. The runner is ephemeral. The agent is ephemeral. They match.

### Generic CI

```bash
eval $(ostk env)
ostk run --headless agent.yaml
```

Same pattern as `ssh-agent`. The setup action does exactly what the curl installer does: drop the binary, prepend PATH. Runners are disposable, so there is no cleanup concern.

### Multi-Agent CI

`ostk-compose.yaml` defines the topology. `ostk up --headless` runs all agents, collects exit codes, and reports. Same model as `docker compose up --abort-on-container-exit`.

## Agentfile as Dockerfile Analog

The Agentfile describes a single agent type. One Agentfile, one agent type. It builds a context layer, not a filesystem layer.

Directives: `FROM`, `PROMPT`, `TOOL`, `PULL`.

If multiple agents are needed, that is a separate concern addressed by `ostk.yaml` or `ostk-compose.yaml` -- a separate file with a separate mental model. Dockerfiles that tried to do orchestration failed. Compose succeeded because it was separate.

## Open Questions

These emerged from Round 2 synthesis and remain unresolved:

**1. Headless mode: daemonless or embedded daemon?** `--headless` proposes no daemon for CI. But the kernel architecture assumes a daemon always exists (process table and file state live there). Can the coordination layer run in-process for single-agent ephemeral runs, or does headless silently start and stop a daemon for the duration? This affects whether the kernel has one code path or two.

**2. Workspace-scoped OS sandboxing: ship or defer?** The security architect proposes shipping a default sandbox profile applied at `ostk run`. The systems engineer argues against baking sandbox-exec into the kernel as a microkernel violation. Both agree it layers on top. The question is whether v1 includes a default profile or leaves it to the user. Scope call, not architecture disagreement.

**3. Shim capability enforcement: Agentfile-declared or kernel-default?** The security architect proposes the shim refuse tool calls outside the Agentfile's declared scope (write `src/`, read-only `config/`, no `.env`). The Agentfile spec does not yet include capability declarations. Does the Agentfile grow a `CAPABILITY` directive, or does the kernel apply safe defaults (no `.env`, no `~/.ssh/`) without explicit declaration?

## Acceptance Criteria

- [ ] `curl -fsSL https://ostk.dev/install | sh` drops a single binary at `~/.ostk/bin/ostk` and adds it to PATH
- [ ] `ostk init` creates `.ostk/` and writes `.envrc` for direnv
- [ ] `ostk run` works without prior `ostk init` (sensible defaults)
- [ ] Shim directory at `~/.ostk/shims/` contains thin executables for coordinated tools (bash, cat, sed, etc.)
- [ ] `ostk run` prepends shim directory to PATH before exec-ing the agent
- [ ] Shims are byte-for-byte passthrough -- behaviorally indistinguishable from real tools
- [ ] Per-project daemon runs on Unix socket at `$XDG_RUNTIME_DIR/ostk/<project-hash>/daemon.sock`
- [ ] OSTK_SOCKET, OSTK_AGENT, and PATH are the only env vars defining the agent boundary
- [ ] direnv `.envrc` integration activates/deactivates on `cd`
- [ ] `eval $(ostk env)` works for CI and non-interactive contexts
- [ ] `--headless` flag runs agent to completion with exit code passthrough, no persistent daemon
- [ ] DMA bypass detected via stat-on-access (mtime vs gen counter) on next `ss` read
- [ ] Bypass events logged and flagged in digest as `[bypass]`
- [ ] Every shim-routed operation emits a structured audit event (who, file, operation, gen before/after)
- [ ] OS sandboxing, if applied, scopes to workspace level, not per-agent
- [ ] Agentfile describes a single agent type (one file, one agent)
- [ ] No `ostk install` command exists
