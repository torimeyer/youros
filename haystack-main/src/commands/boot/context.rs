//! Register dump, boot gradient, continuation prompt, and stale-P0 detection
//! for `ostk boot`.
//!
//! Everything related to assembling the boot context output: compressed register
//! dump, vDSO line, P0 needle display, stale-P0 warnings, and dynamic
//! continuation prompt generation.

use std::fs;
use std::path::Path;
use std::process::Command;

use super::super::helpers::{count_fleet, count_lines, count_open_needles};
use crate::language::parse_language;

// ── Boot Gradient (->596) ──────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct BootGradient {
    pub confidence: f64,
    pub status: String,
}

impl BootGradient {
    pub fn compute(root: &Path) -> Self {
        let ostk_dir = crate::state_dir(root);
        let audit_path = ostk_dir.join("audit.jsonl");

        let mut total_resolutions = 0;
        let mut weighted_sum = 0.0;
        let mut malformed = 0usize;
        let mut first_bad_line: Option<usize> = None;

        if let Ok(content) = std::fs::read_to_string(&audit_path) {
            let lines: Vec<&str> = content.lines().collect();
            let total_lines = lines.len();
            for (i, line) in lines.iter().rev().take(100).enumerate() {
                let line = line.trim();
                if line.is_empty() {
                    continue;
                }
                match serde_json::from_str::<serde_json::Value>(line) {
                    Ok(event) => {
                        if event["event"] == "tack.resolved" {
                            let tier = event["tier"].as_u64().unwrap_or(3);
                            let weight = match tier {
                                1 => 1.0,
                                2 => 0.8,
                                _ => 0.5,
                            };
                            weighted_sum += weight;
                            total_resolutions += 1;
                        }
                    }
                    Err(_) => {
                        malformed += 1;
                        // Line number in original file (iterating from end)
                        first_bad_line = Some(total_lines - i);
                    }
                }
            }
            if malformed > 0 {
                let line_num = first_bad_line.unwrap_or(0);
                eprintln!(
                    "[warn] audit.jsonl has {malformed} malformed line(s) in last 100, \
                     first at line {line_num} -- run `ostk repair audit` to fix"
                );
            }
        }

        let confidence = if total_resolutions > 0 {
            (weighted_sum / total_resolutions as f64).min(1.0)
        } else {
            0.0
        };

        let status = if confidence >= 0.9 {
            crate::strings::boot::CONFIDENCE_STATUS_FULL.to_string()
        } else if confidence >= 0.5 {
            crate::strings::boot::CONFIDENCE_STATUS_MIN.to_string()
        } else {
            crate::strings::boot::CONFIDENCE_STATUS_RESTRICTED.to_string()
        };

        Self { confidence, status }
    }

    pub fn report(&self) {
        let icon = if self.confidence >= 0.7 { "\u{25c9}" } else { "\u{25ce}" };
        println!("boot confidence: {:.2} {} ({})", self.confidence, icon, self.status);
    }
}

/// Register dump density levels.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Density {
    /// Qwen-style: minimal keys, no labels where predictable (~40 tokens)
    Terse,
    /// Standard format: readable keys, concise values (~80 tokens)
    Standard,
    /// Flash-style: focus on P0 and system state (~60 tokens)
    Surgical,
    /// Grok-style: include more fleet/thread context (~120 tokens)
    Broad,
}

impl Density {
    /// Detect density from model or environment.
    pub fn detect() -> Self {
        if let Ok(m) = std::env::var("OSTK_MODEL") {
            let m = m.to_lowercase();
            if m.contains("qwen") { return Density::Terse; }
            if m.contains("grok") { return Density::Broad; }
            if m.contains("flash") { return Density::Surgical; }
        }
        Density::Standard
    }
}

/// Build the compressed register dump string with specified density.
pub fn build_register_dump(root: &Path, ostk_dir: &Path) -> String {
    let density = Density::detect();
    build_register_dump_with_density(root, ostk_dir, density)
}

