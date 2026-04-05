//! `ostk sign` — sign or re-sign the HUMANFILE with your GPG key.
//!
//! Detached-signs `.ostk/HUMANFILE` producing `.ostk/HUMANFILE.asc`.
//! This is the trust anchor for secret injection: agents refuse to inject
//! secrets unless the HUMANFILE carries a valid GPG signature.

use std::path::Path;
use std::process::Command;

use crate::find_project_root;

/// Result of GPG identity discovery.
pub struct GpgIdentity {
    pub key_id: String,
    pub uid: String, // "Name <email>"
}

/// HUMANFILE trust level — determines whether secrets may be injected.
#[derive(Debug, Clone, PartialEq)]
pub enum HumanfileTrust {
    /// Valid GPG signature on HUMANFILE — full trust, secrets allowed.
    Verified(String), // signer key ID
    /// No .asc file — limited trust, secrets disabled.
    Unsigned,
    /// .asc exists but verification failed — block secret injection.
    BadSignature,
    /// gpg binary not available — cannot verify, treat as unsigned.
    GpgAbsent,
}

/// Run `ostk sign` — sign the HUMANFILE and any Agentfiles in the current project.
///
/// HUMANFILE is signed with the user's personal GPG key.
/// Agentfiles (*.af in .ostk/) are signed with the kernel key from
/// `.ostk/kernel.key` (→773).
pub fn run() -> Result<(), String> {
    let root = find_project_root()?;
    let ostk_dir = crate::state_dir(&root);
    let hf_path = ostk_dir.join("HUMANFILE");

    if !hf_path.exists() {
        return Err(crate::strings::sign::HUMANFILE_NOT_FOUND
            .replacen("{}", &hf_path.display().to_string(), 1));
    }

    // Read declared key from HUMANFILE (key: field) — use that instead of
    // the first GPG secret key, which may be an old/wrong key.
    let declared_key = std::fs::read_to_string(&hf_path).ok()
        .and_then(|c| c.lines()
            .find(|l| l.starts_with("key:"))
            .map(|l| l.strip_prefix("key:").unwrap_or("").trim().to_string()));
    let identity = if let Some(ref dk) = declared_key {
        // Verify the declared key exists in the keyring
        let check = Command::new("gpg")
            .args(["--list-secret-keys", "--keyid-format", "LONG", dk])
            .output();
        if check.map(|o| o.status.success()).unwrap_or(false) {
            GpgIdentity { key_id: dk.clone(), uid: "(from HUMANFILE)".into() }
        } else {
            eprintln!("  warning: HUMANFILE declares key {} but it's not in your keyring", dk);
            verify_gpg_identity()?
        }
    } else {
        verify_gpg_identity()?
    };
    eprintln!(
        "  Signing HUMANFILE with key {} ({})",
        identity.key_id, identity.uid
    );

    // Sign HUMANFILE with user's personal key
    sign_humanfile(&ostk_dir, &identity.key_id)?;

    // →773: Sign Agentfiles with kernel key
    // Sign in .ostk/ (legacy) and ./agents/ (new location), plus root Agentfile
    sign_agentfiles_in_dir(&ostk_dir);
    let agents_dir = root.join("agents");
    if agents_dir.is_dir() {
        sign_agentfiles_in_dir(&agents_dir);
    }
    // Sign root Agentfile if present
    let root_agentfile = root.join("Agentfile");
    if root_agentfile.exists() {
        eprintln!("  Signing Agentfile with kernel key");
        if let Err(e) = sign_agentfile(&root_agentfile, &ostk_dir) {
            eprintln!("  warning: could not sign Agentfile: {}", e);
        }
    }

    // Audit
    let now = crate::now_iso();
    let _ = crate::append_audit(
        &root,
        &serde_json::json!({
            "event": "humanfile.signed",
            "key_id": identity.key_id,
            "uid": identity.uid,
            "timestamp": now,
        }),
    );

    println!("{}", crate::strings::sign::SIGN_COMPLETE);
    Ok(())
}

/// →773: Sign all Agentfiles (*.af) in .ostk/ with the kernel key.
/// Non-fatal — warns on failure, never blocks the overall sign operation.
fn sign_agentfiles_in_dir(ostk_dir: &Path) {
    let entries = match std::fs::read_dir(ostk_dir) {
        Ok(e) => e,
        Err(_) => return,
    };

    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) == Some("af") {
            let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("?");
            eprintln!("  Signing {} with kernel key", name);
            if let Err(e) = sign_agentfile(&path, ostk_dir) {
                eprintln!("  warning: could not sign {}: {}", name, e);
            }
        }
    }
}

