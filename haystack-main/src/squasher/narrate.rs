//! Narrate handler for the squasher pipeline.
//!
//! Silent commands (cp, mv, mkdir, rm, chmod, chown, ln, touch, rmdir) produce
//! little or no stdout. The narrate handler generates a short informative line
//! about what happened by parsing the command arguments.
//!
//! Ported from mish's `handlers/narrate.rs` — pipeline-callable subset only
//! (no process execution, no file stat gathering).

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Generate a narration line for a silent command.
///
/// Parses the command string to extract the command name and arguments,
/// then generates a human-readable narration based on the exit code.
///
/// For successful commands: "copied foo -> bar" / "created directory baz"
/// For failed commands: includes the stderr message.
///
/// # Examples
///
/// ```
/// use ostk::squasher::narrate::narrate_output;
///
/// assert_eq!(narrate_output("cp a.txt b.txt", 0, ""), "\u{2192} cp: a.txt \u{2192} b.txt (ok)");
/// assert_eq!(narrate_output("mkdir -p src/foo", 0, ""), "\u{2192} mkdir: src/foo (nested, created)");
/// ```
pub fn narrate_output(cmd: &str, exit_code: i32, stderr: &str) -> String {
    let tokens: Vec<&str> = cmd.split_whitespace().collect();
    if tokens.is_empty() {
        return "\u{2192} (empty command)".to_string();
    }

    // Extract command name (handle path-qualified commands)
    let cmd_name = tokens[0].rsplit('/').next().unwrap_or(tokens[0]);
    let args: Vec<String> = tokens[1..].iter().map(|s| s.to_string()).collect();

    // Failed commands: include stderr
    if exit_code != 0 {
        return narrate_failure(cmd_name, &args, exit_code, stderr);
    }

    match cmd_name {
        "cp" => narrate_cp(&args),
        "mv" => narrate_mv(&args),
        "rm" => narrate_rm(&args),
        "mkdir" => narrate_mkdir(&args),
        "chmod" => narrate_chmod(&args),
        "chown" => narrate_chown(&args),
        "ln" => narrate_ln(&args),
        "touch" => narrate_touch(&args),
        "rmdir" => narrate_rmdir(&args),
        _ => narrate_generic(cmd_name, &args),
    }
}

// ---------------------------------------------------------------------------
// Per-command narrators (success path)
// ---------------------------------------------------------------------------

fn narrate_cp(args: &[String]) -> String {
    let (source, dest) = paths_from_args(args);
    let recursive = has_flag(args, 'r') || has_flag(args, 'R') || has_long_flag(args, "--recursive");

    if recursive {
        format!("\u{2192} cp: {source} \u{2192} {dest} (recursive, ok)")
    } else {
        format!("\u{2192} cp: {source} \u{2192} {dest} (ok)")
    }
}

fn narrate_mv(args: &[String]) -> String {
    let (source, dest) = paths_from_args(args);
    format!("\u{2192} mv: {source} \u{2192} {dest} (moved)")
}

fn narrate_rm(args: &[String]) -> String {
    let targets = non_flag_args(args);
    let count = targets.len();
    let target = if count == 1 {
        targets[0].clone()
    } else if count > 1 {
        format!("{} files", count)
    } else {
        "?".to_string()
    };

    let recursive = has_flag(args, 'r') || has_flag(args, 'R');
    let is_dir = has_long_flag(args, "-d") || has_long_flag(args, "--dir");

    if recursive {
        format!("\u{2192} rm: {target} (recursive, removed)")
    } else if is_dir {
        format!("\u{2192} rm: {target} (directory, removed)")
    } else {
        format!("\u{2192} rm: {target} (removed)")
    }
}

fn narrate_mkdir(args: &[String]) -> String {
    let targets = non_flag_args(args);
    let target = targets.first().map(|s| s.as_str()).unwrap_or("?");
    let nested = has_long_flag(args, "-p") || has_long_flag(args, "--parents");

    if nested {
        format!("\u{2192} mkdir: {target} (nested, created)")
    } else {
        format!("\u{2192} mkdir: {target} (created)")
    }
}

fn narrate_chmod(args: &[String]) -> String {
    let non_flags = non_flag_args(args);
    let (mode, target) = if non_flags.len() >= 2 {
        (non_flags[0].as_str(), non_flags[1].as_str())
    } else {
        ("?", non_flags.first().map(|s| s.as_str()).unwrap_or("?"))
    };

    format!("\u{2192} chmod: {target} \u{2192} {mode}")
}

fn narrate_chown(args: &[String]) -> String {
    let non_flags = non_flag_args(args);
    let (owner, target) = if non_flags.len() >= 2 {
        (non_flags[0].as_str(), non_flags[1].as_str())
    } else {
        ("?", non_flags.first().map(|s| s.as_str()).unwrap_or("?"))
    };

    format!("\u{2192} chown: {target} \u{2192} {owner}")
}

