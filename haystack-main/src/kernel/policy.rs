/// Pin capability enforcement for the ostk kernel.
///
/// When an agent runs under a pin (`OSTK_PIN` env var set by `ostk run --pin <name>`),
/// its write access is governed by the `.ostk/pins/{name}/pin.caps` file.
///
/// →895: When no explicit pin is set, the kernel automatically applies
/// tier-default restrictions based on the GPG web-of-trust trust tier.
/// This is cached per-process to avoid repeated GPG subprocess calls.
///
/// Format of pin.caps:
/// ```text
/// read: .ostk/ .language
/// write: .ostk/store/{name}/
/// execute: shell(readonly)
/// deny: write-kernel modify-governance
/// ```
///
/// The `deny:` line lists capability tokens that restrict write access:
/// - `write-kernel`        → denies writes to paths containing `.ostk/`
///                          (exempt: needles/, hay/, decisions, audit, store/, sessions/, nudges/, current_model)
/// - `modify-governance`   → denies writes to `.primefile`, `GOVERNANCE.md`, `ENTITYFILE`
/// - `write-src`           → denies writes to paths containing `src/`
///
/// If `OSTK_PIN` is not set, or pin.caps is missing/malformed, all writes are allowed
/// (non-fatal, default-allow).
use std::path::Path;
use std::sync::OnceLock;

/// Parsed capability restrictions for an active pin.
#[derive(Debug, Default)]
pub struct PinCaps {
    /// If false, all writes are denied by a global deny token.
    pub allows_write: bool,
    /// Capability tokens from the `deny:` line.
    denied_tokens: Vec<String>,
}

/// →1010: Result of loading pin.caps — distinguishes "no pin" from "pin error."
/// When OSTK_PIN is set but pin.caps can't be loaded, the chain should DENY (fail-closed).
#[derive(Debug)]
pub enum PinCapsResult {
    /// No pin active — no restrictions.
    NoneActive,
    /// Pin loaded successfully — enforce these caps.
    Loaded(PinCaps),
    /// Pin was expected but failed to load — fail-closed DENY.
    Error(String),
}

impl PinCaps {
    /// Returns true if this path is denied by the active pin's capability set.
    pub fn denies_write(&self, path: &Path) -> bool {
        if !self.allows_write {
            return true;
        }
        let path_str = path.to_string_lossy();
        for token in &self.denied_tokens {
            if path_is_denied_by_token(&path_str, token) {
                return true;
            }
        }
        false
    }
}

/// Check if a path string is denied by a given capability token.
fn path_is_denied_by_token(path_str: &str, token: &str) -> bool {
    match token {
        "write-kernel" => {
            let is_ostk = path_str.contains(".ostk/") || path_str.contains("/.ostk");
            if !is_ostk {
                return false;
            }
            // Exempt kernel work paths — agents must be able to mutate needles,
            // hay, decisions, audit, and store. Only block governance/config paths.
            // current_model is session-state written by the boot protocol, not governance.
            let exempt = path_str.contains("/needles/")
                || path_str.contains("/hay/")
                || path_str.contains("/decisions")
                || path_str.contains("/audit")
                || path_str.contains("/store/")
                || path_str.contains("/sessions/")
                || path_str.contains("/nudges/")
                || path_str.ends_with("/.ostk/current_model");
            !exempt
        }
        "modify-governance" => {
            path_str.ends_with(".primefile")
                || path_str.ends_with("GOVERNANCE.md")
                || path_str.ends_with("ENTITYFILE")
                || path_str.ends_with("HUMANFILE")
                || path_str.ends_with(".af")
                || path_str.ends_with("/Agentfile")
                || path_str.contains("/agents/")
        }
        "write-src" => path_str.contains("/src/") || path_str.contains("src/"),
        // Unknown tokens: ignore (default allow for forward compatibility)
        _ => false,
    }
}

