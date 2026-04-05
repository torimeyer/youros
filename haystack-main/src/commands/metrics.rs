//! `ostk metrics` — token savings report.
//!
//! Reads `.ostk/metrics.jsonl` and prints:
//! - Total tokens saved (estimated from byte savings / 4)
//! - Average compression ratio
//! - Number of squash events
//! - Estimated cost saved (at Sonnet input rate)
//!
//! Note: metrics.jsonl records byte counts (raw.len() and compressed.len()).
//! Token estimates use bytes/4 as a conservative approximation, consistent
//! with quota.rs.

use std::path::Path;

/// Sonnet input price per million tokens (used as default for cost estimates).
const SONNET_INPUT_PER_MILLION: f64 = 3.0;

pub fn run() -> Result<(), String> {
    let root = crate::find_project_root()?;
    run_at(&root)
}

pub fn run_at(root: &Path) -> Result<(), String> {
    let hs_dir = crate::state_dir(root);
    let metrics_path = hs_dir.join("metrics.jsonl");

    // Read metrics — empty file is fine (zero events)
    let content = std::fs::read_to_string(&metrics_path).unwrap_or_default();

    let mut total_original_bytes: u64 = 0;
    let mut _total_compressed_bytes: u64 = 0;
    let mut total_saved_bytes: u64 = 0;
    let mut event_count: u64 = 0;

    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
            if v.get("event").and_then(|e| e.as_str()) != Some("squash") {
                continue;
            }
            let original = v.get("original").and_then(|n| n.as_u64()).unwrap_or(0);
            let compressed = v.get("compressed").and_then(|n| n.as_u64()).unwrap_or(0);
            let saved = v.get("saved").and_then(|n| n.as_u64()).unwrap_or(0);
            total_original_bytes += original;
            _total_compressed_bytes += compressed;
            total_saved_bytes += saved;
            event_count += 1;
        }
    }

    // Convert bytes to estimated tokens (bytes/4 ≈ tokens, matches quota.rs)
    let total_saved_tokens = total_saved_bytes / 4;
    let _total_original_tokens = total_original_bytes / 4;

    // Average compression ratio (% reduction) — computed on bytes, same as tokens
    let avg_compression = if total_original_bytes > 0 {
        (total_saved_bytes as f64 / total_original_bytes as f64) * 100.0
    } else {
        0.0
    };

    // Detect configured model for pricing, fall back to sonnet
    let model = detect_model(root);
    let cost_per_million = model_input_price(&model);

    // Estimated cost saved
    let cost_saved = (total_saved_tokens as f64 / 1_000_000.0) * cost_per_million;

    // Session identity + uptime (best-effort)
    let (session_id, session_uptime) = session_info(&hs_dir);

    let model_label = if model.contains("opus") {
        "opus"
    } else if model.contains("haiku") {
        "haiku"
    } else {
        "sonnet"
    };

    println!("ostk metrics");
    println!("  token savings:  ~{} tokens (↓{:.0}% avg compression)",
        format_number(total_saved_tokens), avg_compression);
    println!("  bytes compressed: {} → {}",
        format_number(total_original_bytes),
        format_number(total_original_bytes.saturating_sub(total_saved_bytes)));
    println!("  squash events:  {}", format_number(event_count));
    println!("  est. cost saved: ~${:.2} ({model_label} input @ ${}/M)",
        cost_saved, cost_per_million as u64);
    println!("  session:        {session_id}, {session_uptime}");

    Ok(())
}

/// Return the input price per million tokens for a model string.
fn model_input_price(model: &str) -> f64 {
    if model.contains("opus") {
        15.0
    } else if model.contains("haiku") {
        0.80
    } else {
        // Sonnet default
        SONNET_INPUT_PER_MILLION
    }
}

/// Best-effort: read configured model from Agentfile or index.json.
fn detect_model(root: &Path) -> String {
    // Try index.json for model field
    let index_path = crate::state_dir(root).join("index.json");
    if let Ok(content) = std::fs::read_to_string(&index_path)
        && let Ok(v) = serde_json::from_str::<serde_json::Value>(&content)
            && let Some(model) = v.get("model").and_then(|m| m.as_str())
                && !model.is_empty() {
                    return model.to_string();
                }

    // Try Agentfile
    let agentfile_path = root.join("Agentfile");
    if let Ok(content) = std::fs::read_to_string(&agentfile_path) {
        for line in content.lines() {
            let trimmed = line.trim();
            if let Some(rest) = trimmed.strip_prefix("model") {
                let rest = rest.trim();
                if let Some(val) = rest.strip_prefix('=').or_else(|| rest.strip_prefix(':')) {
                    let val = val.trim().trim_matches('"').trim_matches('\'');
                    if !val.is_empty() {
                        return val.to_string();
                    }
                }
            }
        }
    }

    // Default
    "claude-sonnet-4-5".to_string()
}

/// Find the active session agent alias and uptime string.
fn session_info(hs_dir: &Path) -> (String, String) {
    use std::time::{SystemTime, UNIX_EPOCH};

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    // Read agents via kernel identity module
    let agents = crate::kernel::identity::read_agents(hs_dir);

    let mut active_alias = String::from("unknown");
    let mut session_start_secs: Option<u64> = None;

    for agent in &agents {
        if agent.status == "active" {
            active_alias = agent.alias.clone();
            session_start_secs = parse_iso_secs(&agent.registered_at);
        }
    }

    let uptime = if let Some(start) = session_start_secs {
        format_duration(now.saturating_sub(start))
    } else {
        String::from("unknown")
    };

    (active_alias, uptime)
}

