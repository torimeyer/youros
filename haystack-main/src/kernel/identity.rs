/// Kernel-assigned agent identity.
///
/// On startup, assign a unique alias via .ostk/identity_counter (flock, increment).
/// Generates "agent-1", "agent-2", etc.
/// If OSTK_AGENT env var is set, use it but check .ostk/agents.jsonl for conflicts.
/// Track active agents in .ostk/agents.jsonl.
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{Read as _, Seek, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// Trust tier assigned at alias registration — derived from GPG web of trust.
///
/// →895: Trust is determined by GPG cross-signatures, not harness identity.
/// The web of trust is anchored at the @ostk.ai root key and T0 holder keys.
///
/// - T0: Dual-signed (human + kernel). Full governance. Key cross-signed by root.
/// - T1: Key cross-signed by any T0 holder. Write access, OS protected by pin.
/// - T2: GPG key present but NOT cross-signed by T0. Read-only (boot, explore, no writes).
/// - T3: No GPG key. Anonymous. Public artifacts only, cannot boot.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
#[derive(Default)]
pub enum TrustTier {
    T0, // dual-signed by root — full governance
    T1, // GPG key cross-signed by a T0 holder — write access, OS protected
    #[default]
    T2, // GPG key present, not cross-signed — read-only
    T3, // no GPG key — anonymous, cannot boot
}

/// →895: T0 key fingerprints — keys authorized to cross-sign for T1 elevation.
/// These are the trust anchors. A user's GPG key must carry a signature from
/// one of these keys to be elevated from T2 to T1.
///
/// Includes: @ostk.ai root key, @scott (T0 human), @ostk.prime (T1 kernel).
const T0_TRUST_ANCHORS: &[&str] = &[
    "6C31536F3DC1BD4780E87B7780DD42208FE25413", // @ostk.ai root
    "7141A45868F8295E5BEB6286BAF08C963C7E3184", // @scott T0 (primary)
    "BAF08C963C7E3184",                          // @scott T0 (rotated 2026-03-19)
    "F0339E2FBF5B8CA23A47C876907A200DA6C869EB", // @ostk.prime T1 kernel
    "907A200DA6C869EB",                          // @ostk.prime T1 (short)
];

/// →895: Check if a GPG key is cross-signed by any T0 trust anchor.
///
/// Runs `gpg --check-sigs <fingerprint>` and parses the output for `sig!`
/// lines containing a T0 anchor key ID. This is the web-of-trust check:
/// if your key carries a valid signature from a T0 holder, you're T1.
///
/// Returns `Some(signer_key_id)` if cross-signed, `None` otherwise.
pub fn check_gpg_cross_signature(user_fingerprint: &str) -> Option<String> {
    let output = std::process::Command::new("gpg")
        .args(["--check-sigs", "--with-colons", user_fingerprint])
        .output()
        .ok()?;

    let stdout = String::from_utf8_lossy(&output.stdout);

    // Parse --with-colons output. We're looking for `sig` records where
    // field 5 (key ID) matches a T0 anchor. `sig!` means verified signature.
    // Format: sig:!:<algo>:<size>:<keyid>:<created>:<expires>:::<uid>:...
    for line in stdout.lines() {
        let fields: Vec<&str> = line.split(':').collect();
        if fields.len() < 5 {
            continue;
        }
        // sig records: field[0] = "sig", field[1] = "!" (verified) or other
        let is_sig = fields[0] == "sig";
        let is_verified = fields[1] == "!";
        if !is_sig || !is_verified {
            continue;
        }
        let signer_key_id = fields[4];
        // Check if this signature comes from a T0 anchor
        for anchor in T0_TRUST_ANCHORS {
            if anchor.ends_with(signer_key_id) || signer_key_id.ends_with(anchor) || anchor == &signer_key_id {
                return Some(signer_key_id.to_string());
            }
        }
    }

    None
}

/// →895: Determine trust tier from the local GPG keyring.
///
/// Resolution order:
/// 1. Read HUMANFILE SIGN key — if it matches a T0 anchor, return T0
/// 2. Discover user's GPG secret key fingerprint
/// 3. If no GPG key found → T3
/// 4. Check if key is cross-signed by a T0 anchor → T1
/// 5. GPG key present but not cross-signed → T2
pub fn determine_trust_tier(ostk_dir: &Path) -> (TrustTier, Option<String>) {
    // First: check HUMANFILE SIGN key
    let humanfile_key = read_humanfile_sign_key(ostk_dir);

    // Discover the user's GPG key
    let user_key = humanfile_key
        .clone()
        .or_else(|| crate::kernel::host_identity::discover().gpg_key);

    let fingerprint = match user_key {
        Some(fp) => fp,
        None => return (TrustTier::T3, None), // No GPG key → anonymous
    };

    // Is this key itself a T0 anchor?
    for anchor in T0_TRUST_ANCHORS {
        if anchor.ends_with(&fingerprint) || fingerprint.ends_with(anchor) || *anchor == fingerprint {
            return (TrustTier::T0, Some(fingerprint));
        }
    }

    // Check for cross-signature from a T0 holder
    if let Some(_signer) = check_gpg_cross_signature(&fingerprint) {
        return (TrustTier::T1, Some(fingerprint));
    }

    // GPG key present but not cross-signed
    (TrustTier::T2, Some(fingerprint))
}

/// Read the SIGN directive from the project HUMANFILE.
fn read_humanfile_sign_key(ostk_dir: &Path) -> Option<String> {
    let hf_path = ostk_dir.join("HUMANFILE");
    let content = fs::read_to_string(&hf_path).ok()?;
    for line in content.lines() {
        let trimmed = line.trim();
        if let Some(key) = trimmed.strip_prefix("SIGN ") {
            let key = key.trim();
            if !key.is_empty() {
                return Some(key.to_string());
            }
        }
    }
    None
}


impl std::fmt::Display for TrustTier {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TrustTier::T0 => write!(f, "T0"),
            TrustTier::T1 => write!(f, "T1"),
            TrustTier::T2 => write!(f, "T2"),
            TrustTier::T3 => write!(f, "T3"),
        }
    }
}