/// Build the register dump string.
pub fn build_register_dump_with_density(root: &Path, ostk_dir: &Path, density: Density) -> String {
    let version = env!("CARGO_PKG_VERSION");

    // Identity counter (instance number) — via kernel for flock safety
    let instance_id = crate::kernel::identity::read_counter(ostk_dir);

    // POST results
    let post_results = super::super::post::run_checks(root);
    let post_passed = post_results.iter().filter(|r| r.passed).count();
    let post_total = post_results.len();

    // Wall clock (ISO 8601)
    let wall = crate::now_iso();
    let wall_short = wall.split('T').nth(1).and_then(|s| s.get(..6)).unwrap_or("?");

    // Audit event count
    let audit_count = count_lines(&ostk_dir.join("audit.jsonl"));

    // Open needles count
    let open_needles = count_open_needles(&ostk_dir.join("needles").join("issues.jsonl"));

    // .language verb count
    let verb_count = count_language_verbs(&ostk_dir.join(".language"));

    // Fleet: alive agents from agents.jsonl (canonical helper)
    let (fleet_alive, _fleet_total) = count_fleet(&ostk_dir.join("agents.jsonl"));

    // Top P0 needle
    let p0_line = find_top_p0_needle(&ostk_dir.join("needles").join("issues.jsonl"));

    // vDSO: top 8 .language verbs by momentum (hot-path syscalls)
    let sys_line = build_vdso(&ostk_dir.join(".language"));

    // Coordination loop -- scheduling verbs available to any booted agent
    let loop_line = "loop: :think \u{2192} :needle \u{2192} :spawn \u{2192} :monitor \u{2192} :yield";

    // Kernel socket -- IPC discoverability
    let sock_line = if crate::serve::socket::kernel_alive(ostk_dir) {
        let pid_str = fs::read_to_string(crate::serve::socket::pid_path(ostk_dir))
            .ok()
            .and_then(|s| s.trim().parse::<u32>().ok())
            .map(|p| p.to_string())
            .unwrap_or_else(|| "?".to_string());
        format!("sock: .ostk/ostk.sock (pid {pid_str})")
    } else {
        "sock: offline".to_string()
    };

    // Driver registry -- boot-time snapshot (internal drivers only at boot)
    let drivers_line = {
        let mut reg = crate::kernel::drivers::DriverRegistry::new();
        reg.register_internal_screen();
        reg.register_internal_web();
        reg.format_table()
    };

    match density {
        Density::Terse => {
            let mut out = format!("[reg] @h.p+{} v{} POST={}/{} wall={} audit={} needles={} verbs={} fleet={}\n",
                instance_id, version, post_passed, post_total, wall_short, audit_count, open_needles, verb_count, fleet_alive);
            out.push_str(&format!("[sys] {}\n", sys_line.strip_prefix("sys: ").unwrap_or(&sys_line)));
            out.push_str(&format!("[p0] {}\n", p0_line.strip_prefix("P0: ").unwrap_or(&p0_line)));
            out.push_str("[loop] :think\u{2192}:needle\u{2192}:spawn\u{2192}:monitor\u{2192}:yield\n");
            out.push_str(&format!("[sock] {}\n", sock_line.strip_prefix("sock: ").unwrap_or(&sock_line)));
            out.push_str(&format!("{}\n", drivers_line));
            out.push_str("[laws] iw ep fs oc ii");
            out
        }
        Density::Surgical => {
            let mut out = format!("@h.p+{} | v{} | POST {}/{}\n", instance_id, version, post_passed, post_total);
            out.push_str(&format!("wall: {} | audit: {} | needles: {} open\n", wall, audit_count, open_needles));
            out.push_str(&format!("{}\n", sys_line));
            out.push_str(&format!("{}\n", p0_line));
            out.push_str(&format!("{}\n", loop_line));
            out.push_str(&format!("{}\n", sock_line));
            out.push_str(&format!("{}\n", drivers_line));
            out.push_str("laws: invisible-write | ephemeral | filesystem | OCC | invisible-infra");
            out
        }
        Density::Broad => {
            let mut out = format!("@ostk.prime+{} | v{} | POST {}/{}\n", instance_id, version, post_passed, post_total);
            out.push_str(&format!("wall: {} | audit: {} | needles: {} open\n", wall, audit_count, open_needles));
            out.push_str(&format!(".language: {} verbs | fleet: {} alive\n", verb_count, fleet_alive));
            out.push_str(&format!("{}\n", p0_line));
            out.push_str(&format!("{}\n", sys_line));
            out.push_str(&format!("{}\n", loop_line));
            out.push_str(&format!("{}\n", sock_line));
            out.push_str(&format!("{}\n", drivers_line));
            out.push_str("laws: invisible-write | ephemeral | filesystem | OCC | invisible-infra");
            out
        }
        Density::Standard => {
            let agents_line = build_agents_summary(ostk_dir);
            let mut dump = String::new();
            dump.push_str(&format!(
                "@ostk.prime+{instance_id} | v{version} | POST {post_passed}/{post_total}\n"
            ));
            dump.push_str(&format!(
                "wall: {wall} | audit: {audit_count} | needles: {open_needles} open\n"
            ));
            dump.push_str(&format!(
                ".language: {verb_count} verbs | fleet: {fleet_alive} alive\n"
            ));
            dump.push_str(&format!("{p0_line}\n"));
            dump.push_str(&format!("{sys_line}\n"));
            dump.push_str(&format!("{agents_line}\n"));
            dump.push_str(&format!("{loop_line}\n"));
            dump.push_str(&format!("{sock_line}\n"));
            dump.push_str(&format!("{drivers_line}\n"));
            dump.push_str("laws: invisible-write | ephemeral | filesystem | OCC | invisible-infra");
            dump
        }
    }
}

