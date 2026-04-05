//! MCP tool handler for `ostk_context_release` — signal that the model has processed turns before a given number.
//!
//! →965: Bookkeeping operation — logs a release event to audit.jsonl.

use serde_json::json;
use std::path::Path;

use crate::serve::types::ToolError;

/// Handle a `context_release` tool call.
pub fn handle(
    before_turn: i64,
    reason: Option<&str>,
    ostk_dir: &Path,
    agent_alias: Option<&str>,
) -> Result<serde_json::Value, ToolError> {
    if before_turn < 1 {
        return Err(ToolError::invalid_params(
            "before_turn must be a positive integer",
        ));
    }

    let alias = agent_alias.unwrap_or("unknown");
    let root = ostk_dir.parent().unwrap_or(ostk_dir);

    let event = json!({
        "event": "context.released",
        "before_turn": before_turn,
        "reason": reason.unwrap_or(""),
        "agent": alias,
        "ts": crate::now_iso(),
    });

    crate::append_audit(root, &event)
        .map_err(|e| ToolError::internal(format!("failed to write audit: {e}")))?;

    let reason_display = reason.unwrap_or("");
    let output = if reason_display.is_empty() {
        format!(
            "context before turn {} released — logged to audit\n(session transcript preserved in .ostk/sessions/ for recovery)",
            before_turn
        )
    } else {
        format!(
            "context before turn {} released — logged to audit\nreason: \"{}\"\n(session transcript preserved in .ostk/sessions/ for recovery)",
            before_turn, reason_display
        )
    };

    Ok(json!({ "text": output }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn tmpdir(suffix: &str) -> std::path::PathBuf {
        use std::sync::atomic::{AtomicU32, Ordering};
        static CTR: AtomicU32 = AtomicU32::new(0);
        let n = CTR.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("hs_ctx_release_{suffix}_{pid}_{n}"))
    }

    #[test]
    fn context_release_invalid_turn() {
        let tmp = tmpdir("invalid");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();
        let result = handle(0, None, &ostk, Some("worker-1"));
        assert!(result.is_err());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn context_release_logs_to_audit() {
        let tmp = tmpdir("audit");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();

        let result =
            handle(20, Some("orientation complete"), &ostk, Some("worker-1")).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("context before turn 20 released"));
        assert!(text.contains("orientation complete"));

        // Verify audit file was written
        let audit = fs::read_to_string(ostk.join("audit.jsonl")).unwrap();
        assert!(audit.contains("context.released"));
        assert!(audit.contains("\"before_turn\":20"));
        assert!(audit.contains("worker-1"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn context_release_no_reason() {
        let tmp = tmpdir("noreason");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();

        let result = handle(5, None, &ostk, Some("worker-1")).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("context before turn 5 released"));
        assert!(!text.contains("reason:"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn context_release_unknown_agent() {
        let tmp = tmpdir("unknown");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();

        let result = handle(10, None, &ostk, None).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("context before turn 10 released"));

        let audit = fs::read_to_string(ostk.join("audit.jsonl")).unwrap();
        assert!(audit.contains("\"agent\":\"unknown\""));
        let _ = fs::remove_dir_all(&tmp);
    }
}
