//! Context peek — modal showing kernel context breakdown.
//!
//! Pure data-gathering function: returns styled lines for the modal.
//! The modal component handles rendering.

use std::path::Path;

use crate::cpu::session::SessionManager;
use crate::fcp_screen::components::status::{fmt_tokens_f, StatusCache};
use crate::fcp_screen::protocol::{self, Color};
use ratatui::text::Line;

/// Build styled lines for the context peek overlay.
///
/// Gathers data from SessionManager and StatusCache to show:
/// - Model + context usage summary
/// - Breakdown: system prompt, boot.md, .language, messages, free
/// - Session tokens (input/output)
/// - Needle and fleet counts
pub fn build_context_overlay(mgr: &SessionManager, status_cache: &StatusCache) -> Vec<Line<'static>> {
    let session = mgr.active_session();
    let model = &session.config.model;
    let budget = session.config.context_budget
        .unwrap_or_else(|| crate::cpu::params::model_context_budget(&session.config.model));
    let context_tokens = status_cache.last_context_tokens.unwrap_or(0);

    let short_model = super::model_short_name(model);

    let pct_total = if budget > 0 {
        ((context_tokens as f64 / budget as f64) * 100.0).min(100.0)
    } else { 0.0 };

    // --- Estimate component sizes ---
    let root = mgr.boot_context.root_path();

    // System prompt size: use actual system_prompt from session config
    let sys_prompt_chars = session.config.system_prompt.as_ref()
        .map(|s| s.len())
        .unwrap_or(0);
    let sys_prompt_toks = (sys_prompt_chars / 4) as u64;

    let sd = crate::state_dir(root);
    let boot_md_toks = estimate_file_tokens(&sd, "boot.md");
    let language_toks = estimate_file_tokens(&sd, ".language");

    // Messages = context used - system prompt estimate (clamped)
    let overhead = sys_prompt_toks;
    let messages_toks = context_tokens.saturating_sub(overhead);
    let free_toks = budget.saturating_sub(context_tokens);

    // --- Needle and fleet counts ---
    let open_needles = crate::read_needles(root)
        .map(|n| n.iter()
            .filter(|v| v.get("status").and_then(|s| s.as_str()) == Some("open"))
            .count())
        .unwrap_or(0);

    let fleet_count = crate::kernel::identity::read_agents(&crate::state_dir(root))
        .iter()
        .filter(|a| a.status == "active")
        .count();

    // --- Build lines ---
    let mut lines = Vec::new();

    // First line = modal title (extracted by ShowOverlay handler for the border)
    lines.push(protocol::line_from_spans(vec![
        protocol::plain(format!(
            "{short_model} \u{00b7} {}/{} ({:.0}%)",
            fmt_tokens_f(context_tokens), fmt_tokens_f(budget), pct_total,
        )),
    ]));

    // Section header
    lines.push(protocol::line_from_spans(vec![protocol::dim("  Usage")]));

    // Breakdown tree
    let pct = |toks: u64| -> String {
        if budget > 0 {
            format!("({:.1}%)", (toks as f64 / budget as f64) * 100.0)
        } else {
            String::new()
        }
    };

    lines.push(protocol::line_from_spans(vec![
        protocol::dim("  \u{251c} System prompt     "),
        protocol::plain(format!("{:<7}", fmt_tokens_f(sys_prompt_toks))),
        protocol::dim(pct(sys_prompt_toks)),
    ]));
    lines.push(protocol::line_from_spans(vec![
        protocol::dim("  \u{251c} boot.md           "),
        protocol::plain(format!("{:<7}", fmt_tokens_f(boot_md_toks))),
        protocol::dim(pct(boot_md_toks)),
    ]));
    lines.push(protocol::line_from_spans(vec![
        protocol::dim("  \u{251c} .language         "),
        protocol::plain(format!("{:<7}", fmt_tokens_f(language_toks))),
        protocol::dim(pct(language_toks)),
    ]));
    lines.push(protocol::line_from_spans(vec![
        protocol::dim("  \u{251c} Messages          "),
        protocol::plain(format!("{:<7}", fmt_tokens_f(messages_toks))),
        protocol::dim(pct(messages_toks)),
    ]));

    let free_color = if free_toks < budget / 10 { Color::Red }
        else if free_toks < budget / 4 { Color::Yellow }
        else { Color::Green };
    lines.push(protocol::line_from_spans(vec![
        protocol::dim("  \u{2514} Free              "),
        protocol::colored(format!("{:<7}", fmt_tokens_f(free_toks)), free_color),
        protocol::dim(pct(free_toks)),
    ]));

    lines.push(protocol::line_plain(""));

    // Session tokens + needle/fleet counts — full cache breakdown
    let st = &session.session_tokens;
    lines.push(protocol::line_from_spans(vec![
        protocol::dim("  Session: "),
        protocol::colored(format!("\u{2193}{}", fmt_tokens_f(st.input)), Color::Cyan),
        protocol::dim(" c:"),
        protocol::colored(fmt_tokens_f(st.cache_read).to_string(), Color::Cyan),
        protocol::dim(" w:"),
        protocol::colored(fmt_tokens_f(st.cache_create).to_string(), Color::Cyan),
        protocol::plain(" "),
        protocol::colored(format!("\u{2191}{}", fmt_tokens_f(st.output)), Color::Cyan),
    ]));

    lines.push(protocol::line_from_spans(vec![
        protocol::dim("  Needles: "),
        protocol::colored(format!("{open_needles} open"), Color::Yellow),
        protocol::dim(" \u{00b7} Fleet: "),
        protocol::colored(format!("{fleet_count}"), Color::Green),
    ]));

    lines.push(protocol::line_plain(""));
    lines.push(protocol::line_from_spans(vec![protocol::dim("  press any key to dismiss")]));

    lines
}

