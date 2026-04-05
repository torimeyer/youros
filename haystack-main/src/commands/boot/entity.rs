//! ENTITYFILE and Agentfile loading for `ostk boot`.
//!
//! Handles loading ENTITYFILE (governance spec) from primary or legacy
//! locations, optional Agentfile context, and identity boot-done signaling.

use std::fs;
use std::path::Path;

/// Load ENTITYFILE from `.ostk/ENTITYFILE` (preferred) or fall back to
/// `docs/spec/ENTITYFILE_v1.0.md` (legacy location).
///
/// Extracts version from `**Version:** X.Y` frontmatter.
/// Checks for GPG signature via `gpg --verify` when available.
/// Prints:
///   `ENTITYFILE: vX.Y (GPG chain intact)`
///   `ENTITYFILE: vX.Y (GPG chain unverified -- gpg not available)`
///   `ENTITYFILE: vX.Y (GPG chain not signed)`
///
/// Non-fatal if file is missing -- prints a warning and continues.
pub(super) fn load_entityfile(ostk_dir: &Path) -> Result<(), String> {
    // Preferred location: .ostk/ENTITYFILE
    let primary = ostk_dir.join("ENTITYFILE");
    // Legacy / spec location: docs/spec/ENTITYFILE_v1.0.md (relative to project root)
    let legacy = ostk_dir
        .parent()
        .map(|r| r.join("docs").join("spec").join("ENTITYFILE_v1.0.md"));

    let (path, is_legacy) = if primary.exists() {
        (primary, false)
    } else if let Some(leg) = legacy.filter(|p| p.exists()) {
        (leg, true)
    } else {
        eprintln!("{}", crate::strings::boot::ENTITYFILE_NOT_FOUND);
        return Ok(());
    };

    let content = fs::read_to_string(&path)
        .map_err(|e| format!("failed to read ENTITYFILE: {e}"))?;

    // Extract version from `**Version:** 1.0` or `version: 1.0`
    let version = content
        .lines()
        .find(|l| {
            let low = l.to_lowercase();
            low.contains("version") && (low.contains("1.") || low.contains("2."))
        })
        .and_then(|l| {
            // Try bold markdown form: **Version:** 1.0
            // or plain: version: 1.0
            l.split(':').nth(1).map(|s| {
                s.trim()
                    .trim_matches('*')
                    .split_whitespace()
                    .next()
                    .unwrap_or("1.0")
                    .to_string()
            })
        })
        .unwrap_or_else(|| "1.0".to_string());

    // Look for a detached .asc signature alongside the file
    let sig_path = {
        let mut p = path.clone();
        p.set_extension(if is_legacy { "md.asc" } else { "asc" });
        p
    };

    let gpg_status = if sig_path.exists() {
        // Try to verify with gpg
        let result = std::process::Command::new("gpg")
            .args(["--verify", &sig_path.to_string_lossy(), &path.to_string_lossy()])
            .output();
        match result {
            Ok(out) if out.status.success() => "GPG chain intact",
            Ok(_) => "GPG chain INVALID",
            Err(_) => "GPG chain unverified -- gpg not available",
        }
    } else {
        // No .asc file -- check if we're at the legacy location (no sig expected yet)
        if is_legacy {
            "GPG chain not signed -- legacy location"
        } else {
            "GPG chain not signed"
        }
    };

    println!("{}", crate::strings::boot::ENTITYFILE_LOADED
        .replacen("{}", &version, 1)
        .replacen("{}", gpg_status, 1));
    Ok(())
}

/// Load Agentfile context from the path given by `OSTK_AGENTFILE` env var.
///
/// Parses:
/// - `FROM <model>` -> model name
/// - `TOOL <name>` lines -> tool count
///
/// Prints:
///   `Agentfile: model=<model>, tools=<N>`
pub(super) fn load_agentfile_context(path: &str) -> Result<(), String> {
    let af_path = std::path::Path::new(path);
    if !af_path.exists() {
        eprintln!("{}", crate::strings::boot::AGENTFILE_NOT_FOUND.replacen("{}", path, 1));
        return Ok(());
    }

    let content = fs::read_to_string(af_path)
        .map_err(|e| format!("failed to read Agentfile at {path}: {e}"))?;

    // FROM <model> -- first non-comment FROM line
    let model = content
        .lines()
        .find(|l| l.trim_start().starts_with("FROM ") && !l.trim_start().starts_with('#'))
        .and_then(|l| l.trim_start().strip_prefix("FROM "))
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "unknown".to_string());

    // Count TOOL lines
    let tool_count = content
        .lines()
        .filter(|l| l.trim_start().starts_with("TOOL ") && !l.trim_start().starts_with('#'))
        .count();

    println!("{}", crate::strings::boot::AGENTFILE_LOADED
        .replacen("{}", &model, 1)
        .replacen("{}", &tool_count.to_string(), 1));
    Ok(())
}

