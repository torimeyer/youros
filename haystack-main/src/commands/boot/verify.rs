//! boot.md GPG signature verification for `ostk boot`.
//!
//! Verifies the detached GPG signature on boot.md, handling key rotation,
//! expired keys, and gpg absence gracefully.

use std::path::Path;
use std::process::Command;

/// ->772: Accepted GPG key suffixes for boot/governance signatures.
///
/// During key rotation, BOTH old and new keys must be accepted:
/// - Old keys (955AF54E / D4889BFC): 90-day expiry, may be expired-but-not-revoked
/// - New keys (BAF08C96 / 907A200D): current active keys
const ACCEPTED_KEY_SUFFIXES: &[&str] = &[
    // Old keys (pre-rotation)
    "955AF54E",  // @scott T0 (old)
    "D4889BFC",  // @ostk.prime T1 (old)
    // New keys (post-rotation 2026-03-19)
    "BAF08C96",  // @scott T0 (BAF08C963C7E3184)
    "907A200D",  // @ostk.prime T1 (907A200DA6C869EB)
];

/// Check whether a key ID contains any accepted key fragment.
pub(super) fn is_accepted_key(key_id: &str) -> bool {
    ACCEPTED_KEY_SUFFIXES.iter().any(|frag| key_id.contains(frag))
}

/// Result of verifying the GPG detached signature on boot.md.
pub(super) enum BootSignatureStatus {
    /// Signature present and verified by gpg against an accepted key.
    Verified(String),
    /// No .asc file found alongside boot.md.
    Unsigned,
    /// Signature valid but the signing key has expired (not revoked).
    /// Boot confidence is reduced but this is not a hard failure.
    ExpiredKey(String),
    /// .asc file found but gpg reported it invalid.
    Invalid(String),
    /// gpg binary not available on this machine.
    GpgAbsent,
}

