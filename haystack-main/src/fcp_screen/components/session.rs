//! Session broadcast receiver — daemon event feed for debug panel.
//!
//! load_session/save_session removed — session persistence is handled by
//! cpu::session::SessionManager. Only drain_broadcasts remains.

use crate::fcp_screen::protocol::{self, Color};
use crate::fcp_screen::renderer::AppState as Screen;

pub fn drain_broadcasts(
    broadcast_rx: &Option<std::sync::mpsc::Receiver<serde_json::Value>>,
    scr: &mut Screen,
) {
    if let Some(rx) = broadcast_rx {
        while let Ok(msg) = rx.try_recv() {
            let summary = if let Some(event) = msg.get("event").and_then(|v| v.as_str()) {
                if let Some(detail) = msg.get("detail").and_then(|v| v.as_str()) {
                    format!("[broadcast] {event}: {detail}")
                } else {
                    format!("[broadcast] {event}")
                }
            } else {
                let s = msg.to_string();
                let truncated: String = s.chars().take(80).collect();
                format!("[broadcast] {truncated}")
            };
            scr.debug_log(
                protocol::line_styled(summary, Color::Cyan));
        }
    }
}