/// Parse a simple ISO 8601 timestamp to Unix seconds (no chrono dependency).
///
/// Handles `YYYY-MM-DDTHH:MM:SSZ` format only.
fn parse_iso_secs(ts: &str) -> Option<u64> {
    // Expected: "2026-03-10T19:59:33Z"
    let ts = ts.trim_end_matches('Z');
    let (date_part, time_part) = ts.split_once('T')?;
    let mut date_parts = date_part.split('-');
    let year: u64 = date_parts.next()?.parse().ok()?;
    let month: u64 = date_parts.next()?.parse().ok()?;
    let day: u64 = date_parts.next()?.parse().ok()?;
    let mut time_parts = time_part.split(':');
    let hour: u64 = time_parts.next()?.parse().ok()?;
    let min: u64 = time_parts.next()?.parse().ok()?;
    let sec: u64 = time_parts.next()?.parse().ok()?;

    // Approximate: days since epoch
    let years_since_epoch = year.saturating_sub(1970);
    let leap_years = years_since_epoch / 4; // rough
    let days_in_months: [u64; 12] = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    let mut day_of_year: u64 = 0;
    for m in 0..(month.saturating_sub(1)) as usize {
        day_of_year += days_in_months.get(m).copied().unwrap_or(30);
    }
    day_of_year += day.saturating_sub(1);

    let total_days = years_since_epoch * 365 + leap_years + day_of_year;
    Some(total_days * 86400 + hour * 3600 + min * 60 + sec)
}

fn format_duration(secs: u64) -> String {
    if secs < 60 {
        format!("{secs}s")
    } else if secs < 3600 {
        format!("{}m {}s", secs / 60, secs % 60)
    } else {
        format!("{}h {}m", secs / 3600, (secs % 3600) / 60)
    }
}

fn format_number(n: u64) -> String {
    // Insert thousands separators
    let s = n.to_string();
    let chars: Vec<char> = s.chars().collect();
    let mut result = String::new();
    let len = chars.len();
    for (i, c) in chars.iter().enumerate() {
        if i > 0 && (len - i).is_multiple_of(3) {
            result.push(',');
        }
        result.push(*c);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn tmpdir(prefix: &str) -> std::path::PathBuf {
        use std::sync::atomic::{AtomicU32, Ordering};
        static CTR: AtomicU32 = AtomicU32::new(0);
        let n = CTR.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("hs_metrics_test_{prefix}_{pid}_{n}"))
    }

    #[test]
    fn test_metrics_no_file() {
        let tmp = tmpdir("no_file");
        let hs = tmp.join(".ostk");
        fs::create_dir_all(&hs).unwrap();
        // No metrics.jsonl — should run without error and report zero
        let result = run_at(&tmp);
        assert!(result.is_ok(), "should succeed with no metrics file: {:?}", result);
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_metrics_empty_file() {
        let tmp = tmpdir("empty");
        let hs = tmp.join(".ostk");
        fs::create_dir_all(&hs).unwrap();
        fs::write(hs.join("metrics.jsonl"), "").unwrap();
        let result = run_at(&tmp);
        assert!(result.is_ok());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_metrics_with_events() {
        let tmp = tmpdir("events");
        let hs = tmp.join(".ostk");
        fs::create_dir_all(&hs).unwrap();
        // original/compressed/saved are in BYTES. 600+300=900 bytes saved = 225 tokens
        let data = concat!(
            "{\"event\":\"squash\",\"original\":1000,\"compressed\":400,\"saved\":600,\"ts\":\"2026-03-10T10:00:00Z\"}\n",
            "{\"event\":\"squash\",\"original\":500,\"compressed\":200,\"saved\":300,\"ts\":\"2026-03-10T10:01:00Z\"}\n",
        );
        fs::write(hs.join("metrics.jsonl"), data).unwrap();
        let result = run_at(&tmp);
        assert!(result.is_ok(), "should succeed: {:?}", result);
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_model_input_price() {
        assert_eq!(model_input_price("claude-sonnet-4-5"), 3.0);
        assert_eq!(model_input_price("claude-opus-4-6"), 15.0);
        assert_eq!(model_input_price("claude-haiku-3-5"), 0.80);
        assert_eq!(model_input_price("unknown-model"), 3.0); // default = sonnet
    }

    #[test]
    fn test_format_number() {
        assert_eq!(format_number(0), "0");
        assert_eq!(format_number(999), "999");
        assert_eq!(format_number(1000), "1,000");
        assert_eq!(format_number(41247), "41,247");
        assert_eq!(format_number(1000000), "1,000,000");
    }

    #[test]
    fn test_format_duration() {
        assert_eq!(format_duration(30), "30s");
        assert_eq!(format_duration(90), "1m 30s");
        assert_eq!(format_duration(3661), "1h 1m");
    }

    #[test]
    fn test_parse_iso_secs_roundtrip() {
        // 2026-03-10T12:00:00Z — just verify it parses without panic
        let result = parse_iso_secs("2026-03-10T12:00:00Z");
        assert!(result.is_some());
        assert!(result.unwrap() > 0);
    }
}
