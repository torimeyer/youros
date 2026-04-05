pub mod parser;

pub use parser::{Humanfile, ParseError, merge, parse};

use std::path::{Path, PathBuf};

use crate::commands::sign::{HumanfileTrust, verify_humanfile_signature_at};

/// Describes what files contributed to a merged Humanfile.
#[derive(Debug, Clone)]
pub enum HumanfileSource {
    /// Both ~/.HUMANFILE and project HUMANFILE loaded and merged.
    GlobalAndProject,
    /// Only ~/.HUMANFILE loaded (no project HUMANFILE).
    GlobalOnly,
    /// Only project HUMANFILE loaded (no ~/.HUMANFILE).
    ProjectOnly,
    /// Neither file exists — using defaults.
    Defaults,
}

impl HumanfileSource {
    /// Human-readable label for boot output.
    pub fn label(&self) -> &'static str {
        match self {
            HumanfileSource::GlobalAndProject => "global: ~/.HUMANFILE + project override",
            HumanfileSource::GlobalOnly => "global only",
            HumanfileSource::ProjectOnly => "project only \u{2014} no global HUMANFILE",
            HumanfileSource::Defaults => "defaults",
        }
    }
}

/// Result of loading and merging HUMANFILEs during boot.
#[derive(Debug)]
pub struct LoadResult {
    pub humanfile: Humanfile,
    pub source: HumanfileSource,
    pub warnings: Vec<String>,
    /// GPG trust level for ~/.HUMANFILE (T0 — global identity).
    pub global_trust: Option<HumanfileTrust>,
    /// GPG trust level for project .ostk/HUMANFILE (T1 — project config).
    pub project_trust: Option<HumanfileTrust>,
}

impl LoadResult {
    /// Human-readable trust summary for boot output (→826).
    ///
    /// Produces strings like:
    ///   "verified (T0 + T1)" — both files signed
    ///   "verified (T0)" — only global signed, no project file
    ///   "project unsigned" — project file exists but is unsigned
    ///   "unsigned" — neither file is signed
    pub fn trust_label(&self) -> String {
        let g_ok = matches!(&self.global_trust, Some(HumanfileTrust::Verified(_)));
        let p_ok = matches!(&self.project_trust, Some(HumanfileTrust::Verified(_)));
        let p_exists = self.project_trust.is_some();

        match (g_ok, p_ok, p_exists) {
            (true, true, _) => "verified (T0 + T1)".to_string(),
            (true, false, true) => "verified (T0), project unsigned".to_string(),
            (true, false, false) => "verified (T0)".to_string(),
            (false, true, _) => "project verified (T1), global unsigned".to_string(),
            (false, false, _) => {
                // Check for bad signatures
                let g_bad = matches!(&self.global_trust, Some(HumanfileTrust::BadSignature));
                let p_bad = matches!(&self.project_trust, Some(HumanfileTrust::BadSignature));
                if g_bad || p_bad {
                    "signature invalid".to_string()
                } else {
                    "unsigned".to_string()
                }
            }
        }
    }
}

/// Expand ~ to the user's home directory.
fn expand_home(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/")
        && let Ok(home) = std::env::var("HOME")
    {
        return PathBuf::from(home).join(rest);
    } else if path == "~"
        && let Ok(home) = std::env::var("HOME")
    {
        return PathBuf::from(home);
    }
    PathBuf::from(path)
}

