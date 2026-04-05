---
status: draft
version: 1.0
date: 2026-03-11
authors: [production-readiness-review]
scope: kernel, commands, serve, squasher, agentfile
---

# Production Readiness Review — ostk kernel

Review date: 2026-03-11
Reviewer: claude-sonnet-4-6 (read-only on src/)
Scope: src/kernel/{file,identity,hotpr,registry,elision}.rs, src/commands/{secret,run,bail,post,shutdown,boot}.rs, src/agentfile/parser.rs, src/serve/dispatch.rs, src/squasher/mod.rs, src/main.rs (head-300)

Prior art: threat-model.md (10 findings, FIND-001 through FIND-010) — all findings in this document are NEW unless explicitly cross-referenced.

---

## Section 1: Security — Boundary Escape

---

### PR-S-001

```
SEVERITY: P1
CATEGORY: security-escape
FILE: src/commands/bail.rs
LINE: 547-552
FINDING: decrypt_os_bin() extracts a GPG-decrypted tar into hs_dir using `tar -xzf`
         without --no-overwrite-dir or path-filter arguments. A crafted os.bin
         could contain archive entries with absolute paths (e.g., /etc/cron.d/pwn)
         or leading ../ components that escape hs_dir. GNU tar does strip leading /
         by default, but macOS BSD tar does not strip ../ traversal by default,
         and neither version validates all traversal vectors.
IMPACT: An attacker who crafts a malicious full-mode bail can write arbitrary files
        anywhere the running user can write on macOS — including ~/.ssh/authorized_keys,
        ~/.bashrc, crontabs, or other sensitive paths — triggered simply by the victim
        running `ostk bail unpack attacker.bail`.
MITIGATION: Add --strip-components=1 to all tar extract calls (already present in
            extract_bail_archive for the outer archive, but absent in decrypt_os_bin),
            and add a path-validation step that checks every archive entry before
            extraction begins. Alternatively: unpack to a staging directory and refuse
            any entry whose canonicalized path falls outside the target.
```

---

### PR-S-002

```
SEVERITY: P1
CATEGORY: security-escape
FILE: src/commands/bail.rs
LINE: 183-193
FINDING: run_unpack() calls verify_gpg_signature() but treats a FAILED verification
         as a warning and PROCEEDS with the unpack ("proceeding…"). If GPG verification
         fails (bad sig, unknown key, GPG absent), the code prints a message and
         continues to copy_bail_files_to_hs() and decrypt_os_bin(). A crafted bail
         with a stripped .asc file (or delivered in an environment without GPG) will
         always warn-and-proceed.
IMPACT: An attacker distributes a bail without a valid signature (or with a signature
        over a different manifest). The victim's kernel state is silently overwritten
        with attacker-controlled boot.md and .primefile. Trust chain is destroyed.
        This is the primary delivery vector for PR-S-001 above.
MITIGATION: Make signature verification mandatory on unpack (fail-closed). Add a
            --allow-unsigned flag behind a deliberate opt-in. Document that absent GPG
            is not equivalent to a passing signature.
```

---

### PR-S-003

```
SEVERITY: P2
CATEGORY: security-escape
FILE: src/commands/bail.rs
LINE: 412-430
FINDING: pack_os_bin() iterates .ostk/ with fs::read_dir() and copies non-excluded
         files into os_staging, but the exclusion list (BAIL_EXCLUDE) is a flat name
         match. Subdirectories other than "sessions/" and "secrets/" are iterated with
         src.is_file() — nested state that should not leave the machine (e.g.,
         .ostk/shadows/, .ostk/offers/, .ostk/store/) is included in os.bin
         unless the exact directory name appears in BAIL_EXCLUDE. The loop also skips
         subdirectory recursion entirely (entry.path().is_file()), so nested sensitive
         files under any unlisted directory are silently omitted; but new directories
         added in future releases may be inadvertently included.
IMPACT: Internal state (shadow files, offer drafts, unreviewed tack patterns) leaks
        into the encrypted os.bin. If the recipient is different from the sender,
        this violates the intended privacy boundary.
MITIGATION: Switch to an explicit allowlist of files/dirs that CAN appear in os.bin,
            rather than a denylist of what must not. Any file not on the allowlist is
            excluded by default.
```

---

### PR-S-004

