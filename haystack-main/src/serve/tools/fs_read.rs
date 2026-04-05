//! file:read (alias: fs_read) — file read with 304 elision + lifecycle commands.
//!
//! Wraps kernel::elision::read_file_with_elision().
//! Returns full content + generation on first/changed read,
//! or a ~5 token [304] response on redundant reads.
//!
//! Supported actions:
//!   "read <path>"                    — full file read with 304 elision
//!   "read <path> start:N end:M"     — line range read with 304 elision
//!   "open <path>"                   — no-op (gen_table tracks everything)
//!   "flush"                         — no-op (writes are immediate)
//!   "close"                         — no-op
//!   "status"                        — return gen_table state for tracked files
//!   "list"                          — return list of files in gen_table

use std::path::{Path, PathBuf};

use crate::kernel::elision;
use crate::kernel::gen_table::GenTable;
use crate::serve::types::{ERR_SHELL_ERROR, FsReadParams, ToolError};

/// Handle a file:read tool call.
///
/// Parses the action string to extract the command and arguments.
pub async fn handle(
    params: FsReadParams,
    ostk_dir: &Path,
    agent: Option<&str>,
) -> Result<serde_json::Value, ToolError> {
    let action = params.action.trim();

    // Parse the action command (first word)
    let (cmd, rest) = match action.split_once(' ') {
        Some((cmd, rest)) => (cmd, rest.trim()),
        None => (action, ""),
    };

    match cmd {
        "read" => handle_read(rest, ostk_dir, agent).await,
        "open" => handle_open(rest),
        "flush" => handle_flush(),
        "close" => handle_close(),
        "status" => handle_status(ostk_dir).await,
        "list" => handle_list(ostk_dir).await,
        _ => Err(ToolError::invalid_params(format!(
            "Unknown file:read action: '{cmd}'. Supported: read, open, flush, close, status, list"
        ))),
    }
}

/// Parse line range from action remainder.
///
/// Format: "path/to/file start:N end:M"
/// Both start and end are optional.
fn parse_read_args(rest: &str) -> Result<(&str, Option<usize>, Option<usize>), ToolError> {
    let parts: Vec<&str> = rest.split_whitespace().collect();
    if parts.is_empty() {
        return Err(ToolError::invalid_params("read requires a file path"));
    }

    let path = parts[0];
    let mut start: Option<usize> = None;
    let mut end: Option<usize> = None;

    for part in &parts[1..] {
        if let Some(val) = part.strip_prefix("start:") {
            start =
                Some(val.parse::<usize>().map_err(|_| {
                    ToolError::invalid_params(format!("invalid start value: '{val}'"))
                })?);
        } else if let Some(val) = part.strip_prefix("end:") {
            end =
                Some(val.parse::<usize>().map_err(|_| {
                    ToolError::invalid_params(format!("invalid end value: '{val}'"))
                })?);
        }
    }

    Ok((path, start, end))
}

/// Handle "read <path> [start:N] [end:M]".
async fn handle_read(
    rest: &str,
    ostk_dir: &Path,
    agent: Option<&str>,
) -> Result<serde_json::Value, ToolError> {
    let (path_str, start, end) = parse_read_args(rest)?;
    let agent = agent.unwrap_or("unknown");
    let file_path = PathBuf::from(path_str);
    let gen_path = path_str.to_string();

    let result = tokio::task::spawn_blocking({
        let file_path = file_path.clone();
        let gen_path = gen_path.clone();
        let agent = agent.to_string();
        let ostk_dir = ostk_dir.to_path_buf();
        move || elision::read_file_with_elision(&file_path, &gen_path, &agent, &ostk_dir)
    })
    .await
    .map_err(|e| ToolError::internal(format!("spawn_blocking join error: {e}")))?;

    match result {
        Ok(read_result) => {
            let text = format_read_result(&read_result, path_str, start, end);
            Ok(serde_json::json!({ "text": text }))
        }
        Err(e) => Err(ToolError::new(
            ERR_SHELL_ERROR,
            format!("file:read error: {e}"),
        )),
    }
}

/// Format a read result, optionally slicing to a line range.
/// For 304 responses, return the short response regardless of range request
/// (if the full file hasn't changed, the range hasn't either).
fn format_read_result(
    read_result: &elision::ReadResult,
    path: &str,
    start: Option<usize>,
    end: Option<usize>,
) -> String {
    match read_result {
        elision::ReadResult::NotModified { generation } => {
            // 304 for any range request too — if full file unchanged, range unchanged
            format!("[304] {path}:gen={generation} (current)")
        }
        elision::ReadResult::Full {
            content,
            generation,
        } => {
            if start.is_some() || end.is_some() {
                let lines: Vec<&str> = content.lines().collect();
                let total = lines.len();
                let s = start.unwrap_or(1).max(1);
                let e = end.unwrap_or(total).min(total);
                let start_idx = s.saturating_sub(1);
                let sliced: Vec<String> = lines
                    .iter()
                    .enumerate()
                    .skip(start_idx)
                    .take(e.saturating_sub(start_idx))
                    .map(|(i, line)| format!("{:>4}\t{}", i + 1, line))
                    .collect();
                format!(
                    "[200] {path}:gen={generation} (lines {s}-{e} of {total})\n{}",
                    sliced.join("\n")
                )
            } else {
                format!("[200] {path}:gen={generation}\n{content}")
            }
        }
    }
}

