//! Error enrichment for failed commands.
//!
//! Pre-fetches diagnostics the LLM would request next: path walks, stat,
//! permissions. Ported from mish's `core::enrich` — simplified to the 80/20:
//! path component walking + file stat + permission checks.
//!
//! Budget: <100ms total, read-only, no network, just filesystem stat calls.

use std::os::unix::fs::MetadataExt;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

// ---------------------------------------------------------------------------
// Budget tracker
// ---------------------------------------------------------------------------

struct Budget {
    start: Instant,
    limit: Duration,
}

impl Budget {
    fn new(limit_ms: u64) -> Self {
        Budget {
            start: Instant::now(),
            limit: Duration::from_millis(limit_ms),
        }
    }

    fn exhausted(&self) -> bool {
        self.start.elapsed() >= self.limit
    }
}

// ---------------------------------------------------------------------------
// Intent detection
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CommandIntent {
    FileOp,
    DirOp,
    Other,
}

fn detect_intent(command: &str) -> CommandIntent {
    let cmd = command
        .split_whitespace()
        .next()
        .unwrap_or("")
        .rsplit('/')
        .next()
        .unwrap_or("");
    match cmd {
        "cp" | "mv" | "ln" | "rm" | "touch" | "chmod" | "chown" | "cat" | "head" | "tail" => {
            CommandIntent::FileOp
        }
        "mkdir" | "rmdir" | "ls" | "cd" => CommandIntent::DirOp,
        _ => CommandIntent::Other,
    }
}

// ---------------------------------------------------------------------------
// Path extraction from command + output
// ---------------------------------------------------------------------------

/// Extract filesystem paths from command arguments and output text.
fn extract_paths(command: &str, output: &str) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    let mut seen = std::collections::HashSet::new();

    // 1. Non-flag arguments from the command (skip argv[0])
    for arg in command.split_whitespace().skip(1) {
        if arg.starts_with('-') {
            continue;
        }
        // Heuristic: looks like a path if it contains '/' or '.' or starts with '~'
        if arg.contains('/') || arg.contains('.') || arg.starts_with('~') {
            let p = PathBuf::from(arg);
            if seen.insert(p.clone()) {
                paths.push(p);
            }
        }
    }

    // 2. Paths mentioned in output (common error patterns)
    for line in output.lines() {
        // "No such file or directory: /some/path"
        // "cannot access '/some/path'"
        // "Permission denied: /some/path"
        for pattern in &[
            "No such file or directory",
            "cannot access",
            "Permission denied",
            "not found",
        ] {
            if let Some(pos) = line.find(pattern) {
                let rest = &line[pos + pattern.len()..];
                if let Some(path_str) = extract_path_from_fragment(rest) {
                    let p = PathBuf::from(path_str);
                    if seen.insert(p.clone()) {
                        paths.push(p);
                    }
                }
            }
        }
    }

    paths
}

/// Try to extract a path from a fragment like ": /foo/bar" or " '/foo/bar'".
fn extract_path_from_fragment(fragment: &str) -> Option<&str> {
    let trimmed = fragment.trim_start_matches([':', ' ', '\'', '"']);
    let end = trimmed
        .find(['\'', '"', '\n', '\r'])
        .unwrap_or(trimmed.len());
    let candidate = trimmed[..end].trim();
    if candidate.is_empty() {
        return None;
    }
    // Must look like a path (contains / or .)
    if candidate.contains('/') || candidate.contains('.') {
        Some(candidate)
    } else {
        None
    }
}

// ---------------------------------------------------------------------------
// Path walking — find where a path breaks
// ---------------------------------------------------------------------------

/// Walk a path component by component, finding the break point.
fn path_walk(path: &Path) -> Option<PathDiagnosis> {
    let mut accumulated = PathBuf::new();
    let mut last_valid = PathBuf::new();

    if path.is_absolute() {
        accumulated.push("/");
        last_valid.push("/");
    }

    for component in path.components() {
        match component {
            std::path::Component::RootDir => continue,
            _ => {
                accumulated.push(component);
                if accumulated.exists() {
                    last_valid = accumulated.clone();
                } else {
                    let siblings = list_siblings(&last_valid);
                    return Some(PathDiagnosis {
                        requested: path.to_path_buf(),
                        last_valid,
                        breaks_at: accumulated,
                        siblings,
                    });
                }
            }
        }
    }

    // Entire path is valid
    None
}

struct PathDiagnosis {
    requested: PathBuf,
    last_valid: PathBuf,
    breaks_at: PathBuf,
    siblings: Vec<String>,
}