```
SEVERITY: P2
CATEGORY: security-escape
FILE: src/commands/secret.rs
LINE: 258-274
FINDING: The OSTK_SECRET_CMD environment variable fallback (lines 258-274) is still
         active even after the →620 P0 mitigation moved primary resolution to
         .ostk/config. The code comment calls this "Legacy env var support — still
         support for backward compat." Any process running as the current UID can set
         OSTK_SECRET_CMD and have it execute as the secret resolver for all
         resolve_secret() calls. The threat-model.md FIND-002 identified the primary
         path; this is the secondary path that remains.
IMPACT: Same as FIND-002: full exfiltration of all API keys during a ostk run
        session. The mitigation in .ostk/config is bypassed if OSTK_SECRET_CMD
        is set in the environment, since env var resolution runs after config_cmd
        succeeds (the env path is only used when config_cmd is absent or returns empty).
        Actually re-reading: the env path IS a fallback and runs only when config_cmd
        returns empty. Still an active exec surface for an attacker-controlled binary.
MITIGATION: Remove the OSTK_SECRET_CMD env var path entirely, or gate it behind
            an explicit HUMANFILE permission (same as env-passthrough). Backward-compat
            users can migrate to .ostk/config.
```

---

### PR-S-005

```
SEVERITY: P2
CATEGORY: security-escape
FILE: src/commands/shutdown.rs
LINE: 344-345
FINDING: compile_humanfile() constructs a session path from the agent name parameter
         without sanitizing it: format!(".ostk/sessions/{}.jsonl", agent). The
         `agent` string is supplied by the caller (from --agent CLI flag, default
         "orchestrator"). A crafted agent name like "../boot" resolves to
         .ostk/sessions/../boot.jsonl = .ostk/boot.jsonl. The function then
         reads that file and writes to HUMANFILE, potentially injecting arbitrary
         content into HUMANFILE (via the verb regex extraction and append logic).
IMPACT: A user who invokes `ostk shutdown --agent ../boot` causes boot.md content
        to be parsed as session JSONL and extracted "verbs" to be appended to HUMANFILE.
        This degrades HUMANFILE integrity. Severity is P2 because the output is
        filtered through a verb regex, limiting injection surface.
MITIGATION: Validate the agent name against [a-zA-Z0-9_-]+ before constructing the
            session path. Reject names containing / or ..
```

---

### PR-S-006

```
SEVERITY: P3
CATEGORY: security-escape
FILE: src/commands/boot.rs
LINE: 196-213
FINDING: load_entityfile() attempts GPG verification via `gpg --verify <asc> <file>`,
         where <asc> and <file> are constructed from the ENTITYFILE path. On failure
         (Ok(out) with !success), the code prints "GPG chain INVALID" and CONTINUES
         to print ENTITYFILE: v{version}. A bad signature is reported but does not
         halt boot. This is a deliberate design choice but means the trust signal is
         advisory-only with no enforcement.
IMPACT: An attacker who overwrites ENTITYFILE (same as compromising .ostk/) also
        overwrites the GPG status to "not signed" rather than "INVALID" (by removing
        the .asc file). The boot output changes but boot does not halt.
MITIGATION: Document explicitly that ENTITYFILE verification is advisory at boot; a
            future --strict-boot flag can halt on invalid or missing signatures.
```

---

## Section 2: Idiomatic Rust

---

### PR-R-001

```
SEVERITY: P2
CATEGORY: rust-idiom
FILE: src/main.rs
LINE: 991
FINDING: CString::new(a.as_str()).unwrap() is called inside an iterator in the
         passthrough exec path. CString::new() returns Err only if the string contains
         a NUL byte. If any argv element from the shell contains an embedded NUL, this
         panics the process rather than returning a clean error. The passthrough path
         is reached for every command that ostk intercepts and passes through.
IMPACT: A shell invocation like `bash -c $'echo \x00'` causes ostk to panic with
        a thread panic in the binary shim layer. Because this is the passthrough path,
        it affects all commands, not just ostk subcommands.
MITIGATION: Replace .unwrap() with .map_err(|_| ...) and propagate the error cleanly,
            or strip NUL bytes from argv before CString conversion.
```

---

### PR-R-002

