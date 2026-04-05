//! Render CpuEvents from SessionManager into screen mutations.
//!
//! Extracted from app.rs (→811) to keep the event loop file focused on
//! input dispatch and session orchestration.

use crate::cpu::agent_loop::CpuEvent;
use crate::cpu::session::SessionManager;
use crate::fcp_screen::protocol::{self, Color};
use crate::fcp_screen::renderer::AppState as Screen;

use super::markdown;
use super::model_badge_color;
use super::model_short_name;
use super::spinner::SpinnerState;
use super::status::{fmt_tokens, fmt_tokens_f, StatusCache};
use super::tool_display;

/// Render a single CpuEvent (used by daemon event polling path →842).
///
/// →929: The daemon path deserializes CpuEvents from JSON — they never flow
/// through SessionManager::drain_events(), so process_event() must be called
/// here to accumulate token counts and reset the busy flag. Without this,
/// session_tokens stays at zero (↓0 c:0 ↑0 $0.00 in status bar).
pub fn render_single_event(
    ev: &CpuEvent, mgr: &mut SessionManager, scr: &mut Screen,
    spinner: &mut SpinnerState, got_text: &mut bool,
    status_cache: &mut StatusCache,
    streaming_buffer: &mut String,
) {
    spinner.touch(); // Reset stall timer on every event
    // Accumulate tokens + update busy flag (mirrors drain_events path)
    mgr.active_session_mut().process_event(ev);
    render_one_event(ev, mgr, scr, spinner, got_text, status_cache, streaming_buffer);
}

/// Render result from render_events — carries both approval info and queued text.
pub struct RenderResult {
    /// `Some((tool_name, tool_input_preview))` if an `ApprovalNeeded` event was found.
    pub approval_needed: Option<(String, String)>,
    /// A ToolResult was processed — caller should check for staged text redirect.
    pub tool_completed: bool,
}

/// Render CpuEvents from SessionManager into screen mutations.
/// Token accumulation and busy-flag reset already handled by SessionManager::drain_events.
pub fn render_events(
    mgr: &mut SessionManager, scr: &mut Screen,
    spinner: &mut SpinnerState, got_text: &mut bool,
    status_cache: &mut StatusCache,
    streaming_buffer: &mut String,
) -> RenderResult {
    let (events, _queued_text) = mgr.drain_events();
    if !events.is_empty() {
        spinner.touch(); // Reset stall timer when events arrive
    }
    let mut approval_needed = None;
    let mut tool_completed = false;
    for ev in events {
        if let CpuEvent::ApprovalNeeded { ref tool_name, ref tool_input_preview } = ev {
            approval_needed = Some((tool_name.clone(), tool_input_preview.clone()));
            continue;
        }
        if matches!(ev, CpuEvent::ToolResult { .. }) {
            tool_completed = true;
        }
        render_one_event(&ev, mgr, scr, spinner, got_text, status_cache, streaming_buffer);
    }
    RenderResult { approval_needed, tool_completed }
}

fn dbg_ts() -> String {
    let secs = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default().as_secs();
    let (hh, mm, ss) = ((secs % 86400) / 3600, (secs % 3600) / 60, secs % 60);
    format!("{hh:02}:{mm:02}:{ss:02}")
}

