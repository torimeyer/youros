/// Nudge queue — the interrupt primitive for the LLM CPU.
///
/// An orchestrator can inject context into an agent's next tool response
/// by pushing a nudge into the agent's queue. The MCP serve dispatch reads
/// and drains the queue on every response.
///
/// Storage: .ostk/nudges/<agent-alias>.jsonl
/// Concurrency: flock on the nudge file itself.
use fs2::FileExt;
use std::fs;
use std::io::Write;
use std::path::Path;

/// Append a nudge message to the agent's queue.
pub fn push_nudge(ostk_dir: &Path, agent: &str, message: &str) -> Result<(), String> {
    let nudge_dir = ostk_dir.join("nudges");
    fs::create_dir_all(&nudge_dir).map_err(|e| format!("failed to create nudges dir: {e}"))?;

    let nudge_path = nudge_dir.join(format!("{agent}.jsonl"));
    let mut file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&nudge_path)
        .map_err(|e| format!("failed to open nudge file: {e}"))?;

    file.lock_exclusive()
        .map_err(|e| format!("failed to lock nudge file: {e}"))?;

    let json = serde_json::json!({ "message": message });
    let mut line = serde_json::to_string(&json).map_err(|e| e.to_string())?;
    line.push('\n');
    file.write_all(line.as_bytes())
        .map_err(|e| format!("failed to write nudge: {e}"))?;

    // flock released on drop
    Ok(())
}

/// Read and clear all pending nudges for an agent. Returns Vec<String>.
///
/// Checks if the nudge service is registered and alive before reading.
/// If the service is not registered (no .language entry) or is dead,
/// returns an empty vec to avoid delivering to void.
pub fn pop_nudges(ostk_dir: &Path, agent: &str) -> Result<Vec<String>, String> {
    // Check if nudge service is registered and alive
    let root = ostk_dir.parent().unwrap_or(ostk_dir);
    if !crate::language::is_capability_alive(root, "nudge") {
        return Ok(vec![]);
    }

    let nudge_path = ostk_dir.join("nudges").join(format!("{agent}.jsonl"));
    if !nudge_path.exists() {
        return Ok(vec![]);
    }

    let file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(&nudge_path)
        .map_err(|e| format!("failed to open nudge file: {e}"))?;

    file.lock_exclusive()
        .map_err(|e| format!("failed to lock nudge file: {e}"))?;

    let content =
        fs::read_to_string(&nudge_path).map_err(|e| format!("failed to read nudge file: {e}"))?;

    let mut messages = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(line)
            && let Some(msg) = v.get("message").and_then(|m| m.as_str())
        {
            messages.push(msg.to_string());
        }
    }

    // Clear the file (truncate to 0)
    file.set_len(0)
        .map_err(|e| format!("failed to clear nudge file: {e}"))?;

    // flock released on drop
    Ok(messages)
}

/// Format nudge messages for injection into a tool response.
pub fn format_nudges(nudges: &[String]) -> String {
    nudges
        .iter()
        .map(|m| format!("[nudge] {m}"))
        .collect::<Vec<_>>()
        .join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn temp_ostk_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join("ostk_test_nudge").join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        // Register the nudge service as alive so pop_nudges checks pass.
        // Root is the parent of the ostk_dir (matches pop_nudges logic).
        let root = dir.parent().unwrap();
        let state = crate::state_dir(root);
        fs::create_dir_all(&state).unwrap();
        crate::language::register_capability(
            root,
            "nudge",
            "service",
            ".ostk/nudges/",
            "(agent,msg) -> ()",
            "inter-agent messaging",
            true,
        )
        .unwrap();
        dir
    }

    #[test]
    fn test_push_pop_nudge() {
        let dir = temp_ostk_dir("push_pop");
        push_nudge(&dir, "agent-1", "use generation instead of gen").unwrap();
        let nudges = pop_nudges(&dir, "agent-1").unwrap();
        assert_eq!(nudges.len(), 1);
        assert_eq!(nudges[0], "use generation instead of gen");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_push_multiple_pop_all() {
        let dir = temp_ostk_dir("multi_pop");
        push_nudge(&dir, "agent-1", "first nudge").unwrap();
        push_nudge(&dir, "agent-1", "second nudge").unwrap();
        push_nudge(&dir, "agent-1", "third nudge").unwrap();

        let nudges = pop_nudges(&dir, "agent-1").unwrap();
        assert_eq!(nudges.len(), 3);
        assert_eq!(nudges[0], "first nudge");
        assert_eq!(nudges[1], "second nudge");
        assert_eq!(nudges[2], "third nudge");

        // Queue should be cleared after pop
        let nudges_after = pop_nudges(&dir, "agent-1").unwrap();
        assert!(nudges_after.is_empty());

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_empty_queue() {
        let dir = temp_ostk_dir("empty_queue");
        let nudges = pop_nudges(&dir, "agent-1").unwrap();
        assert!(nudges.is_empty());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_format_nudges() {
        let nudges = vec!["first message".to_string(), "second message".to_string()];
        let formatted = format_nudges(&nudges);
        assert_eq!(formatted, "[nudge] first message\n[nudge] second message");
    }

    #[test]
    fn test_format_nudges_empty() {
        let formatted = format_nudges(&[]);
        assert_eq!(formatted, "");
    }

    #[test]
    fn test_concurrent_pushes() {
        use std::sync::Arc;
        use std::thread;

        let dir = Arc::new(temp_ostk_dir("concurrent"));
        let mut handles = vec![];

        for i in 0..10 {
            let dir = Arc::clone(&dir);
            handles.push(thread::spawn(move || {
                push_nudge(&dir, "agent-1", &format!("nudge-{i}")).unwrap();
            }));
        }

        for h in handles {
            h.join().unwrap();
        }

        let nudges = pop_nudges(&dir, "agent-1").unwrap();
        assert_eq!(nudges.len(), 10);
        // All nudges should be present (order may vary due to concurrency)
        for i in 0..10 {
            assert!(nudges.contains(&format!("nudge-{i}")), "missing nudge-{i}");
        }

        let _ = fs::remove_dir_all(&*dir);
    }

    #[test]
    fn test_separate_agents() {
        let dir = temp_ostk_dir("separate_agents");
        push_nudge(&dir, "agent-1", "for agent 1").unwrap();
        push_nudge(&dir, "agent-2", "for agent 2").unwrap();

        let n1 = pop_nudges(&dir, "agent-1").unwrap();
        let n2 = pop_nudges(&dir, "agent-2").unwrap();
        assert_eq!(n1.len(), 1);
        assert_eq!(n2.len(), 1);
        assert_eq!(n1[0], "for agent 1");
        assert_eq!(n2[0], "for agent 2");

        let _ = fs::remove_dir_all(&dir);
    }
}