/// Estimate token count for a file (chars / 4).
fn estimate_file_tokens(root: &Path, relative: &str) -> u64 {
    std::fs::read_to_string(root.join(relative))
        .map(|c| (c.len() / 4) as u64)
        .unwrap_or(0)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn estimate_file_tokens_nonexistent() {
        let toks = estimate_file_tokens(std::path::Path::new("/nonexistent"), "file.txt");
        assert_eq!(toks, 0);
    }

    #[test]
    fn estimate_file_tokens_real() {
        let tmp = tempfile::tempdir().unwrap();
        let content = "a".repeat(400); // 400 chars -> 100 tokens
        std::fs::write(tmp.path().join("test.txt"), &content).unwrap();
        let toks = estimate_file_tokens(tmp.path(), "test.txt");
        assert_eq!(toks, 100);
    }

    #[test]
    fn build_context_overlay_basic() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();
        std::fs::create_dir_all(root.join(".ostk/needles")).unwrap();
        std::fs::write(root.join(".ostk/boot.md"), "# Boot\nHello world test").unwrap();
        std::fs::write(root.join(".ostk/.language"), ":deploy | 1 | q").unwrap();
        std::fs::write(root.join(".ostk/agents.jsonl"), "").unwrap();
        std::fs::write(root.join(".ostk/needles/issues.jsonl"), "").unwrap();

        let config = crate::cpu::agent_loop::LoopConfig {
            model: "claude-sonnet-4-5-20250929".into(),
            system_prompt: Some("You are an agent.".into()),
            tools: vec![],
            max_tokens: 1024,
            max_turns: None,
            context_budget: Some(200_000),
            permission_mode: crate::cpu::PermissionMode::Governed,
            fast_mode: false,
            root: Some(root.clone()),
            betas: vec![],
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };
        let mgr = SessionManager::new(root.clone(), config).unwrap();
        let status_cache = StatusCache::new(&root);

        let lines = build_context_overlay(&mgr, &status_cache);

        // Verify structure
        assert!(lines.len() >= 10, "should have at least 10 lines, got {}", lines.len());

        // Collect all text
        let all_text: String = lines.iter()
            .flat_map(|l| l.spans.iter().map(|s| s.content.to_string()))
            .collect();

        // Should contain model short name
        assert!(all_text.contains("sonnet"), "should contain model name, got: {all_text}");
        // Should contain tree markers
        assert!(all_text.contains("\u{251c}"), "should contain tree branch marker");
        assert!(all_text.contains("\u{2514}"), "should contain tree end marker");
        // Should contain category labels
        assert!(all_text.contains("System prompt"), "should show System prompt");
        assert!(all_text.contains("boot.md"), "should show boot.md");
        assert!(all_text.contains(".language"), "should show .language");
        assert!(all_text.contains("Messages"), "should show Messages");
        assert!(all_text.contains("Free"), "should show Free");
        // Should contain session and needle info
        assert!(all_text.contains("Session"), "should show Session");
        assert!(all_text.contains("Needles"), "should show Needles");
        assert!(all_text.contains("Fleet"), "should show Fleet");
        // Dismiss hint
        assert!(all_text.contains("dismiss"), "should show dismiss hint");
    }

    #[test]
    fn build_context_overlay_no_budget() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();
        std::fs::create_dir_all(crate::state_dir(&root)).unwrap();

        let config = crate::cpu::agent_loop::LoopConfig {
            model: "claude-opus-4-6".into(),
            system_prompt: Some("Agent.".into()),
            tools: vec![],
            max_tokens: 1024,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::Governed,
            fast_mode: false,
            root: Some(root.clone()),
            betas: vec![],
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };
        let mgr = SessionManager::new(root.clone(), config).unwrap();
        let status_cache = StatusCache::new(&root);

        let lines = build_context_overlay(&mgr, &status_cache);
        assert!(!lines.is_empty(), "should produce lines even without budget");

        let all_text: String = lines.iter()
            .flat_map(|l| l.spans.iter().map(|s| s.content.to_string()))
            .collect();
        assert!(all_text.contains("opus"), "should show opus model");
    }
}