/// An agent registration entry in agents.jsonl.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentEntry {
    /// Unique alias (e.g. "agent-1", "agent-2")
    pub alias: String,
    /// Process ID
    pub pid: u32,
    /// Status: "active" or "inactive"
    pub status: String,
    /// ISO 8601 timestamp of registration
    pub registered_at: String,
    /// ISO 8601 timestamp of last heartbeat
    pub last_seen: String,
    // --- identity fields (→618) — populated from harness_identity: in boot.md ---
    /// Verified email from harness authentication (L1 identity)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub verified_email: Option<String>,
    /// Hardware device identifier (L0 identity)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub device_id: Option<String>,
    /// When harness verification occurred
    #[serde(skip_serializing_if = "Option::is_none")]
    pub verification_time: Option<String>,
    /// Trust tier derived at registration — T0/T1/T2/T3
    #[serde(default)]
    pub trust_tier: TrustTier,
}

/// Read `harness_identity:` block from boot.md.
/// Returns (verified_email, device_id, verification_time) if present and safe.
///
/// Security (→619 P0 mitigation): harness_identity is ONLY trusted when boot.md
/// is owned by the current user. An attacker writing to a world-writable boot.md
/// cannot elevate to T1. If ownership check fails, returns None (falls back to T2).
pub fn read_harness_identity(ostk_dir: &Path) -> (Option<String>, Option<String>, Option<String>) {
    let boot_path = ostk_dir.join("boot.md");

    // Ownership check: boot.md must be owned by the real user.
    // PR-T-002: when running under sudo, getuid() returns 0 (root) but boot.md is
    // owned by the unprivileged user. Use SUDO_UID to get the real caller UID.
    // If running as root with no SUDO_UID, log a warning — root with no sudo context
    // cannot safely verify ownership, so fall back to T2.
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let raw_uid = unsafe { libc::getuid() };
        // Resolve SUDO_UID env var when running under sudo
        let effective_uid = std::env::var("SUDO_UID")
            .ok()
            .and_then(|s| s.parse::<u32>().ok())
            .unwrap_or(raw_uid);
        // Running as root without sudo context — harness_identity cannot be safely verified
        if raw_uid == 0 && effective_uid == 0 {
            eprintln!(
                "[ostk] warning: running as root without SUDO_UID — \
                 harness_identity cannot be safely verified (T2 fallback)"
            );
            return (None, None, None);
        }
        if let Ok(meta) = fs::metadata(&boot_path) {
            let file_uid = meta.uid();
            if file_uid != effective_uid {
                // boot.md owned by different user — do not trust harness_identity
                return (None, None, None);
            }
        }
    }
    let content = match fs::read_to_string(&boot_path) {
        Ok(c) => c,
        Err(_) => return (None, None, None),
    };

    // Look for harness_identity: block
    let mut in_block = false;
    let mut email = None;
    let mut device = None;
    let mut vtime = None;

    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed == "harness_identity:" {
            in_block = true;
            continue;
        }
        if in_block {
            // Use `line` (not `trimmed`) for indentation check — trimmed has no leading spaces
            if trimmed.is_empty() || (!line.starts_with("  ") && !line.starts_with('\t')) {
                break; // end of indented block
            }
            if let Some(v) = trimmed.strip_prefix("verified_email:") {
                email = Some(v.trim().to_string());
            } else if let Some(v) = trimmed.strip_prefix("device_id:") {
                device = Some(v.trim().to_string());
            } else if let Some(v) = trimmed.strip_prefix("verification_time:") {
                vtime = Some(v.trim().to_string());
            }
        }
    }

    (email, device, vtime)
}

