//! `ostk merge` — OS-aware merge.
//!
//! Wraps git merge with ostk-specific conflict resolution:
//! - needles/counter: take max(ours, theirs) + 1
//! - needles/issues.jsonl: append-only union (no duplicates by ID)
//! - audit.jsonl: concatenate, sort by timestamp
//! - .DS_Store: always delete
//! - lock files: always delete

use std::collections::HashSet;
use std::path::Path;
use std::process::Command;

use crate::{append_audit, find_project_root, now_iso};
use serde_json::json;

/// Run `ostk merge <target>` where target is a branch name or --pr N.
pub fn run(target: &str) -> Result<(), String> {
    let root = find_project_root()?;

    // Resolve target: --pr N → fetch PR branch
    let branch = if target.starts_with("--pr") {
        let pr_num = target
            .trim_start_matches("--pr")
            .trim()
            .trim_start_matches('=')
            .trim();
        let output = Command::new("gh")
            .args(["pr", "view", pr_num, "--json", "headRefName", "-q", ".headRefName"])
            .current_dir(&root)
            .output()
            .map_err(|e| format!("gh pr view failed: {e}"))?;
        if !output.status.success() {
            return Err(format!("failed to resolve PR {pr_num}"));
        }
        String::from_utf8_lossy(&output.stdout).trim().to_string()
    } else {
        target.to_string()
    };

    println!("ostk merge: {branch}");

    // Step 1: Attempt git merge
    let merge = Command::new("git")
        .args(["merge", &branch, "--no-commit", "--no-ff"])
        .current_dir(&root)
        .output()
        .map_err(|e| format!("git merge failed: {e}"))?;

    if merge.status.success() {
        println!("  clean merge — no conflicts");
        // Clean up OS artifacts
        cleanup_os_files(&root);
        commit_merge(&root, &branch)?;
        return Ok(());
    }

    // Step 2: Resolve ostk-owned conflicts
    println!("  conflicts detected — resolving ostk files...");
    let mut resolved = 0;
    let sd = crate::state_dir(&root);
    let sd_rel = sd.strip_prefix(&root).unwrap_or(sd.as_ref()).to_string_lossy();

    // Needle counter: max(ours, theirs)
    if has_conflict(&root, &format!("{sd_rel}/needles/counter")) {
        resolve_counter(&root)?;
        resolved += 1;
    }

    // Needle issues: append-only union
    if has_conflict(&root, &format!("{sd_rel}/needles/issues.jsonl")) {
        resolve_needles(&root)?;
        resolved += 1;
    }

    // Audit: concatenate
    if has_conflict(&root, &format!("{sd_rel}/audit.jsonl")) {
        resolve_audit(&root)?;
        resolved += 1;
    }

    // Identity counter: max
    if has_conflict(&root, &format!("{sd_rel}/identity_counter")) {
        resolve_counter_file(&root, &format!("{sd_rel}/identity_counter"))?;
        resolved += 1;
    }

    // Always delete OS artifacts
    cleanup_os_files(&root);

    // Check for remaining conflicts
    let remaining = count_conflicts(&root);
    if remaining > 0 {
        println!("  resolved {resolved} ostk conflicts, {remaining} remain (manual resolution needed)");
        return Err(format!("{remaining} unresolved conflicts"));
    }

    println!("  resolved {resolved} ostk conflicts — all clean");

    // Step 3: Verify OS integrity before committing
    println!("  verifying OS integrity...");
    verify_os_integrity(&root)?;

    commit_merge(&root, &branch)?;

    // Audit
    let _ = append_audit(
        &root,
        &json!({
            "event": "merge",
            "branch": branch,
            "conflicts_resolved": resolved,
            "verified": true,
            "timestamp": now_iso(),
        }),
    );

    Ok(())
}

fn has_conflict(root: &Path, file: &str) -> bool {
    let output = Command::new("git")
        .args(["diff", "--name-only", "--diff-filter=U"])
        .current_dir(root)
        .output()
        .ok();
    output
        .map(|o| String::from_utf8_lossy(&o.stdout).contains(file))
        .unwrap_or(false)
}

fn count_conflicts(root: &Path) -> usize {
    let output = Command::new("git")
        .args(["diff", "--name-only", "--diff-filter=U"])
        .current_dir(root)
        .output()
        .ok();
    output
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .lines()
                .filter(|l| !l.trim().is_empty())
                .count()
        })
        .unwrap_or(0)
}

fn resolve_counter(root: &Path) -> Result<(), String> {
    let sd = crate::state_dir(root);
    let sd_rel = sd.strip_prefix(root).unwrap_or(sd.as_ref()).to_string_lossy();
    resolve_counter_file(root, &format!("{sd_rel}/needles/counter"))
}

fn resolve_counter_file(root: &Path, rel_path: &str) -> Result<(), String> {
    let path = root.join(rel_path);

    // Get ours
    let ours = Command::new("git")
        .args(["show", &format!("HEAD:{rel_path}")])
        .current_dir(root)
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .and_then(|s| s.trim().parse::<u64>().ok())
        .unwrap_or(0);

    // Get theirs
    let theirs = Command::new("git")
        .args(["show", &format!("MERGE_HEAD:{rel_path}")])
        .current_dir(root)
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .and_then(|s| s.trim().parse::<u64>().ok())
        .unwrap_or(0);

    let resolved = ours.max(theirs);
    std::fs::write(&path, resolved.to_string())
        .map_err(|e| format!("write {rel_path}: {e}"))?;

    let _ = Command::new("git")
        .args(["add", rel_path])
        .current_dir(root)
        .output();

    println!("  {rel_path}: {ours} vs {theirs} → {resolved}");
    Ok(())
}