```
SEVERITY: P2
CATEGORY: rust-idiom
FILE: src/main.rs
LINE: 726
FINDING: unsafe { std::env::set_var(key_name, &val) } in the bench secret injection
         path uses the same pattern as run.rs:138. The safety comment on line 725 says
         "no other threads running at this point in startup" — but this is true only
         for the main thread. In a multi-threaded binary, set_var() while other threads
         exist is undefined behaviour on glibc (the env is not thread-safe). The comment
         could be invalidated by future refactors that spawn threads before this point.
IMPACT: Potential undefined behaviour in multi-threaded context. Currently safe only
        because of the single-threaded invariant; fragile under refactor.
MITIGATION: Use std::env::set_var only at program start before any threads are spawned,
            or switch to a thread-safe env abstraction. Add a prominent comment that
            this call MUST remain before tokio runtime or thread-pool initialization.
```

---

### PR-R-003

```
SEVERITY: P2
CATEGORY: rust-idiom
FILE: src/kernel/file.rs
LINE: 407-431
FINDING: str_replace_all() is missing flock protection — this is already FIND-006 in
         threat-model.md (P1). Separately from the security classification, this is
         also a Rust-idiom finding: the function pattern is inconsistent with str_replace()
         and str_replace_cas(), which both acquire an exclusive flock before reading.
         str_replace_all() calls read_file() (plain fs::read_to_string) then fs::write()
         with no lock, creating both a TOCTOU race and silent data loss under concurrency.
IMPACT: See FIND-006. Under concurrent access, str_replace_all() silently overwrites
        writes made by str_replace() or str_replace_cas() on the same file.
MITIGATION: Apply the same open+lock_exclusive+read+write+set_len pattern used by
            str_replace(). This is a straightforward mechanical fix.
```

---

### PR-R-004

```
SEVERITY: P2
CATEGORY: rust-idiom
FILE: src/serve/dispatch.rs
LINE: 130
FINDING: serde_json::to_value(result).unwrap() in handle_initialize() is called on the
         hot path (every MCP initialize). to_value() can return Err if a value contains
         a non-string map key or other serde-unrepresentable structure. If the
         InitializeResult struct ever changes to contain such a type, this will panic
         the MCP server on connection — a silent availability loss.
IMPACT: MCP server crashes on every new client connection if the serialization fails.
        Currently safe (InitializeResult contains only strings and bools), but fragile.
MITIGATION: Replace .unwrap() with proper error handling: return an ERR_INTERNAL
            response if serialization fails.
```

---

### PR-R-005

```
SEVERITY: P3
CATEGORY: rust-idiom
FILE: src/squasher/mod.rs
LINE: 29
FINDING: The squasher's record_metrics() implements a manual UTC timestamp formatter
         with an incorrect calendar approximation. It uses `year = 1970 + days / 365`
         (ignoring leap years) and `mo = doy / 30 + 1`, `dy = doy % 30 + 1`. This
         produces incorrect dates: month 13 is possible near year-end, and day 31 is
         never reached (max is 29). The same issue exists in identity.rs which has a
         correct days_to_ymd() implementation. Metrics timestamps will be silently wrong.
IMPACT: Metrics JSONL contains incorrect timestamps. Low security impact; moderate
        operational impact (metrics are untrustworthy for debugging).
MITIGATION: Replace the squasher's manual UTC implementation with the correct
            days_to_ymd() already implemented in identity.rs (or extract to a shared
            utility).
```

---

### PR-R-006

```
SEVERITY: P3
CATEGORY: rust-idiom
FILE: src/kernel/hotpr.rs, src/serve/dispatch.rs
LINE: various
FINDING: Multiple large String clones in hot paths. In try_tier2_rebase() (hotpr.rs),
         base_content and current_content are both .to_string() cloned into the
         AssistedMerge variant (which may be large for big files). In dispatch.rs,
         args_summary uses .to_string() and format! in a per-call path. These are
         not currently in tight loops but will become expensive under high-throughput
         multi-agent workloads.
IMPACT: Increased memory pressure and allocation latency under high concurrency.
        Not a correctness bug today.
MITIGATION: Consider Arc<str> for large file content in MergeResult variants, and
            defer args_summary computation until it is actually needed (only when
            logging occurs).
```

---

### PR-R-007

```
SEVERITY: P3
CATEGORY: rust-idiom
FILE: src/commands/bail.rs
LINE: 618-624
FINDING: create_bail_archive() calls staging.parent().unwrap_or(staging) and
         staging.file_name().unwrap_or_default() without handling the None case
         for file_name() properly. If staging is "/" (impossible in practice but
         possible under adversarial path construction), file_name() returns None and
         unwrap_or_default() returns OsStr::default() — an empty path — causing tar
         to fail with an obscure error rather than a clear diagnostic.
IMPACT: Low probability; obscure error message on unexpected path input.
MITIGATION: Validate staging path before the tar invocation; return Err early if
            staging.file_name().is_none().
```