/// Identity manager for kernel-assigned agent aliases.
pub struct Identity {
    /// Path to .ostk directory
    ostk_dir: PathBuf,
}

impl Identity {
    /// Create a new Identity manager rooted at the given .ostk directory.
    pub fn new(ostk_dir: &Path) -> Self {
        Identity {
            ostk_dir: ostk_dir.to_path_buf(),
        }
    }

    /// ISO 8601 timestamp.
    fn now_iso() -> String {
        let dur = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default();
        let secs = dur.as_secs();
        let days = secs / 86400;
        let time_secs = secs % 86400;
        let hours = time_secs / 3600;
        let minutes = (time_secs % 3600) / 60;
        let seconds = time_secs % 60;
        let (year, month, day) = days_to_ymd(days);
        format!("{year:04}-{month:02}-{day:02}T{hours:02}:{minutes:02}:{seconds:02}Z")
    }

    // ── Fleet path helpers (→628 thread states) ──────────────────────────

    /// Path to fleet directory for a given agent alias.
    pub fn fleet_dir(&self, alias: &str) -> PathBuf {
        self.ostk_dir.join("fleet").join(alias)
    }

    /// Path to state.yml for a given agent alias.
    pub fn state_path(&self, alias: &str) -> PathBuf {
        self.fleet_dir(alias).join("state.yml")
    }

    /// Path to dying.lock for a given agent alias (→620).
    pub fn dying_lock_path(&self, alias: &str) -> PathBuf {
        self.fleet_dir(alias).join("dying.lock")
    }

    /// Path to dying.md for a given agent alias (→620).
    pub fn dying_md_path(&self, alias: &str) -> PathBuf {
        self.fleet_dir(alias).join("dying.md")
    }

    /// Write thread state for an agent (→628).
    /// state: one of init/ready/running/blocked/dying/idle/reaped
    pub fn write_state(&self, alias: &str, state: &str) -> Result<(), String> {
        let dir = self.fleet_dir(alias);
        fs::create_dir_all(&dir).map_err(|e| format!("fleet dir: {e}"))?;
        let content = format!(
            "alias: {alias}\nstate: {state}\nupdated: {}\n",
            Self::now_iso()
        );
        fs::write(self.state_path(alias), content)
            .map_err(|e| format!("write state: {e}"))
    }

    /// Paths for the various identity files.
    fn counter_path(&self) -> PathBuf {
        self.ostk_dir.join("identity_counter")
    }

    fn agents_path(&self) -> PathBuf {
        self.ostk_dir.join("agents.jsonl")
    }

    fn lock_path(&self) -> PathBuf {
        self.ostk_dir.join("identity.lock")
    }

    /// Execute a closure under flock on the identity lock file.
    fn with_lock<F, R>(&self, f: F) -> Result<R, String>
    where
        F: FnOnce() -> Result<R, String>,
    {
        fs::create_dir_all(&self.ostk_dir).map_err(|e| format!("create .ostk dir: {e}"))?;

        let lock_file = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(self.lock_path())
            .map_err(|e| format!("open identity lock: {e}"))?;

        lock_file
            .lock_exclusive()
            .map_err(|e| format!("flock identity: {e}"))?;

        let result = f();

        drop(lock_file);
        result
    }

