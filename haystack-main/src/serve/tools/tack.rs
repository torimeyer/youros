//! MCP tool handler for `tack` — human intent resolution via fcp-tack.

use serde_json::json;

use crate::fcp::tack as fcp;
use crate::serve::types::{TackParams, ToolError};

/// Handle a `tack` tool call.
pub fn handle(params: TackParams, ostk_dir: &std::path::Path) -> Result<serde_json::Value, ToolError> {
    let input = &params.input;

    let parsed = fcp::parse(input).ok_or_else(|| {
        ToolError::invalid_params(format!(
            "Not a tack expression. Expected :verb, .? query, →NNN, or :: target. Got: {input}"
        ))
    })?;

    let language_verbs = fcp::load_language_verbs(ostk_dir);
    let humanfile_verbs = fcp::load_humanfile_verbs(ostk_dir);
    let resolution = fcp::resolve(&parsed, &humanfile_verbs, &language_verbs);

    // →596: Record resolution event for confidence gradient
    let root = ostk_dir.parent().unwrap_or(ostk_dir);
    let _ = crate::append_audit(root, &json!({
        "event": "tack.resolved",
        "input": input,
        "verb": resolution.verb,
        "tier": resolution.tier,
        "source": resolution.source.as_str(),
        "resolved": resolution.resolved,
        "timestamp": crate::now_iso()
    }));

    Ok(json!({
        "resolved": resolution.resolved,
        "verb": resolution.verb,
        "intent": resolution.intent.as_str(),
        "command": resolution.command,
        "args": resolution.args,
        "source": resolution.source.as_str(),
        "tier": resolution.tier,
        "confidence": resolution.confidence,
        "suggestions": resolution.suggestions,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn handle_valid_command() {
        let params = TackParams {
            input: ":compile --dry-run".to_string(),
        };
        let result = handle(params, Path::new("/tmp/nonexistent")).unwrap();
        assert_eq!(result["resolved"], true);
        assert_eq!(result["command"], "compile");
        assert_eq!(result["intent"], "command");
        assert_eq!(result["source"], "static");
    }

    #[test]
    fn handle_needle_ref() {
        let params = TackParams {
            input: "→437".to_string(),
        };
        let result = handle(params, Path::new("/tmp/nonexistent")).unwrap();
        assert_eq!(result["resolved"], true);
        assert_eq!(result["command"], "show");
        assert_eq!(result["intent"], "needle_ref");
    }

    #[test]
    fn handle_unknown_verb() {
        let params = TackParams {
            input: ":zzzzz".to_string(),
        };
        let result = handle(params, Path::new("/tmp/nonexistent")).unwrap();
        assert_eq!(result["resolved"], false);
        assert!(result["command"].is_null());
    }

    #[test]
    fn handle_invalid_input() {
        let params = TackParams {
            input: "just text".to_string(),
        };
        let result = handle(params, Path::new("/tmp/nonexistent"));
        assert!(result.is_err());
    }
}