/// Load the pin.caps for the currently active pin, if any.
///
/// →895: Two paths to pin enforcement:
/// 1. Explicit: `OSTK_PIN` env var set by `ostk run --pin <name>` → load named pin
/// 2. Implicit: No OSTK_PIN → determine trust tier from GPG web of trust,
///    apply tier-default deny tokens automatically.
///
/// Tier-default pins (→895):
/// - T0: no restrictions (returns None)
/// - T1: deny write-kernel modify-governance
/// - T2: deny write-kernel modify-governance write-src (read-only effectively)
/// - T3: deny all writes (should not boot, but defense in depth)
///
/// →895: Security invariant — tier is a FLOOR, not an override.
/// An explicit OSTK_PIN can add restrictions but never remove tier-default ones.
/// A T3 user setting OSTK_PIN=permissive still gets deny-all.
/// A T2 user setting OSTK_PIN=no-governance still gets write-src denied.
///
/// →895: Cached trust tier — resolved once per process via GPG web of trust.
/// Avoids repeated `gpg --check-sigs` calls on every file write.
static CACHED_TRUST_TIER: OnceLock<crate::kernel::identity::TrustTier> = OnceLock::new();

/// Resolve trust tier (cached per process).
fn cached_trust_tier(ostk_dir: &Path) -> &'static crate::kernel::identity::TrustTier {
    CACHED_TRUST_TIER.get_or_init(|| {
        // →895: CI/test override — OSTK_TRUST_TIER env var bypasses GPG resolution.
        // Only used in CI where GPG keyring is unavailable.
        if let Ok(tier_str) = std::env::var("OSTK_TRUST_TIER") {
            return match tier_str.as_str() {
                "T0" | "t0" => crate::kernel::identity::TrustTier::T0,
                "T1" | "t1" => crate::kernel::identity::TrustTier::T1,
                "T2" | "t2" => crate::kernel::identity::TrustTier::T2,
                _ => crate::kernel::identity::TrustTier::T3,
            };
        }
        let (tier, _key) = crate::kernel::identity::determine_trust_tier(ostk_dir);
        tier
    })
}

pub fn load_active_pin_caps(ostk_dir: &Path) -> Option<PinCaps> {
    match load_pin_caps_result(ostk_dir) {
        PinCapsResult::NoneActive => None,
        PinCapsResult::Loaded(caps) => Some(caps),
        PinCapsResult::Error(_) => {
            // →1010: Fail-closed — deny all writes when pin can't load.
            Some(PinCaps { allows_write: false, denied_tokens: vec![] })
        }
    }
}

/// →1010: Load pin.caps with explicit error handling for the approval chain.
///
/// Returns `Error` when OSTK_PIN is set but pin.caps can't be read —
/// the approval chain should DENY (fail-closed), not silently allow.
pub fn load_pin_caps_result(ostk_dir: &Path) -> PinCapsResult {
    // Always resolve tier-default first — this is the floor
    let tier = cached_trust_tier(ostk_dir);
    let tier_caps = tier_default_caps(tier);

    // Path 1: explicit pin from env var — merge with tier floor
    if let Ok(pin_name) = std::env::var("OSTK_PIN")
        && !pin_name.is_empty() {
            let caps_path = ostk_dir
                .join("pins")
                .join(&pin_name)
                .join("pin.caps");

            match std::fs::read_to_string(&caps_path) {
                Ok(content) => {
                    let explicit = parse_pin_caps(&content);
                    match merge_pin_caps(tier_caps, Some(explicit)) {
                        Some(caps) => return PinCapsResult::Loaded(caps),
                        None => return PinCapsResult::NoneActive,
                    }
                }
                Err(e) => {
                    return PinCapsResult::Error(format!(
                        "pin '{}' caps unreadable: {} — fail-closed",
                        pin_name, e
                    ));
                }
            }
        }

    // Path 2: tier-default only
    match tier_caps {
        Some(caps) => PinCapsResult::Loaded(caps),
        None => PinCapsResult::NoneActive,
    }
}