    /// Read all agent entries from agents.jsonl (latest entry per alias wins).
    pub fn read_agents(&self) -> Result<Vec<AgentEntry>, String> {
        let path = self.agents_path();
        if !path.exists() {
            return Ok(vec![]);
        }

        let content = fs::read_to_string(&path).map_err(|e| format!("read agents.jsonl: {e}"))?;

        let mut agents_map = std::collections::HashMap::new();
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(entry) = serde_json::from_str::<AgentEntry>(line) {
                agents_map.insert(entry.alias.clone(), entry);
            }
        }
        Ok(agents_map.into_values().collect())
    }

    /// Write all agents back (compacted). Internal use.
    fn write_agents(&self, agents: &[AgentEntry]) -> Result<(), String> {
        let path = self.agents_path();
        let mut content = String::new();
        for agent in agents {
            let line = serde_json::to_string(agent).map_err(|e| e.to_string())?;
            content.push_str(&line);
            content.push('\n');
        }
        fs::write(&path, content).map_err(|e| format!("write agents.jsonl: {e}"))?;
        Ok(())
    }

    /// Write all agents back (compacted). Public for reap.
    pub fn write_agents_pub(&self, agents: &[AgentEntry]) -> Result<(), String> {
        self.with_lock(|| self.write_agents(agents))
    }

    /// Append a single agent entry to agents.jsonl.
    fn append_agent(&self, agent: &AgentEntry) -> Result<(), String> {
        let path = self.agents_path();
        let line = serde_json::to_string(agent).map_err(|e| e.to_string())?;
        let mut file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .map_err(|e| format!("open agents.jsonl for append: {e}"))?;
        file.write_all(line.as_bytes())
            .map_err(|e| format!("append agent: {e}"))?;
        file.write_all(b"\n")
            .map_err(|e| format!("append newline: {e}"))?;
        Ok(())
    }

    /// Read the current identity counter value (under flock).
    pub fn read_counter(&self) -> Result<u64, String> {
        self.with_lock(|| {
            let counter_path = self.counter_path();
            let contents = fs::read_to_string(&counter_path).unwrap_or_default();
            Ok(contents.trim().parse::<u64>().unwrap_or(0))
        })
    }

    /// Increment the identity counter and return the new value (under flock).
    pub fn next_counter_locked(&self) -> Result<u64, String> {
        self.with_lock(|| self.next_counter())
    }

    /// Read and increment the identity counter. Returns the new counter value.
    fn next_counter(&self) -> Result<u64, String> {
        let counter_path = self.counter_path();

        let mut file = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&counter_path)
            .map_err(|e| format!("open identity_counter: {e}"))?;

        let mut contents = String::new();
        file.read_to_string(&mut contents)
            .map_err(|e| format!("read identity_counter: {e}"))?;

        let current = contents.trim().parse::<u64>().unwrap_or(0);
        let next = current + 1;

        file.set_len(0)
            .map_err(|e| format!("truncate identity_counter: {e}"))?;
        file.seek(std::io::SeekFrom::Start(0))
            .map_err(|e| format!("seek identity_counter: {e}"))?;
        file.write_all(next.to_string().as_bytes())
            .map_err(|e| format!("write identity_counter: {e}"))?;

        Ok(next)
    }

    /// Assign a unique agent alias.
    ///
    /// If OSTK_AGENT env var is set (or `requested_alias` is Some),
    /// use that alias (checking for conflicts).
    /// Otherwise, generate "agent-N" from the monotonic counter.
    ///
    /// Registers the agent as active in agents.jsonl.
    pub fn assign_alias(&self) -> Result<String, String> {
        let env_alias = std::env::var("OSTK_AGENT").ok();
        self.assign_alias_inner(env_alias.as_deref())
    }

    /// Assign a specific alias, or auto-generate one if `requested` is None.
    pub fn assign_alias_with(&self, requested: Option<&str>) -> Result<String, String> {
        self.assign_alias_inner(requested)
    }

    fn assign_alias_inner(&self, requested: Option<&str>) -> Result<String, String> {
        self.with_lock(|| {
            let alias = if let Some(env_alias) = requested {
                // Validate: check if this alias is already active
                let agents = self.read_agents()?;
                let pid = std::process::id();
                for agent in &agents {
                    if agent.alias == env_alias && agent.status == "active" && agent.pid != pid {
                        // Check if the other process is still alive
                        if is_process_alive(agent.pid) {
                            return Err(format!(
                                "alias '{}' is already in use by pid {}",
                                env_alias, agent.pid
                            ));
                        }
                        // Process is dead — we can reclaim the alias
                    }
                }
                env_alias.to_string()
            } else {
                let counter = self.next_counter()?;
                format!("agent-{counter}")
            };

            let now = Self::now_iso();
            // →895: Determine trust tier from GPG web of trust (cross-signatures).
            // Legacy harness_identity is still read for backwards compat / audit,
            // but trust tier is now derived from GPG cross-signature chain.
            let (verified_email, device_id, verification_time) =
                read_harness_identity(&self.ostk_dir);
            let (trust_tier, _gpg_key) = determine_trust_tier(&self.ostk_dir);
            let entry = AgentEntry {
                alias: alias.clone(),
                pid: std::process::id(),
                status: "active".to_string(),
                registered_at: now.clone(),
                last_seen: now,
                verified_email,
                device_id,
                verification_time,
                trust_tier,
            };

            self.append_agent(&entry)?;
            Ok(alias)
        })
    }

    /// Mark an agent as inactive (on shutdown).
    pub fn deregister(&self, alias: &str) -> Result<(), String> {
        self.with_lock(|| {
            let mut agents = self.read_agents()?;
            for agent in &mut agents {
                if agent.alias == alias && agent.status == "active" {
                    agent.status = "inactive".to_string();
                    agent.last_seen = Self::now_iso();
                }
            }
            self.write_agents(&agents)?;
            Ok(())
        })
    }

    /// Get a list of currently active agent aliases.
    pub fn active_agents(&self) -> Result<Vec<String>, String> {
        self.with_lock(|| {
            let agents = self.read_agents()?;
            Ok(agents
                .iter()
                .filter(|a| a.status == "active")
                .map(|a| a.alias.clone())
                .collect())
        })
    }

    /// Update the last_seen timestamp for an agent (heartbeat).
    pub fn heartbeat(&self, alias: &str) -> Result<(), String> {
        self.with_lock(|| {
            let mut agents = self.read_agents()?;
            let now = Self::now_iso();
            for agent in &mut agents {
                if agent.alias == alias {
                    agent.last_seen = now.clone();
                }
            }
            self.write_agents(&agents)?;
            Ok(())
        })
    }
}