/// Build agents summary: count Agentfiles and list recent/notable ones.
fn build_agents_summary(ostk_dir: &Path) -> String {
    let mut count = 0usize;
    let mut names: Vec<String> = Vec::new();

    // Scan .ostk/ for Agentfile.* and .ostk/agents/*.Agentfile
    for pattern in &[ostk_dir.to_path_buf(), ostk_dir.join("agents")] {
        if let Ok(entries) = fs::read_dir(pattern) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                if name.starts_with("Agentfile") || name.ends_with(".Agentfile") {
                    count += 1;
                    // Extract short name: "Agentfile.post-battery" -> "post-battery"
                    let short = name
                        .strip_prefix("Agentfile.")
                        .or_else(|| name.strip_suffix(".Agentfile"))
                        .unwrap_or(&name)
                        .to_string();
                    if names.len() < 6 {
                        names.push(short);
                    }
                }
            }
        }
    }

    if count == 0 {
        "agents: 0".to_string()
    } else {
        format!("agents: {} | {}", count, names.join(" "))
    }
}


/// Build the vDSO line: top 8 .language verbs by momentum (hot-path syscalls).
/// Delegates parsing to shared `language::parse_language` (single source of truth),
/// then sorts by momentum descending and takes top 8 user-layer verbs.
fn build_vdso(path: &Path) -> String {
    let content = fs::read_to_string(path).unwrap_or_default();
    let entries = parse_language(&content);

    let mut verbs: Vec<(String, f64)> = entries
        .iter()
        .filter(|e| e.layer != "kernel" && e.layer != "ceremony")
        .map(|e| (format!(":{}", e.verb), e.momentum))
        .collect();

    verbs.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    let top: Vec<&str> = verbs.iter().take(8).map(|(v, _)| v.as_str()).collect();

    if top.is_empty() {
        "sys: (none)".to_string()
    } else {
        format!("sys: {}", top.join(" "))
    }
}

/// Count verbs in .language.
/// Delegates parsing to shared `language::parse_language` (single source of truth).
fn count_language_verbs(path: &Path) -> usize {
    let content = fs::read_to_string(path).unwrap_or_default();
    parse_language(&content).len()
}

