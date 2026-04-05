//! HUMANFILE loading and trust verification for `ostk boot`.
//!
//! Handles hierarchical HUMANFILE resolution, trust display,
//! and auto-population of the AVAILABLE models list.

use std::fs;
use std::path::Path;

/// Load HUMANFILE via hierarchical resolution (->826).
///
/// Resolution order:
///   1. ~/.HUMANFILE (global identity + preferences)
///   2. .ostk/HUMANFILE (project config, may EXTENDS global)
///   3. Merge if both exist (IDENTITY/SIGN immutable from global)
///
/// Graceful degradation:
///   - No ~/.HUMANFILE: warn, use project config only
///   - No project HUMANFILE: use global only
///   - Neither: use defaults (current behavior)
///   - EXTENDS but parent missing: warn, use child only
///
/// Non-fatal -- always returns Ok. Returns the LoadResult for trust + driver wiring.
pub(super) fn load_humanfile(ostk_dir: &Path) -> Result<crate::humanfile::LoadResult, String> {
    let result = crate::humanfile::load(ostk_dir);

    // Print any warnings
    for w in &result.warnings {
        eprintln!("  \x1b[33m\u{26a0}\x1b[0m {w}");
    }

    // Choose output format based on source
    let label = result.source.label();
    match &result.source {
        crate::humanfile::HumanfileSource::Defaults => {
            // Neither file exists -- use legacy fallback for backwards compat
            let path = ostk_dir.join("HUMANFILE");
            if !path.exists() {
                eprintln!("{}", crate::strings::boot::HUMANFILE_NOT_FOUND
                    .replacen("{}", &path.display().to_string(), 1));
                return Ok(result);
            }
            // Legacy project HUMANFILE (YAML-ish format) -- fall back to verb counting
            if let Ok(content) = fs::read_to_string(&path) {
                let verb_count = content.lines()
                    .filter(|l| {
                        let t = l.trim_start();
                        t.starts_with("| :") || t.starts_with("| `:")
                    })
                    .count();
                let last_updated = content.lines()
                    .find(|l| l.trim_start().to_lowercase().starts_with("last updated"))
                    .and_then(|l| l.split_once(':').map(|x| x.1))
                    .map(|s| s.trim().to_string())
                    .unwrap_or_else(|| "unknown".to_string());
                println!("{}", crate::strings::boot::HUMANFILE_LOADED
                    .replacen("{}", &verb_count.to_string(), 1)
                    .replacen("{}", &last_updated, 1));
            }
        }
        crate::humanfile::HumanfileSource::ProjectOnly => {
            println!("{}", crate::strings::boot::HUMANFILE_WARN_V2
                .replacen("{}", label, 1));
        }
        _ => {
            println!("{}", crate::strings::boot::HUMANFILE_LOADED_V2
                .replacen("{}", label, 1));
        }
    }

    Ok(result)
}

/// Display trust status from HUMANFILE LoadResult and set the environment variable.
///
/// Returns the overall trust string for OSTK_HUMANFILE_TRUST env var.
pub(super) fn display_humanfile_trust(hf_result: &crate::humanfile::LoadResult) {
    let trust_label = hf_result.trust_label();
    // Determine overall trust for env var: use the strongest signal from either tier
    let overall_trust = match (&hf_result.global_trust, &hf_result.project_trust) {
        // Any bad signature is a hard block
        (Some(crate::commands::sign::HumanfileTrust::BadSignature), _)
        | (_, Some(crate::commands::sign::HumanfileTrust::BadSignature)) => {
            eprintln!("{}", crate::strings::humanfile_trust::BAD_SIGNATURE);
            "bad_signature"
        }
        // At least one verified -> overall verified
        (Some(crate::commands::sign::HumanfileTrust::Verified(key)), _) => {
            println!("HUMANFILE: \u{2713} {} (key: {})", trust_label, key);
            "verified"
        }
        (_, Some(crate::commands::sign::HumanfileTrust::Verified(key))) => {
            println!("HUMANFILE: \u{2713} {} (key: {})", trust_label, key);
            "verified"
        }
        // GPG absent on all tiers
        (Some(crate::commands::sign::HumanfileTrust::GpgAbsent), _)
        | (_, Some(crate::commands::sign::HumanfileTrust::GpgAbsent)) => {
            println!("{}", crate::strings::humanfile_trust::GPG_ABSENT);
            "gpg_absent"
        }
        // Both unsigned or no files
        _ => {
            if hf_result.global_trust.is_some() || hf_result.project_trust.is_some() {
                eprintln!("HUMANFILE: {} \u{2014} run `ostk sign`", trust_label);
            }
            "unsigned"
        }
    };
    // SAFETY: The boot command runs single-threaded; no concurrent env readers exist.
    unsafe { std::env::set_var("OSTK_HUMANFILE_TRUST", overall_trust) };
}

