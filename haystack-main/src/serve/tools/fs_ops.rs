//! file:edit (alias: fs_ops) — CAS file edit tool with Hot PR conflict resolution.
//!
//! Two modes:
//! - **Quick mode** (`path` provided): single str_replace edit or file creation.
//! - **Batch mode** (`ops` provided): array of operations executed sequentially.
//!
//! All writes route through kernel::file::str_replace_cas() for CAS + Hot PR.
//! All reads route through kernel::elision::read_file_with_elision() for 304.
//! Generation tracking via kernel::gen_table::GenTable.

use std::path::{Path, PathBuf};

use crate::kernel::elision;
use crate::kernel::file::{self, FileError};
use crate::kernel::gen_table::GenTable;
use crate::serve::types::{ERR_INVALID_PARAMS, ERR_SHELL_ERROR, FsOpsParams, ToolError};

/// Handle a file:edit tool call.
///
/// Quick mode (path provided):
///   - old_str + new_str: CAS edit via str_replace_cas (Hot PR)
///   - old_str + new_str + replace_all: replace all occurrences
///   - new_str only: file creation via create_file
///
/// Batch mode (ops provided):
///   - Array of {"method": "file.str_replace"|"file.read"|"file.write", ...}
///   - Executed sequentially, results collected
pub async fn handle(
    params: FsOpsParams,
    ostk_dir: &Path,
    agent: Option<&str>,
) -> Result<serde_json::Value, ToolError> {
    // Quick mode: path provided
    if let Some(ref path) = params.path {
        return handle_quick(path, &params, ostk_dir, agent).await;
    }

    // Batch mode: ops provided
    if let Some(ref ops) = params.ops {
        return handle_batch(ops, ostk_dir, agent).await;
    }

    // Neither path nor ops
    Err(ToolError::invalid_params(
        "provide either path (quick mode) or ops (batch mode)",
    ))
}

/// Quick mode: single file operation.
async fn handle_quick(
    path: &str,
    params: &FsOpsParams,
    ostk_dir: &Path,
    agent: Option<&str>,
) -> Result<serde_json::Value, ToolError> {
    let file_path = PathBuf::from(path);
    let writer = agent.unwrap_or("unknown");
    let gen_path = path.to_string();

    match (&params.old_str, &params.new_str) {
        // CAS edit: old_str + new_str
        (Some(old_str), Some(new_str)) => {
            let replace_all = params.replace_all.unwrap_or(false);

            if replace_all {
                // Replace all occurrences — use str_replace_all + gen bump
                handle_replace_all(
                    &file_path,
                    old_str,
                    new_str,
                    ostk_dir,
                    &gen_path,
                    writer,
                )
                .await
            } else {
                // Single CAS edit through Hot PR
                handle_cas_edit(
                    &file_path,
                    old_str,
                    new_str,
                    ostk_dir,
                    &gen_path,
                    writer,
                )
                .await
            }
        }
        // Create mode: new_str only
        (None, Some(new_str)) => {
            handle_create(&file_path, new_str, ostk_dir, &gen_path, writer).await
        }
        // old_str without new_str
        (Some(_), None) => Err(ToolError::invalid_params("new_str required with old_str")),
        // Neither
        (None, None) => Err(ToolError::invalid_params(
            "provide old_str+new_str or new_str alone",
        )),
    }
}

/// CAS edit via str_replace_cas (Hot PR).
async fn handle_cas_edit(
    file_path: &Path,
    old_str: &str,
    new_str: &str,
    ostk_dir: &Path,
    gen_path: &str,
    writer: &str,
) -> Result<serde_json::Value, ToolError> {
    let result = tokio::task::spawn_blocking({
        let file_path = file_path.to_path_buf();
        let old_str = old_str.to_string();
        let new_str = new_str.to_string();
        let gen_path = gen_path.to_string();
        let writer = writer.to_string();
        let ostk_dir = ostk_dir.to_path_buf();
        move || {
            let gt = GenTable::new(&ostk_dir);
            file::str_replace_cas(
                &file_path,
                &old_str,
                &new_str,
                &gt,
                &gen_path,
                Some(&writer),
            )
        }
    })
    .await
    .map_err(|e| ToolError::internal(format!("spawn_blocking join error: {e}")))?;

    format_cas_result(result, gen_path)
}