/// Verify GPG is available and discover the user's key identity.
pub fn verify_gpg_identity() -> Result<GpgIdentity, String> {
    // 1. Check if gpg is available
    Command::new("gpg")
        .arg("--version")
        .output()
        .map_err(|_| crate::strings::sign::GPG_NOT_FOUND.to_string())?;

    // 2. List user's secret keys (colon-delimited for reliable parsing)
    let keys = Command::new("gpg")
        .args(["--list-secret-keys", "--keyid-format", "LONG", "--with-colons"])
        .output()
        .map_err(|e| format!("gpg --list-secret-keys: {e}"))?;

    if !keys.status.success() || keys.stdout.is_empty() {
        return Err(crate::strings::sign::NO_GPG_KEY.to_string());
    }

    let output = String::from_utf8_lossy(&keys.stdout);

    // 3. Extract key ID from first `sec:` line, uid from first `uid:` line
    let key_id = parse_gpg_key_id(&output)?;
    let uid = parse_gpg_uid(&output)?;

    // 4. Verify key is on GitHub (optional — warn if gh not available)
    check_github_key(&key_id, &uid);

    Ok(GpgIdentity { key_id, uid })
}

/// Parse the key ID from gpg colon-delimited output.
/// Format: sec:...:KEYID:...
fn parse_gpg_key_id(output: &str) -> Result<String, String> {
    for line in output.lines() {
        if line.starts_with("sec:")
            && let Some(fp) = line.split(':').nth(4)
                && !fp.is_empty() {
                    return Ok(fp.to_string());
                }
    }
    Err(crate::strings::sign::NO_GPG_KEY.to_string())
}

/// Parse the uid from gpg colon-delimited output.
/// Format: uid:...:...:...:...:...:...:...:...:Name <email>:...
fn parse_gpg_uid(output: &str) -> Result<String, String> {
    for line in output.lines() {
        if line.starts_with("uid:")
            && let Some(uid_field) = line.split(':').nth(9)
                && !uid_field.is_empty() {
                    return Ok(uid_field.to_string());
                }
    }
    // Fallback: return "unknown" rather than failing
    Ok("unknown".to_string())
}

/// Best-effort check whether the GPG key is published to GitHub.
fn check_github_key(key_id: &str, uid: &str) {
    if let Ok(gh_output) = Command::new("gh")
        .args(["api", "/user/gpg_keys"])
        .output()
        && gh_output.status.success() {
            let gh_keys = String::from_utf8_lossy(&gh_output.stdout);
            if !gh_keys.contains(key_id) {
                eprintln!(
                    "{}",
                    crate::strings::sign::GPG_KEY_NOT_ON_GITHUB.replacen("{}", key_id, 1)
                );
                eprintln!(
                    "{}",
                    crate::strings::sign::GPG_KEY_ADD_HINT.replacen("{}", key_id, 1)
                );
                eprintln!("{}", crate::strings::sign::GPG_KEY_CONTINUING);
            } else {
                eprintln!(
                    "{}",
                    crate::strings::sign::GPG_KEY_VERIFIED.replacen("{}", uid, 1)
                );
            }
        }
}

/// Sign the HUMANFILE with a detached armored GPG signature.
pub fn sign_humanfile(ostk_dir: &Path, key_id: &str) -> Result<(), String> {
    let hf_path = ostk_dir.join("HUMANFILE");
    let asc_path = ostk_dir.join("HUMANFILE.asc");

    // Remove old signature if present (gpg refuses to overwrite)
    if asc_path.exists() {
        std::fs::remove_file(&asc_path)
            .map_err(|e| format!("failed to remove old HUMANFILE.asc: {e}"))?;
    }

    let status = Command::new("gpg")
        .args(["--detach-sign", "--armor", "--default-key", key_id])
        .arg(&hf_path)
        .status()
        .map_err(|e| format!("gpg sign: {e}"))?;

    if !status.success() {
        return Err(crate::strings::sign::HUMANFILE_SIGN_FAIL.to_string());
    }

    eprintln!(
        "{}",
        crate::strings::sign::HUMANFILE_SIGNED.replacen("{}", key_id, 1)
    );
    Ok(())
}