---

## Section 3: Concurrency

---

### PR-C-001

```
SEVERITY: P1
CATEGORY: concurrency
FILE: src/kernel/file.rs
LINE: 407-431
FINDING: str_replace_all() — no flock. Already classified as FIND-006 (P1, security) and
         PR-R-003 (P2, rust-idiom). Concurrency classification: the missing flock means
         a concurrent str_replace_cas() writer (which holds the file's exclusive flock)
         will have its write silently overwritten by a racing str_replace_all() that
         does not acquire the flock. The advisory nature of flock means the OS does not
         prevent str_replace_all() from writing while str_replace_cas() holds the lock.
IMPACT: Silent data loss. The gen_table will record the str_replace_cas() write as gen N;
        str_replace_all() will write a stale version and leave the file at a gen N-1
        content with gen_table at gen N. All future CAS operations on the file will see
        a mismatch.
MITIGATION: Same as FIND-006: add flock to str_replace_all().
```

---

### PR-C-002

```
SEVERITY: P1
CATEGORY: concurrency
FILE: src/kernel/registry.rs
LINE: all read/write functions
FINDING: register_os_instance_at(), update_registry_timestamp_at(), and
         resolve_instance_at() all read-modify-write registry.jsonl without any flock
         or other mutual exclusion. Two concurrent `ostk boot` or `ostk install`
         invocations (common in CI or parallel agent spawning) will race on the registry
         file, producing duplicate entries or lost updates.
IMPACT: registry.jsonl may contain duplicate path entries after concurrent registrations;
        resolve_instance() may return stale data. The registry is used for cross-OS
        nudge routing — a corrupt registry silently breaks nudge delivery.
MITIGATION: Add a registry.lock flock, mirroring the identity.lock pattern in
            identity.rs. All three public functions should acquire the lock before
            reading and release it after writing.
```

---

### PR-C-003

```
SEVERITY: P2
CATEGORY: concurrency
FILE: src/kernel/identity.rs
LINE: 192-211
FINDING: read_agents() is called WITHOUT the identity lock from external callers (e.g.,
         boot.rs via reap::reap_dead_agents, tui commands). The function does a plain
         fs::read_to_string() on agents.jsonl. If a concurrent assign_alias_inner()
         is mid-write to agents.jsonl (holding identity.lock and writing the file),
         the unlocked read_agents() may observe a partial write (a truncated JSONL line).
         serde_json::from_str() on a partial line silently returns Err and skips the
         entry — the read-and-display caller sees a stale agent list.
IMPACT: Race: one agent's entry is invisible to TUI or reap while it is being written.
        This is a display race, not a data-loss race (the write will complete), but it
        means reap_dead_agents() could skip an agent that is actually active.
MITIGATION: Make read_agents() require the lock to be held (move it to a private method),
            or add a separate read lock that allows concurrent reads but exclusive writes.
```

---

### PR-C-004

```
SEVERITY: P2
CATEGORY: concurrency
FILE: src/serve/dispatch.rs
LINE: 81, 94
FINDING: handle_initialize() uses try_write() to write to state.agent_alias and
         state.recovery_text. try_write() returns Err immediately if the write lock
         is contended. The error is silently ignored (if let Ok(mut guard) = ...).
         If initialization races with a concurrent tool call that holds the read lock
         on agent_alias, the alias and recovery text are never stored.
IMPACT: Agent alias is None for all subsequent tool calls, causing heartbeat, digest,
        and nudge operations to silently no-op (they check if alias is Some). The agent
        operates without identity, and its tool calls are not logged to the session JSONL.
MITIGATION: Replace try_write() with write().await for both fields, or restructure
            initialization to guarantee the lock before any concurrent tool calls
            can begin.
```

---

### PR-C-005

