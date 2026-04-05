//! Overlay helpers — fleet, work, help, audit log display.

use std::path::Path;

use crate::fcp_screen::protocol::{self, Color};
use crate::fcp_screen::renderer::AppState as Screen;

// render_post removed — boot POST rendering moved to components/bootloader.rs.

/// Fleet overlay — show active agents from agents.jsonl.
pub fn show_fleet(root: &Path, scr: &mut Screen) {
    let mut ls = vec![protocol::line_styled("  Fleet  ", Color::Cyan)];
    let agents = crate::kernel::identity::read_agents(&crate::state_dir(root));
    for agent in agents.iter().take(12) {
        let nm = &agent.alias;
        let st = &agent.status;
        let co = match st.as_str() { "active" => Color::Green, "exited" => Color::Gray, _ => Color::Yellow };
        let ts = agent.last_seen
            .find('T').and_then(|i| agent.last_seen.get(i+1..i+6))
            .unwrap_or("--:--");
        ls.push(protocol::line_from_spans(vec![
            protocol::plain(format!("  {nm:<16}")),
            protocol::colored(st, co),
            protocol::dim(format!("  {ts}"))]));
    }
    if ls.len() == 1 { ls.push(protocol::line_from_spans(vec![protocol::dim("  (no agents)")])); }
    ls.push(protocol::line_from_spans(vec![protocol::dim("  press any key to dismiss")]));
    scr.show_overlay(ls);
}

/// Work overlay — show open needles sorted by priority.
pub fn show_work(root: &Path, scr: &mut Screen) {
    let mut ls = vec![protocol::line_styled("  Work  ", Color::Yellow)];
    if let Ok(needles) = crate::read_needles(root) {
        let mut open: Vec<_> = needles.iter()
            .filter(|v| v.get("status").and_then(|s| s.as_str()) == Some("open")).collect();
        open.sort_by_key(|v| v.get("priority").and_then(|p| p.as_str()).unwrap_or("P2").to_string());
        for n in open.iter().take(10) {
            let (id, ti) = (n["id"].as_str().unwrap_or("?"), n["title"].as_str().unwrap_or("?"));
            let pr = n["priority"].as_str().unwrap_or("P2");
            let co = match pr { "P0" => Color::Red, "P1" => Color::Yellow, _ => Color::Gray };
            ls.push(protocol::line_from_spans(vec![
                protocol::colored(format!("  {id:<6}"), Color::Cyan),
                protocol::colored(format!("[{pr}] "), co), protocol::plain(ti)]));
        }
    }
    if ls.len() == 1 { ls.push(protocol::line_from_spans(vec![protocol::dim("  (no open needles)")])); }
    ls.push(protocol::line_from_spans(vec![protocol::dim("  press any key to dismiss")]));
    scr.show_overlay(ls);
}

/// Help overlay — keybinding reference.
pub fn show_help(scr: &mut Screen, model: &str, permission_label: &str) {
    scr.show_overlay(vec![
        protocol::line_styled("  Keybindings  ", Color::Green),
        protocol::line_plain("  :verb args  run ostk   free text  scheduling agent"),
        protocol::line_plain("  :quit/:q  exit   Ctrl-C/D  exit   Esc  clear input   :clear  reset session"),
        protocol::line_plain("  :model <name>  switch model   :models  list available models"),
        protocol::line_plain("  :mode <name>   switch permission mode (plan/governed/auto/autonomous)"),
        protocol::line_plain("  Alt+f  fleet   Alt+w  work   Alt+g  spheres   Alt+c  context   Alt+p  mode   Alt+m  model   Alt+d  debug   Alt+?  help"),
        protocol::line_plain("  Tab  autocomplete   Up/Down  history"),
        protocol::line_plain("  PgUp/Shift+Up  scroll up   PgDn/Shift+Down  scroll down   End  jump to bottom"),
        protocol::line_styled(format!("  current: {} | {}", model, permission_label), Color::Cyan),
        protocol::line_from_spans(vec![protocol::dim("  press any key to dismiss")])]);
}

