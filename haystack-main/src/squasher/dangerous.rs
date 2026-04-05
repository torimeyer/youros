//! Dangerous handler for the squasher pipeline.
//!
//! Destructive commands (rm -rf, git push --force, dd, chmod 777, mkfs, DROP TABLE)
//! get a formatted warning. The actual execution gate (blocking) is NOT handled here
//! -- that's a higher-level policy decision. This module just formats the warning
//! that the gate would display.
//!
//! Ported from mish's `handlers/dangerous.rs` — warning formatting subset only.

// ---------------------------------------------------------------------------
// Severity levels
// ---------------------------------------------------------------------------

/// Severity of the dangerous command.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    /// Could cause irreversible system damage (rm -rf /, dd of=/dev/sda, mkfs)
    Critical,
    /// Could cause data loss but scoped to a project/directory (rm -rf dir/, git push --force)
    Warning,
}

impl std::fmt::Display for Severity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Severity::Critical => write!(f, "CRITICAL"),
            Severity::Warning => write!(f, "WARNING"),
        }
    }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Format a warning for a dangerous command.
///
/// Returns a multi-line warning string containing:
/// - Severity level (CRITICAL or WARNING)
/// - What the command does
/// - Why it's dangerous
/// - What it would affect
///
/// # Examples
///
/// ```
/// use ostk::squasher::dangerous::format_dangerous_warning;
///
/// let warning = format_dangerous_warning("rm -rf /tmp/build");
/// assert!(warning.contains("WARNING"));
/// assert!(warning.contains("rm -rf"));
/// ```
pub fn format_dangerous_warning(cmd: &str) -> String {
    let trimmed = cmd.trim();
    if trimmed.is_empty() {
        return String::new();
    }

    // Try each pattern detector in order; first match wins
    if let Some(w) = detect_rm_rf(trimmed) {
        return w;
    }
    if let Some(w) = detect_force_push(trimmed) {
        return w;
    }
    if let Some(w) = detect_dd(trimmed) {
        return w;
    }
    if let Some(w) = detect_chmod_777(trimmed) {
        return w;
    }
    if let Some(w) = detect_mkfs(trimmed) {
        return w;
    }
    if let Some(w) = detect_drop_table(trimmed) {
        return w;
    }
    if let Some(w) = detect_git_reset_hard(trimmed) {
        return w;
    }
    if let Some(w) = detect_git_clean(trimmed) {
        return w;
    }

    // Generic dangerous warning for unrecognized patterns
    format_warning(
        Severity::Warning,
        trimmed,
        "potentially destructive operation",
        "command may cause data loss",
        "unknown scope",
    )
}

// ---------------------------------------------------------------------------
// Pattern detectors
// ---------------------------------------------------------------------------

fn detect_rm_rf(cmd: &str) -> Option<String> {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    let mut i = 0;
    while i < tokens.len() {
        if tokens[i] == "rm" {
            let mut has_r = false;
            let mut has_f = false;
            let mut targets: Vec<&str> = Vec::new();

            for &tok in &tokens[i + 1..] {
                if tok == "&&" || tok == "||" || tok == ";" || tok == "|" {
                    break;
                }
                if tok.starts_with('-') && !tok.starts_with("--") {
                    if tok.contains('r') || tok.contains('R') { has_r = true; }
                    if tok.contains('f') { has_f = true; }
                } else if tok == "--recursive" {
                    has_r = true;
                } else if tok == "--force" {
                    has_f = true;
                } else if !tok.starts_with('-') {
                    targets.push(tok);
                }
            }

            if has_r && has_f {
                let target = targets.first().copied().unwrap_or("(unknown)");
                let severity = classify_rm_target(target);
                let affects = if targets.len() > 1 {
                    format!("{} paths starting with {}", targets.len(), target)
                } else {
                    target.to_string()
                };

                return Some(format_warning(
                    severity,
                    cmd,
                    "force recursive delete -- no confirmation, bypasses trash",
                    "permanently removes all files and directories recursively",
                    &affects,
                ));
            }
        }
        i += 1;
    }
    None
}

fn classify_rm_target(target: &str) -> Severity {
    // Root paths and system directories are CRITICAL
    if target == "/" || target == "/*" || target == "/." {
        return Severity::Critical;
    }
    // Common system paths
    if target.starts_with("/etc")
        || target.starts_with("/usr")
        || target.starts_with("/bin")
        || target.starts_with("/sbin")
        || target.starts_with("/var")
        || target.starts_with("/boot")
        || target.starts_with("/lib")
        || target.starts_with("/System")
        || target == "~"
        || target == "$HOME"
    {
        return Severity::Critical;
    }
    Severity::Warning
}