/// Auto-populate the AVAILABLE heredoc in HUMANFILE.
///
/// Scans known API keys via `resolve_secret` and maps them to models.
/// Only appends if the AVAILABLE list is absent or empty (via directive parser).
/// Writes directive-format `AVAILABLE <<MODELS ... MODELS` block.
/// Non-fatal -- silently returns on any error.
pub(super) fn populate_humanfile_available(ostk_dir: &Path) {
    use crate::commands::secret::resolve_secret;

    // Load via directive parser -- canonical source of truth
    let load_result = crate::humanfile::load(ostk_dir);

    // Skip if available list already populated
    if !load_result.humanfile.available_models.is_empty() {
        return;
    }

    // Only proceed if the HUMANFILE actually exists
    let path = ostk_dir.join("HUMANFILE");
    if !path.exists() {
        return;
    }

    // Map API keys -> models they unlock
    let key_models: &[(&str, &[&str])] = &[
        ("ANTHROPIC_API_KEY", &["claude-sonnet-4-5", "claude-opus-4-6", "claude-haiku-4-5"]),
        ("GEMINI_API_KEY", &["gemini-2.5-pro"]),
        ("OPENROUTER_API_KEY", &["meta-llama/llama-4-maverick"]),
    ];

    let mut available: Vec<String> = Vec::new();
    for (key, models) in key_models {
        if resolve_secret(key).is_ok() {
            for m in *models {
                available.push(m.to_string());
            }
        }
    }

    if available.is_empty() {
        return;
    }

    // Also ensure preferred model and fallback are in the list
    if let Some(ref model) = load_result.humanfile.model
        && !available.contains(model)
    {
        available.insert(0, model.clone());
    }
    if let Some(ref fallback) = load_result.humanfile.fallback
        && !available.contains(fallback)
    {
        available.push(fallback.clone());
    }

    // Append AVAILABLE <<MODELS heredoc to the file
    let mut block = String::from("\nAVAILABLE <<MODELS\n");
    for m in &available {
        block.push_str(m);
        block.push('\n');
    }
    block.push_str("MODELS\n");

    let content = fs::read_to_string(&path).unwrap_or_default();
    let mut new_content = content;
    if !new_content.ends_with('\n') {
        new_content.push('\n');
    }
    new_content.push_str(&block);

    let _ = fs::write(&path, new_content);
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
        std::env::temp_dir().join(format!("ostk_boot_hf_{prefix}_{pid}_{n}"))
    }

    #[test]
    fn test_load_humanfile_counts_verbs() {
        let tmp = test_dir("humanfile_verbs");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();

        let humanfile = r#"# HUMANFILE
## Tack Grammar

| Verb | Meaning | Routes to |
|------|---------|-----------|
| :plan | plan work | ostk draft |
| :compile | triage hay | ostk compile |
| :confirm | proceed | control flow |

Last updated: 2026-03-10T00:00:00Z
"#;
        fs::write(ostk_dir.join("HUMANFILE"), humanfile).unwrap();

        let result = load_humanfile(&ostk_dir);
        assert!(result.is_ok(), "load_humanfile should succeed: {:?}", result);
    }

    #[test]
    fn test_load_humanfile_missing_is_nonfatal() {
        let tmp = test_dir("humanfile_missing");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();
        // No HUMANFILE written

        let result = load_humanfile(&ostk_dir);
        assert!(result.is_ok(), "missing HUMANFILE should not fail boot: {:?}", result);
    }
}