```
SEVERITY: P2
CATEGORY: concurrency
FILE: src/kernel/elision.rs
LINE: 100-197
FINDING: read_file_with_elision() reads the gen_table twice (lines 112 and 142) in
         the bypass detection path: once to get stored_mtime for comparison, and again
         to get the gen entry for hwm checking. Between the two reads, a concurrent
         writer can bump the gen (which bumps the mtime record in the gen_table).
         The second read_gen() call may see a different generation than the first,
         causing the hwm check to compare against a generation that does not correspond
         to the file content just read.
IMPACT: An agent may receive stale 304 responses immediately after a bypass-induced
        gen bump, if a concurrent write happens between the two gen_table reads.
        This is a visibility race, not data corruption.
MITIGATION: Read gen_table once, hold the entry in a local variable, and use that
            single entry for both the bypass check and the hwm comparison.
```

---

### PR-C-006

```
SEVERITY: P3
CATEGORY: concurrency
FILE: src/squasher/mod.rs
LINE: 12-61
FINDING: record_metrics() appends to .ostk/metrics.jsonl using OpenOptions::append
         with create(true). Concurrent appends from multiple squasher calls (possible
         if multiple sh_run commands complete simultaneously in the MCP server) are
         safe for append (O_APPEND is atomic for small writes on Linux/macOS), but the
         path discovery (current_dir walk up) is computed fresh each time. Two concurrent
         callers with different current_dirs could write to different metrics files.
IMPACT: Metrics may be split across files if cwd changes between calls. Very low
        probability; metrics data only.
MITIGATION: Cache the metrics path at initialization rather than recomputing via cwd walk.
```

---

## Section 4: Cryptographic Trust Chain

---

### PR-T-001

```
SEVERITY: P1
CATEGORY: crypto
FILE: src/commands/bail.rs
LINE: 185-194
FINDING: verify_gpg_signature() is called in run_unpack(), and on verification failure
         the code logs a warning and CONTINUES (fail-open). This is the same as
         PR-S-002 viewed through the crypto lens: GPG verification is present in code
         but not enforced. The result is a logged-and-continued pattern for a
         cryptographic gate.
IMPACT: GPG is security theatre — its presence implies integrity guarantee but the
         guarantee is never enforced. An attacker presenting an unsigned or
         wrongly-signed bail file passes through the gate with only a warning printed.
MITIGATION: Fail-closed on signature verification failure. The current behavior is
            appropriate only for a --dry-run or --inspect mode.
```

---

### PR-T-002

```
SEVERITY: P1
CATEGORY: crypto
FILE: src/commands/boot.rs
LINE: 83-89
FINDING: read_harness_identity() checks boot.md ownership via libc::getuid() on Unix.
         This check was added as the →619 P0 mitigation (FIND-001). However, when
         ostk is run under sudo (e.g., `sudo ostk boot`), getuid() returns 0
         (root), and the ownership check compares the file owner against root. If
         boot.md is owned by the unprivileged user (the normal case), the check FAILS
         and harness_identity is silently rejected (falls back to T2). This is
         actually the safe behavior — but it means legitimate T1 escalation is broken
         for any admin workflow involving sudo. More critically, if ostk is run
         as root and an attacker has modified boot.md while running as root, the check
         passes and T1 is granted based on a root-owned but attacker-controlled file.
IMPACT: Under sudo: legitimate T1 suppressed (acceptable). Under root with compromised
        boot.md: T1 granted on attacker-controlled identity data.
MITIGATION: Document that running ostk as root is unsupported and may break the
            identity trust model. Add a getuid() == 0 guard that explicitly downgrades
            to T3 when running as root.
```

---

### PR-T-003

```
SEVERITY: P2
CATEGORY: crypto
FILE: src/commands/bail.rs
LINE: 310-333
FINDING: read_prime_signer() and find_prime_recipient() parse .primefile with string
         matching but do not verify that the extracted fingerprint is a key that
         actually exists in the local GPG keyring. pack_os_bin() then passes this
         fingerprint as --recipient to `gpg --encrypt`. If the fingerprint is not in
         the keyring, GPG will fail at encryption time (not at fingerprint parse time),
         producing a confusing error. More importantly, find_prime_recipient() accepts
         an 8-char short key ID (line 511-513) as a valid recipient — 8-char key IDs
         are known to be brute-forceable (Evil32 attack), allowing an attacker to
         generate a key with the same short ID as the legitimate prime key and
         substitute their key as the encryption recipient.
IMPACT: Encrypted os.bin could be encrypted to an attacker's key if the recipient is
        specified by short key ID (8 chars). The legitimate holder's prime key would
        not be able to decrypt the bail; the attacker's key would.
MITIGATION: Remove short key ID support from find_prime_recipient(). Require full
            40-char fingerprints. Verify the fingerprint is present in the keyring
            before attempting encryption.
```

