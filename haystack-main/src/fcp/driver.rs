use std::path::Path;
use serde_json::json;
use crate::fcp::tack::resolve_tack;
use crate::serve::socket::BROADCAST;
use crate::{append_audit, now_iso};

/// Execute a raw tack string from an agent or human.
///
/// 1. Resolves the tack string via fcp::tack.
/// 2. If resolved, broadcasts the event to presentation layers.
/// 3. Returns the resolved command (if any).
pub async fn execute_fcp(input: &str, ostk_dir: &Path) -> Option<String> {
    let res = resolve_tack(input, ostk_dir)?;
    
    if res.resolved {
        let cmd = res.command.clone().unwrap_or_default();
        let args = res.args.join(" ");
        let full_cmd = if args.is_empty() { cmd.clone() } else { format!("{} {}", cmd, args) };

        // Broadcast to TUI / Fleet
        BROADCAST.broadcast(json!({
            "event": "fcp.resolved",
            "input": input,
            "verb": res.verb,
            "command": full_cmd,
            "intent": res.intent.as_str(),
            "source": res.source.as_str(),
            "timestamp": now_iso()
        })).await;

        // Audit for persistence
        let root = ostk_dir.parent().unwrap_or(ostk_dir);
        let _ = append_audit(root, &json!({
            "event": "tack.resolved",
            "input": input,
            "verb": res.verb,
            "command": full_cmd,
            "tier": res.tier,
            "timestamp": now_iso()
        }));

        Some(full_cmd)
    } else {
        // Broadcast unknown verb for TUI suggestions
        BROADCAST.broadcast(json!({
            "event": "fcp.unknown",
            "input": input,
            "suggestions": res.suggestions,
            "timestamp": now_iso()
        })).await;
        
        None
    }
}