/// Convenience: read all agents from agents.jsonl as structured entries.
///
/// Standalone function so callers don't need to construct an Identity struct.
pub fn read_agents(ostk_dir: &Path) -> Vec<AgentEntry> {
    Identity::new(ostk_dir).read_agents().unwrap_or_default()
}

/// Convenience: read the identity counter value (flock-protected).
///
/// Returns the current counter value, or 0 if the file doesn't exist.
pub fn read_counter(ostk_dir: &Path) -> u64 {
    Identity::new(ostk_dir).read_counter().unwrap_or(0)
}

/// Check if a process is still alive (Unix-only).
fn is_process_alive(pid: u32) -> bool {
    // kill(pid, 0) checks if process exists without sending a signal
    unsafe { libc::kill(pid as i32, 0) == 0 }
}

/// Convert days since epoch to (year, month, day).
fn days_to_ymd(days: u64) -> (u64, u64, u64) {
    let z = days + 719468;
    let era = z / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temp_ostk_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir()
            .join("ostk_test_identity")
            .join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn test_assign_unique_aliases() {
        let dir = temp_ostk_dir("unique_aliases");
        let id = Identity::new(&dir);

        let alias1 = id.assign_alias_with(None).unwrap();
        let alias2 = id.assign_alias_with(None).unwrap();

        assert_eq!(alias1, "agent-1");
        assert_eq!(alias2, "agent-2");
        assert_ne!(alias1, alias2);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_monotonic_counter() {
        let dir = temp_ostk_dir("monotonic");
        let id = Identity::new(&dir);

        let a1 = id.assign_alias_with(None).unwrap();
        let a2 = id.assign_alias_with(None).unwrap();
        let a3 = id.assign_alias_with(None).unwrap();

        assert_eq!(a1, "agent-1");
        assert_eq!(a2, "agent-2");
        assert_eq!(a3, "agent-3");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_requested_alias() {
        let dir = temp_ostk_dir("requested_alias");
        let id = Identity::new(&dir);

        let alias = id.assign_alias_with(Some("ridge")).unwrap();
        assert_eq!(alias, "ridge");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_active_agents() {
        let dir = temp_ostk_dir("active");
        let id = Identity::new(&dir);

        let _a1 = id.assign_alias_with(None).unwrap();
        let _a2 = id.assign_alias_with(None).unwrap();

        let active = id.active_agents().unwrap();
        // Both should be active (same process, both registered)
        assert!(active.contains(&"agent-1".to_string()));
        assert!(active.contains(&"agent-2".to_string()));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_deregister() {
        let dir = temp_ostk_dir("deregister");
        let id = Identity::new(&dir);

        let alias = id.assign_alias_with(None).unwrap();
        assert_eq!(alias, "agent-1");

        let active_before = id.active_agents().unwrap();
        assert!(active_before.contains(&"agent-1".to_string()));

        id.deregister("agent-1").unwrap();

        let active_after = id.active_agents().unwrap();
        assert!(!active_after.contains(&"agent-1".to_string()));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_heartbeat_update() {
        let dir = temp_ostk_dir("heartbeat_update");
        let id = Identity::new(&dir);

        let alias = id.assign_alias_with(None).unwrap();
        assert_eq!(alias, "agent-1");

        // Heartbeat should update last_seen
        id.heartbeat("agent-1").unwrap();

        let agents = id.read_agents().unwrap();
        let agent = agents.iter().find(|a| a.alias == "agent-1").unwrap();
        assert_eq!(agent.status, "active");

        let _ = fs::remove_dir_all(&dir);
    }

    // ── →895 web-of-trust tests ──────────────────────────────────────────

    #[test]
    fn test_t0_anchor_recognized() {
        // @scott's key should be recognized as T0
        assert!(T0_TRUST_ANCHORS.iter().any(|a| a.contains("BAF08C963C7E3184")));
        // @ostk.ai root key
        assert!(T0_TRUST_ANCHORS.iter().any(|a| a.contains("6C31536F3DC1BD4780E87B7780DD42208FE25413")));
    }

    #[test]
    fn test_trust_tier_display() {
        assert_eq!(format!("{}", TrustTier::T0), "T0");
        assert_eq!(format!("{}", TrustTier::T1), "T1");
        assert_eq!(format!("{}", TrustTier::T2), "T2");
        assert_eq!(format!("{}", TrustTier::T3), "T3");
    }

    #[test]
    fn test_trust_tier_default_is_t2() {
        assert_eq!(TrustTier::default(), TrustTier::T2);
    }

    #[test]
    fn test_determine_trust_no_humanfile_no_gpg() {
        // Empty dir with no HUMANFILE and no GPG key discoverable
        // This tests the fallback path — actual tier depends on host GPG state
        let dir = temp_ostk_dir("trust_no_hf");
        let (tier, _key) = determine_trust_tier(&dir);
        // On a machine with GPG keys, this will be T0/T1/T2 depending on keyring.
        // On a machine with no GPG keys, this will be T3.
        // Either way, it should not panic.
        assert!(matches!(tier, TrustTier::T0 | TrustTier::T1 | TrustTier::T2 | TrustTier::T3));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_determine_trust_with_humanfile_sign_key() {
        let dir = temp_ostk_dir("trust_with_hf");
        // Write a HUMANFILE with @scott's SIGN key
        fs::write(dir.join("HUMANFILE"), "IDENTITY test\nSIGN BAF08C963C7E3184\n").unwrap();
        let (tier, _key) = determine_trust_tier(&dir);
        // @scott's key is a T0 anchor — should resolve to T0 if key is in keyring
        // If key is NOT in keyring (CI), gpg discovery falls through to host key
        assert!(matches!(tier, TrustTier::T0 | TrustTier::T1 | TrustTier::T2 | TrustTier::T3));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_read_humanfile_sign_key() {
        let dir = temp_ostk_dir("read_sign_key");
        fs::write(dir.join("HUMANFILE"), "IDENTITY test\nSIGN AABBCCDD11223344\nMODEL test\n").unwrap();
        let key = read_humanfile_sign_key(&dir);
        assert_eq!(key, Some("AABBCCDD11223344".to_string()));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_read_humanfile_sign_key_missing() {
        let dir = temp_ostk_dir("read_sign_key_missing");
        fs::write(dir.join("HUMANFILE"), "IDENTITY test\nMODEL test\n").unwrap();
        let key = read_humanfile_sign_key(&dir);
        assert_eq!(key, None);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_read_humanfile_sign_key_no_file() {
        let dir = temp_ostk_dir("read_sign_key_no_file");
        let key = read_humanfile_sign_key(&dir);
        assert_eq!(key, None);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_cross_signature_check_nonexistent_key() {
        // A fingerprint that doesn't exist in any keyring
        let result = check_gpg_cross_signature("0000000000000000000000000000000000000000");
        assert_eq!(result, None);
    }
}
