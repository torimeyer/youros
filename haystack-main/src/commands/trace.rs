use std::fmt::Write;
use std::path::Path;
use std::process::Command;

use crate::{
    normalize_spec_path, parse_needle_ids, read_audit_events, read_needles,
    resolve_remaps,
};
use crate::kernel::verb_ctx::VerbCtx;

/// CLI entry point.
pub fn run(id: &str) -> Result<(), String> {
    let root = crate::find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx, id)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation — writes to VerbCtx (→1157).
pub fn run_verb(ctx: &mut VerbCtx, id: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    if id.starts_with("bd-") || id.starts_with("nd-") || id.starts_with("\u{2192}") {
        trace_needle(ctx, id, &root)
    } else if id.contains('/') || id.ends_with(".md") {
        let normalized = normalize_spec_path(&root, id);
        trace_spec(ctx, &normalized, &root)
    } else {
        trace_commit(ctx, id, &root)
    }
}

/// Format a JSON array field as a comma-separated string, or "(none)" if absent/empty.
fn format_array(needle: &serde_json::Value, field: &str) -> String {
    needle
        .get(field)
        .and_then(|v| v.as_array())
        .filter(|a| !a.is_empty())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str())
                .collect::<Vec<&str>>()
                .join(", ")
        })
        .unwrap_or_else(|| "(none)".to_string())
}

/// Format commit_refs: show short hashes, or "(none)" if absent/empty.
fn format_commit_refs(needle: &serde_json::Value) -> String {
    needle
        .get("commit_refs")
        .and_then(|v| v.as_array())
        .filter(|a| !a.is_empty())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str())
                .map(|h| if h.len() >= 7 { &h[..7] } else { h })
                .collect::<Vec<&str>>()
                .join(", ")
        })
        .unwrap_or_else(|| "(none)".to_string())
}

fn trace_needle(w: &mut VerbCtx, id: &str, root: &Path) -> Result<(), String> {
    let needles = read_needles(root)?;
    let needle = needles
        .iter()
        .find(|b| b.get("id").and_then(|v| v.as_str()) == Some(id))
        .cloned()
        .ok_or_else(|| format!("needle '{}' not found", id))?;

    // Header line: →843: title [priority, status]
    let title = needle.get("title").and_then(|v| v.as_str()).unwrap_or("?");
    let status = needle.get("status").and_then(|v| v.as_str()).unwrap_or("?");
    let priority = needle.get("priority").and_then(|v| v.as_str()).unwrap_or("?");
    writeln!(w, "{}: {} [{}, {}]", id, title, priority, status).unwrap();

    // AC
    if let Some(ac) = needle.get("ac").and_then(|v| v.as_str()) {
        writeln!(w, "  AC: {}", ac).unwrap();
    }

    // Linked artifacts
    writeln!(w, "  specs: {}", format_array(&needle, "specs")).unwrap();
    writeln!(w, "  drafts: {}", format_array(&needle, "drafts")).unwrap();
    writeln!(w, "  agentfiles: {}", format_array(&needle, "agentfiles")).unwrap();

    // Graph edges
    writeln!(w, "  depends_on: {}", format_array(&needle, "depends_on")).unwrap();
    writeln!(w, "  blocks: {}", format_array(&needle, "blocks")).unwrap();

    // Commits: from commit_refs[] first, then fall back to git grep
    let stored_refs = format_commit_refs(&needle);
    if stored_refs != "(none)" {
        writeln!(w, "  commits: {}", stored_refs).unwrap();
    } else {
        // Fall back to git log --grep for older needles without commit_refs
        let root_str = root.to_str().unwrap_or(".");
        let git_result = Command::new("git")
            .args(["-C", root_str, "log", "--oneline", &format!("--grep={}", id)])
            .output();

        match git_result {
            Ok(output) if output.status.success() => {
                let stdout = String::from_utf8_lossy(&output.stdout);
                let lines: Vec<&str> = stdout.lines().filter(|l| !l.is_empty()).collect();
                if lines.is_empty() {
                    writeln!(w, "  commits: (none)").unwrap();
                } else {
                    let audit_events = read_audit_events(root)?;
                    let remaps = resolve_remaps(&audit_events);
                    let hashes: Vec<String> = lines
                        .iter()
                        .map(|l| {
                            let hash = l.split_whitespace().next().unwrap_or(l);
                            if let Some(final_hash) = remaps.get(hash) {
                                format!("{} (remapped to {})", hash, final_hash)
                            } else {
                                hash.to_string()
                            }
                        })
                        .collect();
                    writeln!(w, "  commits: {}", hashes.join(", ")).unwrap();
                }
            }
            _ => {
                writeln!(w, "  commits: (none)").unwrap();
            }
        }
    }

    Ok(())
}