fn detect_force_push(cmd: &str) -> Option<String> {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    for window in tokens.windows(2) {
        if window[0] == "git" && window[1] == "push" {
            let push_idx = tokens.iter().position(|&t| t == "push").unwrap();
            let mut has_force = false;
            let mut remote = None;
            let mut branch = None;

            for &tok in &tokens[push_idx + 1..] {
                if tok == "&&" || tok == "||" || tok == ";" || tok == "|" {
                    break;
                }
                if tok == "--force" || tok == "--force-with-lease" || tok == "-f" {
                    has_force = true;
                } else if !tok.starts_with('-') {
                    if remote.is_none() {
                        remote = Some(tok);
                    } else if branch.is_none() {
                        branch = Some(tok);
                    }
                }
            }

            if has_force {
                let remote_name = remote.unwrap_or("origin");
                let branch_name = branch.unwrap_or("(current)");
                let severity = if branch_name == "main" || branch_name == "master" {
                    Severity::Critical
                } else {
                    Severity::Warning
                };

                return Some(format_warning(
                    severity,
                    cmd,
                    "force push -- overwrites remote history",
                    "rewrites commit history on the remote, other collaborators will lose work",
                    &format!("{remote_name}/{branch_name}"),
                ));
            }
        }
    }
    None
}

fn detect_dd(cmd: &str) -> Option<String> {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();

    // Check if dd is at command position
    let is_dd = tokens.first() == Some(&"dd")
        || tokens.iter().enumerate().any(|(pos, &tok)| {
            tok == "dd"
                && pos > 0
                && matches!(
                    tokens.get(pos - 1),
                    Some(&"&&") | Some(&"||") | Some(&";") | Some(&"|")
                )
        });

    if !is_dd {
        return None;
    }

    // Extract of= target
    let of_target = tokens
        .iter()
        .find(|t| t.starts_with("of="))
        .map(|t| &t[3..])
        .unwrap_or("(unknown)");

    let severity = if of_target.starts_with("/dev/") {
        Severity::Critical
    } else {
        Severity::Warning
    };

    Some(format_warning(
        severity,
        cmd,
        "direct disk write -- no safety checks, no undo",
        "writes raw bytes directly to the target, bypassing filesystem safeguards",
        of_target,
    ))
}

fn detect_chmod_777(cmd: &str) -> Option<String> {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();

    // Look for "chmod" followed by "777" anywhere after it (flags may be in between)
    let chmod_pos = tokens.iter().position(|&t| t == "chmod")?;
    let has_777 = tokens[chmod_pos + 1..].contains(&"777");

    if !has_777 {
        return None;
    }

    // Find what's being chmod'd (non-flag, non-chmod, non-777 tokens after chmod)
    let target = tokens[chmod_pos + 1..]
        .iter()
        .find(|t| !t.starts_with('-') && **t != "777")
        .copied()
        .unwrap_or("(unknown)");

    let recursive = tokens[chmod_pos + 1..].iter().any(|&t| t == "-R" || t == "--recursive");
    let severity = if recursive {
        Severity::Critical
    } else {
        Severity::Warning
    };

    Some(format_warning(
        severity,
        cmd,
        "world-writable permissions -- security vulnerability",
        "sets read/write/execute for all users, any process can modify these files",
        target,
    ))
}

fn detect_mkfs(cmd: &str) -> Option<String> {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    let mkfs_token = tokens.iter().find(|t| **t == "mkfs" || t.starts_with("mkfs."));
    if let Some(&mkfs) = mkfs_token {
        let device = tokens.iter().find(|t| t.starts_with("/dev/"))
            .copied()
            .unwrap_or("(unknown device)");

        return Some(format_warning(
            Severity::Critical,
            cmd,
            &format!("filesystem creation ({mkfs}) -- destroys all existing data"),
            "overwrites the partition table and all data on the device",
            device,
        ));
    }
    None
}

fn detect_drop_table(cmd: &str) -> Option<String> {
    let upper = cmd.to_uppercase();
    if let Some(pos) = upper.find("DROP TABLE") {
        // Try to extract table name
        let after = &cmd[pos + 10..].trim_start();
        let table_name = after.split_whitespace()
            .next()
            .unwrap_or("(unknown)");

        return Some(format_warning(
            Severity::Critical,
            cmd,
            "DROP TABLE -- permanently destroys database table",
            "removes the table and all its data from the database with no undo",
            table_name,
        ));
    }
    None
}

fn detect_git_reset_hard(cmd: &str) -> Option<String> {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    for i in 0..tokens.len().saturating_sub(2) {
        if tokens[i] == "git" && tokens[i + 1] == "reset"
            && tokens[i + 2..].contains(&"--hard") {
                return Some(format_warning(
                    Severity::Warning,
                    cmd,
                    "git reset --hard -- discards uncommitted changes",
                    "all uncommitted modifications and staged changes are permanently lost",
                    "working tree + staging area",
                ));
            }
    }
    None
}

fn detect_git_clean(cmd: &str) -> Option<String> {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    for i in 0..tokens.len().saturating_sub(2) {
        if tokens[i] == "git" && tokens[i + 1] == "clean"
            && tokens[i + 2..].iter().any(|&t| t == "-f" || t == "--force" || t.contains('f')) {
                return Some(format_warning(
                    Severity::Warning,
                    cmd,
                    "git clean -f -- removes untracked files",
                    "permanently deletes files not tracked by git",
                    "untracked files in working tree",
                ));
            }
    }
    None
}

// ---------------------------------------------------------------------------
// Warning formatting
// ---------------------------------------------------------------------------