/// Verify the detached GPG signature for boot.md (`.ostk/boot.md.asc`).
///
/// ->772: Accepts both old and new key fingerprints during key rotation.
/// Expired-but-not-revoked keys produce a warning (ExpiredKey) rather than failure.
/// Always non-fatal -- returns a status enum, never errors.
pub(super) fn verify_boot_md_signature(ostk_dir: &Path) -> BootSignatureStatus {
    let boot_path = ostk_dir.join("boot.md");
    let sig_path = ostk_dir.join("boot.md.asc");

    if !sig_path.exists() {
        return BootSignatureStatus::Unsigned;
    }

    // Use --status-fd to get machine-readable output for distinguishing
    // GOODSIG vs EXPKEYSIG (expired key, valid signature).
    let output = Command::new("gpg")
        .args(["--status-fd", "1", "--verify"])
        .arg(&sig_path)
        .arg(&boot_path)
        .output();

    match output {
        Ok(o) => {
            let status_out = String::from_utf8_lossy(&o.stdout);
            let stderr = String::from_utf8_lossy(&o.stderr);
            let key_id = crate::commands::sign::extract_signer_key(&stderr);

            // Parse GnuPG status-fd output:
            //   [GNUPG:] GOODSIG <keyid> <uid>    -- valid signature, key not expired
            //   [GNUPG:] EXPKEYSIG <keyid> <uid>   -- valid signature, but key expired
            //   [GNUPG:] BADSIG ...                -- signature does not verify
            let has_goodsig = status_out.lines().any(|l| l.contains("[GNUPG:] GOODSIG"));
            let has_expkeysig = status_out.lines().any(|l| l.contains("[GNUPG:] EXPKEYSIG"));
            let has_validsig = status_out.lines().any(|l| l.contains("[GNUPG:] VALIDSIG"));

            if has_goodsig || (has_validsig && o.status.success()) {
                // Signature valid, key not expired
                if is_accepted_key(&key_id) {
                    tracing::info!(key = %key_id, "boot: signature verified");
                    BootSignatureStatus::Verified(key_id)
                } else {
                    tracing::warn!(key = %key_id, "boot: signature from unrecognized key");
                    let msg = format!("signed by unrecognized key {key_id}");
                    BootSignatureStatus::Invalid(msg)
                }
            } else if has_expkeysig || (has_validsig && !o.status.success()) {
                // Signature cryptographically valid but key has expired.
                // ->772: treat as warning, not error -- the key isn't revoked.
                if is_accepted_key(&key_id) {
                    tracing::warn!(key = %key_id, "boot: signature from expired (not revoked) key -- confidence reduced");
                    BootSignatureStatus::ExpiredKey(key_id)
                } else {
                    let msg = format!("expired unrecognized key {key_id}");
                    BootSignatureStatus::Invalid(msg)
                }
            } else {
                let msg = String::from_utf8_lossy(&o.stderr).trim().to_string();
                BootSignatureStatus::Invalid(msg)
            }
        }
        Err(_) => BootSignatureStatus::GpgAbsent,
    }
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
        std::env::temp_dir().join(format!("ostk_boot_verify_{prefix}_{pid}_{n}"))
    }

    /// No .asc file -> Unsigned status.
    #[test]
    fn test_verify_boot_md_unsigned() {
        let tmp = test_dir("sig_unsigned");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();
        fs::write(ostk_dir.join("boot.md"), "# test\n").unwrap();
        // Deliberately no boot.md.asc

        let status = verify_boot_md_signature(&ostk_dir);
        assert!(
            matches!(status, BootSignatureStatus::Unsigned),
            "expected Unsigned when no .asc file present"
        );

        let _ = fs::remove_dir_all(&tmp);
    }

    /// If gpg is absent, verify_boot_md_signature returns GpgAbsent (not a panic or error).
    /// We simulate this by pointing gpg at a nonexistent binary via PATH manipulation.
    /// Since we can't easily swap PATH in a unit test, we test with a real .asc file that
    /// would trigger the gpg execution path, and accept either GpgAbsent or Invalid.
    #[test]
    fn test_verify_boot_md_with_asc_present_does_not_panic() {
        let tmp = test_dir("sig_asc_present");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();
        fs::write(ostk_dir.join("boot.md"), "# test\n").unwrap();
        // Write a dummy (invalid) .asc -- enough to trigger verification
        fs::write(ostk_dir.join("boot.md.asc"), "not a real gpg sig\n").unwrap();

        let status = verify_boot_md_signature(&ostk_dir);
        // With a fake .asc, gpg should report Invalid (if present) or GpgAbsent (if absent).
        // Either way, must not panic.
        match status {
            BootSignatureStatus::Invalid(_) | BootSignatureStatus::GpgAbsent => {}
            BootSignatureStatus::Verified(_) => {
                panic!("should not verify a dummy .asc as valid")
            }
            BootSignatureStatus::ExpiredKey(_) => {
                panic!("should not return ExpiredKey for a dummy .asc")
            }
            BootSignatureStatus::Unsigned => {
                panic!("should not return Unsigned when .asc file exists")
            }
        }

        let _ = fs::remove_dir_all(&tmp);
    }

    // ── ->772: accepted key tests ────────────────────────────────────────────

    #[test]
    fn test_accepted_key_old_keys() {
        // Old keys (pre-rotation) should be accepted
        assert!(is_accepted_key("955AF54E2E5E5F71"), "old scott T0 key should be accepted");
        assert!(is_accepted_key("D4889BFC"), "old ostk.prime T1 key should be accepted");
        assert!(is_accepted_key("AABBCCDD955AF54E"), "suffix match for old scott key");
    }

    #[test]
    fn test_accepted_key_new_keys() {
        // New keys (post-rotation) should be accepted
        assert!(is_accepted_key("BAF08C963C7E3184"), "new scott T0 key should be accepted");
        assert!(is_accepted_key("907A200DA6C869EB"), "new ostk.prime T1 key should be accepted");
        assert!(is_accepted_key("BAF08C96"), "short form new scott key");
        assert!(is_accepted_key("907A200D"), "short form new ostk.prime key");
    }

    #[test]
    fn test_rejected_key_unknown() {
        assert!(!is_accepted_key("DEADBEEF12345678"), "unknown key should be rejected");
        assert!(!is_accepted_key("unknown"), "fallback 'unknown' should be rejected");
        assert!(!is_accepted_key(""), "empty key should be rejected");
    }
}