/// Handle "open <path>" — compatibility no-op.
/// ostk's gen_table tracks everything; explicit sessions aren't needed.
fn handle_open(rest: &str) -> Result<serde_json::Value, ToolError> {
    let paths: Vec<&str> = rest.split_whitespace().collect();
    if paths.is_empty() {
        return Ok(serde_json::json!({ "text": "+ open (no-op — gen_table tracks state)" }));
    }
    let text = format!(
        "+ open {} (no-op — gen_table tracks state)",
        paths.join(", ")
    );
    Ok(serde_json::json!({ "text": text }))
}

/// Handle "flush" — compatibility no-op.
/// Writes are immediate in ostk (no buffered session).
fn handle_flush() -> Result<serde_json::Value, ToolError> {
    Ok(serde_json::json!({ "text": "+ flush (no-op — writes are immediate)" }))
}

/// Handle "close" — compatibility no-op.
fn handle_close() -> Result<serde_json::Value, ToolError> {
    Ok(serde_json::json!({ "text": "+ close (no-op)" }))
}

/// Handle "status" — return gen_table state for tracked files.
async fn handle_status(ostk_dir: &Path) -> Result<serde_json::Value, ToolError> {
    let ostk_dir = ostk_dir.to_path_buf();
    let result = tokio::task::spawn_blocking(move || {
        let gt = GenTable::new(&ostk_dir);
        gt.list_gens()
    })
    .await
    .map_err(|e| ToolError::internal(format!("spawn_blocking join error: {e}")))?;

    match result {
        Ok(entries) => {
            if entries.is_empty() {
                return Ok(serde_json::json!({ "text": "+ status: no tracked files" }));
            }
            let mut lines = vec!["+ status".to_string()];
            for entry in &entries {
                lines.push(format!(
                    "  {} gen={} writer={} {}",
                    entry.path, entry.generation, entry.writer, entry.timestamp
                ));
            }
            Ok(serde_json::json!({ "text": lines.join("\n") }))
        }
        Err(e) => Err(ToolError::new(
            ERR_SHELL_ERROR,
            format!("status error: {e}"),
        )),
    }
}

