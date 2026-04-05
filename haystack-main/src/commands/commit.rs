use std::fmt::Write;

use crate::kernel::verb_ctx::VerbCtx;
use crate::{append_audit, find_project_root, now_iso, read_needles};
use regex::Regex;
use serde_json::{json, Value};
use std::fs;
use std::path::Path;
use std::process::Command;

/// Auto-detect commit type from message prefix.
/// Supports: fix, feat, docs, refactor, test, chore, ci, perf, style, build.
fn detect_type(message: &str) -> &'static str {
    let lower = message.to_lowercase();
    let prefixes: &[(&str, &str)] = &[
        ("fix", "fix"),
        ("feat", "feat"),
        ("docs", "docs"),
        ("refactor", "refactor"),
        ("test", "test"),
        ("chore", "chore"),
        ("ci", "ci"),
        ("perf", "perf"),
        ("style", "style"),
        ("build", "build"),
    ];
    for (prefix, typ) in prefixes {
        if lower.starts_with(prefix) {
            return typ;
        }
    }
    "feat"
}

/// Build the formatted commit message following the spec convention:
///   <type>: <description> (spec:<spec-name>#<section>, <needle-id>)
///
///   Agent: <agent-name>
fn build_commit_message(
    message: &str,
    spec: Option<&str>,
    section: Option<&str>,
    bead: Option<&str>,
    agent: &str,
) -> String {
    let commit_type = detect_type(message);

    // Strip type prefix from message if present (e.g. "feat: foo" -> "foo")
    let desc = message
        .trim_start_matches(commit_type)
        .trim_start_matches(':')
        .trim();

    // Build the parenthetical reference
    let mut refs = Vec::new();
    if let Some(s) = spec {
        let spec_ref = if let Some(sec) = section {
            format!("spec:{}#{}", s, sec)
        } else {
            format!("spec:{}", s)
        };
        refs.push(spec_ref);
    }
    if let Some(b) = bead {
        refs.push(b.to_string());
    }

    let subject = if refs.is_empty() {
        format!("{}: {}", commit_type, desc)
    } else {
        format!("{}: {} ({})", commit_type, desc, refs.join(", "))
    };

    format!("{}\n\nAgent: {}", subject, agent)
}

/// Read audit.jsonl events from .ostk/audit.jsonl
fn read_audit_events(root: &Path) -> Result<Vec<Value>, String> {
    let audit_path = crate::state_dir(root).join("audit.jsonl");
    if !audit_path.exists() {
        return Ok(vec![]);
    }
    let content =
        fs::read_to_string(&audit_path).map_err(|e| format!("failed to read audit.jsonl: {e}"))?;
    let mut events = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(v) = serde_json::from_str::<Value>(line) {
            events.push(v);
        }
    }
    Ok(events)
}

/// Generate conversation attribution from audit.jsonl.
/// Finds the last needle.committed event (previous commit boundary),
/// counts events since, extracts time range.
fn conversation_attribution(root: &Path) -> Option<String> {
    let events = read_audit_events(root).ok()?;
    if events.is_empty() {
        return None;
    }

    // Find the index of the last committed event
    let last_committed_idx = events.iter().rposition(|e| {
        matches!(
            e.get("event").and_then(|v| v.as_str()),
            Some("needle.committed") | Some("bead.committed")
        )
    });

    // Events since last commit (exclusive of the commit event itself)
    let since_events = match last_committed_idx {
        Some(idx) => &events[idx + 1..],
        None => &events[..], // no previous commit — count all events
    };

    let count = since_events.len();
    if count == 0 {
        return None;
    }

    // Extract timestamps from the slice
    let first_ts = since_events
        .first()
        .and_then(|e| e.get("timestamp"))
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let last_ts = since_events
        .last()
        .and_then(|e| e.get("timestamp"))
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");

    Some(format!(
        "Conversation: {} — {}\nEvents: {} events since last commit",
        first_ts, last_ts, count
    ))
}