fn trace_spec(w: &mut VerbCtx, spec_path: &str, root: &Path) -> Result<(), String> {
    let needles = read_needles(root)?;

    // Search both the old spec_ref field and the new specs[] array
    let matching: Vec<&serde_json::Value> = needles
        .iter()
        .filter(|b| {
            // Check legacy spec_ref field
            let matches_spec_ref = b
                .get("spec_ref")
                .and_then(|v| v.as_str())
                .map(|s| s == spec_path || s.ends_with(spec_path))
                .unwrap_or(false);

            // Check new specs[] array
            let matches_specs_array = b
                .get("specs")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter().any(|v| {
                        v.as_str()
                            .map(|s| s == spec_path || s.ends_with(spec_path))
                            .unwrap_or(false)
                    })
                })
                .unwrap_or(false);

            matches_spec_ref || matches_specs_array
        })
        .collect();

    if matching.is_empty() {
        writeln!(w, "no needles found for spec '{}'", spec_path).unwrap();
        return Ok(());
    }

    writeln!(w, "Spec: {}", spec_path).unwrap();

    let mut closed_count = 0usize;
    let total = matching.len();

    for needle in &matching {
        let needle_id = needle.get("id").and_then(|v| v.as_str()).unwrap_or("?");
        let title = needle.get("title").and_then(|v| v.as_str()).unwrap_or("?");
        let status = needle.get("status").and_then(|v| v.as_str()).unwrap_or("?");
        let priority = needle.get("priority").and_then(|v| v.as_str()).unwrap_or("?");

        if status == "closed" {
            closed_count += 1;
        }

        writeln!(w, "  {} [{}, {}] {}", needle_id, priority, status, title).unwrap();
    }

    writeln!(w, "{}/{} needles closed", closed_count, total).unwrap();

    Ok(())
}

fn trace_commit(w: &mut VerbCtx, hash: &str, root: &Path) -> Result<(), String> {
    let audit_events = read_audit_events(root)?;
    let remaps = resolve_remaps(&audit_events);

    let mut current_hash = hash.to_string();
    let mut chain = vec![current_hash.clone()];

    while let Some(next) = remaps.get(&current_hash) {
        current_hash = next.clone();
        chain.push(current_hash.clone());
    }

    let final_hash = &current_hash;
    let root_str = root.to_str().unwrap_or(".");
    let output = Command::new("git")
        .args(["-C", root_str, "log", "-1", "--format=%s", final_hash])
        .output()
        .map_err(|e| format!("failed to run git: {}", e))?;

    if !output.status.success() {
        return Err(format!(
            "git log failed for commit '{}': {}",
            final_hash,
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }

    let message = String::from_utf8_lossy(&output.stdout);
    let message = message.trim();
    if message.is_empty() {
        return Err(format!("commit '{}' not found", final_hash));
    }

    if chain.len() > 1 {
        writeln!(
            w,
            "Commit: {} (remapped chain: {})",
            hash,
            chain.join(" \u{2192} ")
        )
        .unwrap();
    } else {
        writeln!(w, "Commit: {}", hash).unwrap();
    }
    writeln!(w, "  message: {}", message).unwrap();

    let bead_ids = parse_needle_ids(message);
    if bead_ids.is_empty() {
        writeln!(w, "  needles: none").unwrap();
        return Ok(());
    }

    let beads = read_needles(root)?;
    writeln!(w, "  needles:").unwrap();
    for bead_id in &bead_ids {
        let bead = beads
            .iter()
            .find(|b| b.get("id").and_then(|v| v.as_str()) == Some(bead_id.as_str()));
        match bead {
            Some(b) => {
                let title = b.get("title").and_then(|v| v.as_str()).unwrap_or("?");
                let status = b.get("status").and_then(|v| v.as_str()).unwrap_or("?");
                writeln!(w, "    {} [{}] {}", bead_id, status, title).unwrap();
            }
            None => writeln!(w, "    {} (not found)", bead_id).unwrap(),
        }
    }

    Ok(())
}
