//! Internal verb resolution registry (→1157).
//!
//! Maps .language verb names to Rust functions, eliminating fork+exec
//! for verbs that resolve to `ostk <cmd>`. The agent loop checks this
//! registry before falling through to shell execution.
//!
//! Resolution strategy:
//!   1. Check resolve::try_internal() — direct function call
//!   2. Fall through to handle_language_tool() — shell out via handle_bash()
//!
//! Migration is incremental: verbs move from shell → internal one at a time.
//! The CLI surface (`ostk <cmd>`) remains as the human entry point.

use std::path::Path;

use serde_json::Value;

use super::verb_ctx::VerbCtx;

/// Result of an internal verb execution.
pub struct InternalResult {
    pub output: String,
    pub success: bool,
}

/// Try to resolve a verb internally. Returns None if the verb should
/// fall through to shell execution.
///
/// Verbs are resolved by name (without the `:` prefix).
/// Input is the tool call's JSON arguments.
/// Root is the project root path.
pub fn try_internal(verb: &str, input: &Value, root: &Path) -> Option<InternalResult> {
    // Check if this verb has an internal handler
    // Verbs with no args — dispatch directly via VerbCtx
    let no_arg: Option<fn(&mut VerbCtx) -> Result<(), String>> = match verb {
        "boot" => Some(handle_boot),
        "clock" => Some(crate::commands::clock::run_verb),
        "reap" => Some(crate::commands::reap::run_verb),
        "diff" => Some(crate::commands::diff::run_verb),
        "ps" => Some(crate::commands::fleet::run_ps_verb),
        _ => None,
    };
    if let Some(handler) = no_arg {
        let mut ctx = VerbCtx::new(root, input);
        return match handler(&mut ctx) {
            Ok(()) => Some(InternalResult { output: ctx.into_output(), success: true }),
            Err(e) => Some(InternalResult { output: format!("{verb} error: {e}"), success: false }),
        };
    }

    // Verbs with args — extract from input JSON
    let str_arg = |key: &str| input.get(key).and_then(|v| v.as_str()).unwrap_or("");

    let handler: Option<Box<dyn FnOnce(&mut VerbCtx) -> Result<(), String>>> = match verb {
        "trace" => {
            let id = str_arg("id").to_string();
            Some(Box::new(move |ctx| crate::commands::trace::run_verb(ctx, &id)))
        }
        "draft" => {
            let title = str_arg("title").to_string();
            Some(Box::new(move |ctx| crate::commands::draft::run_verb(ctx, &title)))
        }
        "search" | "find" | "grep" => {
            let query = str_arg("query").to_string();
            let path = input.get("path").and_then(|v| v.as_str()).map(|s| s.to_string());
            let semantic = input.get("semantic").and_then(|v| v.as_bool()).unwrap_or(false);
            Some(Box::new(move |ctx| crate::commands::search::run_verb(ctx, &query, path.as_deref(), semantic)))
        }
        "correct" | "nudge" => {
            let agent = str_arg("agent").to_string();
            let message = str_arg("message").to_string();
            Some(Box::new(move |ctx| crate::commands::nudge::run_verb(ctx, &agent, &message)))
        }
        "shelve" => {
            let id = str_arg("id").to_string();
            Some(Box::new(move |ctx| crate::commands::shelve::run_shelve_verb(ctx, &id)))
        }
        "unshelve" => {
            let id = str_arg("id").to_string();
            Some(Box::new(move |ctx| crate::commands::shelve::run_unshelve_verb(ctx, &id)))
        }
        "audit" => {
            Some(Box::new(|ctx| crate::commands::audit::run_check_verb(ctx)))
        }
        // "secret" intentionally NOT internalized — secret values must never
        // flow through VerbCtx into LLM context. Secrets are kernel-mediated:
        // declared in HUMANFILE SECRET, resolved from env/vault, injected into
        // driver environments. CLI-only for human management.
        _ => None,
    };

    let handler = handler?;

    let mut ctx = VerbCtx::new(root, input);
    match handler(&mut ctx) {
        Ok(()) => Some(InternalResult { output: ctx.into_output(), success: true }),
        Err(e) => Some(InternalResult { output: format!("{verb} error: {e}"), success: false }),
    }
}

/// Return the list of verbs that have internal resolution.
pub fn internalized_verbs() -> &'static [&'static str] {
    &[
        "boot", "clock", "reap", "diff", "ps",
        "trace", "draft", "search", "find", "grep",
        "correct", "nudge", "shelve", "unshelve",
        "audit",
    ]
}