/// Load and merge HUMANFILEs from global (~/.HUMANFILE) and project (.ostk/HUMANFILE).
///
/// Implements boot resolution from the inheritable-humanfile spec (→826):
///   1. Load ~/.HUMANFILE if it exists → base identity + preferences
///   2. Load project .ostk/HUMANFILE if it exists
///   3. Verify GPG signatures for both tiers independently
///   4. If project has EXTENDS, merge over base (IDENTITY/SIGN immutable)
///   5. Graceful degradation at every step
pub fn load(ostk_dir: &Path) -> LoadResult {
    let mut warnings: Vec<String> = Vec::new();

    // 1. Try loading global HUMANFILE
    // Resolution order: HUMANFILE env → ~/.ostk/HUMANFILE → ~/.HUMANFILE
    let global_path = if let Ok(p) = std::env::var("HUMANFILE") {
        PathBuf::from(p)
    } else {
        let ostk_path = expand_home("~/.ostk/HUMANFILE");
        if ostk_path.is_file() {
            ostk_path
        } else {
            expand_home("~/.HUMANFILE")
        }
    };
    let global_loaded = global_path.is_file();
    let global = if global_loaded {
        match std::fs::read_to_string(&global_path) {
            Ok(content) => match parse(&content) {
                Ok(hf) => Some(hf),
                Err(e) => {
                    warnings.push(format!("~/.HUMANFILE parse error: {e}"));
                    None
                }
            },
            Err(e) => {
                warnings.push(format!("failed to read ~/.HUMANFILE: {e}"));
                None
            }
        }
    } else {
        None
    };
    let have_global = global.is_some();

    // 2. Verify global HUMANFILE GPG signature (T0)
    let global_trust = if global_loaded {
        let global_sig = global_path.with_extension("asc"); // .asc sibling of wherever we found it
        Some(verify_humanfile_signature_at(&global_path, &global_sig))
    } else {
        None
    };

    // 3. Try loading project .ostk/HUMANFILE
    //    Parse errors are silently ignored — the project may still use the
    //    legacy YAML-ish format, which boot.rs handles via fallback.
    let project_path = ostk_dir.join("HUMANFILE");
    let project_loaded = project_path.is_file();
    let project = if project_loaded {
        match std::fs::read_to_string(&project_path) {
            Ok(content) => parse(&content).ok(),
            Err(e) => {
                warnings.push(format!("failed to read project HUMANFILE: {e}"));
                None
            }
        }
    } else {
        None
    };

    // 4. Verify project HUMANFILE GPG signature (T1)
    let project_trust = if project_loaded {
        let project_sig = ostk_dir.join("HUMANFILE.asc");
        Some(verify_humanfile_signature_at(&project_path, &project_sig))
    } else {
        None
    };

    // 5. Merge based on what we have
    match (global, project) {
        (Some(g), Some(p)) => {
            // Both exist — merge (child over parent)
            let merged = merge(g, p);
            LoadResult {
                humanfile: merged,
                source: HumanfileSource::GlobalAndProject,
                warnings,
                global_trust,
                project_trust,
            }
        }
        (Some(g), None) => {
            // Global only — no project HUMANFILE
            LoadResult {
                humanfile: g,
                source: HumanfileSource::GlobalOnly,
                warnings,
                global_trust,
                project_trust,
            }
        }
        (None, Some(p)) => {
            // Project only — no ~/.HUMANFILE
            if !have_global {
                warnings.push(
                    "global HUMANFILE not found \u{2014} using project config only".to_string(),
                );
            }
            if p.extends.is_some() {
                warnings.push(
                    "EXTENDS declared but parent ~/.HUMANFILE missing \u{2014} using project config only"
                        .to_string(),
                );
            }
            LoadResult {
                humanfile: p,
                source: HumanfileSource::ProjectOnly,
                warnings,
                global_trust,
                project_trust,
            }
        }
        (None, None) => {
            // Neither exists — defaults
            LoadResult {
                humanfile: Humanfile::default(),
                source: HumanfileSource::Defaults,
                warnings,
                global_trust,
                project_trust,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// Mutex that serializes all tests which mutate the HUMANFILE env var.
    /// Without this, parallel test threads race on the process-global env,
    /// causing `load()` to see another test's HUMANFILE value mid-execution.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    fn minimal_humanfile() -> &'static str {
        "IDENTITY testuser\nSIGN AABB1122\n"
    }

    /// Run a test with HUMANFILE env var pointing to a nonexistent path,
    /// isolating from the real ~/.ostk/HUMANFILE or ~/.HUMANFILE.
    /// Holds ENV_LOCK for the duration to prevent parallel env var races.
    fn with_isolated_humanfile<F: FnOnce()>(f: F) {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let sentinel = "/tmp/ostk-test-no-humanfile-exists";
        // SAFETY: env var access is serialized by ENV_LOCK
        unsafe { std::env::set_var("HUMANFILE", sentinel) };
        f();
        unsafe { std::env::remove_var("HUMANFILE") };
    }

    #[test]
    fn load_from_project_humanfile() {
        with_isolated_humanfile(|| {
            let tmp = tempfile::tempdir().unwrap();
            let ostk_dir = tmp.path().join(".ostk");
            std::fs::create_dir_all(&ostk_dir).unwrap();
            std::fs::write(ostk_dir.join("HUMANFILE"), minimal_humanfile()).unwrap();

            let result = load(&ostk_dir);
            assert!(
                matches!(result.source, HumanfileSource::ProjectOnly),
                "should load from project: {:?}",
                result.source
            );
            assert_eq!(result.humanfile.identity, Some("testuser".into()));
        });
    }

    #[test]
    fn load_returns_defaults_when_no_humanfile() {
        with_isolated_humanfile(|| {
            let tmp = tempfile::tempdir().unwrap();
            let ostk_dir = tmp.path().join(".ostk");
            std::fs::create_dir_all(&ostk_dir).unwrap();

            let result = load(&ostk_dir);
            assert!(
                matches!(result.source, HumanfileSource::Defaults),
                "should return defaults when no HUMANFILE exists: {:?}",
                result.source
            );
        });
    }

    #[test]
    fn load_env_var_override() {
        // This test also mutates HUMANFILE — must hold the same lock.
        let _guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());

        let tmp = tempfile::tempdir().unwrap();
        let ostk_dir = tmp.path().join(".ostk");
        std::fs::create_dir_all(&ostk_dir).unwrap();

        let custom = tmp.path().join("custom-humanfile");
        std::fs::write(&custom, minimal_humanfile()).unwrap();

        // SAFETY: env var access is serialized by ENV_LOCK
        unsafe { std::env::set_var("HUMANFILE", &custom) };
        let result = load(&ostk_dir);
        unsafe { std::env::remove_var("HUMANFILE") };

        assert!(
            matches!(
                result.source,
                HumanfileSource::GlobalOnly | HumanfileSource::GlobalAndProject
            ),
            "HUMANFILE env var should be found as global: {:?}",
            result.source
        );
        assert_eq!(result.humanfile.identity, Some("testuser".into()));
    }

    #[test]
    fn load_handles_parse_errors_gracefully() {
        with_isolated_humanfile(|| {
            let tmp = tempfile::tempdir().unwrap();
            let ostk_dir = tmp.path().join(".ostk");
            std::fs::create_dir_all(&ostk_dir).unwrap();
            // FROM is an unknown directive — parser rejects it
            std::fs::write(ostk_dir.join("HUMANFILE"), "FROM invalid\n").unwrap();

            let result = load(&ostk_dir);
            assert!(
                matches!(result.source, HumanfileSource::Defaults),
                "parse error should fall back to defaults: {:?}",
                result.source
            );
        });
    }

    #[test]
    fn load_project_humanfile_has_identity() {
        with_isolated_humanfile(|| {
            let tmp = tempfile::tempdir().unwrap();
            let ostk_dir = tmp.path().join(".ostk");
            std::fs::create_dir_all(&ostk_dir).unwrap();
            std::fs::write(
                ostk_dir.join("HUMANFILE"),
                "IDENTITY alice\nSIGN AB12CD34\n",
            )
            .unwrap();

            let result = load(&ostk_dir);
            assert_eq!(result.humanfile.identity, Some("alice".into()));
        });
    }
}