fn format_warning(
    severity: Severity,
    cmd: &str,
    what: &str,
    why: &str,
    affects: &str,
) -> String {
    format!(
        "\u{26a0} [{severity}] {cmd}\n  what: {what}\n  why:  {why}\n  affects: {affects}"
    )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // -----------------------------------------------------------------------
    // rm -rf tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_rm_rf_root_is_critical() {
        let w = format_dangerous_warning("rm -rf /");
        assert!(w.contains("CRITICAL"));
        assert!(w.contains("rm -rf /"));
        assert!(w.contains("force recursive delete"));
    }

    #[test]
    fn test_rm_rf_tmp_is_warning() {
        let w = format_dangerous_warning("rm -rf /tmp/build");
        assert!(w.contains("WARNING"));
        assert!(w.contains("force recursive delete"));
        assert!(w.contains("/tmp/build"));
    }

    #[test]
    fn test_rm_rf_system_path_is_critical() {
        let w = format_dangerous_warning("rm -rf /usr/local");
        assert!(w.contains("CRITICAL"));
    }

    #[test]
    fn test_rm_rf_combined_flags() {
        let w = format_dangerous_warning("rm -fr /tmp/foo");
        assert!(w.contains("WARNING"));
        assert!(w.contains("force recursive delete"));
    }

    #[test]
    fn test_rm_rf_separate_flags() {
        let w = format_dangerous_warning("rm -r -f /tmp/foo");
        assert!(w.contains("force recursive delete"));
    }

    // -----------------------------------------------------------------------
    // git push --force tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_force_push_main_is_critical() {
        let w = format_dangerous_warning("git push origin main --force");
        assert!(w.contains("CRITICAL"));
        assert!(w.contains("force push"));
        assert!(w.contains("origin/main"));
    }

    #[test]
    fn test_force_push_feature_is_warning() {
        let w = format_dangerous_warning("git push origin feature-branch --force");
        assert!(w.contains("WARNING"));
        assert!(w.contains("force push"));
    }

    #[test]
    fn test_force_push_short_flag() {
        let w = format_dangerous_warning("git push -f");
        assert!(w.contains("force push"));
    }

    #[test]
    fn test_force_with_lease() {
        let w = format_dangerous_warning("git push --force-with-lease");
        assert!(w.contains("force push"));
    }

    // -----------------------------------------------------------------------
    // dd tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_dd_dev_is_critical() {
        let w = format_dangerous_warning("dd if=/dev/zero of=/dev/sda");
        assert!(w.contains("CRITICAL"));
        assert!(w.contains("direct disk write"));
        assert!(w.contains("/dev/sda"));
    }

    #[test]
    fn test_dd_file_is_warning() {
        let w = format_dangerous_warning("dd if=/dev/zero of=disk.img bs=1M count=100");
        assert!(w.contains("WARNING"));
        assert!(w.contains("direct disk write"));
    }

    // -----------------------------------------------------------------------
    // chmod 777 tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_chmod_777_is_warning() {
        let w = format_dangerous_warning("chmod 777 /etc/passwd");
        assert!(w.contains("WARNING"));
        assert!(w.contains("world-writable"));
    }

    #[test]
    fn test_chmod_777_recursive_is_critical() {
        let w = format_dangerous_warning("chmod -R 777 /var/www");
        assert!(w.contains("CRITICAL"));
        assert!(w.contains("world-writable"));
    }

    // -----------------------------------------------------------------------
    // mkfs tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_mkfs_is_critical() {
        let w = format_dangerous_warning("mkfs.ext4 /dev/sda1");
        assert!(w.contains("CRITICAL"));
        assert!(w.contains("filesystem creation"));
        assert!(w.contains("/dev/sda1"));
    }

    // -----------------------------------------------------------------------
    // DROP TABLE tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_drop_table_is_critical() {
        let w = format_dangerous_warning("mysql -e 'DROP TABLE users'");
        assert!(w.contains("CRITICAL"));
        assert!(w.contains("DROP TABLE"));
    }

    // -----------------------------------------------------------------------
    // git reset --hard tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_git_reset_hard() {
        let w = format_dangerous_warning("git reset --hard HEAD~3");
        assert!(w.contains("WARNING"));
        assert!(w.contains("discards uncommitted"));
    }

    // -----------------------------------------------------------------------
    // git clean tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_git_clean_f() {
        let w = format_dangerous_warning("git clean -f");
        assert!(w.contains("WARNING"));
        assert!(w.contains("removes untracked"));
    }

    // -----------------------------------------------------------------------
    // Edge cases
    // -----------------------------------------------------------------------

    #[test]
    fn test_empty_command() {
        let w = format_dangerous_warning("");
        assert!(w.is_empty());
    }

    #[test]
    fn test_non_dangerous_returns_generic() {
        let w = format_dangerous_warning("ls -la");
        assert!(w.contains("WARNING"));
        assert!(w.contains("potentially destructive"));
    }

    #[test]
    fn test_severity_display() {
        assert_eq!(format!("{}", Severity::Critical), "CRITICAL");
        assert_eq!(format!("{}", Severity::Warning), "WARNING");
    }
}