/// Write `~/.ostk/store/identity-boot-done.txt` to signal that the
/// identity layer loaded successfully. Creates the store dir if needed.
pub(super) fn write_identity_boot_done() -> Result<(), String> {
    let store_dir = dirs_or_home().join(".ostk").join("store");
    if let Err(e) = fs::create_dir_all(&store_dir) {
        // Non-fatal -- just warn
        eprintln!("boot: could not create ~/.ostk/store/: {e}");
        return Ok(());
    }
    let done_path = store_dir.join("identity-boot-done.txt");
    let ts = crate::now_iso();
    fs::write(&done_path, format!("identity-boot-done: {ts}\n"))
        .map_err(|e| format!("failed to write identity-boot-done.txt: {e}"))?;
    Ok(())
}

/// Resolve the user's home directory for the `~/.ostk/store` path.
fn dirs_or_home() -> std::path::PathBuf {
    std::env::var("HOME")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| std::path::PathBuf::from("/tmp"))
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
        std::env::temp_dir().join(format!("ostk_boot_entity_{prefix}_{pid}_{n}"))
    }

    #[test]
    fn test_load_entityfile_primary_location() {
        let tmp = test_dir("entityfile_primary");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();

        let entityfile = r#"# ENTITYFILE v1.0

**Version:** 1.0 (DRAFT)
**Date:** 2026-03-10

Intelligence governance spec.
"#;
        fs::write(ostk_dir.join("ENTITYFILE"), entityfile).unwrap();

        let result = load_entityfile(&ostk_dir);
        assert!(result.is_ok(), "load_entityfile should succeed: {:?}", result);
    }

    #[test]
    fn test_load_entityfile_legacy_location() {
        let tmp = test_dir("entityfile_legacy");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        let docs_spec = tmp.join("docs").join("spec");
        fs::create_dir_all(&ostk_dir).unwrap();
        fs::create_dir_all(&docs_spec).unwrap();

        let entityfile = "**Version:** 1.0\nSome governance content.\n";
        fs::write(docs_spec.join("ENTITYFILE_v1.0.md"), entityfile).unwrap();

        // No primary .ostk/ENTITYFILE -- should fall back to legacy
        let result = load_entityfile(&ostk_dir);
        assert!(result.is_ok(), "legacy entityfile location should work: {:?}", result);
    }

    #[test]
    fn test_load_entityfile_missing_is_nonfatal() {
        let tmp = test_dir("entityfile_missing");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();
        // No ENTITYFILE anywhere

        let result = load_entityfile(&ostk_dir);
        assert!(result.is_ok(), "missing ENTITYFILE should not fail boot: {:?}", result);
    }

    #[test]
    fn test_load_agentfile_context_parses_model_and_tools() {
        let tmp = test_dir("agentfile_parse");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        let agentfile = r#"FROM claude-sonnet-4-6

PROMPT """
You are an agent.
"""

TOOL Bash
TOOL Read
TOOL Edit
TOOL Write
TOOL Glob
"#;
        let af_path = tmp.join("test.Agentfile");
        fs::write(&af_path, agentfile).unwrap();

        let result = load_agentfile_context(af_path.to_str().unwrap());
        assert!(result.is_ok(), "load_agentfile_context should succeed: {:?}", result);
    }

    #[test]
    fn test_load_agentfile_missing_is_nonfatal() {
        let result = load_agentfile_context("/nonexistent/path/agent.Agentfile");
        assert!(result.is_ok(), "missing Agentfile should not fail boot: {:?}", result);
    }

    #[test]
    fn test_write_identity_boot_done() {
        // Just verify the function runs without error.
        // The actual file path depends on HOME env var.
        let result = write_identity_boot_done();
        assert!(result.is_ok(), "write_identity_boot_done should succeed: {:?}", result);
    }
}