/// Display last 20 lines of audit.jsonl formatted concisely.
pub fn display_audit_log(root: &Path, scr: &mut Screen) {
    let path = crate::state_dir(root).join("audit.jsonl");
    let content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(_) => {
            scr.append_line(
                protocol::line_styled("No audit log found.", Color::Red));
            return;
        }
    };
    let lines: Vec<&str> = content.lines().collect();
    let start = lines.len().saturating_sub(20);
    scr.append_line(
        protocol::line_styled(format!("  Audit Log (last {} of {})", lines.len() - start, lines.len()), Color::Cyan));
    for raw in &lines[start..] {
        let formatted = format_audit_line(raw);
        scr.append_line(protocol::line_plain(&formatted));
    }
}

/// Format a single audit.jsonl line into a concise display string.
pub fn format_audit_line(raw: &str) -> String {
    let v: serde_json::Value = match serde_json::from_str(raw) {
        Ok(v) => v,
        Err(_) => return raw.to_string(),
    };
    let event = v.get("event").and_then(|e| e.as_str()).unwrap_or("?");
    let ts = v.get("ts").and_then(|t| t.as_str()).unwrap_or("");
    let hhmm = ts.find('T').and_then(|i| ts.get(i+1..i+6)).unwrap_or("??:??");

    match event {
        "api.call" => {
            let model_raw = v.get("model").and_then(|m| m.as_str()).unwrap_or("?");
            let short_model = super::model_short_name(model_raw);
            let input_k = v.get("input_tokens").and_then(|t| t.as_u64()).unwrap_or(0) / 1000;
            let output_k = v.get("output_tokens").and_then(|t| t.as_u64()).unwrap_or(0) / 1000;
            let stop = v.get("stop_reason").and_then(|s| s.as_str()).unwrap_or("?");
            let tools = v.get("tool_calls").and_then(|t| t.as_u64()).unwrap_or(0);
            let tools_str = if tools > 0 { format!(" ({tools} tools)") } else { String::new() };
            format!("  {hhmm} api.call {short_model} \u{2193}{input_k}k \u{2191}{output_k}k {stop}{tools_str}")
        }
        "api.error" => {
            let error = v.get("error").and_then(|e| e.as_str()).unwrap_or("?");
            let short_err = if error.len() > 60 { &error[..60] } else { error };
            format!("  {hhmm} api.error {short_err}")
        }
        _ => {
            format!("  {hhmm} {event}")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn format_audit_api_call() {
        let line = r#"{"event":"api.call","model":"claude-sonnet-4-5-20250929","input_tokens":2000,"output_tokens":1000,"stop_reason":"end_turn","tool_calls":0,"ts":"2026-03-16T19:58:00Z"}"#;
        let formatted = format_audit_line(line);
        assert!(formatted.contains("19:58"), "should contain timestamp: {formatted}");
        assert!(formatted.contains("api.call"), "should contain event: {formatted}");
        assert!(formatted.contains("sonnet"), "should contain short model: {formatted}");
        assert!(formatted.contains("end_turn"), "should contain stop_reason: {formatted}");
    }

    #[test]
    fn format_audit_api_error() {
        let line = r#"{"event":"api.error","model":"claude-sonnet-4-5-20250929","error":"API error 400: Extra inputs not permitted","ts":"2026-03-16T19:57:00Z"}"#;
        let formatted = format_audit_line(line);
        assert!(formatted.contains("19:57"), "should contain timestamp: {formatted}");
        assert!(formatted.contains("api.error"), "should contain event: {formatted}");
        assert!(formatted.contains("Extra inputs"), "should contain error: {formatted}");
    }

    #[test]
    fn format_audit_api_call_with_tools() {
        let line = r#"{"event":"api.call","model":"claude-sonnet-4-5-20250929","input_tokens":4000,"output_tokens":2000,"stop_reason":"tool_use","tool_calls":3,"ts":"2026-03-16T19:55:00Z"}"#;
        let formatted = format_audit_line(line);
        assert!(formatted.contains("(3 tools)"), "should contain tool count: {formatted}");
        assert!(formatted.contains("tool_use"), "should contain stop_reason: {formatted}");
    }

    #[test]
    fn format_audit_generic_event() {
        let line = r#"{"event":"tack.resolved","ts":"2026-03-16T20:00:00Z"}"#;
        let formatted = format_audit_line(line);
        assert!(formatted.contains("20:00"), "should contain timestamp: {formatted}");
        assert!(formatted.contains("tack.resolved"), "should contain event: {formatted}");
    }

    #[test]
    fn format_audit_invalid_json() {
        let line = "this is not json";
        let formatted = format_audit_line(line);
        assert_eq!(formatted, line, "invalid JSON should pass through unchanged");
    }
}