/// Verify the HUMANFILE GPG detached signature.
///
/// Returns the trust level. Always non-fatal — callers decide policy.
pub fn verify_humanfile_signature(ostk_dir: &Path) -> HumanfileTrust {
    let hf = ostk_dir.join("HUMANFILE");
    let sig = ostk_dir.join("HUMANFILE.asc");
    verify_humanfile_signature_at(&hf, &sig)
}

/// Verify a GPG detached signature for an arbitrary HUMANFILE path.
///
/// Used by the inheritable-humanfile hierarchy (→826) to verify both
/// ~/.HUMANFILE and project .ostk/HUMANFILE independently.
/// Returns the trust level. Always non-fatal — callers decide policy.
pub fn verify_humanfile_signature_at(hf_path: &Path, sig_path: &Path) -> HumanfileTrust {
    if !hf_path.exists() {
        return HumanfileTrust::Unsigned;
    }

    if !sig_path.exists() {
        return HumanfileTrust::Unsigned;
    }

    let result = Command::new("gpg")
        .args(["--verify"])
        .arg(sig_path)
        .arg(hf_path)
        .output();

    match result {
        Ok(out) if out.status.success() => {
            // Extract signer key ID from stderr (gpg prints verification info there)
            let stderr = String::from_utf8_lossy(&out.stderr);
            let key_id = extract_signer_key(&stderr);
            HumanfileTrust::Verified(key_id)
        }
        Ok(_) => HumanfileTrust::BadSignature,
        Err(_) => HumanfileTrust::GpgAbsent,
    }
}

/// →773: Resolve the kernel signing key from the standard resolution chain.
///
/// Priority: OSTK_KERNEL_KEY env var > .ostk/kernel.key file > hardcoded default.
/// The kernel key is used to sign boot.md, Agentfiles, and other governance artifacts.
/// Default: 907A200DA6C869EB (@ostk.prime T1, rotated 2026-03-19).
pub fn resolve_kernel_key(ostk_dir: &Path) -> String {
    std::env::var("OSTK_KERNEL_KEY")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| {
            let key_file = ostk_dir.join("kernel.key");
            std::fs::read_to_string(&key_file)
                .ok()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
        })
        .unwrap_or_else(|| "907A200DA6C869EB".to_string())
}

// ── Agentfile signing (→773) ──────────────────────────────────────────────────

/// Agentfile trust level — mirrors HumanfileTrust for Agentfile governance.
#[derive(Debug, Clone, PartialEq)]
pub enum AgentfileTrust {
    /// Valid GPG signature from kernel key — full trust.
    Verified(String), // signer key ID
    /// No .asc file — unsigned, warn but allow.
    Unsigned,
    /// .asc exists but verification failed — tampered Agentfile.
    BadSignature(String),
    /// gpg binary not available — cannot verify.
    GpgAbsent,
}

/// Sign an Agentfile with the kernel key (resolved from .ostk/kernel.key).
///
/// Produces a detached armored signature at `<agentfile_path>.asc`.
/// Uses `resolve_kernel_key` to determine which GPG key to sign with.
pub fn sign_agentfile(agentfile_path: &Path, ostk_dir: &Path) -> Result<(), String> {
    if !agentfile_path.exists() {
        return Err(format!("Agentfile not found at {}", agentfile_path.display()));
    }

    let kernel_key = resolve_kernel_key(ostk_dir);

    let asc_path = {
        let mut p = agentfile_path.as_os_str().to_owned();
        p.push(".asc");
        std::path::PathBuf::from(p)
    };

    // Remove old signature if present (gpg refuses to overwrite)
    if asc_path.exists() {
        std::fs::remove_file(&asc_path)
            .map_err(|e| format!("failed to remove old {}: {e}", asc_path.display()))?;
    }

    let status = Command::new("gpg")
        .args([
            "--batch",
            "--yes",
            "--detach-sign",
            "--armor",
            "--local-user",
            &kernel_key,
            "--output",
        ])
        .arg(&asc_path)
        .arg(agentfile_path)
        .status()
        .map_err(|e| format!("gpg sign: {e}"))?;

    if !status.success() {
        return Err(crate::strings::sign::AGENTFILE_SIGN_FAIL.to_string());
    }

    eprintln!(
        "{}",
        crate::strings::sign::AGENTFILE_SIGNED.replacen("{}", &kernel_key, 1)
    );
    Ok(())
}