/// Find the top P0 open needle. Returns a formatted line like `P0: ->NNN description`.
/// Falls back to `P0: none` if no open P0 exists.
fn find_top_p0_needle(path: &Path) -> String {
    let content = match fs::read_to_string(path) {
        Ok(c) => c,
        Err(_) => return "P0: none".to_string(),
    };

    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(line) {
            let status = val.get("status").and_then(|s| s.as_str()).unwrap_or("");
            let priority = val.get("priority").and_then(|s| s.as_str()).unwrap_or("");
            if status != "closed" && priority == "P0" {
                let id = val.get("id").and_then(|s| s.as_str()).unwrap_or("???");
                let title = val.get("title").and_then(|s| s.as_str()).unwrap_or("untitled");
                return format!("P0: \u{2192}{id} {title}");
            }
        }
    }

    "P0: none".to_string()
}

// ── ->759: Stale P0 detection ────────────────────────────────────────────────

/// Check if the current P0 needle referenced in the register dump is already
/// closed in issues.jsonl. If so, emit a warning -- the milestone may be stale.
///
/// This catches cases like boot.md showing "P0: ->540 ship v0.6.0" when that
/// needle was closed weeks ago.
pub(super) fn check_stale_p0(ostk_dir: &Path) {
    for id in detect_stale_p0(ostk_dir) {
        eprintln!(
            "[warn] boot.md P0 references closed needle \u{2192}{} \u{2014} run `ostk boot --update-prompt` to regenerate",
            id
        );
    }
}

/// Detect stale P0 references in boot.md (->751).
///
/// Returns closed needle IDs that boot.md's P0 line references.
/// Empty vec means boot.md P0 is current.
/// Used by `check_stale_p0()` (CLI warning) and `BootContext` (system prompt injection).
pub fn detect_stale_p0(ostk_dir: &Path) -> Vec<String> {
    let issues_path = ostk_dir.join("needles").join("issues.jsonl");
    let content = match fs::read_to_string(&issues_path) {
        Ok(c) => c,
        Err(_) => return vec![],
    };

    // Collect all closed needle IDs and the current P0 needle
    let mut closed_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut p0_id: Option<String> = None;

    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(line) {
            let id = val.get("id").and_then(|s| s.as_str()).unwrap_or("");
            let status = val.get("status").and_then(|s| s.as_str()).unwrap_or("");
            let priority = val.get("priority").and_then(|s| s.as_str()).unwrap_or("");

            if status == "closed" {
                closed_ids.insert(id.to_string());
            }

            // Track the first open P0 (same logic as find_top_p0_needle)
            if p0_id.is_none() && status != "closed" && priority == "P0" {
                p0_id = Some(id.to_string());
            }
        }
    }

    let mut stale_ids = Vec::new();

    // Scan boot.md for explicit P0/milestone references to closed needles
    let boot_path = ostk_dir.join("boot.md");
    if let Ok(boot_content) = fs::read_to_string(&boot_path) {
        for needle_id in extract_needle_ids(&boot_content) {
            if closed_ids.contains(&needle_id) && !stale_ids.contains(&needle_id) {
                stale_ids.push(needle_id);
            }
        }
    }

    // If the top P0 needle itself is somehow in the closed set (edge case with
    // stale data), include it too
    if let Some(ref id) = p0_id
        && closed_ids.contains(id) && !stale_ids.contains(id) {
            stale_ids.push(id.clone());
        }

    stale_ids
}

/// Extract needle IDs from text content. Matches patterns like:
/// - `->NNN` or `->NNN` (arrow followed by alphanumeric ID, possibly with dashes)
/// - Bare IDs like `bd-NNN` or `n-NNN`
pub fn extract_needle_ids(text: &str) -> Vec<String> {
    let mut ids = Vec::new();
    // Match ->NNN or ->NNN patterns (alphanumeric + dashes, no trailing punctuation)
    let arrow_re = regex::Regex::new(r"(?:\u{2192}|->)([\w][\w-]*)").unwrap();
    for cap in arrow_re.captures_iter(text) {
        if let Some(m) = cap.get(1) {
            ids.push(m.as_str().to_string());
        }
    }
    ids
}

// ── ->813: Dynamic continuation prompt ──────────────────────────────────────

