use std::path::{Path, PathBuf};

/// State directory name. Binary is ostk, state dir is .ostk.
pub const STATE_DIR: &str = ".ostk";

/// Resolve the state directory for a project root.
pub fn state_dir(root: &Path) -> PathBuf {
    root.join(STATE_DIR)
}

/// Normalize a spec path to a project-relative string.
/// Resolves relative to CWD, strips the project root prefix,
/// and returns a clean path like "docs/spec/foo.md".
pub fn normalize_spec_path(root: &Path, path: &str) -> String {
    let p = Path::new(path);
    let abs = if p.is_absolute() {
        PathBuf::from(path)
    } else {
        // Canonicalize CWD to resolve symlinks (e.g. /var -> /private/var
        // on macOS), matching how callers typically canonicalize root.
        let cwd = std::env::current_dir()
            .and_then(|c| c.canonicalize())
            .unwrap_or_else(|_| root.to_path_buf());
        cwd.join(path)
    };

    let normalized = normalize_path_components(&abs);
    let root_normalized = normalize_path_components(root);

    if let Ok(rel) = normalized.strip_prefix(&root_normalized) {
        rel.to_string_lossy().into_owned()
    } else {
        path.trim_start_matches("./").to_string()
    }
}

fn normalize_path_components(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for component in path.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                result.pop();
            }
            c => result.push(c),
        }
    }
    result
}

/// Walk up from CWD looking for .ostk/ directory
pub fn find_project_root() -> Result<PathBuf, String> {
    let mut dir = std::env::current_dir().map_err(|e| e.to_string())?;
    loop {
        if dir.join(STATE_DIR).is_dir() {
            return Ok(dir);
        }
        if !dir.pop() {
            return Err(format!("no {STATE_DIR}/ directory found in any parent"));
        }
    }
}
