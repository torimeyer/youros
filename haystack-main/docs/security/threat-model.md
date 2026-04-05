---
status: accepted
version: 1.0
date: 2026-03-11
authors: [security-review]
scope: identity-layers, run, secret, file CAS
---

# Threat Model — ostk kernel

Review date: 2026-03-11
Reviewer: security-review agent (read-only)
Scope: `src/kernel/identity.rs`, `src/commands/run.rs`, `src/commands/secret.rs`, `src/kernel/file.rs`, `docs/spec/identity-layers.md`, `.ostk/HUMANFILE`
bail package: NOT SHIPPED — no findings possible (bail.rs / bail.md absent)

---

## Findings

---

### FIND-001

```
SEVERITY: P0
SURFACE: identity.rs — read_harness_identity()
THREAT: Any local process with write access to .ostk/boot.md can inject an arbitrary
        harness_identity: block before kernel startup. The kernel reads boot.md with a
        plain fs::read_to_string() — no signature check, no ownership check, no GPG
        verification. An attacker who can write to boot.md (same-UID process, agent
        with file-write access, or a concurrent race) can set verified_email to any
        string and have the kernel assign TrustTier::T1 to their session.
PRECONDITION: Write access to .ostk/boot.md before the kernel calls assign_alias().
              On a single-user workstation this is any process running as the same UID.
              In a multi-agent scenario this is any agent with ostk write access.
IMPACT: A T2 agent (CI, untrusted) escalates to T1. Attribution in agents.jsonl and
        gen_table.jsonl is permanently falsified — "scott@anthropic.com" appears as the
        author of writes made by the attacker. The dual-signature model (T0) is unaffected
        because it requires GPG ratification, but T1 is fully spoofable without GPG.
MITIGATION: The harness that writes boot.md must sign the harness_identity block with a
            harness-held key; the kernel must verify the signature before trusting the
            block. Without signature verification, harness_identity is advisory only and
            MUST NOT be used to derive a trust tier above T3 without out-of-band proof.
```

---

### FIND-002

```
SEVERITY: P0
SURFACE: secret.rs — resolve_secret() / OSTK_SECRET_CMD
THREAT: OSTK_SECRET_CMD is read from the environment and executed as a subprocess
        without any validation. An attacker who can set this environment variable (e.g.,
        a compromised parent shell, a malicious .env file loaded before ostk, or a
        symlink attack on a wrapper script) can point it at any executable. Every call
        to resolve_secret() — which is invoked for ANTHROPIC_API_KEY at run() entry,
        and for every --env-passthrough key — will execute the attacker's binary with
        the key name as an argument. The binary receives the key name and can exfiltrate
        it; if the legitimate keychain also stores the secret, the attacker's binary can
        retrieve and exfiltrate the value.
PRECONDITION: Ability to set OSTK_SECRET_CMD in the process environment before
              ostk run is called. This is trivially achievable from any shell running
              as the same UID.
IMPACT: Full exfiltration of all API keys resolved during a ostk run session
        (ANTHROPIC_API_KEY plus any --env-passthrough keys). The audit log records only
        "secret.injected" with key names — the exfiltration is silent.
MITIGATION: Validate OSTK_SECRET_CMD against an allowlist of known vault binaries
            (bw, op, pass) or a path stored in .ostk/HUMANFILE; alternatively, warn
            loudly if OSTK_SECRET_CMD is set and require explicit human confirmation.
```

---

### FIND-003

```
SEVERITY: P1
SURFACE: secret.rs — run_env()
THREAT: ostk secret env KEY prints "export KEY=VALUE" to stdout, exposing the raw
        secret value as a plain string. If stdout is captured by a log aggregator, shell
        history (via eval $(ostk secret env KEY)), or a pipe to a less-than-trusted
        process, the secret leaks. The comment says "for shell eval" but shell history
        logging (e.g. HISTFILE, script(1), CI log capture) will record the expanded value.
PRECONDITION: User or CI script runs `eval $(ostk secret env KEY)` in a logged
              terminal or a CI environment that captures stdout.
IMPACT: API key appears in shell history, CI build logs, or terminal recordings in
        plaintext. Severity is P1 (not P0) because it requires user initiation, but CI
        pipelines routinely capture all stdout.
MITIGATION: Remove run_env() or restrict it to write directly to /dev/tty (bypassing
            stdout capture); document that eval usage is prohibited in CI contexts.
```

---

### FIND-004