---

### PR-T-004

```
SEVERITY: P2
CATEGORY: crypto
FILE: src/commands/boot.rs
LINE: 147-216
FINDING: load_entityfile() checks for a .asc file alongside ENTITYFILE and runs
         gpg --verify. The function finds the .asc path as:
             p.set_extension(if is_legacy { "md.asc" } else { "asc" });
         For the primary location .ostk/ENTITYFILE, this becomes
         .ostk/ENTITYFILE.asc. If the .asc file does not exist, the code prints
         "GPG chain not signed" and continues — there is no check that this is
         expected. An attacker who deletes .ostk/ENTITYFILE.asc downgrades the
         status from "GPG chain INVALID" (which would require a bad sig) to
         "GPG chain not signed" (which reads as merely unconfigured). The distinction
         matters when auditing: "not signed" looks like a fresh install, "INVALID"
         looks like tampering.
IMPACT: Tampering with ENTITYFILE can be concealed by also deleting the .asc file,
        changing the boot output from a red flag to a neutral message.
MITIGATION: If ENTITYFILE exists at the primary location and no .asc exists, and if
            a previous boot recorded a "GPG chain intact" status, treat the missing
            .asc as a potential integrity violation. At minimum, log a warning
            distinguishing "never signed" from "signature was present but is now absent".
```

---

### PR-T-005

```
SEVERITY: P3
CATEGORY: crypto
FILE: src/commands/bail.rs
LINE: 374-398
FINDING: verify_gpg_signature() extracts signer information from gpg stderr using
         string matching ("issuer", "fingerprint", "Good signature"). This is fragile
         and locale-dependent: gpg --verify output format varies by version and locale.
         The signer string is used only for display, not for authorization, so this is
         a P3 display bug rather than a security issue. However, a future refactor that
         uses the signer string for allowlist checking would inherit this fragility.
IMPACT: Signer display may be incorrect or empty on non-English GPG installations.
        No current security impact.
MITIGATION: Use `gpg --with-colons --status-fd=1` for machine-readable output parsing
            instead of scraping stderr.
```

---

## Cross-Reference: Existing threat-model.md Findings

The following previously-filed findings (FIND-001 through FIND-010) remain open as of
this review. No new needles are filed for them — they are referenced for completeness.

| ID       | Severity | Status | Note |
|----------|----------|--------|------|
| FIND-001 | P0 | open | harness_identity unsigned — PR-T-002 is a related secondary issue |
| FIND-002 | P0 | open | OSTK_SECRET_CMD exec — PR-S-004 is the secondary path |
| FIND-003 | P1 | open | run_env() stdout plaintext |
| FIND-004 | P1 | open | env-passthrough key name injection — mitigated for '=' and NUL in run.rs:118-121, but only partially (see code) |
| FIND-005 | P1 | open | HUMANFILE absent fail-closed doc |
| FIND-006 | P1 | open | str_replace_all() missing flock — also PR-C-001, PR-R-003 |
| FIND-007 | P2 | open | alias reclaim race |
| FIND-008 | P2 | open | stale harness_identity reuse |
| FIND-009 | P2 | open | PROMPT file:// path traversal |
| FIND-010 | P3 | open | macOS -w exposes secret in argv |

Note on FIND-004: The current code at run.rs:117-121 does check for '=' and '\0' in
env-passthrough keys. This was added as a partial mitigation. The full mitigation
(allowlist [A-Z_][A-Z0-9_]*) is still recommended.

---

## Additional Observations (Not Filed as Findings)

**Trust tier not enforced in code**: TrustTier (T0/T1/T2/T3) is assigned to agent
entries in agents.jsonl but there is no enforcement code anywhere that gates operations
on trust tier. The tier is stored as metadata and visible in the process table, but
T2 agents can call str_replace_cas() identically to T0 agents. This is an architectural
known trade-off (the write path is invisible — all agents coordinate through the
filesystem equally). Filed as observation, not finding.

**Squasher thread safety**: The compress() function in squasher/mod.rs uses only
local state (ImplicitDedup is stack-allocated, no shared mutable state). It is safe
to call from multiple threads concurrently. No finding.

**GPG verification in load_entityfile**: When gpg is not installed, the code maps
Err(_) to "GPG chain unverified — gpg not available". This is a graceful degradation,
not a fail-open, because the function does not proceed as if verification passed. No
additional finding beyond PR-T-004.