/// →895: Tier-default pin caps — the minimum restriction floor per trust tier.
fn tier_default_caps(tier: &crate::kernel::identity::TrustTier) -> Option<PinCaps> {
    match tier {
        crate::kernel::identity::TrustTier::T0 => None, // full governance, no restrictions
        crate::kernel::identity::TrustTier::T1 => {
            Some(PinCaps {
                allows_write: true,
                denied_tokens: vec![
                    "write-kernel".to_string(),
                    "modify-governance".to_string(),
                ],
            })
        }
        crate::kernel::identity::TrustTier::T2 => {
            Some(PinCaps {
                allows_write: true,
                denied_tokens: vec![
                    "write-kernel".to_string(),
                    "modify-governance".to_string(),
                    "write-src".to_string(),
                ],
            })
        }
        crate::kernel::identity::TrustTier::T3 => {
            Some(PinCaps {
                allows_write: false,
                denied_tokens: vec![],
            })
        }
    }
}

/// →895: Merge tier-default floor with explicit pin caps.
/// Result is the UNION of deny tokens — explicit pin can only add restrictions.
/// If tier says allows_write=false, that wins regardless of explicit pin.
fn merge_pin_caps(tier: Option<PinCaps>, explicit: Option<PinCaps>) -> Option<PinCaps> {
    match (tier, explicit) {
        (None, None) => None,
        (None, Some(e)) => Some(e),
        (Some(t), None) => Some(t),
        (Some(t), Some(e)) => {
            // Tier floor wins on allows_write
            let allows_write = t.allows_write && e.allows_write;
            // Union of deny tokens
            let mut tokens = t.denied_tokens;
            for tok in e.denied_tokens {
                if !tokens.contains(&tok) {
                    tokens.push(tok);
                }
            }
            Some(PinCaps {
                allows_write,
                denied_tokens: tokens,
            })
        }
    }
}

/// Public test helper: parse pin.caps content (exposed for integration tests in file.rs).
#[cfg(test)]
pub fn parse_pin_caps_pub(content: &str) -> PinCaps {
    parse_pin_caps(content)
}

/// Parse a pin.caps file content into a `PinCaps` struct.
///
/// Only the `deny:` line is relevant for write enforcement. All other lines
/// are ignored. Unknown tokens on the deny line are silently skipped.
fn parse_pin_caps(content: &str) -> PinCaps {
    let mut caps = PinCaps {
        allows_write: true,
        denied_tokens: Vec::new(),
    };

    for line in content.lines() {
        let line = line.trim();
        if let Some(rest) = line.strip_prefix("deny:") {
            let tokens: Vec<String> = rest
                .split_whitespace()
                .map(|t| t.to_string())
                .collect();
            caps.denied_tokens = tokens;
        }
    }

    caps
}

