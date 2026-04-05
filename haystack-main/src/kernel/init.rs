//! INIT execution engine (→1157 Phase 3).
//!
//! Parses `.boot/INIT` and validates the boot sequence against `.language`.
//! INIT has two phases separated by `:load .language`:
//!
//! **Pre-language** (firmware): verbs are hardcoded in the binary.
//! **Post-language** (resolved): verbs must exist in `.language`.
//!
//! The boot sequence in `boot/mod.rs` is the actual executor. This module
//! provides parsing and validation — it doesn't replace the boot code,
//! it ensures INIT and the boot code agree.

use std::fs;
use std::path::Path;

/// Parsed INIT file.
#[derive(Debug)]
pub struct Init {
    /// Metadata declarations (`:identity`, `:kernel`, `:trust`, etc.)
    pub metadata: Vec<(String, String)>,
    /// Pre-language verbs (before `:load .language`)
    pub pre_language: Vec<String>,
    /// Post-language verbs (after `:load .language`)
    pub post_language: Vec<String>,
}

/// Parse `.boot/INIT` into structured phases.
pub fn parse_init(root: &Path) -> Result<Init, String> {
    let init_path = crate::state_dir(root).join(".boot/INIT");
    let text = fs::read_to_string(&init_path)
        .map_err(|e| format!("failed to read .boot/INIT: {e}"))?;

    let mut metadata = Vec::new();
    let mut pre_language = Vec::new();
    let mut post_language = Vec::new();
    let mut past_metadata = false;
    let mut past_pivot = false;

    for line in text.lines() {
        let trimmed = line.trim();

        // Skip comments and blank lines
        if trimmed.is_empty() || trimmed.starts_with('#') || trimmed.starts_with("login") {
            continue;
        }

        if !trimmed.starts_with(':') {
            continue;
        }

        // Metadata declarations have inline values: `:identity  ephemeral | ...`
        // Verb lines are just `:verb` or `:verb arg`
        let (verb, rest) = match trimmed.split_once(char::is_whitespace) {
            Some((v, r)) => (v.trim(), r.trim()),
            None => (trimmed, ""),
        };

        // Detect the pivot: `:load .language`
        if verb == ":load" && rest == ".language" {
            past_metadata = true;
            past_pivot = true;
            pre_language.push(trimmed.to_string());
            continue;
        }

        if !past_metadata {
            // Check if this is a metadata declaration (has descriptive value)
            // vs a verb (has a simple argument or no argument)
            let is_metadata = matches!(verb,
                ":identity" | ":kernel" | ":trust" | ":laws" | ":memory" |
                ":tools" | ":files" | ":fcp" | ":tack" | ":mode" | ":govern"
            );
            if is_metadata {
                metadata.push((verb.to_string(), rest.to_string()));
                continue;
            }
            past_metadata = true;
        }

        if past_pivot {
            post_language.push(verb.trim_start_matches(':').to_string());
        } else {
            pre_language.push(trimmed.to_string());
        }
    }

    Ok(Init { metadata, pre_language, post_language })
}

/// Validate that all post-language INIT verbs exist in `.language`.
/// Returns a list of verbs that failed to resolve.
pub fn validate_post_language(root: &Path, init: &Init) -> Vec<String> {
    let entries = crate::language::parse_language_file(root).unwrap_or_default();
    let mut missing = Vec::new();

    for verb in &init.post_language {
        // Strip annotation (e.g., "boot" from "boot    → read state, report")
        let verb_name = verb.split_whitespace().next().unwrap_or(verb);
        if !entries.iter().any(|e| e.verb == verb_name) {
            missing.push(verb_name.to_string());
        }
    }

    missing
}

/// Run validation at boot and report results.
/// Returns the confidence adjustment (0.0 = all good, negative = issues).
pub fn validate_at_boot(root: &Path) -> f64 {
    let init = match parse_init(root) {
        Ok(i) => i,
        Err(_) => return 0.0, // No INIT file — skip validation
    };

    let missing = validate_post_language(root, &init);
    if missing.is_empty() {
        return 0.0;
    }

    for verb in &missing {
        eprintln!("[init] warning: post-language verb :{verb} not found in .language");
    }
    -(missing.len() as f64 * 0.05) // -5% confidence per missing verb
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn setup(name: &str) -> tempfile::TempDir {
        let tmp = tempfile::Builder::new()
            .prefix(&format!("ostk_init_{name}_"))
            .tempdir()
            .unwrap();
        let ostk = tmp.path().join(".ostk");
        let boot = ostk.join(".boot");
        fs::create_dir_all(&boot).unwrap();
        tmp
    }

    #[test]
    fn test_parse_init_splits_phases() {
        let tmp = setup("parse");
        let init_path = tmp.path().join(".ostk/.boot/INIT");
        fs::write(&init_path, "\
:identity ephemeral
:kernel @prime

:verify .primefile
:init @prime+N
:load .language
:load fcp-*

:boot
:refine
:compile
:work
").unwrap();

        let init = parse_init(tmp.path()).unwrap();
        assert_eq!(init.metadata.len(), 2);
        assert_eq!(init.metadata[0].0, ":identity");
        assert!(init.pre_language.iter().any(|v| v.contains("verify")));
        assert!(init.pre_language.iter().any(|v| v.contains("load .language")));
        assert_eq!(init.post_language, vec!["load", "boot", "refine", "compile", "work"]);
    }

    #[test]
    fn test_parse_init_missing_file() {
        let tmp = setup("missing");
        let result = parse_init(tmp.path());
        assert!(result.is_err());
    }

    #[test]
    fn test_validate_post_language_all_present() {
        let tmp = setup("valid");
        let ostk = tmp.path().join(".ostk");
        // Write .language with the verbs INIT expects
        fs::write(ostk.join(".language"),
            ":boot | 1 | kernel | 0 | 0 | 1.0 | internal | () -> () | boot\n\
             :refine | 1 | user | 0 | 0 | 1.0 | internal | () -> () | refine\n\
             :compile | 1 | user | 0 | 0 | 1.0 | internal | () -> () | compile\n\
             :work | 1 | user | 0 | 0 | 1.0 | internal | () -> () | work\n"
        ).unwrap();

        let init = Init {
            metadata: vec![],
            pre_language: vec![],
            post_language: vec!["boot".into(), "refine".into(), "compile".into(), "work".into()],
        };

        let missing = validate_post_language(tmp.path(), &init);
        assert!(missing.is_empty(), "all verbs should be found: {missing:?}");
    }

    #[test]
    fn test_validate_post_language_missing_verb() {
        let tmp = setup("missing_verb");
        let ostk = tmp.path().join(".ostk");
        // .language missing :work
        fs::write(ostk.join(".language"),
            ":boot | 1 | kernel | 0 | 0 | 1.0 | internal | () -> () | boot\n"
        ).unwrap();

        let init = Init {
            metadata: vec![],
            pre_language: vec![],
            post_language: vec!["boot".into(), "work".into()],
        };

        let missing = validate_post_language(tmp.path(), &init);
        assert_eq!(missing, vec!["work"]);
    }

    #[test]
    fn test_validate_at_boot_no_init() {
        let tmp = setup("no_init");
        let adj = validate_at_boot(tmp.path());
        assert_eq!(adj, 0.0, "missing INIT should not affect confidence");
    }
}