/// List children of a directory for sibling hints (max 20).
fn list_siblings(dir: &Path) -> Vec<String> {
    let mut result = Vec::new();
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten().take(20) {
            let name = entry.file_name().to_string_lossy().to_string();
            if entry.path().is_dir() {
                result.push(format!("{name}/"));
            } else {
                result.push(name);
            }
        }
    }
    result.sort();
    result
}

// ---------------------------------------------------------------------------
// Permission diagnosis
// ---------------------------------------------------------------------------

fn diagnose_permissions(path: &Path) -> Option<String> {
    let meta = std::fs::metadata(path).ok()?;
    let mode = meta.permissions().mode();
    let file_uid = meta.uid();
    let file_gid = meta.gid();

    let running_uid = unsafe { libc::getuid() };
    let running_gid = unsafe { libc::getgid() };

    let owner = resolve_username(running_uid);
    let file_owner = resolve_username(file_uid);
    let file_group = resolve_groupname(file_gid);

    let is_owner = running_uid == file_uid;
    let is_group = running_gid == file_gid;

    let issue = if is_owner {
        if mode & 0o200 == 0 {
            "owner lacks write permission"
        } else if mode & 0o100 == 0 && meta.is_dir() {
            "owner lacks execute (traverse) permission"
        } else {
            return None; // permissions look fine
        }
    } else if is_group {
        if mode & 0o020 == 0 {
            "group lacks write permission"
        } else if mode & 0o010 == 0 && meta.is_dir() {
            "group lacks execute (traverse) permission"
        } else {
            return None;
        }
    } else if mode & 0o002 == 0 {
        "others lack write permission"
    } else if mode & 0o001 == 0 && meta.is_dir() {
        "others lack execute (traverse) permission"
    } else {
        return None;
    };

    Some(format!(
        "  permissions: {} (mode {:04o}, {file_owner}:{file_group}, running as {owner}) \u{2014} {issue}",
        path.display(),
        mode & 0o7777,
    ))
}

/// Resolve a UID to a username via libc (fast, no subprocess).
fn resolve_username(uid: u32) -> String {
    use std::ffi::CStr;
    use std::mem::MaybeUninit;

    let mut buf = vec![0u8; 1024];
    let mut pwd = MaybeUninit::<libc::passwd>::uninit();
    let mut result: *mut libc::passwd = std::ptr::null_mut();

    let ret = unsafe {
        libc::getpwuid_r(
            uid,
            pwd.as_mut_ptr(),
            buf.as_mut_ptr() as *mut libc::c_char,
            buf.len(),
            &mut result,
        )
    };

    if ret == 0 && !result.is_null() {
        let pwd = unsafe { pwd.assume_init() };
        let name = unsafe { CStr::from_ptr(pwd.pw_name) };
        name.to_string_lossy().to_string()
    } else {
        uid.to_string()
    }
}

/// Resolve a GID to a group name via libc (fast, no subprocess).
fn resolve_groupname(gid: u32) -> String {
    use std::ffi::CStr;
    use std::mem::MaybeUninit;

    let mut buf = vec![0u8; 1024];
    let mut grp = MaybeUninit::<libc::group>::uninit();
    let mut result: *mut libc::group = std::ptr::null_mut();

    let ret = unsafe {
        libc::getgrgid_r(
            gid,
            grp.as_mut_ptr(),
            buf.as_mut_ptr() as *mut libc::c_char,
            buf.len(),
            &mut result,
        )
    };

    if ret == 0 && !result.is_null() {
        let grp = unsafe { grp.assume_init() };
        let name = unsafe { CStr::from_ptr(grp.gr_name) };
        name.to_string_lossy().to_string()
    } else {
        gid.to_string()
    }
}

// ---------------------------------------------------------------------------
// Exit code mapping
// ---------------------------------------------------------------------------

