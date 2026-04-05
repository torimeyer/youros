//! Passthrough handler for the squasher pipeline.
//!
//! Commands like cat, grep, ls, jq, diff produce output that should be passed
//! through verbatim with a metadata footer. NO compression is applied to the
//! output body.
//!
//! Ported from mish's `handlers/passthrough.rs` — pipeline-callable formatting only.

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Format output for a passthrough command.
///
/// Returns the original output unchanged with a metadata footer appended.
/// The footer includes: command name, exit code, duration, and line count.
///
/// # Arguments
///
/// * `cmd`         - The full command string
/// * `output`      - The command's output (already captured)
/// * `exit_code`   - Process exit code
/// * `duration_ms` - How long the command took in milliseconds
///
/// # Examples
///
/// ```
/// use ostk::squasher::passthrough::format_passthrough;
///
/// let result = format_passthrough("ls -la", "total 42\ndrwxr-xr-x 5 user staff\n", 0, 12);
/// assert!(result.contains("total 42"));
/// assert!(result.contains("[passthrough]"));
/// ```
pub fn format_passthrough(cmd: &str, output: &str, exit_code: i32, duration_ms: u64) -> String {
    let line_count = if output.is_empty() {
        0
    } else {
        output.lines().count()
    };

    // Build footer
    let footer = format!(
        "[passthrough] {} | exit:{} | {}ms | {} lines",
        cmd, exit_code, duration_ms, line_count
    );

    if output.is_empty() {
        footer
    } else if output.ends_with('\n') {
        format!("{output}{footer}")
    } else {
        format!("{output}\n{footer}")
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_passthrough_output_verbatim() {
        let output = "line one\nline two\nline three\n";
        let result = format_passthrough("cat file.txt", output, 0, 5);
        // Output body is preserved exactly
        assert!(result.starts_with("line one\nline two\nline three\n"));
        // Footer is appended
        assert!(result.contains("[passthrough]"));
        assert!(result.contains("cat file.txt"));
    }

    #[test]
    fn test_passthrough_footer_format() {
        let result = format_passthrough("grep -rn foo", "match1\nmatch2\n", 0, 42);
        assert!(result.contains("[passthrough] grep -rn foo | exit:0 | 42ms | 2 lines"));
    }

    #[test]
    fn test_passthrough_empty_output() {
        let result = format_passthrough("grep nothing", "", 1, 3);
        assert_eq!(result, "[passthrough] grep nothing | exit:1 | 3ms | 0 lines");
    }

    #[test]
    fn test_passthrough_no_trailing_newline() {
        let result = format_passthrough("echo hello", "hello", 0, 1);
        // Should insert a newline before the footer
        assert!(result.contains("hello\n[passthrough]"));
    }

    #[test]
    fn test_passthrough_nonzero_exit() {
        let result = format_passthrough("ls /nonexistent", "ls: /nonexistent: No such file or directory\n", 2, 7);
        assert!(result.contains("exit:2"));
        assert!(result.contains("1 lines"));
    }

    #[test]
    fn test_passthrough_large_duration() {
        let result = format_passthrough("find / -name '*.rs'", "a.rs\nb.rs\nc.rs\n", 0, 15432);
        assert!(result.contains("15432ms"));
        assert!(result.contains("3 lines"));
    }

    #[test]
    fn test_passthrough_preserves_exact_output() {
        let output = "  indented\n\ttabbed\n\nempty line above\n";
        let result = format_passthrough("cat weird.txt", output, 0, 1);
        assert!(result.starts_with(output));
    }

    #[test]
    fn test_passthrough_multiline_output_count() {
        let output = "a\nb\nc\nd\ne\n";
        let result = format_passthrough("wc -l *", output, 0, 2);
        assert!(result.contains("5 lines"));
    }

    #[test]
    fn test_passthrough_single_line() {
        let result = format_passthrough("pwd", "/home/user\n", 0, 0);
        assert!(result.contains("1 lines"));
    }
}