/// Generate a continuation prompt dynamically from kernel state.
///
/// Sections built:
/// - Architecture: stable template header
/// - Open needles: from issues.jsonl, grouped by priority
/// - Recent commits: from `git log --oneline -10`
/// - Fleet state: from agents.jsonl
/// - Key specs: from docs/spec/ directory listing
pub fn generate_continuation_prompt(ostk_dir: &Path) -> String {
    let root = ostk_dir.parent().unwrap_or(Path::new("."));
    let mut out = String::new();

    // Architecture header (stable template)
    out.push_str("# Continuation Prompt \u{2014} generated by ostk boot\n\n");
    out.push_str("## Architecture\n\n");
    out.push_str("ostk is a Rust-native llmOS coordination kernel.\n");
    out.push_str("- Binary: `ostk` (build: `cargo build --bin ostk`, test: `cargo test`)\n");
    out.push_str("- State: `.ostk/` directory (audit.jsonl, needles/, agents.jsonl, .language)\n");
    out.push_str("- Laws: invisible-write | ephemeral | filesystem | OCC | invisible-infra\n");
    out.push_str("- Coordination: :think \u{2192} :needle \u{2192} :spawn \u{2192} :monitor \u{2192} :yield\n\n");

    // Open needles grouped by priority
    out.push_str("## Open Needles\n\n");
    let issues_path = ostk_dir.join("needles").join("issues.jsonl");
    if let Ok(content) = fs::read_to_string(&issues_path) {
        let mut p0s: Vec<(String, String)> = Vec::new();
        let mut p1s: Vec<(String, String)> = Vec::new();
        let mut p2s: Vec<(String, String)> = Vec::new();
        let mut others: Vec<(String, String)> = Vec::new();

        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() { continue; }
            if let Ok(val) = serde_json::from_str::<serde_json::Value>(line) {
                let status = val.get("status").and_then(|s| s.as_str()).unwrap_or("");
                if status == "closed" { continue; }
                let id = val.get("id").and_then(|s| s.as_str()).unwrap_or("???").to_string();
                let title = val.get("title").and_then(|s| s.as_str()).unwrap_or("untitled").to_string();
                let priority = val.get("priority").and_then(|s| s.as_str()).unwrap_or("");
                match priority {
                    "P0" => p0s.push((id, title)),
                    "P1" => p1s.push((id, title)),
                    "P2" => p2s.push((id, title)),
                    _ => others.push((id, title)),
                }
            }
        }

        if !p0s.is_empty() {
            out.push_str("### P0 (critical)\n");
            for (id, title) in &p0s {
                out.push_str(&format!("- \u{2192}{id} {title}\n"));
            }
            out.push('\n');
        }
        if !p1s.is_empty() {
            out.push_str("### P1 (important)\n");
            for (id, title) in p1s.iter().take(10) {
                out.push_str(&format!("- \u{2192}{id} {title}\n"));
            }
            if p1s.len() > 10 {
                out.push_str(&format!("- ... and {} more\n", p1s.len() - 10));
            }
            out.push('\n');
        }
        if !p2s.is_empty() {
            out.push_str(&format!("### P2 ({} items)\n\n", p2s.len()));
        }
        if !others.is_empty() {
            out.push_str(&format!("### Other ({} items)\n\n", others.len()));
        }
    } else {
        out.push_str("(no issues.jsonl found)\n\n");
    }

    // Recent commits
    out.push_str("## Recent Commits\n\n");
    if let Ok(output) = Command::new("git")
        .args(["log", "--oneline", "-10"])
        .current_dir(root)
        .output()
    {
        if output.status.success() {
            let log = String::from_utf8_lossy(&output.stdout);
            out.push_str("```\n");
            out.push_str(&log);
            out.push_str("```\n\n");
        } else {
            out.push_str("(git log unavailable)\n\n");
        }
    } else {
        out.push_str("(git not available)\n\n");
    }

    // Fleet state
    out.push_str("## Fleet State\n\n");
    let agents_path = ostk_dir.join("agents.jsonl");
    if let Ok(content) = fs::read_to_string(&agents_path) {
        let mut active = 0usize;
        let mut total = 0usize;
        let mut agent_names: Vec<String> = Vec::new();
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() { continue; }
            total += 1;
            if let Ok(val) = serde_json::from_str::<serde_json::Value>(line)
                && val.get("status").and_then(|s| s.as_str()) == Some("active") {
                    active += 1;
                    if let Some(alias) = val.get("alias").and_then(|s| s.as_str())
                        && agent_names.len() < 10 {
                            agent_names.push(alias.to_string());
                        }
                }
        }
        out.push_str(&format!("- {active} active / {total} total\n"));
        if !agent_names.is_empty() {
            out.push_str(&format!("- Active: {}\n", agent_names.join(", ")));
        }
    } else {
        out.push_str("- No agents.jsonl found\n");
    }
    out.push('\n');

    // Key specs
    out.push_str("## Key Specs\n\n");
    let spec_dir = root.join("docs").join("spec");
    if spec_dir.is_dir() {
        if let Ok(entries) = fs::read_dir(&spec_dir) {
            let mut specs: Vec<String> = entries
                .flatten()
                .filter_map(|e| {
                    let name = e.file_name().to_string_lossy().into_owned();
                    if name.ends_with(".md") { Some(name) } else { None }
                })
                .collect();
            specs.sort();
            for spec in specs.iter().take(20) {
                out.push_str(&format!("- `docs/spec/{spec}`\n"));
            }
            if specs.len() > 20 {
                out.push_str(&format!("- ... and {} more\n", specs.len() - 20));
            }
        }
    } else {
        out.push_str("- No docs/spec/ directory\n");
    }
    out.push('\n');

    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU32, Ordering};

    static TEST_COUNTER: AtomicU32 = AtomicU32::new(0);
    fn test_dir(prefix: &str) -> std::path::PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("ostk_boot_ctx_{prefix}_{pid}_{n}"))
    }

    // ── Register dump tests ─────────────────────────────────────────────────

    #[test]
    fn test_register_dump_format() {
        let tmp = test_dir("reg_dump_fmt");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        fs::write(ostk_dir.join("needles/counter"), "42").unwrap();
        fs::write(ostk_dir.join("identity_counter"), "1088").unwrap();
        fs::write(ostk_dir.join("audit.jsonl"), "{\"event\":\"test\"}\n{\"event\":\"test2\"}\n").unwrap();
        // One open needle, one closed
        fs::write(
            ostk_dir.join("needles/issues.jsonl"),
            "{\"id\":\"n-001\",\"status\":\"open\",\"priority\":\"P0\",\"title\":\"CAS TOCTOU flock\"}\n\
             {\"id\":\"n-002\",\"status\":\"closed\",\"priority\":\"P1\",\"title\":\"done thing\"}\n",
        ).unwrap();
        // .language with 3 verbs
        fs::write(
            ostk_dir.join(".language"),
            "# .language\n:boot | 1 | kernel | 0 | 0 | 1.00 | ostk boot\n\
             :shutdown | 1 | kernel | 0 | 0 | 1.00 | ostk shutdown\n\
             :work | 2 | user | 0 | 200 | 0.50 | ostk work\n",
        ).unwrap();
        // One active agent (proper AgentEntry format for kernel::identity)
        fs::write(
            ostk_dir.join("agents.jsonl"),
            "{\"alias\":\"agent-1010\",\"pid\":1,\"status\":\"active\",\"registered_at\":\"2026-01-01T00:00:00Z\",\"last_seen\":\"2026-01-01T00:00:00Z\"}\n\
             {\"alias\":\"agent-1011\",\"pid\":2,\"status\":\"dead\",\"registered_at\":\"2026-01-01T00:00:00Z\",\"last_seen\":\"2026-01-01T00:00:00Z\"}\n",
        ).unwrap();
        // POST needs HUMANFILE + .primefile
        fs::write(ostk_dir.join("HUMANFILE"), "scott").unwrap();
        fs::write(ostk_dir.join(".primefile"), "kernel").unwrap();

        let dump = build_register_dump(&tmp, &ostk_dir);
        let lines: Vec<&str> = dump.lines().collect();

        // 6 lines: identity, wall, .language, P0, sys, laws
        assert!(lines.len() >= 5, "register dump must have at least 5 lines, got:\n{dump}");
        assert!(lines[0].starts_with("@ostk.prime+1088"), "line 1 identity: {}", lines[0]);
        assert!(lines[0].contains(&format!("v{}", env!("CARGO_PKG_VERSION"))), "line 1 version: {}", lines[0]);
        assert!(lines[0].contains("POST"), "line 1 POST: {}", lines[0]);
        assert!(lines[1].contains("audit: 2"), "line 2 audit count: {}", lines[1]);
        assert!(lines[1].contains("needles: 1 open"), "line 2 open needles: {}", lines[1]);
        assert!(lines[2].contains(".language: 3 verbs"), "line 3 verb count: {}", lines[2]);
        assert!(lines[2].contains("fleet: 1 alive"), "line 3 fleet: {}", lines[2]);
        assert!(lines[3].contains("P0:"), "line 4 P0: {}", lines[3]);
        assert!(lines[3].contains("n-001"), "line 4 needle id: {}", lines[3]);
        assert!(lines[3].contains("CAS TOCTOU flock"), "line 4 needle title: {}", lines[3]);
        // Last line is always the laws
        let last = lines.last().unwrap();
        assert_eq!(*last, "laws: invisible-write | ephemeral | filesystem | OCC | invisible-infra");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_register_dump_includes_post_status() {
        // Minimal valid env -- all 7 POST checks should pass
        let tmp = test_dir("reg_dump_post");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        fs::write(ostk_dir.join("needles/counter"), "0").unwrap();
        fs::write(ostk_dir.join("needles/issues.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("audit.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("HUMANFILE"), "scott").unwrap();
        fs::write(ostk_dir.join(".primefile"), "kernel").unwrap();
        fs::write(ostk_dir.join("identity_counter"), "99").unwrap();

        let dump = build_register_dump(&tmp, &ostk_dir);
        // All 7 checks pass
        assert!(dump.contains("POST 7/7"), "expected POST 7/7 in:\n{dump}");

        // Now break one check -- remove HUMANFILE
        fs::remove_file(ostk_dir.join("HUMANFILE")).unwrap();
        let dump2 = build_register_dump(&tmp, &ostk_dir);
        assert!(dump2.contains("POST 6/7"), "expected POST 6/7 in:\n{dump2}");

        let _ = fs::remove_dir_all(&tmp);
    }
}

// ── ->759/->813 tests ────────────────────────────────────────────────────────

#[cfg(test)]
mod stale_p0_tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU32, Ordering};

    static TEST_COUNTER: AtomicU32 = AtomicU32::new(0);
    fn test_dir(prefix: &str) -> std::path::PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("ostk_stale_p0_{prefix}_{pid}_{n}"))
    }

    #[test]
    fn test_extract_needle_ids_arrow() {
        let text = "P0: \u{2192}540 ship v0.6.0\nAlso see \u{2192}541 and ->542.";
        let ids = extract_needle_ids(text);
        assert!(ids.contains(&"540".to_string()));
        assert!(ids.contains(&"541".to_string()));
        assert!(ids.contains(&"542".to_string()));
    }

    #[test]
    fn test_extract_needle_ids_dash_arrow() {
        let text = "P0: ->bd-001 fix the bug";
        let ids = extract_needle_ids(text);
        assert!(ids.contains(&"bd-001".to_string()));
    }

    #[test]
    fn test_extract_needle_ids_empty() {
        let ids = extract_needle_ids("no needles here");
        assert!(ids.is_empty());
    }

    #[test]
    fn test_check_stale_p0_with_closed_needle_in_boot_md() {
        let tmp = test_dir("stale_closed");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();

        // One closed needle
        let issues = r#"{"id":"540","status":"closed","priority":"P0","title":"ship v0.6.0","close_reason":"shipped"}
{"id":"541","status":"open","priority":"P1","title":"fix tests"}
"#;
        fs::write(ostk_dir.join("needles/issues.jsonl"), issues).unwrap();

        // boot.md references the closed needle
        let boot = "# boot.md\nP0: \u{2192}540 ship v0.6.0\n";
        fs::write(ostk_dir.join("boot.md"), boot).unwrap();

        // Should not panic -- just emits a warning to stderr
        check_stale_p0(&ostk_dir);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_check_stale_p0_no_stale_needles() {
        let tmp = test_dir("stale_none");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();

        // One open P0 needle
        let issues = r#"{"id":"600","status":"open","priority":"P0","title":"current work"}
"#;
        fs::write(ostk_dir.join("needles/issues.jsonl"), issues).unwrap();

        // boot.md references the open needle
        let boot = "# boot.md\nP0: \u{2192}600 current work\n";
        fs::write(ostk_dir.join("boot.md"), boot).unwrap();

        // Should not produce any warning
        check_stale_p0(&ostk_dir);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_check_stale_p0_missing_issues() {
        let tmp = test_dir("stale_missing");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();
        // No needles directory -- should not panic
        check_stale_p0(&ostk_dir);
        let _ = fs::remove_dir_all(&tmp);
    }
}

#[cfg(test)]
mod continuation_prompt_tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU32, Ordering};

    static TEST_COUNTER: AtomicU32 = AtomicU32::new(0);
    fn test_dir(prefix: &str) -> std::path::PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("ostk_cont_prompt_{prefix}_{pid}_{n}"))
    }

    #[test]
    fn test_generate_continuation_prompt_basic() {
        let tmp = test_dir("basic");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();

        // Open needles
        let issues = r#"{"id":"100","status":"open","priority":"P0","title":"critical bug"}
{"id":"101","status":"open","priority":"P1","title":"add feature"}
{"id":"102","status":"closed","priority":"P1","title":"done"}
"#;
        fs::write(ostk_dir.join("needles/issues.jsonl"), issues).unwrap();

        // agents.jsonl (proper AgentEntry format for kernel::identity)
        let agents = r#"{"alias":"agent-1","pid":1234,"status":"active","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}
{"alias":"agent-2","pid":5678,"status":"dead","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}
"#;
        fs::write(ostk_dir.join("agents.jsonl"), agents).unwrap();

        let prompt = generate_continuation_prompt(&ostk_dir);

        // Should contain sections
        assert!(prompt.contains("# Continuation Prompt"), "missing header");
        assert!(prompt.contains("## Architecture"), "missing architecture");
        assert!(prompt.contains("## Open Needles"), "missing open needles");
        assert!(prompt.contains("P0 (critical)"), "missing P0 group");
        assert!(prompt.contains("\u{2192}100 critical bug"), "missing P0 needle");
        assert!(prompt.contains("\u{2192}101 add feature"), "missing P1 needle");
        assert!(!prompt.contains("\u{2192}102 done"), "closed needle should not appear");
        assert!(prompt.contains("## Fleet State"), "missing fleet state");
        assert!(prompt.contains("1 active / 2 total"), "missing fleet counts");
        assert!(prompt.contains("## Recent Commits"), "missing recent commits");
        assert!(prompt.contains("## Key Specs"), "missing key specs");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_generate_continuation_prompt_empty_state() {
        let tmp = test_dir("empty");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        fs::write(ostk_dir.join("needles/issues.jsonl"), "").unwrap();

        let prompt = generate_continuation_prompt(&ostk_dir);

        // Should still have all sections (even if empty)
        assert!(prompt.contains("# Continuation Prompt"));
        assert!(prompt.contains("## Architecture"));
        assert!(prompt.contains("## Open Needles"));

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_generate_continuation_prompt_groups_priorities() {
        let tmp = test_dir("priorities");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();

        let issues = r#"{"id":"1","status":"open","priority":"P0","title":"urgent"}
{"id":"2","status":"open","priority":"P2","title":"low prio"}
{"id":"3","status":"open","priority":"P1","title":"medium"}
"#;
        fs::write(ostk_dir.join("needles/issues.jsonl"), issues).unwrap();

        let prompt = generate_continuation_prompt(&ostk_dir);

        assert!(prompt.contains("### P0 (critical)"));
        assert!(prompt.contains("### P1 (important)"));
        assert!(prompt.contains("### P2 ("));

        let _ = fs::remove_dir_all(&tmp);
    }
}
