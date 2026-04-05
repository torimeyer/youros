use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::Path;

use super::needles::read_needles;
use super::paths::state_dir;
use super::time::now_iso;
use crate::BROADCAST;

// ---------------------------------------------------------------------------
// Implicit compile: lightweight index update after every mutation
// ---------------------------------------------------------------------------

/// Mutation events that trigger an index update.
#[derive(Debug, Clone)]
pub enum MutationEvent {
    NeedleAdded { id: String, priority: String },
    NeedleClosed { id: String },
    HayFiled,
    HayCompiled { as_needle: bool },
    ThreadCreated,
    SpecPromoted,
}

/// Lightweight post-mutation hook. Rebuilds `.ostk/index.json` from
/// current issues.jsonl + audit.jsonl state. This is NOT a full compile --
/// it only updates counts and timestamps so `ostk show status` can
/// read fast without scanning.
pub fn post_mutation(root: &Path, event: &MutationEvent) -> Result<(), String> {
    // Read current needle state
    let needles = read_needles(root)?;

    let total = needles.len();
    let mut open = 0usize;
    let mut closed = 0usize;
    let mut by_priority: HashMap<String, usize> = HashMap::new();

    for n in &needles {
        let status = n.get("status").and_then(|v| v.as_str()).unwrap_or("");
        match status {
            "open" | "in_progress" => {
                open += 1;
                let p = n
                    .get("priority")
                    .and_then(|v| v.as_str())
                    .unwrap_or("P1")
                    .to_string();
                *by_priority.entry(p).or_insert(0) += 1;
            }
            "closed" => {
                closed += 1;
            }
            _ => {}
        }
    }

    // Read existing index to preserve hay/thread counts, then update
    let index_path = state_dir(root).join("index.json");
    let mut index: Value = if index_path.exists() {
        let content = fs::read_to_string(&index_path)
            .map_err(|e| format!("failed to read index.json: {e}"))?;
        serde_json::from_str(&content).unwrap_or_else(|_| serde_json::json!({}))
    } else {
        serde_json::json!({})
    };

    // Update needle counts from ground truth
    index["needles"] = serde_json::json!({
        "total": total,
        "open": open,
        "closed": closed,
        "by_priority": by_priority,
    });

    // Incrementally update hay/thread counts based on event type
    let hay = index
        .get("hay")
        .cloned()
        .unwrap_or_else(|| serde_json::json!({"pending": 0, "compiled": 0}));
    let mut hay_pending = hay
        .get("pending")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let mut hay_compiled = hay
        .get("compiled")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);

    let threads = index
        .get("threads")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let mut thread_count = threads;

    match event {
        MutationEvent::HayFiled => {
            hay_pending += 1;
        }
        MutationEvent::HayCompiled { .. } => {
            hay_pending = hay_pending.saturating_sub(1);
            hay_compiled += 1;
        }
        MutationEvent::ThreadCreated => {
            thread_count += 1;
        }
        // Needle counts already rebuilt from ground truth above
        MutationEvent::NeedleAdded { .. }
        | MutationEvent::NeedleClosed { .. }
        | MutationEvent::SpecPromoted => {}
    }

    index["hay"] = serde_json::json!({
        "pending": hay_pending,
        "compiled": hay_compiled,
    });
    index["threads"] = serde_json::json!(thread_count);
    index["last_mutation"] = serde_json::json!(now_iso());

    // Preserve last_compile if it exists
    if index.get("last_compile").is_none() {
        index["last_compile"] = serde_json::json!(null);
    }

    // Write atomically
    let tmp_path = state_dir(root).join("index.json.tmp");
    let content =
        serde_json::to_string_pretty(&index).map_err(|e| format!("failed to serialize index: {e}"))?;
    fs::write(&tmp_path, content.as_bytes())
        .map_err(|e| format!("failed to write index.json.tmp: {e}"))?;
    fs::rename(&tmp_path, &index_path)
        .map_err(|e| format!("failed to rename index.json.tmp: {e}"))?;

    // Broadcast mutation to all presentation layers
    if let Ok(handle) = tokio::runtime::Handle::try_current() {
        drop(handle.spawn(async move {
            BROADCAST.broadcast(serde_json::json!({
                "event": "filesystem.mutated",
                "index": index,
            })).await;
        }));
    }

    Ok(())
}

/// Verify OS integrity -- no corruption in ostk state files.
///
/// Checks:
/// 1. Needle counter >= max ID in issues.jsonl
/// 2. No merge conflict markers in ostk files
/// 3. issues.jsonl is valid JSONL (every line parses)
/// 4. No duplicate needle IDs
pub fn verify_os_integrity(root: &Path) -> Result<(), String> {
    let mut errors = Vec::new();

    // 1. Needle counter matches max ID in issues.jsonl
    let counter_path = state_dir(root).join("needles/counter");
    let issues_path = state_dir(root).join("needles/issues.jsonl");

    if counter_path.exists() && issues_path.exists() {
        let counter: u64 = fs::read_to_string(&counter_path)
            .unwrap_or_default()
            .trim()
            .parse()
            .unwrap_or(0);

        let max_id = fs::read_to_string(&issues_path)
            .unwrap_or_default()
            .lines()
            .filter_map(|l| serde_json::from_str::<serde_json::Value>(l).ok())
            .filter_map(|v| {
                v.get("id")
                    .and_then(|id| id.as_str())
                    .and_then(|s| {
                        s.trim_start_matches('\u{2192}')
                            .parse::<u64>()
                            .ok()
                    })
            })
            .max()
            .unwrap_or(0);

        if counter < max_id {
            errors.push(format!(
                "needle counter ({counter}) < max ID ({max_id})"
            ));
        }
    }

    // 2. No merge conflict markers in ostk files
    let sd = state_dir(root);
    for rel in &[
        "needles/issues.jsonl",
        "audit.jsonl",
        "boot.md",
    ] {
        let path = sd.join(rel);
        if path.exists() {
            let content = fs::read_to_string(&path).unwrap_or_default();
            if content.contains("<<<<<<<")
                || content.contains(">>>>>>>")
                || content.contains("=======")
            {
                errors.push(format!("{rel} contains merge conflict markers"));
            }
        }
    }

    // 3. issues.jsonl is valid JSONL (every line parses)
    if issues_path.exists() {
        let content = fs::read_to_string(&issues_path).unwrap_or_default();
        for (i, line) in content.lines().enumerate() {
            let trimmed = line.trim();
            if !trimmed.is_empty()
                && serde_json::from_str::<serde_json::Value>(trimmed).is_err() {
                    errors.push(format!("issues.jsonl line {} is invalid JSON", i + 1));
                    break;
                }
        }
    }

    // 4. No duplicate needle IDs
    if issues_path.exists() {
        let content = fs::read_to_string(&issues_path).unwrap_or_default();
        let mut ids = HashSet::new();
        for line in content.lines() {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(line.trim())
                && let Some(id) = v.get("id").and_then(|i| i.as_str())
                    && !ids.insert(id.to_string()) {
                        errors.push(format!("duplicate needle ID: {id}"));
                    }
        }
    }

    if errors.is_empty() {
        println!("  OS integrity: OK");
        Ok(())
    } else {
        for e in &errors {
            println!("  OS CORRUPTION: {e}");
        }
        Err(format!("{} integrity errors", errors.len()))
    }
}