/// Replace all occurrences of old_str with new_str.
///
/// →805: Now uses str_replace_all_cas which holds an flock during the entire
/// read-replace-write-gen_bump cycle, preventing TOCTOU races between agents.
async fn handle_replace_all(
    file_path: &Path,
    old_str: &str,
    new_str: &str,
    ostk_dir: &Path,
    gen_path: &str,
    writer: &str,
) -> Result<serde_json::Value, ToolError> {
    let result = tokio::task::spawn_blocking({
        let file_path = file_path.to_path_buf();
        let old_str = old_str.to_string();
        let new_str = new_str.to_string();
        let gen_path = gen_path.to_string();
        let writer = writer.to_string();
        let ostk_dir = ostk_dir.to_path_buf();
        move || {
            let gt = GenTable::new(&ostk_dir);
            file::str_replace_all_cas(
                &file_path,
                &old_str,
                &new_str,
                &gt,
                &gen_path,
                Some(&writer),
            )
        }
    })
    .await
    .map_err(|e| ToolError::internal(format!("spawn_blocking join error: {e}")))?;

    match result {
        Ok(cas_result) => {
            let text = format!(
                "+ file:edit {} gen={} replace_all ({} -> {} bytes)",
                gen_path, cas_result.generation, cas_result.edit.old_len, cas_result.edit.new_len
            );
            Ok(serde_json::json!({ "text": text }))
        }
        Err(FileError::NoMatch { path }) => Err(ToolError::new(
            ERR_INVALID_PARAMS,
            format!("CAS failed: old_str not found in {path}. Re-read the file and retry."),
        )),
        Err(e) => Err(ToolError::new(ERR_SHELL_ERROR, format!("file:edit error: {e}"))),
    }
}

/// Create a new file.
///
/// →805: Now uses write_file_cas for atomic file creation + gen_bump.
async fn handle_create(
    file_path: &Path,
    content: &str,
    ostk_dir: &Path,
    gen_path: &str,
    writer: &str,
) -> Result<serde_json::Value, ToolError> {
    let content_len = content.len();
    let result = tokio::task::spawn_blocking({
        let file_path = file_path.to_path_buf();
        let content = content.to_string();
        let gen_path = gen_path.to_string();
        let writer = writer.to_string();
        let ostk_dir = ostk_dir.to_path_buf();
        move || {
            let gt = GenTable::new(&ostk_dir);
            file::write_file_cas(
                &file_path,
                &content,
                &gt,
                &gen_path,
                Some(&writer),
            )
        }
    })
    .await
    .map_err(|e| ToolError::internal(format!("spawn_blocking join error: {e}")))?;

    match result {
        Ok(write_result) => {
            let text = format!(
                "+ file:edit created {} gen={} ({} bytes)",
                gen_path, write_result.generation, content_len
            );
            Ok(serde_json::json!({ "text": text }))
        }
        Err(e) => Err(ToolError::new(
            ERR_SHELL_ERROR,
            format!("file:edit create error: {e}"),
        )),
    }
}

/// Format a CAS edit result into a tool response.
fn format_cas_result(
    result: Result<file::CasEditResult, FileError>,
    gen_path: &str,
) -> Result<serde_json::Value, ToolError> {
    match result {
        Ok(cas_result) => {
            let mut text = format!(
                "+ file:edit {} gen={} ({} -> {} bytes)",
                gen_path, cas_result.generation, cas_result.edit.old_len, cas_result.edit.new_len
            );
            if let Some(ref note) = cas_result.auto_merge_note {
                text.push('\n');
                text.push_str(note);
            }
            Ok(serde_json::json!({ "text": text }))
        }
        Err(FileError::NoMatch { path }) => Err(ToolError::new(
            ERR_INVALID_PARAMS,
            format!("CAS failed: old_str not found in {path}. Re-read the file and retry."),
        )),
        Err(FileError::AmbiguousMatch { path, count }) => Err(ToolError::new(
            ERR_INVALID_PARAMS,
            format!(
                "CAS failed: found {count} matches for old_str in {path}. Use more context to make it unique."
            ),
        )),
        Err(FileError::Tier2Conflict { path, message }) => Err(ToolError::new(
            ERR_SHELL_ERROR,
            format!("Tier 2 conflict in {path}:\n{message}"),
        )),
        Err(FileError::Conflict { path, message }) => Err(ToolError::new(
            ERR_SHELL_ERROR,
            format!("Tier 3 conflict in {path}:\n{message}"),
        )),
        Err(e) => Err(ToolError::new(ERR_SHELL_ERROR, format!("file:edit error: {e}"))),
    }
}