fn render_one_event(
    ev: &CpuEvent, mgr: &mut SessionManager, scr: &mut Screen,
    spinner: &mut SpinnerState, got_text: &mut bool,
    status_cache: &mut StatusCache,
    streaming_buffer: &mut String,
) {
    // Any non-TextDelta/non-TextComplete event finalizes streaming state.
    // →924: TextComplete handles its own finalization — if it runs through the
    // generic finalizer first, empty TextComplete("") deletes streaming lines
    // without replacing them (redirect causes text to vanish).
    if !matches!(ev, CpuEvent::TextDelta(_) | CpuEvent::TextComplete(_))
        && scr.streaming_line_count() > 0
    {
        scr.finalize_streaming();
        streaming_buffer.clear();
    }

    match ev {
            CpuEvent::TextDelta(chunk) => {
                spinner.touch();
                streaming_buffer.push_str(chunk);
                // Use the same markdown styler as TextComplete so the
                // streaming→final transition is invisible. style_ai_response
                // is lightweight (~20 lines, inline formatting only).
                let model_id = &mgr.active_session().config.model;
                let model_tag = model_short_name(model_id);
                let badge_color = model_badge_color(model_id); // →903: provider-aware color
                let styled_lines = markdown::style_ai_response(streaming_buffer);
                let mut styled = Vec::new();
                // Empty line separator on first chunk
                if scr.streaming_line_count() == 0 {
                    styled.push(protocol::line_plain(""));
                }
                for (i, line) in styled_lines.into_iter().enumerate() {
                    if i == 0 {
                        // Prepend model tag — same as TextComplete
                        let mut spans = vec![protocol::colored(format!("[{}] ", model_tag), badge_color)];
                        spans.extend(line.spans);
                        styled.push(protocol::line_from_spans(spans));
                    } else {
                        styled.push(line);
                    }
                }
                scr.replace_streaming(styled);
            }
            CpuEvent::TextComplete(t) => {
                if t.trim().is_empty() && scr.streaming_line_count() > 0 {
                    // →924: Empty TextComplete (e.g. redirect) — promote streaming
                    // lines to permanent by just resetting the streaming counter.
                    // The lines stay in the buffer; they're no longer "streaming".
                    scr.promote_streaming();
                    streaming_buffer.clear();
                } else {
                    streaming_buffer.clear();
                    scr.finalize_streaming();
                }
                if !t.trim().is_empty() {
                    *got_text = true;
                    scr.append_line(protocol::line_plain(""));
                    let styled_lines = markdown::style_ai_response(t);
                    for (i, styled) in styled_lines.into_iter().enumerate() {
                        if i == 0 {
                            // Prepend model tag to first line — →903: provider-aware color
                            let model_id = &mgr.active_session().config.model;
                            let model_tag = model_short_name(model_id);
                            let badge_color = model_badge_color(model_id);
                            let mut spans = vec![protocol::colored(format!("[{}] ", model_tag), badge_color)];
                            spans.extend(styled.spans);
                            scr.append_line(protocol::line_from_spans(spans));
                        } else {
                            scr.append_line(styled);
                        }
                    }
                    scr.debug_log(protocol::line_plain(format!("{} ai: {} chars", dbg_ts(), t.len())));
                }
            }
            CpuEvent::ToolStart { name, .. } => {
                spinner.increment_tools();
                spinner.touch(); // Reset stall timer — tool execution can take minutes
                // Only update the left side — preserve the right side (model · ctx · cost).
                let right = scr.status_right.clone();
                scr.set_status(
                    protocol::line_from_spans(vec![
                        protocol::dim(format!("[working] {name} ({} tools)", spinner.tool_count()))]),
                    right,
                );
                scr.debug_log(protocol::line_plain(format!("{} tool: {name}", dbg_ts())));
            }
            CpuEvent::ToolInput { name, preview } => {
                // Show tool name + args in chat zone so user sees what's about to run
                let display_preview: String = preview.chars().take(120).collect();
                scr.append_line(protocol::line_from_spans(vec![
                    protocol::colored("  ▸ ", Color::Cyan),
                    protocol::bold(name.to_string()),
                    protocol::dim(format!(" {display_preview}")),
                ]));
            }
            CpuEvent::ToolResult { name, output, success } => {
                spinner.touch(); // Tool completed — reset stall timer
                // Debug log (always)
                let status = if *success { "ok" } else { "err" };
                let truncated: String = output.chars().take(40).collect();
                scr.debug_log(protocol::line_plain(format!("{} result: {status} ({truncated})", dbg_ts())));

                // Chat zone: human-readable tool result
                let display = tool_display::format_tool_result(name, output, *success);
                scr.append_line(display.prefix_line);
                for line in display.detail_lines {
                    scr.append_line(line);
                }

                // append_line already compensates scroll_offset when user_scrolled.
                // No explicit scroll needed — is_at_bottom means offset==0 already.
            }
            CpuEvent::Usage { .. } => {
                // Token accumulation handled by process_event in drain_events.
                // Push live counts to status bar so the human sees progress.
                let tokens = &mgr.active_session().session_tokens;
                let right = scr.status_right.clone();
                scr.set_status(
                    protocol::line_from_spans(vec![
                        protocol::dim(format!("[thinking \u{2193}{} c:{} \u{2191}{}]",
                            fmt_tokens_f(tokens.input), fmt_tokens_f(tokens.cache_read), fmt_tokens_f(tokens.output)))]),
                    right,
                );
                scr.debug_log(protocol::line_plain(format!("{} usage: \u{2193}{} c:{} w:{} \u{2191}{}",
                    dbg_ts(), fmt_tokens(tokens.input), fmt_tokens(tokens.cache_read),
                    fmt_tokens(tokens.cache_create), fmt_tokens(tokens.output))));
            }
            CpuEvent::PreCallTokenCount { tokens } => {
                // Track current context size (actual window usage, not cumulative)
                status_cache.last_context_tokens = Some(*tokens);
                // →928: Use model_context_budget fallback when context_budget is None,
                // matching build_params. Without this, Opus shows 96k/200k=48% instead
                // of 96k/1M=10% because the status bar only checked the explicit budget.
                let budget = mgr.active_session().config.context_budget
                    .unwrap_or_else(|| crate::cpu::params::model_context_budget(&mgr.active_session().config.model));
                if budget > 0 {
                    let pct = ((*tokens as f64 / budget as f64) * 100.0).min(100.0) as u8;
                    status_cache.context_pct = Some(pct);
                }
                let right = scr.status_right.clone();
                scr.set_status(
                    protocol::line_from_spans(vec![
                        protocol::dim(format!("[context] {} tokens", fmt_tokens(*tokens)))]),
                    right,
                );
            }
            CpuEvent::TurnComplete { .. } => {
                // busy reset + token accumulation handled by process_event
                // Show per-turn output + cost (clean user-facing line)
                let ctx = status_cache.last_context_tokens.unwrap_or(0);
                let tokens = &mgr.active_session().session_tokens;
                let model = &mgr.active_session().config.model;
                let turn_cost = tokens.last_turn_cost(model);
                let elapsed = spinner.elapsed_secs();
                scr.append_line(protocol::line_from_spans(vec![protocol::dim(format!(
                    "[done] {elapsed}s \u{00b7} ctx:{} \u{2191}{} \u{00b7} ${turn_cost:.2}",
                    fmt_tokens_f(ctx), fmt_tokens_f(tokens.last_turn_output)))]));
                scr.debug_log(protocol::line_plain(format!("{} done: ctx:{} \u{2193}{} c:{} w:{} \u{2191}{} (session: \u{2193}{} c:{} w:{} \u{2191}{} ${:.2})",
                    dbg_ts(), fmt_tokens(ctx),
                    fmt_tokens(tokens.last_turn_input), fmt_tokens(tokens.last_turn_cache_read),
                    fmt_tokens(tokens.last_turn_cache_create), fmt_tokens(tokens.last_turn_output),
                    fmt_tokens(tokens.input), fmt_tokens(tokens.cache_read),
                    fmt_tokens(tokens.cache_create), fmt_tokens(tokens.output),
                    tokens.session_cost(model))));
                spinner.reset();
            }
            CpuEvent::Heartbeat => {
                // SSE keepalive — API connection alive, model thinking.
                // Explicit touch prevents the watchdog from firing.
                spinner.touch();
            }
            CpuEvent::ApprovalNeeded { .. } => {
                // Handled by the TUI state machine in app.rs, not the renderer.
                // The event triggers a TuiState transition to AwaitingApproval.
            }
            CpuEvent::Error(msg) => {
                if msg.contains("cancelled by operator") {
                    scr.append_line(protocol::line_styled("[cancelled]", Color::Yellow));
                } else {
                    scr.append_line(protocol::line_styled(format!("[error] {msg}"), Color::Red));
                }
                scr.debug_log(protocol::line_plain(format!("{} ERROR: {msg}", dbg_ts())));
                spinner.reset();
            }
        }
    }