**Public API surface in kernel/**: Several internal types (GenEntry, RecoverySummary,
ToolCallEntry, EditSummary, CommandSummary, Digest) are pub but are only used within
the crate. These could be pub(crate) to reduce the public surface. Low priority;
no finding filed.

---

## Summary

| ID        | Severity | Category         | File                          | Finding summary |
|-----------|----------|------------------|-------------------------------|-----------------|
| PR-S-001  | P1       | security-escape  | commands/bail.rs:547          | decrypt_os_bin tar path traversal |
| PR-S-002  | P1       | security-escape  | commands/bail.rs:183          | unpack proceeds on GPG failure (fail-open) |
| PR-S-003  | P2       | security-escape  | commands/bail.rs:412          | os.bin includes unlisted future dirs |
| PR-S-004  | P2       | security-escape  | commands/secret.rs:258        | OSTK_SECRET_CMD env fallback still active |
| PR-S-005  | P2       | security-escape  | commands/shutdown.rs:344      | agent name path traversal in compile_humanfile |
| PR-S-006  | P3       | security-escape  | commands/boot.rs:196          | load_entityfile bad-sig continues (advisory) |
| PR-R-001  | P2       | rust-idiom       | main.rs:991                   | CString::new().unwrap() on NUL in argv |
| PR-R-002  | P2       | rust-idiom       | main.rs:726                   | unsafe set_var in potentially threaded context |
| PR-R-003  | P2       | rust-idiom       | kernel/file.rs:407            | str_replace_all() missing flock (also C-001) |
| PR-R-004  | P2       | rust-idiom       | serve/dispatch.rs:130         | to_value().unwrap() on MCP hot path |
| PR-R-005  | P3       | rust-idiom       | squasher/mod.rs:29            | incorrect calendar in record_metrics timestamp |
| PR-R-006  | P3       | rust-idiom       | kernel/hotpr.rs, dispatch.rs  | large String clones in hot paths |
| PR-R-007  | P3       | rust-idiom       | commands/bail.rs:618          | file_name().unwrap_or_default() on edge path |
| PR-C-001  | P1       | concurrency      | kernel/file.rs:407            | str_replace_all() no flock (data loss under concurrency) |
| PR-C-002  | P1       | concurrency      | kernel/registry.rs            | registry read-modify-write without flock |
| PR-C-003  | P2       | concurrency      | kernel/identity.rs:192        | read_agents() called without lock |
| PR-C-004  | P2       | concurrency      | serve/dispatch.rs:81          | try_write() silently drops alias on contention |
| PR-C-005  | P2       | concurrency      | kernel/elision.rs:100         | double gen_table read with TOCTOU window |
| PR-C-006  | P3       | concurrency      | squasher/mod.rs:12            | metrics path recomputed per-call via cwd |
| PR-T-001  | P1       | crypto           | commands/bail.rs:185          | GPG verify logged-and-continued on failure |
| PR-T-002  | P1       | crypto           | commands/boot.rs:83           | getuid() check broken under sudo |
| PR-T-003  | P2       | crypto           | commands/bail.rs:310          | 8-char short key ID accepted (Evil32 risk) |
| PR-T-004  | P2       | crypto           | commands/boot.rs:147          | deleted .asc conceals ENTITYFILE tampering |
| PR-T-005  | P3       | crypto           | commands/bail.rs:374          | gpg stderr parsing is locale-dependent |

**Total new findings: 25**
**Breakdown: P0: 0, P1: 6, P2: 12, P3: 7**

Including existing threat-model.md findings:
**Grand total: 35 findings (P0: 2, P1: 10, P2: 15, P3: 8)**

---

## Recommended Prioritization

### Immediate (block on release)

1. **PR-S-002 + PR-T-001** — bail unpack fail-open on GPG: one-line fix, blocks the
   entire bail feature from being trustworthy.
2. **PR-C-002** — registry.jsonl no flock: concurrent boot causes data corruption.
3. **PR-S-001** — tar path traversal in decrypt_os_bin: arbitrary file write on macOS.

### Next sprint

4. **PR-C-004** — MCP server loses agent alias on lock contention: silently breaks
   heartbeat, digest, nudge, and session logging.
5. **PR-T-002** — sudo breaks harness identity trust model.
6. **PR-T-003** — short key ID in bail encryption (Evil32).

### Maintenance

All P3 findings can be deferred to a maintenance pass.
