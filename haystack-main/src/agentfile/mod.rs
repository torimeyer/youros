pub mod parser;

pub use parser::{Agentfile, Interrupt, Limit, ParseError, PromptSource, WorkFilter, parse};

use std::path::{Path, PathBuf};

/// Resolve the default agent definition.
///
/// Search order: `./Agentfile` -> `.ostk/scheduler.af` (legacy).
/// Returns `None` when neither exists (caller should use sane defaults).
pub fn resolve_default_agentfile(root: &Path) -> Option<PathBuf> {
    let default = root.join("Agentfile");
    if default.exists() {
        return Some(default);
    }
    let legacy = crate::state_dir(root).join("scheduler.af");
    if legacy.exists() {
        return Some(legacy);
    }
    None
}

/// Resolve a named agent definition.
///
/// Search order:
/// 1. `./agents/<name>.af`
/// 2. `./<name>.af`
/// 3. `./<name>`
/// 4. `.ostk/<name>` (legacy)
/// 5. `.ostk/Agentfile.<name>` (legacy)
pub fn resolve_agentfile(root: &Path, name: &str) -> Option<PathBuf> {
    let candidates = [
        root.join("agents").join(format!("{name}.af")),
        root.join(format!("{name}.af")),
        root.join(name),
        crate::state_dir(root).join(name),
        crate::state_dir(root).join(format!("Agentfile.{name}")),
    ];
    candidates.into_iter().find(|p| p.exists())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    fn setup_project() -> TempDir {
        let tmp = TempDir::new().unwrap();
        fs::create_dir_all(tmp.path().join(".ostk")).unwrap();
        tmp
    }

    #[test]
    fn test_resolve_default_agentfile_root() {
        let tmp = setup_project();
        fs::write(tmp.path().join("Agentfile"), "FROM auto\n").unwrap();
        let result = resolve_default_agentfile(tmp.path());
        assert_eq!(result, Some(tmp.path().join("Agentfile")));
    }

    #[test]
    fn test_resolve_default_agentfile_legacy() {
        let tmp = setup_project();
        fs::write(tmp.path().join(".ostk/scheduler.af"), "FROM auto\n").unwrap();
        let result = resolve_default_agentfile(tmp.path());
        assert_eq!(result, Some(tmp.path().join(".ostk/scheduler.af")));
    }

    #[test]
    fn test_resolve_default_agentfile_prefers_root() {
        let tmp = setup_project();
        fs::write(tmp.path().join("Agentfile"), "FROM auto\n").unwrap();
        fs::write(tmp.path().join(".ostk/scheduler.af"), "FROM legacy\n").unwrap();
        let result = resolve_default_agentfile(tmp.path());
        assert_eq!(result, Some(tmp.path().join("Agentfile")));
    }

    #[test]
    fn test_resolve_default_agentfile_none() {
        let tmp = setup_project();
        let result = resolve_default_agentfile(tmp.path());
        assert_eq!(result, None);
    }

    #[test]
    fn test_resolve_named_agentfile_agents_dir() {
        let tmp = setup_project();
        fs::create_dir_all(tmp.path().join("agents")).unwrap();
        fs::write(tmp.path().join("agents/review.af"), "FROM auto\n").unwrap();
        let result = resolve_agentfile(tmp.path(), "review");
        assert_eq!(result, Some(tmp.path().join("agents/review.af")));
    }

    #[test]
    fn test_resolve_named_agentfile_root_af() {
        let tmp = setup_project();
        fs::write(tmp.path().join("review.af"), "FROM auto\n").unwrap();
        let result = resolve_agentfile(tmp.path(), "review");
        assert_eq!(result, Some(tmp.path().join("review.af")));
    }

    #[test]
    fn test_resolve_named_agentfile_bare_name() {
        let tmp = setup_project();
        fs::write(tmp.path().join("review"), "FROM auto\n").unwrap();
        let result = resolve_agentfile(tmp.path(), "review");
        assert_eq!(result, Some(tmp.path().join("review")));
    }

    #[test]
    fn test_resolve_named_agentfile_legacy_ostk() {
        let tmp = setup_project();
        fs::write(tmp.path().join(".ostk/review"), "FROM auto\n").unwrap();
        let result = resolve_agentfile(tmp.path(), "review");
        assert_eq!(result, Some(tmp.path().join(".ostk/review")));
    }

    #[test]
    fn test_resolve_named_agentfile_legacy_prefix() {
        let tmp = setup_project();
        fs::write(tmp.path().join(".ostk/Agentfile.review"), "FROM auto\n").unwrap();
        let result = resolve_agentfile(tmp.path(), "review");
        assert_eq!(result, Some(tmp.path().join(".ostk/Agentfile.review")));
    }

    #[test]
    fn test_resolve_named_agentfile_none() {
        let tmp = setup_project();
        let result = resolve_agentfile(tmp.path(), "nonexistent");
        assert_eq!(result, None);
    }

    #[test]
    fn test_resolve_named_agentfile_prefers_agents_dir() {
        let tmp = setup_project();
        fs::create_dir_all(tmp.path().join("agents")).unwrap();
        fs::write(tmp.path().join("agents/review.af"), "FROM agents\n").unwrap();
        fs::write(tmp.path().join(".ostk/Agentfile.review"), "FROM legacy\n").unwrap();
        let result = resolve_agentfile(tmp.path(), "review");
        assert_eq!(result, Some(tmp.path().join("agents/review.af")));
    }
}