fn map_exit_code(exit_code: i32) -> Option<&'static str> {
    match exit_code {
        126 => Some("permission denied (exit 126)"),
        127 => Some("command not found (exit 127)"),
        130 => Some("interrupted by user (SIGINT)"),
        137 => Some("killed by signal 9 (likely OOM)"),
        139 => Some("segmentation fault (SIGSEGV)"),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// File info helper
// ---------------------------------------------------------------------------

fn file_info(path: &Path) -> String {
    match std::fs::metadata(path) {
        Ok(meta) => {
            let kind = if meta.is_dir() {
                "dir"
            } else if meta.is_symlink() {
                "symlink"
            } else {
                "file"
            };
            let size = meta.len();
            let mode = meta.permissions().mode() & 0o7777;
            format!("{} ({kind}, {size}B, {:04o})", path.display(), mode)
        }
        Err(_) => format!("{} (not found)", path.display()),
    }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Enrich a failed command with diagnostic lines.
///
/// Returns diagnostic lines to append to squasher output. Only call when
/// `exit_code != 0`. Read-only, no network, budget-capped at 100ms.
pub fn enrich_on_failure(command: &str, output: &str, exit_code: i32) -> Vec<String> {
    let budget = Budget::new(100);
    let mut diagnostics: Vec<String> = Vec::new();

    // 1. Exit code mapping (instant)
    if let Some(desc) = map_exit_code(exit_code) {
        diagnostics.push(format!("  signal: {desc}"));
    }

    // Early exit for signals where filesystem diagnosis won't help
    if exit_code == 130 || exit_code == 137 || exit_code == 139 {
        return diagnostics;
    }

    // 2. Extract paths from command and output
    let paths = extract_paths(command, output);
    if paths.is_empty() && exit_code != 127 {
        return diagnostics;
    }

    let intent = detect_intent(command);

    // 3. Path walking — find where paths break
    for path in &paths {
        if budget.exhausted() {
            break;
        }
        if let Some(diag) = path_walk(path) {
            diagnostics.push(format!(
                "  path: {} exists, {} does not exist",
                diag.last_valid.display(),
                diag.breaks_at.display(),
            ));
            if !diag.siblings.is_empty() {
                diagnostics.push(format!(
                    "  nearest: {} contains: {}",
                    diag.last_valid.display(),
                    diag.siblings.join(", "),
                ));
            }
            // Also note the originally requested path if different from breaks_at
            if diag.requested != diag.breaks_at {
                diagnostics.push(format!(
                    "  requested: {}",
                    diag.requested.display(),
                ));
            }
        }
    }

    // 4. Permission checks for file operations
    if (intent == CommandIntent::FileOp || intent == CommandIntent::DirOp || exit_code == 126)
        && !budget.exhausted()
    {
        for path in &paths {
            if budget.exhausted() {
                break;
            }
            if let Some(line) = diagnose_permissions(path) {
                diagnostics.push(line);
            }
        }
    }

    // 5. For file ops with source + dest, report existence
    if intent == CommandIntent::FileOp && paths.len() >= 2 && !budget.exhausted() {
        let src = &paths[0];
        let dst = &paths[paths.len() - 1];
        let src_mark = if src.exists() { "\u{2713}" } else { "\u{2717}" };
        let dst_mark = if dst.exists() { "\u{2713}" } else { "\u{2717}" };
        diagnostics.push(format!("  source: {} {src_mark}", file_info(src)));
        diagnostics.push(format!("  dest: {} {dst_mark}", file_info(dst)));

        // Check dest parent
        if let Some(parent) = dst.parent()
            && !parent.as_os_str().is_empty() && !budget.exhausted() {
                let parent_mark = if parent.exists() { "\u{2713}" } else { "\u{2717}" };
                diagnostics.push(format!(
                    "  dest_parent: {} {parent_mark}",
                    file_info(parent)
                ));
            }
    }

    diagnostics
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn test_path_walk_finds_break_point() {
        let dir = TempDir::new().unwrap();
        let existing = dir.path().join("a");
        fs::create_dir(&existing).unwrap();

        let deep_path = existing.join("b").join("c");
        let diag = path_walk(&deep_path).unwrap();

        assert_eq!(diag.last_valid, existing);
        assert_eq!(diag.breaks_at, existing.join("b"));
        assert_eq!(diag.requested, deep_path);
    }

    #[test]
    fn test_path_walk_fully_valid() {
        let dir = TempDir::new().unwrap();
        let result = path_walk(dir.path());
        assert!(result.is_none(), "fully valid path should return None");
    }

    #[test]
    fn test_extract_paths_from_command() {
        let paths = extract_paths("cp /tmp/source.txt /tmp/dest/file.txt", "");
        assert_eq!(paths.len(), 2);
        assert_eq!(paths[0], PathBuf::from("/tmp/source.txt"));
        assert_eq!(paths[1], PathBuf::from("/tmp/dest/file.txt"));
    }

    #[test]
    fn test_extract_paths_skips_flags() {
        let paths = extract_paths("cp -r --verbose /tmp/src /tmp/dst", "");
        assert_eq!(paths.len(), 2);
        assert_eq!(paths[0], PathBuf::from("/tmp/src"));
        assert_eq!(paths[1], PathBuf::from("/tmp/dst"));
    }

    #[test]
    fn test_extract_paths_from_output() {
        let output = "cp: cannot access '/tmp/nonexistent/file.txt': No such file or directory";
        let paths = extract_paths("cp foo bar", output);
        // Should pick up /tmp/nonexistent/file.txt from the error message
        assert!(paths.iter().any(|p| p.to_string_lossy().contains("nonexistent")));
    }

    #[test]
    fn test_enrich_on_failure_nonexistent_path() {
        let dir = TempDir::new().unwrap();
        let missing = dir.path().join("no").join("such").join("path.txt");
        let cmd = format!("cat {}", missing.display());

        let lines = enrich_on_failure(&cmd, "No such file or directory", 1);

        // Should have path diagnosis
        assert!(!lines.is_empty(), "should produce diagnostics for missing path");
        assert!(
            lines.iter().any(|l| l.contains("does not exist")),
            "should report path break: {lines:?}"
        );
    }

    #[test]
    fn test_enrich_on_failure_exit_127() {
        let lines = enrich_on_failure("nonexistent_cmd", "command not found", 127);
        assert!(
            lines.iter().any(|l| l.contains("command not found")),
            "should report exit 127: {lines:?}"
        );
    }

    #[test]
    fn test_enrich_on_failure_sigint_minimal() {
        let lines = enrich_on_failure("sleep 100", "", 130);
        // SIGINT: should have at most the signal line, no path diagnosis
        assert!(lines.len() <= 1);
        if !lines.is_empty() {
            assert!(lines[0].contains("SIGINT"));
        }
    }

    #[test]
    fn test_enrich_on_failure_file_op_with_source_dest() {
        let dir = TempDir::new().unwrap();
        let src = dir.path().join("source.txt");
        fs::write(&src, "hello").unwrap();
        let dst = dir.path().join("nonexistent_dir").join("dest.txt");

        let cmd = format!("cp {} {}", src.display(), dst.display());
        let lines = enrich_on_failure(&cmd, "No such file or directory", 1);

        // Should have source/dest info
        assert!(
            lines.iter().any(|l| l.contains("source:") && l.contains("\u{2713}")),
            "should report source exists: {lines:?}"
        );
        assert!(
            lines.iter().any(|l| l.contains("dest:") && l.contains("\u{2717}")),
            "should report dest missing: {lines:?}"
        );
    }

    #[test]
    fn test_enrich_on_failure_permission_check() {
        let dir = TempDir::new().unwrap();
        let file = dir.path().join("readonly.txt");
        fs::write(&file, "test").unwrap();

        // Make read-only
        let mut perms = fs::metadata(&file).unwrap().permissions();
        perms.set_mode(0o444);
        fs::set_permissions(&file, perms).unwrap();

        let cmd = format!("rm {}", file.display());
        let lines = enrich_on_failure(&cmd, "Permission denied", 1);

        // rm is a FileOp, so permissions should be checked
        // The file exists but is read-only — permission diagnosis should fire
        assert!(
            lines.iter().any(|l| l.contains("permissions")),
            "should report permission issue: {lines:?}"
        );
    }

    #[test]
    fn test_budget_exhaustion() {
        let budget = Budget::new(0); // 0ms budget = immediately exhausted
        assert!(budget.exhausted());
    }

    #[test]
    fn test_exit_code_mapping() {
        assert!(map_exit_code(126).unwrap().contains("permission denied"));
        assert!(map_exit_code(127).unwrap().contains("command not found"));
        assert!(map_exit_code(130).unwrap().contains("SIGINT"));
        assert!(map_exit_code(137).unwrap().contains("OOM"));
        assert!(map_exit_code(139).unwrap().contains("SIGSEGV"));
        assert!(map_exit_code(1).is_none());
        assert!(map_exit_code(0).is_none());
    }

    #[test]
    fn test_detect_intent() {
        assert_eq!(detect_intent("cp a b"), CommandIntent::FileOp);
        assert_eq!(detect_intent("mv a b"), CommandIntent::FileOp);
        assert_eq!(detect_intent("rm file"), CommandIntent::FileOp);
        assert_eq!(detect_intent("chmod 755 file"), CommandIntent::FileOp);
        assert_eq!(detect_intent("mkdir dir"), CommandIntent::DirOp);
        assert_eq!(detect_intent("ls dir"), CommandIntent::DirOp);
        assert_eq!(detect_intent("cargo build"), CommandIntent::Other);
    }

    #[test]
    fn test_siblings_listed() {
        let dir = TempDir::new().unwrap();
        fs::create_dir(dir.path().join("aaa")).unwrap();
        fs::write(dir.path().join("bbb.txt"), "").unwrap();

        let siblings = list_siblings(dir.path());
        assert!(siblings.contains(&"aaa/".to_string()));
        assert!(siblings.contains(&"bbb.txt".to_string()));
    }
}