/// Batch mode: execute ops sequentially, collect results.
async fn handle_batch(
    ops: &[serde_json::Value],
    ostk_dir: &Path,
    agent: Option<&str>,
) -> Result<serde_json::Value, ToolError> {
    if ops.is_empty() {
        return Err(ToolError::invalid_params("ops array is empty"));
    }

    let writer = agent.unwrap_or("unknown");
    let mut results: Vec<String> = Vec::new();

    for (i, op) in ops.iter().enumerate() {
        let method = op.get("method").and_then(|m| m.as_str()).unwrap_or("");

        let path = op.get("path").and_then(|p| p.as_str()).unwrap_or("");

        if path.is_empty() {
            return Err(ToolError::invalid_params(format!(
                "op[{i}]: missing 'path'"
            )));
        }

        let result_text = match method {
            "file.str_replace" => {
                let old_str = op.get("old_str").and_then(|s| s.as_str()).ok_or_else(|| {
                    ToolError::invalid_params(format!("op[{i}]: missing 'old_str'"))
                })?;
                let new_str = op.get("new_str").and_then(|s| s.as_str()).ok_or_else(|| {
                    ToolError::invalid_params(format!("op[{i}]: missing 'new_str'"))
                })?;
                let replace_all = op
                    .get("replace_all")
                    .and_then(|b| b.as_bool())
                    .unwrap_or(false);

                let file_path = PathBuf::from(path);

                if replace_all {
                    let r = handle_replace_all(
                        &file_path,
                        old_str,
                        new_str,
                        ostk_dir,
                        path,
                        writer,
                    )
                    .await?;
                    r["text"].as_str().unwrap_or("").to_string()
                } else {
                    let r =
                        handle_cas_edit(&file_path, old_str, new_str, ostk_dir, path, writer)
                            .await?;
                    r["text"].as_str().unwrap_or("").to_string()
                }
            }
            "file.read" => {
                let file_path = PathBuf::from(path);
                let start = op.get("start").and_then(|s| s.as_u64()).map(|s| s as usize);
                let end = op.get("end").and_then(|e| e.as_u64()).map(|e| e as usize);

                let read_result = tokio::task::spawn_blocking({
                    let file_path = file_path.clone();
                    let gen_path = path.to_string();
                    let agent = writer.to_string();
                    let ostk_dir = ostk_dir.to_path_buf();
                    move || {
                        elision::read_file_with_elision(
                            &file_path,
                            &gen_path,
                            &agent,
                            &ostk_dir,
                        )
                    }
                })
                .await
                .map_err(|e| ToolError::internal(format!("spawn_blocking join error: {e}")))?
                .map_err(|e| ToolError::new(ERR_SHELL_ERROR, format!("read error: {e}")))?;

                format_read_result(&read_result, path, start, end)
            }
            "file.write" => {
                let content = op.get("content").and_then(|s| s.as_str()).ok_or_else(|| {
                    ToolError::invalid_params(format!("op[{i}]: missing 'content'"))
                })?;
                let file_path = PathBuf::from(path);

                // file.write creates or overwrites
                let r = handle_write(&file_path, content, ostk_dir, path, writer).await?;
                r["text"].as_str().unwrap_or("").to_string()
            }
            "" => {
                return Err(ToolError::invalid_params(format!(
                    "op[{i}]: missing 'method'"
                )));
            }
            other => {
                return Err(ToolError::invalid_params(format!(
                    "op[{i}]: unknown method '{other}'. Supported: file.str_replace, file.read, file.write"
                )));
            }
        };

        results.push(result_text);
    }

    let text = results.join("\n");
    Ok(serde_json::json!({ "text": text }))
}

/// Handle file.write op — creates or overwrites a file.
///
/// →805: Now uses write_file_cas which holds an flock during the entire
/// read-old-content + write-new-content + gen_bump cycle, preventing
/// concurrent writes from silently overwriting each other.
async fn handle_write(
    file_path: &Path,
    content: &str,
    ostk_dir: &Path,
    gen_path: &str,
    writer: &str,
) -> Result<serde_json::Value, ToolError> {
    let content_len = content.len();
    let result = tokio::task::spawn_blocking({
        let file_path = file_path.to_path_buf();
        let content = content.to_string();
        let gen_path = gen_path.to_string();
        let writer = writer.to_string();
        let ostk_dir = ostk_dir.to_path_buf();
        move || {
            let gt = GenTable::new(&ostk_dir);
            file::write_file_cas(
                &file_path,
                &content,
                &gt,
                &gen_path,
                Some(&writer),
            )
        }
    })
    .await
    .map_err(|e| ToolError::internal(format!("spawn_blocking join error: {e}")))?;

    match result {
        Ok(write_result) => {
            let text = format!(
                "+ file:edit wrote {} gen={} ({} bytes)",
                gen_path, write_result.generation, content_len
            );
            Ok(serde_json::json!({ "text": text }))
        }
        Err(e) => Err(ToolError::new(
            ERR_SHELL_ERROR,
            format!("file:edit write error: {e}"),
        )),
    }
}