/// Handle "list" — return list of files in gen_table.
async fn handle_list(ostk_dir: &Path) -> Result<serde_json::Value, ToolError> {
    let ostk_dir = ostk_dir.to_path_buf();
    let result = tokio::task::spawn_blocking(move || {
        let gt = GenTable::new(&ostk_dir);
        gt.list_gens()
    })
    .await
    .map_err(|e| ToolError::internal(format!("spawn_blocking join error: {e}")))?;

    match result {
        Ok(entries) => {
            if entries.is_empty() {
                return Ok(serde_json::json!({ "text": "+ list: no tracked files" }));
            }
            let mut lines = vec!["+ list".to_string()];
            for entry in &entries {
                lines.push(format!("  {}:gen={}", entry.path, entry.generation));
            }
            Ok(serde_json::json!({ "text": lines.join("\n") }))
        }
        Err(e) => Err(ToolError::new(ERR_SHELL_ERROR, format!("list error: {e}"))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::elision::ReadResult;
    use crate::serve::types::FsReadParams;

    // ── parse_read_args ──

    #[test]
    fn parse_read_args_path_only() {
        let (path, start, end) = parse_read_args("src/main.rs").unwrap();
        assert_eq!(path, "src/main.rs");
        assert!(start.is_none());
        assert!(end.is_none());
    }

    #[test]
    fn parse_read_args_with_range() {
        let (path, start, end) = parse_read_args("src/lib.rs start:10 end:50").unwrap();
        assert_eq!(path, "src/lib.rs");
        assert_eq!(start, Some(10));
        assert_eq!(end, Some(50));
    }

    #[test]
    fn parse_read_args_start_only() {
        let (path, start, end) = parse_read_args("file.rs start:5").unwrap();
        assert_eq!(path, "file.rs");
        assert_eq!(start, Some(5));
        assert!(end.is_none());
    }

    #[test]
    fn parse_read_args_end_only() {
        let (path, start, end) = parse_read_args("file.rs end:20").unwrap();
        assert_eq!(path, "file.rs");
        assert!(start.is_none());
        assert_eq!(end, Some(20));
    }

    #[test]
    fn parse_read_args_empty() {
        let err = parse_read_args("").unwrap_err();
        assert_eq!(err.code, crate::serve::types::ERR_INVALID_PARAMS);
    }

    #[test]
    fn parse_read_args_invalid_start() {
        let err = parse_read_args("file.rs start:abc").unwrap_err();
        assert!(err.message.contains("invalid start"));
    }

    #[test]
    fn parse_read_args_invalid_end() {
        let err = parse_read_args("file.rs end:xyz").unwrap_err();
        assert!(err.message.contains("invalid end"));
    }

    // ── format_read_result ──

    #[test]
    fn format_read_result_not_modified() {
        let result = ReadResult::NotModified { generation: 5 };
        let text = format_read_result(&result, "src/main.rs", None, None);
        assert_eq!(text, "[304] src/main.rs:gen=5 (current)");
    }

    #[test]
    fn format_read_result_not_modified_with_range() {
        // Even with range, 304 should be returned as-is
        let result = ReadResult::NotModified { generation: 3 };
        let text = format_read_result(&result, "file.rs", Some(1), Some(10));
        assert!(text.contains("[304]"));
    }

    #[test]
    fn format_read_result_full_no_range() {
        let result = ReadResult::Full {
            content: "line1\nline2\nline3".to_string(),
            generation: 1,
        };
        let text = format_read_result(&result, "test.rs", None, None);
        assert!(text.starts_with("[200] test.rs:gen=1"));
        assert!(text.contains("line1\nline2\nline3"));
    }

    #[test]
    fn format_read_result_full_with_range() {
        let result = ReadResult::Full {
            content: "alpha\nbeta\ngamma\ndelta\nepsilon".to_string(),
            generation: 2,
        };
        let text = format_read_result(&result, "data.rs", Some(2), Some(4));
        assert!(text.contains("[200] data.rs:gen=2"));
        assert!(text.contains("lines 2-4"));
        assert!(text.contains("beta"));
        assert!(text.contains("gamma"));
        assert!(text.contains("delta"));
        assert!(!text.contains("alpha"));
        assert!(!text.contains("epsilon"));
    }

    #[test]
    fn format_read_result_full_range_clamped() {
        let result = ReadResult::Full {
            content: "one\ntwo".to_string(),
            generation: 1,
        };
        // end beyond file length should be clamped
        let text = format_read_result(&result, "short.rs", Some(1), Some(100));
        assert!(text.contains("one"));
        assert!(text.contains("two"));
    }

    // ── handle_open ──

    #[test]
    fn handle_open_no_paths() {
        let result = handle_open("").unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("no-op"));
    }

    #[test]
    fn handle_open_with_paths() {
        let result = handle_open("src/main.rs src/lib.rs").unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("src/main.rs"));
        assert!(text.contains("src/lib.rs"));
        assert!(text.contains("no-op"));
    }

    // ── handle_flush ──

    #[test]
    fn handle_flush_noop() {
        let result = handle_flush().unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("no-op"));
        assert!(text.contains("immediate"));
    }

    // ── handle_close ──

    #[test]
    fn handle_close_noop() {
        let result = handle_close().unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("no-op"));
    }

    // ── handle: unknown action ──

    #[tokio::test]
    async fn handle_unknown_action() {
        let params = FsReadParams {
            action: "delete everything".to_string(),
        };
        let dir = std::path::PathBuf::from("/tmp/test-ostk-fs-read");
        let err = handle(params, &dir, None).await.unwrap_err();
        assert!(err.message.contains("Unknown file:read action"));
    }

    // ── handle: read with real temp file ──

    #[tokio::test]
    async fn handle_read_real_file() {
        let tmp = tempdir();
        let ostk_dir = tmp.join(".ostk");
        std::fs::create_dir_all(&ostk_dir).unwrap();
        let test_file = tmp.join("test.txt");
        std::fs::write(&test_file, "hello world\nsecond line").unwrap();

        let params = FsReadParams {
            action: format!("read {}", test_file.display()),
        };
        let result = handle(params, &ostk_dir, Some("test-agent")).await.unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("[200]"));
        assert!(text.contains("hello world"));
    }

    // ── handle: status and list with empty gen table ──

    #[tokio::test]
    async fn handle_status_empty() {
        let tmp = tempdir();
        let ostk_dir = tmp.join(".ostk");
        std::fs::create_dir_all(&ostk_dir).unwrap();

        let params = FsReadParams {
            action: "status".to_string(),
        };
        let result = handle(params, &ostk_dir, None).await.unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("no tracked files"));
    }

    #[tokio::test]
    async fn handle_list_empty() {
        let tmp = tempdir();
        let ostk_dir = tmp.join(".ostk");
        std::fs::create_dir_all(&ostk_dir).unwrap();

        let params = FsReadParams {
            action: "list".to_string(),
        };
        let result = handle(params, &ostk_dir, None).await.unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("no tracked files"));
    }

    // ── handle: read missing path ──

    #[tokio::test]
    async fn handle_read_no_path() {
        let dir = std::path::PathBuf::from("/tmp/test-ostk-fs-read-2");
        let params = FsReadParams {
            action: "read".to_string(),
        };
        let err = handle(params, &dir, None).await.unwrap_err();
        assert!(err.message.contains("file path"));
    }

    fn tempdir() -> PathBuf {
        use std::sync::atomic::{AtomicU64, Ordering};
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let id = COUNTER.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!(
            "ostk-test-{}-{}",
            std::process::id(),
            id
        ));
        // Clean any leftover from a previous run, then create fresh
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }
}