/// Verify the GPG detached signature on an Agentfile.
///
/// Looks for `<agentfile_path>.asc` alongside the Agentfile.
/// Always non-fatal — returns a trust level, never errors.
pub fn verify_agentfile_signature(agentfile_path: &Path) -> AgentfileTrust {
    if !agentfile_path.exists() {
        return AgentfileTrust::Unsigned;
    }

    let asc_path = {
        let mut p = agentfile_path.as_os_str().to_owned();
        p.push(".asc");
        std::path::PathBuf::from(p)
    };

    if !asc_path.exists() {
        return AgentfileTrust::Unsigned;
    }

    let result = Command::new("gpg")
        .args(["--verify"])
        .arg(&asc_path)
        .arg(agentfile_path)
        .output();

    match result {
        Ok(out) if out.status.success() => {
            let stderr = String::from_utf8_lossy(&out.stderr);
            let key_id = extract_signer_key(&stderr);
            AgentfileTrust::Verified(key_id)
        }
        Ok(out) => {
            let msg = String::from_utf8_lossy(&out.stderr).trim().to_string();
            AgentfileTrust::BadSignature(msg)
        }
        Err(_) => AgentfileTrust::GpgAbsent,
    }
}

/// Verify an Agentfile signature and log the result to stderr.
///
/// Respects HUMANFILE TRUST: if `trust_unsigned` is true, the Unsigned
/// variant is silenced (no warning). All other variants (Verified,
/// BadSignature, GpgAbsent) still print.
///
/// This is the single callsite for signature checks — replaces the
/// inline match blocks in run.rs, firecracker.rs, and fcp_screen/app.rs.
pub fn verify_and_log_agentfile(agentfile_path: &std::path::Path, trust_unsigned: bool) -> AgentfileTrust {
    let trust = verify_agentfile_signature(agentfile_path);
    match &trust {
        AgentfileTrust::Verified(key_id) => {
            eprintln!("{}", crate::strings::sign::AGENTFILE_SIG_VERIFIED
                .replacen("{}", key_id, 1));
        }
        AgentfileTrust::Unsigned => {
            if !trust_unsigned {
                eprintln!("{}", crate::strings::sign::AGENTFILE_SIG_UNSIGNED);
            }
        }
        AgentfileTrust::BadSignature(msg) => {
            eprintln!("{}", crate::strings::sign::AGENTFILE_SIG_INVALID
                .replacen("{}", msg, 1));
        }
        AgentfileTrust::GpgAbsent => {
            eprintln!("{}", crate::strings::sign::AGENTFILE_SIG_GPG_ABSENT);
        }
    }
    trust
}