/// CLI entry point (thin wrapper).
pub fn run(
    message: &str,
    spec: Option<&str>,
    section: Option<&str>,
    bead: Option<&str>,
    agent: &str,
) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx, message, spec, section, bead, agent)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for :ship / commit (→1157).
pub fn run_verb(
    ctx: &mut VerbCtx,
    message: &str,
    spec: Option<&str>,
    section: Option<&str>,
    bead: Option<&str>,
    agent: &str,
) -> Result<(), String> {
    let root = ctx.root.to_path_buf();

    // 1. Validate --spec references an existing file in docs/spec/
    if let Some(spec_name) = spec {
        let spec_file = if spec_name.ends_with(".md") {
            spec_name.to_string()
        } else {
            format!("{}.md", spec_name)
        };
        let spec_path = root.join("docs/spec").join(&spec_file);
        if !spec_path.exists() {
            return Err(crate::strings::commit::SPEC_NOT_FOUND
                .replacen("{}", spec_name, 1)
                .replacen("{}", &spec_path.display().to_string(), 1));
        }
    } else {
        eprintln!("{}", crate::strings::commit::NO_SPEC_WARNING);
    }

    // 2. Validate --bead or auto-detect from message / in_progress beads
    let detected_bead: Option<String>;
    if let Some(bead_id) = bead {
        let beads = read_needles(&root)?;
        let found = beads
            .iter()
            .any(|b| b.get("id").and_then(|v| v.as_str()) == Some(bead_id));
        if !found {
            return Err(crate::strings::commit::NEEDLE_NOT_FOUND.replacen("{}", bead_id, 1));
        }
        detected_bead = Some(bead_id.to_string());
    } else {
        // Auto-detect: scan commit message for →NNN pattern
        let re = Regex::new(r"→\d+").unwrap();
        if let Some(m) = re.find(message) {
            let candidate = m.as_str().to_string();
            // Validate it exists in issues.jsonl
            let beads = read_needles(&root)?;
            let found = beads
                .iter()
                .any(|b| b.get("id").and_then(|v| v.as_str()) == Some(candidate.as_str()));
            if found {
                eprintln!("{}", crate::strings::commit::AUTO_DETECTED_NEEDLE.replacen("{}", &candidate, 1));
                detected_bead = Some(candidate);
            } else {
                eprintln!("{}", crate::strings::commit::NEEDLE_NOT_IN_ISSUES.replacen("{}", &candidate, 1));
                detected_bead = None;
            }
        } else {
            // Fallback: look for in_progress needle assigned to current agent
            let beads = read_needles(&root)?;
            let in_progress = beads.iter().find(|b| {
                b.get("status").and_then(|v| v.as_str()) == Some("in_progress")
                    && b.get("assignee").and_then(|v| v.as_str()) == Some(agent)
            });
            if let Some(b) = in_progress {
                if let Some(id) = b.get("id").and_then(|v| v.as_str()) {
                    eprintln!("{}", crate::strings::commit::AUTO_DETECTED_IN_PROGRESS
                        .replacen("{}", id, 1)
                        .replacen("{}", agent, 1));
                    detected_bead = Some(id.to_string());
                } else {
                    eprintln!("{}", crate::strings::commit::NO_NEEDLE_REF);
                    detected_bead = None;
                }
            } else {
                eprintln!("{}", crate::strings::commit::NO_NEEDLE_REF);
                detected_bead = None;
            }
        }
    }

    let effective_bead = detected_bead.as_deref();

    // 3. Construct commit message
    let mut commit_msg = build_commit_message(message, spec, section, effective_bead, agent);

    // 3b. Add conversation attribution for chore/nit commits (no --spec and no --bead)
    if spec.is_none() && bead.is_none()
        && let Some(attribution) = conversation_attribution(&root) {
            commit_msg.push_str("\n\n");
            commit_msg.push_str(&attribution);
        }

    // 4. Check if there are staged changes; if not, run git add -A
    let staged = Command::new("git")
        .args(["diff", "--cached", "--quiet"])
        .current_dir(&root)
        .status()
        .map_err(|e| format!("failed to run git diff: {}", e))?;

    if staged.success() {
        // Exit code 0 means no staged changes — stage everything
        let add = Command::new("git")
            .args(["add", "-A"])
            .current_dir(&root)
            .status()
            .map_err(|e| format!("failed to run git add: {}", e))?;
        if !add.success() {
            return Err(crate::strings::errors::GIT_ADD_FAILED.into());
        }
    }

    // 5. Run git commit
    let commit_output = Command::new("git")
        .args(["commit", "-m", &commit_msg])
        .current_dir(&root)
        .output()
        .map_err(|e| format!("failed to run git commit: {}", e))?;

    if !commit_output.status.success() {
        let stderr = String::from_utf8_lossy(&commit_output.stderr);
        let stdout = String::from_utf8_lossy(&commit_output.stdout);
        return Err(format!("git commit failed:\n{}{}", stdout, stderr));
    }

    // 6. Get the commit hash
    let hash_output = Command::new("git")
        .args(["log", "-1", "--format=%H"])
        .current_dir(&root)
        .output()
        .map_err(|e| format!("failed to get commit hash: {}", e))?;
    let commit_hash = String::from_utf8_lossy(&hash_output.stdout)
        .trim()
        .to_string();
    let short_hash = if commit_hash.len() >= 7 {
        &commit_hash[..7]
    } else {
        &commit_hash
    };

    // 6b. Append commit hash to needle's commit_refs[] array
    if let Some(needle_id) = effective_bead {
        let hash_for_needle = commit_hash.clone();
        let _ = crate::with_needles_locked(&root, |needles| {
            if let Some(needle) = needles.iter_mut().find(|n| {
                n.get("id").and_then(|v| v.as_str()) == Some(needle_id)
            }) {
                let mut refs = needle
                    .get("commit_refs")
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default();
                let hash_val = serde_json::json!(hash_for_needle);
                if !refs.contains(&hash_val) {
                    refs.push(hash_val);
                }
                needle["commit_refs"] = serde_json::json!(refs);
            }
            Ok(())
        });
    }

    // 7. Emit audit event
    let spec_ref = match (spec, section) {
        (Some(s), Some(sec)) => format!("docs/spec/{}#{}", s, sec),
        (Some(s), None) => format!("docs/spec/{}", s),
        _ => String::new(),
    };

    append_audit(
        &root,
        &json!({
            "event": "bead.committed",
            "bead": effective_bead.unwrap_or("none"),
            "commit": commit_hash,
            "spec_ref": spec_ref,
            "agent": agent,
            "timestamp": now_iso()
        }),
    )?;

    // 8. Print result
    writeln!(ctx,
        "committed {} {}",
        short_hash,
        commit_msg.lines().next().unwrap_or("")
    ).unwrap();

    // →596: Record resolution event for :ship (exact match static)
    let _ = append_audit(&root, &json!({
        "event": "tack.resolved",
        "input": ":ship",
        "verb": "ship",
        "tier": 1,
        "source": "static",
        "resolved": true,
        "timestamp": now_iso()
    }));

    if let Err(e) = crate::verify_os_integrity(&root) {
        eprintln!("{}", crate::strings::commit::OS_INTEGRITY_FAIL.replacen("{}", &e, 1));
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_detect_type() {
        assert_eq!(detect_type("fix: something broken"), "fix");
        assert_eq!(detect_type("feat: new feature"), "feat");
        assert_eq!(detect_type("docs: update readme"), "docs");
        assert_eq!(detect_type("refactor: clean up"), "refactor");
        assert_eq!(detect_type("something else"), "feat");
    }

    #[test]
    fn test_build_commit_message_full() {
        let msg = build_commit_message(
            "feat: implement commit command",
            Some("document-lifecycle"),
            Some("commit-convention"),
            Some("→011"),
            "orchestrator",
        );
        assert!(msg.contains("feat: implement commit command"));
        assert!(msg.contains("spec:document-lifecycle#commit-convention"));
        assert!(msg.contains("→011"));
        assert!(msg.contains("Agent: orchestrator"));
    }

    #[test]
    fn test_build_commit_message_no_spec() {
        let msg = build_commit_message("fix: bug", None, None, Some("→001"), "agent-1");
        assert!(msg.contains("fix: bug (→001)"));
        assert!(msg.contains("Agent: agent-1"));
        assert!(!msg.contains("bd-"));
    }

    #[test]
    fn test_build_commit_message_no_bead() {
        let msg = build_commit_message(
            "docs: update spec",
            Some("document-lifecycle"),
            None,
            None,
            "orchestrator",
        );
        assert!(msg.contains("docs: update spec (spec:document-lifecycle)"));
        assert!(!msg.contains("bd-"));
    }

    #[test]
    fn test_build_commit_message_no_refs() {
        let msg = build_commit_message("chore: cleanup", None, None, None, "orchestrator");
        assert_eq!(msg, "chore: cleanup\n\nAgent: orchestrator");
    }

    #[test]
    fn test_conversation_attribution_no_audit() {
        let tmp = std::env::temp_dir().join("ostk_test_no_audit");
        let _ = fs::create_dir_all(tmp.join(".ostk"));
        // No audit.jsonl exists
        let _ = fs::remove_file(tmp.join(".ostk/audit.jsonl"));
        let result = conversation_attribution(&tmp);
        assert!(result.is_none());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_conversation_attribution_empty_audit() {
        let tmp = std::env::temp_dir().join("ostk_test_empty_audit");
        let _ = fs::create_dir_all(tmp.join(".ostk"));
        fs::write(tmp.join(".ostk/audit.jsonl"), "").unwrap();
        let result = conversation_attribution(&tmp);
        assert!(result.is_none());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_conversation_attribution_no_prior_commit() {
        let tmp = std::env::temp_dir().join("ostk_test_no_prior");
        let _ = fs::create_dir_all(tmp.join(".ostk"));
        let audit = r#"{"event":"task.added","id":"→001","timestamp":"2026-03-08T10:00:00Z"}
{"event":"task.claimed","id":"→001","timestamp":"2026-03-08T10:05:00Z"}
{"event":"task.closed","id":"→001","timestamp":"2026-03-08T10:10:00Z"}
"#;
        fs::write(tmp.join(".ostk/audit.jsonl"), audit).unwrap();
        let result = conversation_attribution(&tmp);
        assert!(result.is_some());
        let attr = result.unwrap();
        assert!(attr.contains("2026-03-08T10:00:00Z"));
        assert!(attr.contains("2026-03-08T10:10:00Z"));
        assert!(attr.contains("3 events since last commit"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_conversation_attribution_with_prior_commit() {
        let tmp = std::env::temp_dir().join("ostk_test_with_prior");
        let _ = fs::create_dir_all(tmp.join(".ostk"));
        let audit = r#"{"event":"task.added","id":"→001","timestamp":"2026-03-08T09:00:00Z"}
{"event":"bead.committed","bead":"→001","commit":"abc1234","timestamp":"2026-03-08T09:30:00Z"}
{"event":"task.added","id":"→002","timestamp":"2026-03-08T10:00:00Z"}
{"event":"task.claimed","id":"→002","timestamp":"2026-03-08T10:05:00Z"}
"#;
        fs::write(tmp.join(".ostk/audit.jsonl"), audit).unwrap();
        let result = conversation_attribution(&tmp);
        assert!(result.is_some());
        let attr = result.unwrap();
        assert!(attr.contains("2026-03-08T10:00:00Z"));
        assert!(attr.contains("2026-03-08T10:05:00Z"));
        assert!(attr.contains("2 events since last commit"));
        // Should NOT include events before the commit
        assert!(!attr.contains("09:00:00Z"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_conversation_attribution_nothing_since_commit() {
        let tmp = std::env::temp_dir().join("ostk_test_nothing_since");
        let _ = fs::create_dir_all(tmp.join(".ostk"));
        let audit = r#"{"event":"task.added","id":"→001","timestamp":"2026-03-08T09:00:00Z"}
{"event":"bead.committed","bead":"→001","commit":"abc1234","timestamp":"2026-03-08T09:30:00Z"}
"#;
        fs::write(tmp.join(".ostk/audit.jsonl"), audit).unwrap();
        let result = conversation_attribution(&tmp);
        assert!(result.is_none());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_conversation_attribution_needle_committed() {
        let tmp = std::env::temp_dir().join("ostk_test_needle_committed");
        let _ = fs::create_dir_all(tmp.join(".ostk"));
        let audit = r#"{"event":"task.added","id":"→001","timestamp":"2026-03-08T09:00:00Z"}
{"event":"needle.committed","bead":"→001","commit":"def5678","timestamp":"2026-03-08T09:30:00Z"}
{"event":"hay.filed","source":"agent","straw":"observation","timestamp":"2026-03-08T10:00:00Z"}
"#;
        fs::write(tmp.join(".ostk/audit.jsonl"), audit).unwrap();
        let result = conversation_attribution(&tmp);
        assert!(result.is_some());
        let attr = result.unwrap();
        assert!(attr.contains("1 events since last commit"));
        assert!(attr.contains("2026-03-08T10:00:00Z"));
        let _ = fs::remove_dir_all(&tmp);
    }
}