fn resolve_needles(root: &Path) -> Result<(), String> {
    let sd = crate::state_dir(root);
    let sd_rel = sd.strip_prefix(root).unwrap_or(sd.as_ref()).to_string_lossy();
    let rel = format!("{sd_rel}/needles/issues.jsonl");
    let rel = rel.as_str();
    let path = root.join(rel);

    // Get both versions
    let ours = git_show(root, &format!("HEAD:{rel}"));
    let theirs = git_show(root, &format!("MERGE_HEAD:{rel}"));

    // Union by ID — ours wins on conflict
    let mut seen_ids = HashSet::new();
    let mut lines = Vec::new();

    for line in ours.lines().chain(theirs.lines()) {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(trimmed)
            && let Some(id) = val.get("id").and_then(|v| v.as_str())
                && seen_ids.insert(id.to_string()) {
                    lines.push(trimmed.to_string());
                }
    }

    let content = lines.join("\n") + "\n";
    std::fs::write(&path, &content).map_err(|e| format!("write needles: {e}"))?;

    let _ = Command::new("git")
        .args(["add", rel])
        .current_dir(root)
        .output();

    println!("  {rel}: merged {}/{} unique needles", lines.len(), seen_ids.len());
    Ok(())
}

fn resolve_audit(root: &Path) -> Result<(), String> {
    let sd = crate::state_dir(root);
    let sd_rel = sd.strip_prefix(root).unwrap_or(sd.as_ref()).to_string_lossy();
    let rel = format!("{sd_rel}/audit.jsonl");
    let rel = rel.as_str();
    let path = root.join(rel);

    let ours = git_show(root, &format!("HEAD:{rel}"));
    let theirs = git_show(root, &format!("MERGE_HEAD:{rel}"));

    // Append-only: concat, dedup exact lines, sort by timestamp
    let mut seen = HashSet::new();
    let mut lines: Vec<String> = Vec::new();

    for line in ours.lines().chain(theirs.lines()) {
        let trimmed = line.trim();
        if !trimmed.is_empty() && seen.insert(trimmed.to_string()) {
            lines.push(trimmed.to_string());
        }
    }

    // Sort by timestamp if present
    lines.sort_by(|a, b| {
        let ts_a = serde_json::from_str::<serde_json::Value>(a)
            .ok()
            .and_then(|v| v.get("timestamp").and_then(|t| t.as_str()).map(String::from))
            .unwrap_or_default();
        let ts_b = serde_json::from_str::<serde_json::Value>(b)
            .ok()
            .and_then(|v| v.get("timestamp").and_then(|t| t.as_str()).map(String::from))
            .unwrap_or_default();
        ts_a.cmp(&ts_b)
    });

    let content = lines.join("\n") + "\n";
    std::fs::write(&path, &content).map_err(|e| format!("write audit: {e}"))?;

    let _ = Command::new("git")
        .args(["add", rel])
        .current_dir(root)
        .output();

    println!("  {rel}: merged {} events", lines.len());
    Ok(())
}

fn git_show(root: &Path, spec: &str) -> String {
    Command::new("git")
        .args(["show", spec])
        .current_dir(root)
        .output()
        .ok()
        .and_then(|o| {
            if o.status.success() {
                String::from_utf8(o.stdout).ok()
            } else {
                None
            }
        })
        .unwrap_or_default()
}

/// Verify OS integrity after merge — delegates to the shared implementation.
fn verify_os_integrity(root: &Path) -> Result<(), String> {
    crate::verify_os_integrity(root)
        .map_err(|e| format!("{e} — merge aborted"))
}

fn cleanup_os_files(root: &Path) {
    // Delete files the OS never wants
    for file in &[".DS_Store", "docs/.DS_Store"] {
        let path = root.join(file);
        if path.exists() {
            let _ = std::fs::remove_file(&path);
            let _ = Command::new("git")
                .args(["rm", "--cached", "-f", file])
                .current_dir(root)
                .output();
        }
    }

    // Delete lock files
    let sd = crate::state_dir(root);
    let sd_rel = sd.strip_prefix(root).unwrap_or(sd.as_ref()).to_string_lossy();
    let lock_files = [
        format!("{sd_rel}/gen_table.lock"),
        format!("{sd_rel}/hwm.lock"),
        format!("{sd_rel}/identity.lock"),
        format!("{sd_rel}/needles/issues.lock"),
        ".beads/issues.lock".to_string(),
        format!("{sd_rel}/beads/issues.lock"),
    ];
    for lock in &lock_files {
        let path = root.join(lock);
        if path.exists() {
            let _ = std::fs::remove_file(&path);
            let _ = Command::new("git")
                .args(["rm", "--cached", "-f", lock])
                .current_dir(root)
                .output();
        }
    }
}

fn commit_merge(root: &Path, branch: &str) -> Result<(), String> {
    let msg = format!("merge: {branch} (ostk-resolved)");
    let output = Command::new("git")
        .args(["commit", "-m", &msg])
        .current_dir(root)
        .output()
        .map_err(|e| format!("git commit: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("commit failed: {stderr}"));
    }

    println!("  committed: {msg}");
    Ok(())
}