/// Return metadata for internalized verbs: (verb, layer, signature, doc).
/// Used by register_capability at boot to update .language entries.
pub fn internalized_verb_metadata() -> &'static [(&'static str, &'static str, &'static str, &'static str)] {
    &[
        ("boot", "kernel", "() \u{2192} (state)", "read state, report identity"),
        ("clock", "kernel", "() \u{2192} (time,id,audit)", "temporal position"),
        ("reap", "kernel", "() \u{2192} (count)", "clean dead agents"),
        ("diff", "kernel", "() \u{2192} (delta)", "session delta since boot"),
        ("ps", "kernel", "() \u{2192} (fleet)", "daemon and agent status"),
        ("trace", "kernel", "(id) \u{2192} (graph)", "trace needle, spec, or commit"),
        ("draft", "user", "(title) \u{2192} (path)", "create draft document"),
        ("search", "user", "(query, path?, semantic?) \u{2192} (matches)", "recursive content search"),
        ("find", "user", "(query, path?) \u{2192} (matches)", "recursive content search"),
        ("grep", "user", "(query, path?) \u{2192} (matches)", "recursive content search"),
        ("correct", "user", "(agent, message) \u{2192} ()", "send correction nudge"),
        ("nudge", "user", "(agent, message) \u{2192} ()", "inter-agent messaging"),
        ("shelve", "user", "(id) \u{2192} (snapshot)", "pause needle work"),
        ("unshelve", "user", "(id) \u{2192} ()", "resume needle work"),
        ("audit", "kernel", "() \u{2192} (report)", "audit integrity check"),
        // secret: CLI-only — values must not enter LLM context
    ]
}

// ── Verb handlers ──────────────────────────────────────────────────────────

/// Boot handler — uses build_register_dump which already returns String.
/// Wrapped in VerbCtx for uniform calling convention.
fn handle_boot(ctx: &mut VerbCtx) -> Result<(), String> {
    use std::fmt::Write;
    let ostk_dir = ctx.ostk_dir();
    let dump = crate::commands::boot::build_register_dump(ctx.root, &ostk_dir);
    write!(ctx, "{dump}").unwrap();
    Ok(())
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn setup_ostk_dir(name: &str) -> tempfile::TempDir {
        let tmp = tempfile::Builder::new()
            .prefix(&format!("ostk_resolve_{name}_"))
            .tempdir()
            .unwrap();
        let ostk = tmp.path().join(".ostk");
        fs::create_dir_all(&ostk).unwrap();
        fs::create_dir_all(ostk.join("needles")).unwrap();
        fs::write(ostk.join("needles/counter"), "0").unwrap();
        fs::write(ostk.join("needles/issues.jsonl"), "").unwrap();
        fs::write(ostk.join(".language"), "").unwrap();
        fs::write(ostk.join("version"), env!("CARGO_PKG_VERSION")).unwrap();
        tmp
    }

    #[test]
    fn test_try_internal_boot_returns_output() {
        let tmp = setup_ostk_dir("boot");
        let result = try_internal("boot", &serde_json::json!({}), tmp.path());
        assert!(result.is_some(), "boot should resolve internally");
        let r = result.unwrap();
        assert!(r.success);
        assert!(!r.output.is_empty(), "boot output should not be empty");
    }

    #[test]
    fn test_try_internal_clock_returns_output() {
        let tmp = setup_ostk_dir("clock");
        let result = try_internal("clock", &serde_json::json!({}), tmp.path());
        assert!(result.is_some(), "clock should resolve internally");
        let r = result.unwrap();
        assert!(r.success);
        assert!(r.output.contains("ostk clock"), "clock output should contain header");
        assert!(r.output.contains("wall"), "clock output should contain wall time");
    }

    #[test]
    fn test_try_internal_reap_returns_output() {
        let tmp = setup_ostk_dir("reap");
        // Write empty agents.jsonl so reap has something to work with
        fs::write(tmp.path().join(".ostk/agents.jsonl"), "").unwrap();
        let result = try_internal("reap", &serde_json::json!({}), tmp.path());
        assert!(result.is_some(), "reap should resolve internally");
        let r = result.unwrap();
        assert!(r.success);
        assert!(r.output.contains("reap:"), "reap output should contain status");
    }

    #[test]
    fn test_try_internal_unknown_returns_none() {
        let tmp = setup_ostk_dir("unknown");
        let result = try_internal("nonexistent_verb", &serde_json::json!({}), tmp.path());
        assert!(result.is_none(), "unknown verb should return None (fall through to shell)");
    }

    #[test]
    fn test_internalized_verbs_matches_registry() {
        let tmp = setup_ostk_dir("verbs");
        fs::write(tmp.path().join(".ostk/agents.jsonl"), "").unwrap();
        for verb in internalized_verbs() {
            let result = try_internal(verb, &serde_json::json!({}), tmp.path());
            assert!(result.is_some(), "internalized verb '{verb}' should resolve");
        }
    }
}