/// Format a read result, optionally slicing to a line range.
fn format_read_result(
    read_result: &elision::ReadResult,
    path: &str,
    start: Option<usize>,
    end: Option<usize>,
) -> String {
    match read_result {
        elision::ReadResult::NotModified { generation } => {
            format!("[304] {path}:gen={generation} (current)")
        }
        elision::ReadResult::Full {
            content,
            generation,
        } => {
            if start.is_some() || end.is_some() {
                let lines: Vec<&str> = content.lines().collect();
                let start = start.unwrap_or(1).max(1);
                let end = end.unwrap_or(lines.len()).min(lines.len());
                let start_idx = start.saturating_sub(1);
                let sliced: Vec<String> = lines
                    .iter()
                    .enumerate()
                    .skip(start_idx)
                    .take(end.saturating_sub(start_idx))
                    .map(|(i, line)| format!("{:>4}\t{}", i + 1, line))
                    .collect();
                format!(
                    "[200] {path}:gen={generation} (lines {start}-{end})\n{}",
                    sliced.join("\n")
                )
            } else {
                format!("[200] {path}:gen={generation}\n{content}")
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::serve::types::FsOpsParams;

    fn tempdir() -> PathBuf {
        // Ensure trust tier is T0 for tests — without this, CI environments
        // without GPG resolve to T3 (deny-all-writes), failing every write test.
        // Must be set before the OnceLock in kernel::policy is initialized.
        unsafe { std::env::set_var("OSTK_TRUST_TIER", "T0") };
        let dir = std::env::temp_dir().join(format!(
            "ostk-fs-ops-test-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        // When OSTK_PIN is set (e.g. running tests inside an ostk session),
        // create a permissive pin.caps so policy checks don't fail-closed
        // on temp dir writes.
        if let Ok(pin_name) = std::env::var("OSTK_PIN") {
            if !pin_name.is_empty() {
                let pin_dir = dir.join(".ostk").join("pins").join(&pin_name);
                std::fs::create_dir_all(&pin_dir).unwrap();
                std::fs::write(
                    pin_dir.join("pin.caps"),
                    "read: *
write: *
execute: *
deny:
",
                ).unwrap();
            }
        }
        dir
    }

    // ── handle: no path or ops ──

    #[tokio::test]
    async fn handle_no_path_no_ops() {
        let params = FsOpsParams {
            path: None,
            old_str: None,
            new_str: None,
            replace_all: None,
            ops: None,
        };
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let err = handle(params, &ostk, None).await.unwrap_err();
        assert!(err.message.contains("path"));
    }

    // ── handle quick: old_str without new_str ──

    #[tokio::test]
    async fn handle_old_str_without_new_str() {
        let params = FsOpsParams {
            path: Some("test.rs".to_string()),
            old_str: Some("foo".to_string()),
            new_str: None,
            replace_all: None,
            ops: None,
        };
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let err = handle(params, &ostk, None).await.unwrap_err();
        assert!(err.message.contains("new_str required"));
    }

    // ── handle quick: neither old_str nor new_str ──

    #[tokio::test]
    async fn handle_no_old_no_new() {
        let params = FsOpsParams {
            path: Some("test.rs".to_string()),
            old_str: None,
            new_str: None,
            replace_all: None,
            ops: None,
        };
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let err = handle(params, &ostk, None).await.unwrap_err();
        assert!(err.message.contains("old_str+new_str"));
    }

    // ── handle quick: create file ──

    #[tokio::test]
    async fn handle_create_file() {
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let file_path = dir.join("created.txt");

        let params = FsOpsParams {
            path: Some(file_path.to_string_lossy().to_string()),
            old_str: None,
            new_str: Some("hello world".to_string()),
            replace_all: None,
            ops: None,
        };
        let result = handle(params, &ostk, Some("test-agent")).await.unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("created"));
        assert!(text.contains("gen="));

        // Verify file was actually created
        let content = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(content, "hello world");
    }

    // ── handle quick: CAS edit ──

    #[tokio::test]
    async fn handle_cas_edit_success() {
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let file_path = dir.join("edit-me.txt");
        std::fs::write(&file_path, "hello world").unwrap();

        let params = FsOpsParams {
            path: Some(file_path.to_string_lossy().to_string()),
            old_str: Some("hello".to_string()),
            new_str: Some("goodbye".to_string()),
            replace_all: None,
            ops: None,
        };
        let result = handle(params, &ostk, Some("agent")).await.unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("gen="));

        let content = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(content, "goodbye world");
    }

    // ── handle quick: CAS edit — no match ──

    #[tokio::test]
    async fn handle_cas_edit_no_match() {
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let file_path = dir.join("nomatch.txt");
        std::fs::write(&file_path, "alpha beta").unwrap();

        let params = FsOpsParams {
            path: Some(file_path.to_string_lossy().to_string()),
            old_str: Some("gamma".to_string()),
            new_str: Some("delta".to_string()),
            replace_all: None,
            ops: None,
        };
        let err = handle(params, &ostk, None).await.unwrap_err();
        assert!(err.message.contains("CAS failed"));
        assert!(err.message.contains("not found"));
    }

    // ── handle quick: CAS edit — ambiguous match ──

    #[tokio::test]
    async fn handle_cas_edit_ambiguous() {
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let file_path = dir.join("ambiguous.txt");
        std::fs::write(&file_path, "foo bar foo").unwrap();

        let params = FsOpsParams {
            path: Some(file_path.to_string_lossy().to_string()),
            old_str: Some("foo".to_string()),
            new_str: Some("baz".to_string()),
            replace_all: None,
            ops: None,
        };
        let err = handle(params, &ostk, None).await.unwrap_err();
        assert!(err.message.contains("CAS failed"));
    }

    // ── handle quick: replace_all ──

    #[tokio::test]
    async fn handle_replace_all_success() {
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let file_path = dir.join("replall.txt");
        std::fs::write(&file_path, "foo bar foo baz foo").unwrap();

        let params = FsOpsParams {
            path: Some(file_path.to_string_lossy().to_string()),
            old_str: Some("foo".to_string()),
            new_str: Some("qux".to_string()),
            replace_all: Some(true),
            ops: None,
        };
        let result = handle(params, &ostk, Some("agent")).await.unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("replace_all"));

        let content = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(content, "qux bar qux baz qux");
    }

    // ── handle batch: empty ops ──

    #[tokio::test]
    async fn handle_batch_empty_ops() {
        let params = FsOpsParams {
            path: None,
            old_str: None,
            new_str: None,
            replace_all: None,
            ops: Some(vec![]),
        };
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let err = handle(params, &ostk, None).await.unwrap_err();
        assert!(err.message.contains("empty"));
    }

    // ── handle batch: missing path ──

    #[tokio::test]
    async fn handle_batch_missing_path() {
        let params = FsOpsParams {
            path: None,
            old_str: None,
            new_str: None,
            replace_all: None,
            ops: Some(vec![serde_json::json!({"method": "file.read"})]),
        };
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let err = handle(params, &ostk, None).await.unwrap_err();
        assert!(err.message.contains("path"));
    }

    // ── handle batch: missing method ──

    #[tokio::test]
    async fn handle_batch_missing_method() {
        let params = FsOpsParams {
            path: None,
            old_str: None,
            new_str: None,
            replace_all: None,
            ops: Some(vec![serde_json::json!({"path": "test.rs"})]),
        };
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let err = handle(params, &ostk, None).await.unwrap_err();
        assert!(err.message.contains("method"));
    }

    // ── handle batch: unknown method ──

    #[tokio::test]
    async fn handle_batch_unknown_method() {
        let params = FsOpsParams {
            path: None,
            old_str: None,
            new_str: None,
            replace_all: None,
            ops: Some(vec![serde_json::json!({
                "method": "file.delete",
                "path": "test.rs"
            })]),
        };
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let err = handle(params, &ostk, None).await.unwrap_err();
        assert!(err.message.contains("unknown method"));
    }

    // ── handle batch: file.read ──

    #[tokio::test]
    async fn handle_batch_file_read() {
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let file_path = dir.join("batch-read.txt");
        std::fs::write(&file_path, "content here").unwrap();

        let params = FsOpsParams {
            path: None,
            old_str: None,
            new_str: None,
            replace_all: None,
            ops: Some(vec![serde_json::json!({
                "method": "file.read",
                "path": file_path.to_string_lossy()
            })]),
        };
        let result = handle(params, &ostk, Some("agent")).await.unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("content here"));
    }

    // ── handle batch: file.write ──

    #[tokio::test]
    async fn handle_batch_file_write() {
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();
        let file_path = dir.join("batch-write.txt");

        let params = FsOpsParams {
            path: None,
            old_str: None,
            new_str: None,
            replace_all: None,
            ops: Some(vec![serde_json::json!({
                "method": "file.write",
                "path": file_path.to_string_lossy(),
                "content": "batch written"
            })]),
        };
        let result = handle(params, &ostk, Some("agent")).await.unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("wrote"));

        let content = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(content, "batch written");
    }

    // ── handle batch: file.write missing content ──

    #[tokio::test]
    async fn handle_batch_file_write_no_content() {
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();

        let params = FsOpsParams {
            path: None,
            old_str: None,
            new_str: None,
            replace_all: None,
            ops: Some(vec![serde_json::json!({
                "method": "file.write",
                "path": "/tmp/test"
            })]),
        };
        let err = handle(params, &ostk, None).await.unwrap_err();
        assert!(err.message.contains("content"));
    }

    // ── handle batch: file.str_replace missing old_str ──

    #[tokio::test]
    async fn handle_batch_str_replace_missing_old_str() {
        let dir = tempdir();
        let ostk = dir.join(".ostk");
        std::fs::create_dir_all(&ostk).unwrap();

        let params = FsOpsParams {
            path: None,
            old_str: None,
            new_str: None,
            replace_all: None,
            ops: Some(vec![serde_json::json!({
                "method": "file.str_replace",
                "path": "/tmp/test",
                "new_str": "replacement"
            })]),
        };
        let err = handle(params, &ostk, None).await.unwrap_err();
        assert!(err.message.contains("old_str"));
    }

    // ── format_cas_result ──

    #[test]
    fn format_cas_result_no_match() {
        let result = Err(FileError::NoMatch {
            path: "test.rs".to_string(),
        });
        let err = format_cas_result(result, "test.rs").unwrap_err();
        assert!(err.message.contains("CAS failed"));
        assert!(err.message.contains("not found"));
    }

    #[test]
    fn format_cas_result_ambiguous() {
        let result = Err(FileError::AmbiguousMatch {
            path: "test.rs".to_string(),
            count: 3,
        });
        let err = format_cas_result(result, "test.rs").unwrap_err();
        assert!(err.message.contains("3 matches"));
    }

    #[test]
    fn format_cas_result_tier2_conflict() {
        let result = Err(FileError::Tier2Conflict {
            path: "test.rs".to_string(),
            message: "concurrent edit".to_string(),
        });
        let err = format_cas_result(result, "test.rs").unwrap_err();
        assert!(err.message.contains("Tier 2"));
    }

    #[test]
    fn format_cas_result_tier3_conflict() {
        let result = Err(FileError::Conflict {
            path: "test.rs".to_string(),
            message: "hard conflict".to_string(),
        });
        let err = format_cas_result(result, "test.rs").unwrap_err();
        assert!(err.message.contains("Tier 3"));
    }

    // ── format_read_result ──

    #[test]
    fn ss_format_read_result_304() {
        let result = elision::ReadResult::NotModified { generation: 7 };
        let text = format_read_result(&result, "file.rs", None, None);
        assert!(text.contains("[304]"));
        assert!(text.contains("gen=7"));
    }

    #[test]
    fn ss_format_read_result_200_full() {
        let result = elision::ReadResult::Full {
            content: "abc\ndef".to_string(),
            generation: 2,
        };
        let text = format_read_result(&result, "f.rs", None, None);
        assert!(text.contains("[200]"));
        assert!(text.contains("abc\ndef"));
    }

    #[test]
    fn ss_format_read_result_200_range() {
        let result = elision::ReadResult::Full {
            content: "a\nb\nc\nd".to_string(),
            generation: 1,
        };
        let text = format_read_result(&result, "f.rs", Some(2), Some(3));
        assert!(text.contains("lines 2-3"));
        assert!(text.contains("b"));
        assert!(text.contains("c"));
    }
}