```
SEVERITY: P1
SURFACE: run.rs — --env-passthrough key name validation
THREAT: Key names passed via --env-passthrough are used in three places without
        sanitization: (1) looked up in secrets_map (HashMap key), (2) passed to
        resolve_secret() which embeds them in a Command::new(parts[0]).arg(key) call
        when OSTK_SECRET_CMD is active, and (3) passed to std::env::set_var(key, &value).
        std::env::set_var() on Unix will panic or produce undefined behaviour if the key
        contains a NUL byte; it will silently corrupt the environment if the key contains
        '=' (which env(1) treats as a delimiter). An attacker who controls the CLI
        invocation or an Agentfile can supply a key like "FOO=BAR" to inject a spurious
        env var, or "FOO\0" to trigger a panic.
PRECONDITION: Ability to control the --env-passthrough argument list, either by
              controlling the CLI invocation or by writing a malicious Agentfile that
              is then executed with ostk run.
IMPACT: Environment variable injection (can override PATH, LD_PRELOAD, etc.) or process
        panic. An Agentfile containing ENV_PASSTHROUGH PATH=/attacker/bin could redirect
        all subprocess binary lookups.
MITIGATION: Validate key names against [A-Z_][A-Z0-9_]* before any use; reject keys
            containing '=', NUL, or whitespace with a hard error.
```

---

### FIND-005

```
SEVERITY: P1
SURFACE: run.rs — read_humanfile_secrets() — HUMANFILE absent → fail open
THREAT: When .ostk/HUMANFILE does not exist, read_humanfile_secrets() returns an
        empty HashMap. The authorization check then evaluates:
            secrets_map.get(key).map(|s| s.as_str()).unwrap_or("")
        which yields "" for any key. The guard `if status != "authorized"` then FAILS
        CLOSED correctly — so the primary path is safe. HOWEVER: if HUMANFILE exists
        but the `secrets:` block is absent (e.g., a stripped HUMANFILE with only the
        philosophy and boot sections), the same empty-map result is returned and the
        same fail-closed behavior applies. This is correct today.
        The latent risk: a future refactor that checks `secrets_map.is_empty()` as a
        "HUMANFILE not configured" condition and fails OPEN ("no policy = allow all")
        would be a silent P0 regression. Document the invariant explicitly.
PRECONDITION: Future code change to authorization logic.
IMPACT: All --env-passthrough keys authorized if the check is inverted.
MITIGATION: Add a comment in read_humanfile_secrets() and in run() explicitly stating
            "empty map = deny all — this is the fail-closed invariant; do not invert."
            File as a code-clarity needle, not a security fix needle.
```

---

### FIND-006

```
SEVERITY: P1
SURFACE: kernel/file.rs — str_replace_all()
THREAT: str_replace_all() (lines 406-431) performs a read-then-write WITHOUT acquiring
        an flock. It calls read_file() (which does a plain fs::read_to_string) and then
        fs::write(). There is a TOCTOU window between the read and the write. A concurrent
        agent calling str_replace_cas() on the same file will hold the flock during its
        write, but str_replace_all() will overwrite that write silently — the flock is
        advisory on Linux/macOS and str_replace_all() never acquires it.
PRECONDITION: Two concurrent agents: one using str_replace_all(), one using str_replace()
              or str_replace_cas() on the same file.
IMPACT: Silent data loss. The str_replace_all() write wins regardless of generation,
        bypasses Hot PR, and leaves gen_table inconsistent with file contents.
MITIGATION: Add flock acquisition to str_replace_all() using the same pattern as
            str_replace() (open read+write, lock_exclusive, read, replace, seek+write+set_len,
            drop releases lock).
```

---

### FIND-007

```
SEVERITY: P2
SURFACE: identity.rs — assign_alias_inner() — alias reclaim race
THREAT: The is_process_alive() check (kill(pid, 0) == 0) inside assign_alias_inner()
        uses the Unix PID as a liveness proxy. On Linux, PIDs wrap at 32768 (4194304
        on some kernels). A dead agent's PID can be recycled by an unrelated process;
        is_process_alive() will return true for the recycled PID, blocking alias
        reclaim. More critically, the check itself is outside the flock: the sequence
        is (1) read agents under flock, (2) call is_process_alive() [flock held], (3)
        reclaim or reject. This is correct in isolation. However, a second agent
        concurrently attempting the same reclaim will block on the flock, read the same
        stale agent entry, and also call is_process_alive() — both will see the PID as
        dead and both will attempt to reclaim the same alias. The last writer to
        append_agent() wins, producing two active entries for the same alias.
PRECONDITION: Two agents simultaneously racing to claim the same alias after the
              original holder dies, combined with PID reuse by an unrelated process.
IMPACT: Duplicate active alias entries in agents.jsonl; gen_table attribution ambiguous
        between two aliases with the same name. Low probability in practice, higher in
        stress testing.
MITIGATION: After winning the flock, re-read agents.jsonl and verify the alias is still
            available (double-checked locking pattern) before appending the new entry.
```

---

### FIND-008