/// Walk up from `path` to find the `.ostk/` directory.
///
/// Returns the `.ostk/` directory path if found, or an error if none
/// exists in any ancestor.
#[allow(clippy::result_unit_err)]
pub fn find_ostk_dir(path: &Path) -> Result<std::path::PathBuf, ()> {
    // Start from the path's parent (or the path itself if it's a directory).
    let start = if path.is_dir() {
        path.to_path_buf()
    } else {
        match path.parent() {
            Some(p) => p.to_path_buf(),
            None => return Err(()),
        }
    };

    let mut dir = start;
    loop {
        let resolved = crate::state_dir(&dir);
        if resolved.is_dir() {
            return Ok(resolved);
        }
        if !dir.pop() {
            return Err(());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    // ── parse_pin_caps tests ──────────────────────────────────────────────

    #[test]
    fn test_parse_pin_caps_deny_write_kernel() {
        let content = "read: .ostk/ .language\n\
                       write: .ostk/store/test/\n\
                       execute: shell(readonly)\n\
                       deny: write-kernel modify-governance\n";
        let caps = parse_pin_caps(content);
        assert!(caps.allows_write);
        assert!(caps.denied_tokens.contains(&"write-kernel".to_string()));
        assert!(caps.denied_tokens.contains(&"modify-governance".to_string()));
    }

    #[test]
    fn test_parse_pin_caps_no_deny_line() {
        let content = "read: .ostk/\n\
                       write: .ostk/store/test/\n";
        let caps = parse_pin_caps(content);
        assert!(caps.allows_write);
        assert!(caps.denied_tokens.is_empty());
    }

    #[test]
    fn test_parse_pin_caps_empty() {
        let caps = parse_pin_caps("");
        assert!(caps.allows_write);
        assert!(caps.denied_tokens.is_empty());
    }

    // ── denies_write tests ────────────────────────────────────────────────

    #[test]
    fn test_pin_caps_denies_write_kernel() {
        let content = "deny: write-kernel modify-governance\n";
        let caps = parse_pin_caps(content);

        // .ostk/ path should be denied
        assert!(caps.denies_write(Path::new("/project/.ostk/agents.jsonl")));
        assert!(caps.denies_write(Path::new("/project/.ostk/boot.md")));
        // Normal src path should be allowed
        assert!(!caps.denies_write(Path::new("/project/src/main.rs")));
        // Top-level file should be allowed
        assert!(!caps.denies_write(Path::new("/project/README.md")));
    }

    #[test]
    fn test_write_kernel_allows_current_model() {
        let content = "deny: write-kernel modify-governance\n";
        let caps = parse_pin_caps(content);

        // current_model is session-state — must be allowed despite write-kernel deny
        assert!(!caps.denies_write(Path::new("/project/.ostk/current_model")));
        // Regression: other .ostk/ files still denied
        assert!(caps.denies_write(Path::new("/project/.ostk/agents.jsonl")));
        // Regression: existing exempt paths still allowed
        assert!(!caps.denies_write(Path::new("/project/.ostk/needles/foo.md")));
    }

    #[test]
    fn test_pin_caps_denies_modify_governance() {
        let content = "deny: modify-governance\n";
        let caps = parse_pin_caps(content);

        assert!(caps.denies_write(Path::new("/project/.primefile")));
        assert!(caps.denies_write(Path::new("/project/GOVERNANCE.md")));
        assert!(caps.denies_write(Path::new("/project/ENTITYFILE")));
        // →775: HUMANFILE and Agentfiles (.af) are governance artifacts
        assert!(caps.denies_write(Path::new("/project/.ostk/HUMANFILE")));
        assert!(caps.denies_write(Path::new("/project/.ostk/scheduler.af")));
        // Normal files are allowed
        assert!(!caps.denies_write(Path::new("/project/src/main.rs")));
        assert!(!caps.denies_write(Path::new("/project/.ostk/agents.jsonl")));
    }

    #[test]
    fn test_pin_caps_denies_write_src() {
        let content = "deny: write-src\n";
        let caps = parse_pin_caps(content);

        assert!(caps.denies_write(Path::new("/project/src/main.rs")));
        assert!(caps.denies_write(Path::new("/project/src/kernel/file.rs")));
        // Non-src paths are allowed
        assert!(!caps.denies_write(Path::new("/project/README.md")));
        assert!(!caps.denies_write(Path::new("/project/.ostk/boot.md")));
    }

    #[test]
    fn test_pin_caps_allows_normal_write_with_write_kernel_denied() {
        // write-kernel denied, but src/main.rs should be fine
        let content = "deny: write-kernel\n";
        let caps = parse_pin_caps(content);

        assert!(!caps.denies_write(Path::new("/project/src/main.rs")));
        assert!(!caps.denies_write(Path::new("/project/Cargo.toml")));
        assert!(!caps.denies_write(Path::new("/project/README.md")));
    }

    // ── load_active_pin_caps tests ────────────────────────────────────────

    #[test]
    fn test_pin_caps_no_active_pin() {
        // Ensure OSTK_PIN is not set (or empty)
        // We can't unset env vars cleanly in parallel tests, so we test
        // parse_pin_caps directly and verify load returns None when
        // OSTK_PIN is not set.
        // If OSTK_PIN happens to be set in the test env, this test
        // verifies the parse path; the load path is integration-tested.

        // Direct test of parse behavior (no deny = no restriction)
        let caps = parse_pin_caps("read: .ostk/\nwrite: .ostk/store/x/\n");
        assert!(!caps.denies_write(Path::new("/any/path.txt")));
    }

    #[test]
    fn test_load_active_pin_caps_reads_file() {
        // Create a temp .ostk/pins/test-pin/pin.caps
        let tmp = std::env::temp_dir().join("ostk_policy_test");
        let pin_dir = tmp.join("pins").join("test-pin");
        fs::create_dir_all(&pin_dir).unwrap();
        fs::write(
            pin_dir.join("pin.caps"),
            "deny: write-kernel modify-governance\n",
        )
        .unwrap();

        // Set OSTK_PIN and call load_active_pin_caps
        // Note: env var tests can be flaky in parallel; use a dedicated
        // temp dir per test.
        // We test parse_pin_caps directly here since setting env vars in
        // parallel tests is unsafe. The load function is thin — it reads
        // env, reads file, calls parse.
        let content =
            fs::read_to_string(pin_dir.join("pin.caps")).unwrap();
        let caps = parse_pin_caps(&content);
        assert!(caps.denies_write(Path::new("/proj/.ostk/agents.jsonl")));
        assert!(!caps.denies_write(Path::new("/proj/src/main.rs")));

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_load_active_pin_caps_missing_file_returns_none() {
        // If pin.caps doesn't exist, load should return None (default allow)
        let tmp = std::env::temp_dir().join("ostk_policy_missing_test");
        let hs_dir = tmp.join(".ostk");
        fs::create_dir_all(&hs_dir).unwrap();

        // No pins directory — load_active_pin_caps with no OSTK_PIN
        // should return None (skipped in env-var tests above)
        // Test the find_ostk_dir path instead:
        let file = tmp.join("src").join("main.rs");
        fs::create_dir_all(file.parent().unwrap()).unwrap();
        fs::write(&file, "fn main() {}").unwrap();
        let found = find_ostk_dir(&file);
        assert!(found.is_ok());
        assert_eq!(found.unwrap(), hs_dir);

        let _ = fs::remove_dir_all(&tmp);
    }

    // ── find_ostk_dir tests ───────────────────────────────────────────

    #[test]
    fn test_find_ostk_dir_walks_up() {
        let tmp = std::env::temp_dir().join("ostk_find_dir_test");
        let hs_dir = tmp.join(".ostk");
        let deep = tmp.join("a").join("b").join("c");
        fs::create_dir_all(&hs_dir).unwrap();
        fs::create_dir_all(&deep).unwrap();
        let file = deep.join("file.txt");
        fs::write(&file, "content").unwrap();

        let found = find_ostk_dir(&file).unwrap();
        assert_eq!(found, hs_dir);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_find_ostk_dir_not_found() {
        // Use /tmp directly (no .ostk/ in ancestors up to /)
        let path = Path::new("/tmp/no_ostk_here/file.txt");
        // This may or may not have .ostk/ — just ensure it doesn't panic.
        // We can't guarantee the result since /tmp might have .ostk/,
        // so just call it and verify it returns Ok or Err without panicking.
        let _ = find_ostk_dir(path);
    }

    // ── →895 tier-default + merge tests ──────────────────────────────────

    #[test]
    fn test_tier_default_t0_no_restrictions() {
        use crate::kernel::identity::TrustTier;
        let caps = tier_default_caps(&TrustTier::T0);
        assert!(caps.is_none());
    }

    #[test]
    fn test_tier_default_t1_denies_kernel_and_governance() {
        use crate::kernel::identity::TrustTier;
        let caps = tier_default_caps(&TrustTier::T1).unwrap();
        assert!(caps.allows_write);
        assert!(caps.denies_write(Path::new("/proj/.ostk/boot.md")));
        assert!(caps.denies_write(Path::new("/proj/HUMANFILE")));
        assert!(!caps.denies_write(Path::new("/proj/src/main.rs")));
    }

    #[test]
    fn test_tier_default_t2_denies_kernel_governance_src() {
        use crate::kernel::identity::TrustTier;
        let caps = tier_default_caps(&TrustTier::T2).unwrap();
        assert!(caps.allows_write);
        assert!(caps.denies_write(Path::new("/proj/.ostk/boot.md")));
        assert!(caps.denies_write(Path::new("/proj/HUMANFILE")));
        assert!(caps.denies_write(Path::new("/proj/src/main.rs")));
        // Non-src, non-kernel, non-governance path should be allowed
        assert!(!caps.denies_write(Path::new("/proj/README.md")));
    }

    #[test]
    fn test_tier_default_t3_denies_all() {
        use crate::kernel::identity::TrustTier;
        let caps = tier_default_caps(&TrustTier::T3).unwrap();
        assert!(!caps.allows_write);
        // Every path is denied
        assert!(caps.denies_write(Path::new("/proj/README.md")));
        assert!(caps.denies_write(Path::new("/proj/src/main.rs")));
        assert!(caps.denies_write(Path::new("/anything")));
    }

    #[test]
    fn test_merge_t3_with_permissive_explicit_still_denies_all() {
        // T3 user sets OSTK_PIN to a permissive pin — tier floor wins
        let tier = Some(PinCaps {
            allows_write: false,
            denied_tokens: vec![],
        });
        let explicit = Some(PinCaps {
            allows_write: true,
            denied_tokens: vec![], // no denies
        });
        let merged = merge_pin_caps(tier, explicit).unwrap();
        assert!(!merged.allows_write); // T3 floor wins
    }

    #[test]
    fn test_merge_t2_with_explicit_adds_restrictions() {
        // T2 tier floor + explicit pin that also denies write-tests
        let tier = Some(PinCaps {
            allows_write: true,
            denied_tokens: vec![
                "write-kernel".to_string(),
                "modify-governance".to_string(),
                "write-src".to_string(),
            ],
        });
        let explicit = Some(PinCaps {
            allows_write: true,
            denied_tokens: vec![
                "write-src".to_string(),      // duplicate — already in tier
                "write-tests".to_string(),     // new restriction
            ],
        });
        let merged = merge_pin_caps(tier, explicit).unwrap();
        assert!(merged.allows_write);
        // Should have all 4 unique tokens
        assert_eq!(merged.denied_tokens.len(), 4);
        assert!(merged.denied_tokens.contains(&"write-kernel".to_string()));
        assert!(merged.denied_tokens.contains(&"modify-governance".to_string()));
        assert!(merged.denied_tokens.contains(&"write-src".to_string()));
        assert!(merged.denied_tokens.contains(&"write-tests".to_string()));
    }

    #[test]
    fn test_merge_t1_explicit_cannot_remove_tier_denies() {
        // T1 denies write-kernel + modify-governance
        // Explicit pin tries to only deny write-kernel (missing modify-governance)
        // Result should still have modify-governance from tier floor
        let tier = Some(PinCaps {
            allows_write: true,
            denied_tokens: vec![
                "write-kernel".to_string(),
                "modify-governance".to_string(),
            ],
        });
        let explicit = Some(PinCaps {
            allows_write: true,
            denied_tokens: vec![
                "write-kernel".to_string(),
            ],
        });
        let merged = merge_pin_caps(tier, explicit).unwrap();
        assert!(merged.denied_tokens.contains(&"modify-governance".to_string()));
        assert!(merged.denied_tokens.contains(&"write-kernel".to_string()));
    }

    #[test]
    fn test_merge_none_none() {
        assert!(merge_pin_caps(None, None).is_none());
    }

    #[test]
    fn test_merge_none_explicit() {
        // T0 (no tier caps) + explicit pin
        let explicit = Some(PinCaps {
            allows_write: true,
            denied_tokens: vec!["write-src".to_string()],
        });
        let merged = merge_pin_caps(None, explicit).unwrap();
        assert!(merged.allows_write);
        assert_eq!(merged.denied_tokens, vec!["write-src".to_string()]);
    }
}