fn narrate_ln(args: &[String]) -> String {
    let (source, dest) = paths_from_args(args);
    let symlink = has_long_flag(args, "-s") || has_long_flag(args, "--symbolic");

    if symlink {
        format!("\u{2192} ln: {dest} \u{2192} {source} (symlink)")
    } else {
        format!("\u{2192} ln: {dest} \u{2192} {source} (hard link)")
    }
}

fn narrate_touch(args: &[String]) -> String {
    let targets = non_flag_args(args);
    let target = targets.first().map(|s| s.as_str()).unwrap_or("?");
    format!("\u{2192} touch: {target} (ok)")
}

fn narrate_rmdir(args: &[String]) -> String {
    let targets = non_flag_args(args);
    let target = targets.first().map(|s| s.as_str()).unwrap_or("?");
    format!("\u{2192} rmdir: {target} (removed)")
}

fn narrate_generic(command: &str, args: &[String]) -> String {
    let targets = non_flag_args(args);
    let target = targets.first().map(|s| s.as_str()).unwrap_or("");

    if target.is_empty() {
        format!("\u{2192} {command}: ok")
    } else {
        format!("\u{2192} {command}: {target} (ok)")
    }
}

// ---------------------------------------------------------------------------
// Failure narration
// ---------------------------------------------------------------------------

