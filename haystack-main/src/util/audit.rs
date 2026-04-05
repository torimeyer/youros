use fs2::FileExt;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::Write;
use std::path::Path;

use super::paths::state_dir;

/// Append one JSON line to .ostk/audit.jsonl (O_APPEND + flock).
///
/// →902: flock protection prevents interleaved writes when multiple
/// agents or threads append concurrently.
///
/// →FIND-010: Truncation detection — records file size before and after
/// write. If the file shrinks between lock acquisitions, the audit log
/// has been tampered with and the write is refused.
pub fn append_audit(root: &Path, event: &Value) -> Result<(), String> {
    let path = state_dir(root).join("audit.jsonl");
    let file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("failed to open audit.jsonl: {e}"))?;
    file.lock_exclusive()
        .map_err(|e| format!("failed to flock audit.jsonl: {e}"))?;

    // →FIND-010: Record size under lock to detect truncation.
    let size_before = file.metadata()
        .map(|m| m.len())
        .unwrap_or(0);

    // Check cached size from previous write — detect external truncation.
    let size_file = state_dir(root).join("audit.size");
    if let Ok(cached) = fs::read_to_string(&size_file)
        && let Ok(expected) = cached.trim().parse::<u64>()
            && size_before < expected {
                file.unlock().ok();
                return Err(format!(
                    "audit.jsonl truncated: expected >= {expected} bytes, found {size_before}. \
                     Refusing write — audit integrity compromised."
                ));
            }

    let mut line = serde_json::to_string(event).map_err(|e| e.to_string())?;
    line.push('\n');
    // Use a BufWriter wrapping the &File reference so write_all goes through
    // the locked fd without moving ownership away from us.
    let mut writer = std::io::BufWriter::new(&file);
    writer.write_all(line.as_bytes())
        .map_err(|e| format!("failed to write audit: {e}"))?;
    writer.flush()
        .map_err(|e| format!("failed to flush audit: {e}"))?;

    // →FIND-010: Persist new size for next truncation check.
    let size_after = size_before + line.len() as u64;
    let _ = fs::write(&size_file, size_after.to_string());

    file.unlock()
        .map_err(|e| format!("failed to unlock audit.jsonl: {e}"))?;
    Ok(())
}

/// Read all audit events from .ostk/audit.jsonl
pub fn read_audit_events(root: &Path) -> Result<Vec<Value>, String> {
    let audit_path = state_dir(root).join("audit.jsonl");
    if !audit_path.exists() {
        return Ok(vec![]);
    }
    let content = fs::read_to_string(&audit_path).map_err(|e| format!("failed to read audit.jsonl: {e}"))?;
    let mut events = Vec::new();
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() { continue; }
        if let Ok(v) = serde_json::from_str(trimmed) {
            events.push(v);
        }
    }
    Ok(events)
}

/// Resolve all commit.remapped events transitively.
/// Returns a map of old_hash -> final_new_hash.
pub fn resolve_remaps(events: &[Value]) -> HashMap<String, String> {
    let mut remaps = HashMap::new();
    for event in events {
        if event["event"] == "commit.remapped"
            && let (Some(old), Some(new)) =
                (event["old_commit"].as_str(), event["new_commit"].as_str())
            {
                remaps.insert(old.to_string(), new.to_string());
            }
    }

    let mut resolved = HashMap::new();
    for old in remaps.keys() {
        let mut current = old.to_string();
        let mut visited = HashSet::new();
        visited.insert(current.clone());
        while let Some(next) = remaps.get(&current) {
            if visited.contains(next) {
                break; // cycle
            }
            visited.insert(next.clone());
            current = next.clone();
        }
        if current != *old {
            resolved.insert(old.clone(), current);
        }
    }
    resolved
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    fn tmpdir(prefix: &str) -> std::path::PathBuf {
        use std::sync::atomic::{AtomicU32, Ordering};
        static CTR: AtomicU32 = AtomicU32::new(0);
        let n = CTR.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("hs_audit_test_{prefix}_{pid}_{n}"))
    }

    #[test]
    fn test_append_audit_basic() {
        let tmp = tmpdir("basic");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();
        let event = serde_json::json!({"event": "test", "msg": "hello"});
        append_audit(&tmp, &event).unwrap();
        let content = fs::read_to_string(ostk.join("audit.jsonl")).unwrap();
        assert!(content.contains("\"event\":\"test\""));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_append_audit_concurrent_flock() {
        // →902: Verify two concurrent writers both succeed without corruption.
        let tmp = tmpdir("flock");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();

        let root = Arc::new(tmp.clone());
        let mut handles = Vec::new();
        let writes_per_thread = 50;
        let num_threads = 4;

        for t in 0..num_threads {
            let r = Arc::clone(&root);
            handles.push(std::thread::spawn(move || {
                for i in 0..writes_per_thread {
                    let event = serde_json::json!({
                        "event": "test",
                        "thread": t,
                        "seq": i,
                    });
                    append_audit(&r, &event).unwrap();
                }
            }));
        }

        for h in handles {
            h.join().unwrap();
        }

        // Verify: every line is valid JSON and total count matches
        let content = fs::read_to_string(ostk.join("audit.jsonl")).unwrap();
        let lines: Vec<&str> = content.lines().filter(|l| !l.trim().is_empty()).collect();
        assert_eq!(
            lines.len(),
            (num_threads * writes_per_thread) as usize,
            "expected {} lines, got {}",
            num_threads * writes_per_thread,
            lines.len()
        );
        for (i, line) in lines.iter().enumerate() {
            assert!(
                serde_json::from_str::<serde_json::Value>(line).is_ok(),
                "line {} is not valid JSON: {}",
                i,
                line
            );
        }
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_append_audit_truncation_detection() {
        // →FIND-010: If audit.jsonl is externally truncated, append should refuse.
        let tmp = tmpdir("truncation");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();

        // Write two events to establish a size checkpoint
        let e1 = serde_json::json!({"event": "first"});
        let e2 = serde_json::json!({"event": "second"});
        append_audit(&tmp, &e1).unwrap();
        append_audit(&tmp, &e2).unwrap();

        // Now externally truncate the file (simulating tampering)
        fs::write(ostk.join("audit.jsonl"), "").unwrap();

        // Next append should detect truncation and refuse
        let e3 = serde_json::json!({"event": "third"});
        let result = append_audit(&tmp, &e3);
        assert!(result.is_err(), "expected truncation error");
        assert!(result.unwrap_err().contains("truncated"));

        let _ = fs::remove_dir_all(&tmp);
    }
}