/// Extract the signer's key ID from gpg --verify stderr output.
pub fn extract_signer_key(stderr: &str) -> String {
    // gpg outputs lines like:
    //   gpg: Signature made ... using RSA key ABCDEF1234567890
    //   gpg: Good signature from "Name <email>"
    for line in stderr.lines() {
        if line.contains("using") && line.contains("key")
            && let Some(key_part) = line.rsplit("key ").next() {
                let key = key_part.trim().trim_end_matches('"');
                if !key.is_empty() {
                    return key.to_string();
                }
            }
    }
    "unknown".to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU32, Ordering};

    static TEST_COUNTER: AtomicU32 = AtomicU32::new(0);
    fn test_dir(prefix: &str) -> std::path::PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("ostk_test_{prefix}_{pid}_{n}"))
    }

    #[test]
    fn test_verify_humanfile_no_asc_is_unsigned() {
        let tmp = test_dir("hf_unsigned");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();
        fs::write(ostk_dir.join("HUMANFILE"), "# test\n").unwrap();
        // No HUMANFILE.asc

        let trust = verify_humanfile_signature(&ostk_dir);
        assert_eq!(trust, HumanfileTrust::Unsigned);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_verify_humanfile_no_humanfile_is_unsigned() {
        let tmp = test_dir("hf_missing");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();
        // No HUMANFILE at all

        let trust = verify_humanfile_signature(&ostk_dir);
        assert_eq!(trust, HumanfileTrust::Unsigned);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_verify_humanfile_bad_asc_is_bad_signature_or_gpg_absent() {
        let tmp = test_dir("hf_bad_asc");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();
        fs::write(ostk_dir.join("HUMANFILE"), "# test\n").unwrap();
        fs::write(
            ostk_dir.join("HUMANFILE.asc"),
            "not a real gpg signature\n",
        )
        .unwrap();

        let trust = verify_humanfile_signature(&ostk_dir);
        // Depending on whether gpg is installed:
        // - gpg present: BadSignature
        // - gpg absent: GpgAbsent
        match trust {
            HumanfileTrust::BadSignature | HumanfileTrust::GpgAbsent => {}
            HumanfileTrust::Verified(_) => panic!("should not verify a dummy .asc"),
            HumanfileTrust::Unsigned => panic!("should not return Unsigned when .asc exists"),
        }

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_parse_gpg_key_id() {
        let output = "sec:u:4096:1:ABCDEF1234567890:1620000000:::u:::scESC:::\nuid:u::::1620000000::HASH::Test User <test@example.com>:::::::\n";
        let key = parse_gpg_key_id(output).unwrap();
        assert_eq!(key, "ABCDEF1234567890");
    }

    #[test]
    fn test_parse_gpg_uid() {
        let output = "sec:u:4096:1:ABCDEF1234567890:1620000000:::u:::scESC:::\nuid:u::::1620000000::HASH::Test User <test@example.com>:::::::\n";
        let uid = parse_gpg_uid(output).unwrap();
        assert_eq!(uid, "Test User <test@example.com>");
    }

    #[test]
    fn test_parse_gpg_key_id_missing() {
        let result = parse_gpg_key_id("nothing useful here\n");
        assert!(result.is_err());
    }

    #[test]
    fn test_extract_signer_key() {
        let stderr = "gpg: Signature made Mon 17 Mar 2026 using RSA key DEADBEEF12345678\n\
                       gpg: Good signature from \"Scott Meyer <scott@example.com>\"\n";
        let key = extract_signer_key(stderr);
        assert_eq!(key, "DEADBEEF12345678");
    }

    #[test]
    fn test_extract_signer_key_unknown() {
        let key = extract_signer_key("no key info here");
        assert_eq!(key, "unknown");
    }

    #[test]
    fn test_resolve_kernel_key_from_file() {
        let tmp = test_dir("kernel_key_file");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        fs::write(tmp.join("kernel.key"), "  TESTKEY1234  \n").unwrap();

        // Should read from file and trim whitespace
        let key = resolve_kernel_key(&tmp);
        assert_eq!(key, "TESTKEY1234");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_resolve_kernel_key_fallback() {
        let tmp = test_dir("kernel_key_fallback");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        // No kernel.key file → falls back to default

        let key = resolve_kernel_key(&tmp);
        assert_eq!(key, "907A200DA6C869EB");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_resolve_kernel_key_empty_file_uses_fallback() {
        let tmp = test_dir("kernel_key_empty");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        fs::write(tmp.join("kernel.key"), "   \n").unwrap();
        // Empty/whitespace-only file → falls back to default

        let key = resolve_kernel_key(&tmp);
        assert_eq!(key, "907A200DA6C869EB");

        let _ = fs::remove_dir_all(&tmp);
    }

    // --- →773: Agentfile signing tests ---

    #[test]
    fn test_verify_agentfile_no_asc_is_unsigned() {
        let tmp = test_dir("af_unsigned");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let af_path = tmp.join("scheduler.af");
        fs::write(&af_path, "FROM m\nPROMPT \"p\"\n").unwrap();
        // No .asc file

        let trust = verify_agentfile_signature(&af_path);
        assert_eq!(trust, AgentfileTrust::Unsigned);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_verify_agentfile_missing_file_is_unsigned() {
        let tmp = test_dir("af_missing");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let af_path = tmp.join("nonexistent.af");
        // File doesn't exist at all

        let trust = verify_agentfile_signature(&af_path);
        assert_eq!(trust, AgentfileTrust::Unsigned);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_verify_agentfile_bad_asc() {
        let tmp = test_dir("af_bad_asc");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let af_path = tmp.join("scheduler.af");
        fs::write(&af_path, "FROM m\nPROMPT \"p\"\n").unwrap();
        fs::write(tmp.join("scheduler.af.asc"), "not a real gpg signature\n").unwrap();

        let trust = verify_agentfile_signature(&af_path);
        // Depending on whether gpg is installed:
        // - gpg present: BadSignature
        // - gpg absent: GpgAbsent
        match trust {
            AgentfileTrust::BadSignature(_) | AgentfileTrust::GpgAbsent => {}
            AgentfileTrust::Verified(_) => panic!("should not verify a dummy .asc"),
            AgentfileTrust::Unsigned => panic!("should not return Unsigned when .asc exists"),
        }

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_sign_agentfile_missing_file_errors() {
        let tmp = test_dir("af_sign_missing");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let af_path = tmp.join("nonexistent.af");

        let result = sign_agentfile(&af_path, &tmp);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("not found"));

        let _ = fs::remove_dir_all(&tmp);
    }
}