```
SEVERITY: P2
SURFACE: identity.rs — read_harness_identity() — no integrity check on boot.md
THREAT: read_harness_identity() performs line-by-line YAML-like parsing. It detects the
        harness_identity: block by exact string match and parses child keys by prefix
        strip. A crafted boot.md can include a second harness_identity: block later in
        the file — the parser breaks on the first non-indented line after the first
        block, so only the first block is read. However, if an attacker can prepend
        content to boot.md (race with harness write), they can inject a fake block that
        the parser reads first, before the legitimate harness-written block.
        Additionally, there is no check that verification_time is recent; a stale
        harness_identity block from a previous session can be reused to bootstrap T1
        trust in a new session.
PRECONDITION: Ability to prepend to boot.md before kernel reads it (same as FIND-001),
              OR boot.md from a previous session is not cleared at session start.
IMPACT: Stale or injected identity reused for a new session. T1 trust granted based on
        stale or fake verification_time.
MITIGATION: (1) Kernel should clear or verify boot.md at session start. (2) Kernel
            should reject harness_identity blocks where verification_time is older than
            a configurable threshold (e.g., 24 hours). (3) Long-term: sign the block.
```

---

### FIND-009

```
SEVERITY: P2
SURFACE: run.rs — Agentfile PROMPT file:// path traversal
THREAT: The Agentfile parser accepts `PROMPT file://path` where path is any string
        after stripping the "file://" prefix. In run.rs the resolved path is:
            base_dir.join(file_path)
        where base_dir is the parent directory of the Agentfile. Path::join() on Rust
        does NOT strip leading "/" from the joined component — if file_path starts with
        "/", it replaces base_dir entirely. A malicious Agentfile can therefore read any
        absolute path on the filesystem:
            PROMPT file:///etc/passwd
            PROMPT file:///Users/scott/.ssh/id_ed25519
        The content is loaded into system_prompt and sent to the Anthropic API.
PRECONDITION: Ability to supply a crafted Agentfile to `ostk run`. Any agent with
              write access to the filesystem can create an Agentfile. A user running
              `ostk run` on an untrusted Agentfile is also affected.
IMPACT: Arbitrary file read, exfiltration via the Anthropic API call (file contents
        become part of the system prompt sent over the network). Scope: any file
        readable by the ostk process owner.
MITIGATION: Validate that the resolved PROMPT file path is a subdirectory of base_dir
            (canonicalize both and check prefix); reject absolute paths and paths
            containing "..".
```

---

### FIND-010

```
SEVERITY: P3
SURFACE: secret.rs — run_set() on macOS uses -w flag with password as CLI argument
THREAT: On macOS, `security add-generic-password -w <password>` passes the password as
        a command-line argument to the `security` binary. On macOS the argument list is
        visible to other processes via `ps aux` or the kern.procargs2 sysctl until the
        process exits. The window is narrow but non-zero. On Linux the secret is piped
        via stdin (correct approach), not exposed in argv.
PRECONDITION: Another process running as the same UID (or root) calls ps or reads
              /proc/PID/cmdline during the brief window while `security add-generic-password`
              is executing.
IMPACT: API key exposed in process listing for the duration of the `security` subprocess.
        Mitigated by macOS's restricted /proc equivalent and the narrow time window.
MITIGATION: Use the `-P` flag (read password from stdin pipe) on macOS, same as the
            Linux secret-tool path, to keep the secret out of argv entirely.
```

---

## Summary

| ID        | Severity | Surface                                      | Status   |
|-----------|----------|----------------------------------------------|----------|
| FIND-001  | P0       | identity.rs — unsigned harness_identity      | open     |
| FIND-002  | P0       | secret.rs — OSTK_SECRET_CMD exec         | open     |
| FIND-003  | P1       | secret.rs — run_env() stdout plaintext       | open     |
| FIND-004  | P1       | run.rs — env-passthrough key name injection  | open     |
| FIND-005  | P1       | run.rs — HUMANFILE absent fail-closed doc    | open     |
| FIND-006  | P1       | file.rs — str_replace_all() missing flock    | open     |
| FIND-007  | P2       | identity.rs — alias reclaim race             | open     |
| FIND-008  | P2       | identity.rs — stale harness_identity reuse   | open     |
| FIND-009  | P2       | run.rs — PROMPT file:// path traversal       | open     |
| FIND-010  | P3       | secret.rs — macOS -w exposes secret in argv  | open     |

**Total: 10 findings (2 P0, 4 P1, 3 P2, 1 P3)**

bail package: not shipped — 0 findings (surface not present).

---

## Out of Scope / Not Findings

- boot.md world-writability: filesystem permissions are OS policy, not kernel policy.
  The kernel cannot enforce this without knowing the install context. Document as a
  deployment recommendation, not a code finding.
- HUMANFILE GPG signature gap: HUMANFILE is signed by the human operator at edit time.
  The kernel does not re-verify the GPG signature on every read (by design — local-first).
  This is a known architectural trade-off, not an unintended gap.
- agents.jsonl JSONL scale: a scaling concern (noted in MEMORY.md as killer #2), not a
  security threat. Out of scope for this review.