fn narrate_failure(cmd_name: &str, args: &[String], exit_code: i32, stderr: &str) -> String {
    let targets = non_flag_args(args);
    let target = targets.first().map(|s| s.as_str()).unwrap_or("");

    let stderr_snippet = if stderr.is_empty() {
        String::new()
    } else {
        // Take first non-empty line of stderr as the reason
        let first_line = stderr.lines()
            .find(|l| !l.trim().is_empty())
            .unwrap_or(stderr.trim());
        let truncated = crate::util::safe_truncate(first_line, 120);
        format!(" -- {truncated}")
    };

    if target.is_empty() {
        format!("! {cmd_name}: failed (exit {exit_code}){stderr_snippet}")
    } else {
        format!("! {cmd_name}: {target} -- failed (exit {exit_code}){stderr_snippet}")
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Filter args to only non-flag arguments (those not starting with `-`).
fn non_flag_args(args: &[String]) -> Vec<String> {
    args.iter()
        .filter(|a| !a.starts_with('-'))
        .cloned()
        .collect()
}

/// Check if any combined flag arg contains a specific short flag character.
fn has_flag(args: &[String], flag: char) -> bool {
    args.iter().any(|a| a.starts_with('-') && !a.starts_with("--") && a.contains(flag))
}

/// Check if a specific long flag is present.
fn has_long_flag(args: &[String], flag: &str) -> bool {
    args.iter().any(|a| a == flag)
}

/// Extract the last two non-flag arguments as (source, dest) for display.
fn paths_from_args(args: &[String]) -> (String, String) {
    let non_flags = non_flag_args(args);
    if non_flags.len() >= 2 {
        (
            non_flags[non_flags.len() - 2].clone(),
            non_flags[non_flags.len() - 1].clone(),
        )
    } else if non_flags.len() == 1 {
        (non_flags[0].clone(), "?".to_string())
    } else {
        ("?".to_string(), "?".to_string())
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // -----------------------------------------------------------------------
    // Success narrations
    // -----------------------------------------------------------------------

    #[test]
    fn test_narrate_cp() {
        let result = narrate_output("cp src.txt dst.txt", 0, "");
        assert_eq!(result, "\u{2192} cp: src.txt \u{2192} dst.txt (ok)");
    }

    #[test]
    fn test_narrate_cp_recursive() {
        let result = narrate_output("cp -r src/ dst/", 0, "");
        assert!(result.contains("recursive"));
        assert!(result.contains("ok"));
    }

    #[test]
    fn test_narrate_mv() {
        let result = narrate_output("mv old.txt new.txt", 0, "");
        assert_eq!(result, "\u{2192} mv: old.txt \u{2192} new.txt (moved)");
    }

    #[test]
    fn test_narrate_rm_single() {
        let result = narrate_output("rm file.txt", 0, "");
        assert_eq!(result, "\u{2192} rm: file.txt (removed)");
    }

    #[test]
    fn test_narrate_rm_multiple() {
        let result = narrate_output("rm a.txt b.txt c.txt", 0, "");
        assert_eq!(result, "\u{2192} rm: 3 files (removed)");
    }

    #[test]
    fn test_narrate_rm_recursive() {
        let result = narrate_output("rm -rf mydir", 0, "");
        assert!(result.contains("recursive"));
        assert!(result.contains("removed"));
    }

    #[test]
    fn test_narrate_rm_dir_flag() {
        let result = narrate_output("rm -d mydir", 0, "");
        assert!(result.contains("directory"));
        assert!(result.contains("removed"));
    }

    #[test]
    fn test_narrate_mkdir() {
        let result = narrate_output("mkdir newdir", 0, "");
        assert_eq!(result, "\u{2192} mkdir: newdir (created)");
    }

    #[test]
    fn test_narrate_mkdir_nested() {
        let result = narrate_output("mkdir -p a/b/c", 0, "");
        assert!(result.contains("nested"));
        assert!(result.contains("created"));
    }

    #[test]
    fn test_narrate_chmod() {
        let result = narrate_output("chmod 755 script.sh", 0, "");
        assert_eq!(result, "\u{2192} chmod: script.sh \u{2192} 755");
    }

    #[test]
    fn test_narrate_chmod_symbolic() {
        let result = narrate_output("chmod +x script.sh", 0, "");
        assert!(result.contains("script.sh"));
        assert!(result.contains("+x"));
    }

    #[test]
    fn test_narrate_chown() {
        let result = narrate_output("chown root:root file.txt", 0, "");
        assert!(result.contains("file.txt"));
        assert!(result.contains("root:root"));
    }

    #[test]
    fn test_narrate_ln_symlink() {
        let result = narrate_output("ln -s /usr/bin/python3 /usr/local/bin/python", 0, "");
        assert!(result.contains("symlink"));
        assert!(result.contains("/usr/bin/python3"));
        assert!(result.contains("/usr/local/bin/python"));
    }

    #[test]
    fn test_narrate_ln_hard() {
        let result = narrate_output("ln source link", 0, "");
        assert!(result.contains("hard link"));
    }

    #[test]
    fn test_narrate_touch() {
        let result = narrate_output("touch newfile.txt", 0, "");
        assert_eq!(result, "\u{2192} touch: newfile.txt (ok)");
    }

    #[test]
    fn test_narrate_rmdir() {
        let result = narrate_output("rmdir emptydir", 0, "");
        assert_eq!(result, "\u{2192} rmdir: emptydir (removed)");
    }

    // -----------------------------------------------------------------------
    // Failure narrations
    // -----------------------------------------------------------------------

    #[test]
    fn test_narrate_failure_with_stderr() {
        let result = narrate_output("cp src.txt dst.txt", 1, "cp: src.txt: No such file or directory");
        assert!(result.starts_with("! cp:"));
        assert!(result.contains("failed"));
        assert!(result.contains("exit 1"));
        assert!(result.contains("No such file or directory"));
    }

    #[test]
    fn test_narrate_failure_no_stderr() {
        let result = narrate_output("mkdir /root/nope", 1, "");
        assert!(result.starts_with("! mkdir:"));
        assert!(result.contains("failed"));
        assert!(result.contains("exit 1"));
    }

    #[test]
    fn test_narrate_rm_failure() {
        let result = narrate_output("rm nofile.txt", 1, "rm: nofile.txt: No such file or directory");
        assert!(result.starts_with("! rm:"));
        assert!(result.contains("failed"));
    }

    // -----------------------------------------------------------------------
    // Edge cases
    // -----------------------------------------------------------------------

    #[test]
    fn test_narrate_empty_command() {
        let result = narrate_output("", 0, "");
        assert!(result.contains("empty command"));
    }

    #[test]
    fn test_narrate_path_qualified() {
        let result = narrate_output("/bin/cp src dst", 0, "");
        assert!(result.contains("cp:"));
        assert!(result.contains("src"));
        assert!(result.contains("dst"));
    }

    #[test]
    fn test_narrate_generic_unknown() {
        let result = narrate_output("install -m 644 foo bar", 0, "");
        assert!(result.contains("install:"));
    }

    #[test]
    fn test_narrate_generic_no_target() {
        let result = narrate_output("sync", 0, "");
        assert_eq!(result, "\u{2192} sync: ok");
    }

    // -----------------------------------------------------------------------
    // Helper tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_non_flag_args() {
        let args: Vec<String> = vec!["-r", "-f", "dir1", "--verbose", "dir2"]
            .into_iter()
            .map(String::from)
            .collect();
        let result = non_flag_args(&args);
        assert_eq!(result, vec!["dir1", "dir2"]);
    }

    #[test]
    fn test_has_flag() {
        let args: Vec<String> = vec!["-rf", "dir"]
            .into_iter()
            .map(String::from)
            .collect();
        assert!(has_flag(&args, 'r'));
        assert!(has_flag(&args, 'f'));
        assert!(!has_flag(&args, 'v'));
    }

    #[test]
    fn test_paths_from_args_two() {
        let args: Vec<String> = vec!["-r", "src", "dst"]
            .into_iter()
            .map(String::from)
            .collect();
        let (s, d) = paths_from_args(&args);
        assert_eq!(s, "src");
        assert_eq!(d, "dst");
    }

    #[test]
    fn test_paths_from_args_one() {
        let args: Vec<String> = vec!["only"]
            .into_iter()
            .map(String::from)
            .collect();
        let (s, d) = paths_from_args(&args);
        assert_eq!(s, "only");
        assert_eq!(d, "?");
    }
}
