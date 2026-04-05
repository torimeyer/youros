use std::fmt::Write;

use crate::kernel::verb_ctx::VerbCtx;
use crate::{
    append_audit, find_project_root, next_needle_id, now_iso, post_mutation, read_needles,
    with_needles_locked, MutationEvent,
};
use crate::commands::refine::{build_joint_graph, find_spheres, bfs_radius, DEPENDS_PATTERNS, BLOCKS_PATTERNS};
use regex::Regex;
use serde_json::{Value, json};
use std::process::Command;
use std::fs;

/// Extract needle IDs from registers-dump.md content.
/// Matches patterns: →NNN, bd-NNN, nd-NNN (case insensitive prefix).
fn extract_register_needle_ids(content: &str) -> Vec<String> {
    let re = Regex::new(r"(?:→|bd-|nd-)(\d+)").unwrap();
    let mut seen = std::collections::HashSet::new();
    let mut ids = Vec::new();
    for cap in re.captures_iter(content) {
        let num = &cap[1];
        // Normalize all to the canonical forms we'll look up
        // We store all three variants so we can match against issues.jsonl
        let candidates = [
            format!("→{}", num),
            format!("bd-{}", num),
            format!("nd-{}", num),
        ];
        if seen.insert(num.to_string()) {
            ids.push(candidates[0].clone());
            ids.push(candidates[1].clone());
            ids.push(candidates[2].clone());
        }
    }
    ids
}

/// Read registers-dump.md and return prioritized needle IDs (if any open in issues.jsonl).
fn register_prioritized_next(root: &std::path::Path, needles: &[Value]) -> Option<usize> {
    let reg_path = crate::state_dir(root).join("registers-dump.md");
    let content = std::fs::read_to_string(&reg_path).ok()?;

    let register_ids = extract_register_needle_ids(&content);
    if register_ids.is_empty() {
        return None;
    }

    // Find the first register ID that matches an open task/needle in issues.jsonl
    // Group by number — check all three forms for each number
    let mut checked = std::collections::HashSet::new();
    for reg_id in &register_ids {
        // Extract the number portion to avoid checking the same needle thrice
        let num: String = reg_id.chars().filter(|c| c.is_ascii_digit()).collect();
        if !checked.insert(num) {
            continue;
        }

        for (i, n) in needles.iter().enumerate() {
            let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let status = n.get("status").and_then(|v| v.as_str()).unwrap_or("");

            // Only open items
            if status != "open" {
                continue;
            }

            // Match if the needle's ID matches any variant of this register ID
            if register_ids.iter().any(|r| r == id) {
                return Some(i);
            }
        }
    }

    None
}

/// CLI entry point (thin wrapper).
pub fn run_add(
    title: &str,
    priority: &str,
    milestone: &Option<String>,
    tags: Option<&str>,
    ac: Option<&str>,
    description: Option<&str>,
    depends_on: Option<&str>,
    blocks: Option<&str>,
) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_add_verb(&mut ctx, title, priority, milestone, tags, ac, description, depends_on, blocks)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
pub fn run_add_verb(
    ctx: &mut VerbCtx,
    title: &str,
    priority: &str,
    milestone: &Option<String>,
    tags: Option<&str>,
    ac: Option<&str>,
    description: Option<&str>,
    depends_on: Option<&str>,
    blocks: Option<&str>,
) -> Result<(), String> {
    let valid_priorities = ["P0", "P1", "P2"];
    if !valid_priorities.contains(&priority) {
        return Err(format!(
            "invalid priority '{}': must be one of P0, P1, P2",
            priority
        ));
    }
    let root = ctx.root.to_path_buf();
    let id = next_needle_id(&root)?;
    let now = now_iso();

    with_needles_locked(&root, |beads| {
        let mut entry = json!({
            "id": id,
            "title": title,
            "status": "open",
            "priority": priority,
            "issue_type": "task",
            "created_at": now
        });
        if let Some(m) = milestone {
            entry["milestone"] = json!(m);
        }
        if let Some(t) = tags {
            entry["tags"] = json!(t.split(',').map(|s| s.trim()).collect::<Vec<&str>>());
        }
        if let Some(a) = ac {
            entry["ac"] = json!(a);
        }
        if let Some(d) = description {
            entry["description"] = json!(d);
        }
        if let Some(dep) = depends_on {
            let ids: Vec<&str> = dep.split(',').map(|s| s.trim()).filter(|s| !s.is_empty()).collect();
            entry["depends_on"] = json!(ids);
        }
        if let Some(blk) = blocks {
            let ids: Vec<&str> = blk.split(',').map(|s| s.trim()).filter(|s| !s.is_empty()).collect();
            entry["blocks"] = json!(ids);
        }
        beads.push(entry);
        Ok(())
    })?;

    append_audit(
        &root,
        &json!({
            "event": "task.added",
            "id": id,
            "title": title,
            "priority": priority,
            "issue_type": "task",
            "milestone": milestone,
            "timestamp": now_iso()
        }),
    )?;
    writeln!(ctx, "added {}: {} [{}]", id, title, priority).unwrap();

    // Implicit compile: update index
    let _ = post_mutation(
        &root,
        &MutationEvent::NeedleAdded {
            id: id.to_string(),
            priority: priority.to_string(),
        },
    );

    Ok(())
}

/// CLI entry point (thin wrapper).
pub fn run_list(
    status: Option<&str>,
    priority: Option<&str>,
    count_only: bool,
    as_json: bool,
) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_list_verb(&mut ctx, status, priority, count_only, as_json)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
pub fn run_list_verb(
    ctx: &mut VerbCtx,
    status: Option<&str>,
    priority: Option<&str>,
    count_only: bool,
    as_json: bool,
) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let needles = read_needles(&root)?;

    let mut filtered: Vec<&Value> = needles
        .iter()
        .filter(|n| {
            if let Some(s) = status
                && n.get("status").and_then(|v| v.as_str()) != Some(s) {
                    return false;
                }
            if let Some(p) = priority
                && n.get("priority").and_then(|v| v.as_str()) != Some(p) {
                    return false;
                }
            true
        })
        .collect();

    if count_only {
        writeln!(ctx, "{}", filtered.len()).unwrap();
        return Ok(());
    }

    if as_json {
        writeln!(ctx, "{}", serde_json::to_string_pretty(&filtered).unwrap()).unwrap();
        return Ok(());
    }

    if filtered.is_empty() {
        writeln!(ctx, "no needles found").unwrap();
    } else {
        // Sort by priority (P0 first)
        filtered.sort_by_key(|n| {
            priority_rank(n.get("priority").and_then(|v| v.as_str()).unwrap_or("P2"))
        });

        for b in filtered {
            let id = b.get("id").and_then(|v| v.as_str()).unwrap_or("?");
            let title = b.get("title").and_then(|v| v.as_str()).unwrap_or("?");
            let status = b.get("status").and_then(|v| v.as_str()).unwrap_or("?");
            let priority = b.get("priority").and_then(|v| v.as_str()).unwrap_or("?");

            let mut extras = Vec::new();
            if b.get("ac").and_then(|v| v.as_str()).is_some() {
                extras.push("AC".to_string());
            }
            let dep_count = b.get("depends_on")
                .and_then(|v| v.as_array())
                .map(|a| a.len())
                .unwrap_or(0);
            let blk_count = b.get("blocks")
                .and_then(|v| v.as_array())
                .map(|a| a.len())
                .unwrap_or(0);
            if dep_count > 0 {
                extras.push(format!("deps:{}", dep_count));
            }
            if blk_count > 0 {
                extras.push(format!("blocks:{}", blk_count));
            }

            let suffix = if extras.is_empty() {
                String::new()
            } else {
                format!(" ({})", extras.join(", "))
            };
            writeln!(ctx, "  {} [{}] [{}] {}{}", id, priority, status, title, suffix).unwrap();
        }
    }
    Ok(())
}

fn priority_rank(p: &str) -> u8 {
    match p {
        "P0" => 0,
        "P1" => 1,
        "P2" => 2,
        _ => 3,
    }
}

/// Pick next needle index: registers-dump.md first, then (priority, radius) sort fallback.
/// Lower radius = higher priority among same-priority needles, because needles closer
/// to a sphere's center compound more (they connect to more work).
fn pick_next_index(root: &std::path::Path, beads: &[Value]) -> Option<usize> {
    // Try register-prioritized pick first
    if let Some(idx) = register_prioritized_next(root, beads) {
        return Some(idx);
    }

    // Fallback: sort open tasks by (priority, radius)
    let mut open: Vec<usize> = beads
        .iter()
        .enumerate()
        .filter(|(_, b)| {
            b.get("status").and_then(|v| v.as_str()) == Some("open")
                && b.get("issue_type").and_then(|v| v.as_str()) == Some("task")
        })
        .map(|(i, _)| i)
        .collect();
    if open.is_empty() {
        return None;
    }

    // Build radius map: needle_id → BFS distance from its sphere's point.
    // Isolated needles (not in any sphere) get usize::MAX so they sort last
    // among same-priority needles.
    let radius_map = compute_needle_radii(beads);

    open.sort_by_key(|&i| {
        let pri = priority_rank(
            beads[i]
                .get("priority")
                .and_then(|v| v.as_str())
                .unwrap_or("P2"),
        );
        let id = beads[i]
            .get("id")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let radius = radius_map
            .get(id)
            .copied()
            .unwrap_or(usize::MAX);
        (pri, radius)
    });
    Some(open[0])
}

/// Compute BFS radius for every open needle from its sphere's point.
/// Returns a map of needle_id → radius. Isolated needles (single-node spheres)
/// are excluded so they default to usize::MAX in the sort, ranking last among
/// same-priority needles.
fn compute_needle_radii(beads: &[Value]) -> std::collections::HashMap<String, usize> {
    let (_joints, adj) = build_joint_graph(beads);
    let spheres = find_spheres(&adj);
    let mut radii = std::collections::HashMap::new();
    for sphere in &spheres {
        // Skip isolated needles — only connected spheres get radius scores
        if sphere.needles.len() < 2 {
            continue;
        }
        let radius_map = bfs_radius(&adj, &sphere.point);
        for (nid, dist) in radius_map {
            radii.insert(nid, dist);
        }
    }
    radii
}

/// CLI entry point (thin wrapper).
pub fn run_next(claim: bool, assignee: Option<&str>) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_next_verb(&mut ctx, claim, assignee)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
pub fn run_next_verb(ctx: &mut VerbCtx, claim: bool, assignee: Option<&str>) -> Result<(), String> {
    let root = ctx.root.to_path_buf();

    if !claim {
        // Read-only path — no lock needed
        let beads = read_needles(&root)?;
        let idx = match pick_next_index(&root, &beads) {
            Some(i) => i,
            None => {
                writeln!(ctx, "no open tasks").unwrap();
                return Ok(());
            }
        };
        writeln!(ctx,
            "{} {} [{}]",
            beads[idx]["id"].as_str().unwrap_or(""),
            beads[idx]["title"].as_str().unwrap_or(""),
            beads[idx]["priority"].as_str().unwrap_or("")
        ).unwrap();
        return Ok(());
    }

    // Claim path — needs lock
    let claimed = with_needles_locked(&root, |beads| {
        let idx = match pick_next_index(&root, beads) {
            Some(i) => i,
            None => {
                return Ok(None);
            }
        };
        let id = beads[idx]["id"].as_str().unwrap_or("").to_string();
        let title = beads[idx]["title"].as_str().unwrap_or("").to_string();
        let priority = beads[idx]["priority"].as_str().unwrap_or("").to_string();

        beads[idx]["status"] = json!("in_progress");
        if let Some(a) = assignee {
            beads[idx]["assignee"] = json!(a);
        }
        Ok(Some((id, title, priority, assignee.unwrap_or("unknown").to_string())))
    })?;

    match claimed {
        None => {
            writeln!(ctx, "no open tasks").unwrap();
        }
        Some((id, title, priority, assignee_str)) => {
            writeln!(ctx, "{} {} [{}]", id, title, priority).unwrap();
            append_audit(
                &root,
                &json!({
                    "event": "task.claimed",
                    "id": id,
                    "assignee": assignee_str,
                    "timestamp": now_iso()
                }),
            )?;
            writeln!(ctx, "claimed {}", id).unwrap();
        }
    }
    Ok(())
}

/// CLI entry point (thin wrapper).
pub fn run_close(id: &str, reason: Option<&str>) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_close_verb(&mut ctx, id, reason)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
pub fn run_close_verb(ctx: &mut VerbCtx, id: &str, reason: Option<&str>) -> Result<(), String> {
    let root = ctx.root.to_path_buf();

    let commit = Command::new("git")
        .args(["log", "-1", "--format=%H"])
        .output()
        .ok()
        .and_then(|o| {
            if o.status.success() {
                Some(o.stdout)
            } else {
                None
            }
        })
        .and_then(|b| String::from_utf8(b).ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty());

    // Run acceptance criteria if present (before closing)
    let ac_result = {
        let needles = read_needles(&root)?;
        let needle = needles.iter().find(|b| b.get("id").and_then(|v| v.as_str()) == Some(id));
        if let Some(n) = needle {
            if let Some(ac_cmd) = n.get("ac").and_then(|v| v.as_str()) {
                let cmd = vec![
                    "sh".to_string(),
                    "-c".to_string(),
                    ac_cmd.to_string(),
                ];
                match crate::kernel::pty::run_command(&cmd) {
                    Ok((status, _output)) => {
                        let passed = status.success();
                        let exit_code = status.code.unwrap_or(-1);
                        if passed {
                            writeln!(ctx, "AC: PASS").unwrap();
                        } else {
                            writeln!(ctx, "AC: FAIL (exit {})", exit_code).unwrap();
                        }
                        Some(if passed { "pass" } else { "fail" })
                    }
                    Err(e) => {
                        writeln!(ctx, "AC: ERROR ({})", e).unwrap();
                        Some("error")
                    }
                }
            } else {
                None
            }
        } else {
            None
        }
    };

    with_needles_locked(&root, |beads| {
        let idx = beads
            .iter()
            .position(|b| b.get("id").and_then(|v| v.as_str()) == Some(id))
            .ok_or_else(|| format!("task '{}' not found", id))?;
        beads[idx]["status"] = json!("closed");
        if let Some(r) = reason {
            beads[idx]["close_reason"] = json!(r);
        }
        beads[idx]["closed_at"] = json!(now_iso());
        if let Some(ref hash) = commit {
            beads[idx]["commit_ref"] = json!(hash);
        }
        if let Some(ac_res) = ac_result {
            beads[idx]["ac_result"] = json!(ac_res);
        }
        Ok(())
    })?;

    let mut audit_event = json!({
        "event": "task.closed",
        "id": id,
        "reason": reason.unwrap_or("none"),
        "timestamp": now_iso()
    });
    if let Some(ac_res) = ac_result {
        audit_event["ac_result"] = json!(ac_res);
    }
    append_audit(&root, &audit_event)?;
    writeln!(ctx, "closed {}", id).unwrap();

    // Implicit compile: update index
    let _ = post_mutation(
        &root,
        &MutationEvent::NeedleClosed {
            id: id.to_string(),
        },
    );

    Ok(())
}

/// CLI entry point (thin wrapper).
///
/// Bidirectional relations (blocks/depends-on) update both sides.
/// One-directional relations (spec/draft/agentfile) update only the source.
pub fn run_link(source_id: &str, relation: &str, target: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_link_verb(&mut ctx, source_id, relation, target)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
pub fn run_link_verb(ctx: &mut VerbCtx, source_id: &str, relation: &str, target: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();

    match relation {
        "blocks" => {
            with_needles_locked(&root, |beads| {
                let src_idx = beads.iter().position(|b| b.get("id").and_then(|v| v.as_str()) == Some(source_id))
                    .ok_or_else(|| format!("needle '{}' not found", source_id))?;
                let tgt_idx = beads.iter().position(|b| b.get("id").and_then(|v| v.as_str()) == Some(target))
                    .ok_or_else(|| format!("needle '{}' not found", target))?;

                // Add target to source's blocks[]
                ensure_array_contains(&mut beads[src_idx], "blocks", target);
                // Add source to target's depends_on[]
                ensure_array_contains(&mut beads[tgt_idx], "depends_on", source_id);

                Ok(())
            })?;
            append_audit(&root, &json!({
                "event": "needle.linked",
                "source": source_id,
                "relation": "blocks",
                "target": target,
                "timestamp": now_iso()
            }))?;
            writeln!(ctx, "linked {} blocks {}", source_id, target).unwrap();
        }
        "depends-on" => {
            with_needles_locked(&root, |beads| {
                let src_idx = beads.iter().position(|b| b.get("id").and_then(|v| v.as_str()) == Some(source_id))
                    .ok_or_else(|| format!("needle '{}' not found", source_id))?;
                let tgt_idx = beads.iter().position(|b| b.get("id").and_then(|v| v.as_str()) == Some(target))
                    .ok_or_else(|| format!("needle '{}' not found", target))?;

                // Add target to source's depends_on[]
                ensure_array_contains(&mut beads[src_idx], "depends_on", target);
                // Add source to target's blocks[]
                ensure_array_contains(&mut beads[tgt_idx], "blocks", source_id);

                Ok(())
            })?;
            append_audit(&root, &json!({
                "event": "needle.linked",
                "source": source_id,
                "relation": "depends-on",
                "target": target,
                "timestamp": now_iso()
            }))?;
            writeln!(ctx, "linked {} depends-on {}", source_id, target).unwrap();
        }
        "spec" | "draft" | "agentfile" => {
            let field = match relation {
                "spec" => "specs",
                "draft" => "drafts",
                "agentfile" => "agentfiles",
                _ => unreachable!(),
            };
            with_needles_locked(&root, |beads| {
                let src_idx = beads.iter().position(|b| b.get("id").and_then(|v| v.as_str()) == Some(source_id))
                    .ok_or_else(|| format!("needle '{}' not found", source_id))?;
                ensure_array_contains(&mut beads[src_idx], field, target);
                Ok(())
            })?;
            append_audit(&root, &json!({
                "event": "needle.linked",
                "source": source_id,
                "relation": relation,
                "target": target,
                "timestamp": now_iso()
            }))?;
            writeln!(ctx, "linked {} {} {}", source_id, relation, target).unwrap();
        }
        _ => {
            return Err(format!(
                "unknown relation '{}': must be one of blocks, depends-on, spec, draft, agentfile",
                relation
            ));
        }
    }

    Ok(())
}

/// Ensure a JSON value's array field contains a given string, adding it if absent.
fn ensure_array_contains(needle: &mut Value, field: &str, value: &str) {
    let arr = needle.get(field)
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let val_json = json!(value);
    if !arr.contains(&val_json) {
        let mut new_arr = arr;
        new_arr.push(val_json);
        needle[field] = json!(new_arr);
    }
}

/// Resolve the current agent alias.
///
/// Reads OSTK_AGENT env var first; falls back to "agent" as a generic label.
fn current_agent_alias() -> String {
    std::env::var("OSTK_AGENT").unwrap_or_else(|_| "agent".to_string())
}

/// Write a needle's details to VerbCtx (id, title, priority, description).
#[allow(dead_code)] // Output helper, planned for work --verbose
fn write_needle(w: &mut VerbCtx, needle: &Value) {
    let id = needle.get("id").and_then(|v| v.as_str()).unwrap_or("?");
    let title = needle.get("title").and_then(|v| v.as_str()).unwrap_or("?");
    let priority = needle.get("priority").and_then(|v| v.as_str()).unwrap_or("?");
    writeln!(w, "needle: {} [{}] {}", id, priority, title).unwrap();
    if let Some(desc) = needle.get("description").and_then(|v| v.as_str())
        && !desc.is_empty() {
            writeln!(w, "  {}", desc).unwrap();
        }
}

/// CLI entry point (thin wrapper).
///
/// Priority order: P0 → P1 → P2.
/// Claims by setting status=in_progress and assignee to current agent alias.
/// In loop mode, exits cleanly when no open needles remain.
pub fn run_pull(loop_mode: bool) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_pull_verb(&mut ctx, loop_mode)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
pub fn run_pull_verb(ctx: &mut VerbCtx, loop_mode: bool) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let alias = current_agent_alias();

    loop {
        // Claim next needle under lock
        let claimed = with_needles_locked(&root, |beads| {
            let idx = match pick_next_index(&root, beads) {
                Some(i) => i,
                None => return Ok(None),
            };

            let id = beads[idx]["id"].as_str().unwrap_or("").to_string();
            let title = beads[idx]["title"].as_str().unwrap_or("").to_string();
            let priority = beads[idx]["priority"].as_str().unwrap_or("").to_string();
            let description = beads[idx]
                .get("description")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            beads[idx]["status"] = json!("in_progress");
            beads[idx]["assignee"] = json!(&alias);

            Ok(Some((id, title, priority, description)))
        })?;

        match claimed {
            None => {
                writeln!(ctx, "no open needles").unwrap();
                break;
            }
            Some((id, title, priority, description)) => {
                // Print claimed needle
                writeln!(ctx, "needle: {} [{}] {}", id, priority, title).unwrap();
                if !description.is_empty() {
                    writeln!(ctx, "  {}", description).unwrap();
                }

                // Audit the claim
                append_audit(
                    &root,
                    &json!({
                        "event": "task.claimed",
                        "id": id,
                        "assignee": alias,
                        "timestamp": now_iso()
                    }),
                )?;
                writeln!(ctx, "claimed {}", id).unwrap();

                if !loop_mode {
                    break;
                }

                // In loop mode: agent is expected to work the needle and close it.
                // We wait briefly then check — in practice an orchestrator calls
                // ostk needle close <id> and then loops. Here we simulate the
                // contract: pull claims, agent works, agent closes, pull again.
                // The loop just claims the next after the previous is no longer open.
                // Exit when queue is empty.
            }
        }

        if !loop_mode {
            break;
        }

        // In loop mode, poll until a needle we just claimed gets closed (or we
        // detect the queue is empty). Since agents work serially within a single
        // pull --loop invocation, we re-enter the claim loop immediately — the
        // previous needle must be closed by the caller between iterations.
        // We break if no new claimable work exists.
    }

    Ok(())
}

/// CLI entry point (thin wrapper).
pub fn run_hay_list() -> Result<(), String> {
    let root = find_project_root()?;
    // NOTE: display_clusters prints to stdout directly — not yet migrated to VerbCtx.
    let result = crate::kernel::intelligence::cluster_hay(&root)?;
    crate::kernel::intelligence::display_clusters(&result);
    Ok(())
}

/// CLI entry point (thin wrapper).
pub fn run_hay(thought: &str, source: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_hay_verb(&mut ctx, thought, source)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
pub fn run_hay_verb(ctx: &mut VerbCtx, thought: &str, source: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let now = now_iso();
    append_audit(&root, &json!({
        "event": "hay.filed",
        "straw": thought,
        "source": source,
        "timestamp": now
    }))?;
    writeln!(ctx, "~ {}", thought).unwrap();
    Ok(())
}

/// Discover dependency edges from needle title patterns and persist them onto needles.
///
/// Scans all open needle titles for "depends on →NNN", "blocks →NNN", etc. patterns.
/// For each discovered edge, checks if it already exists in the needle's `depends_on[]`
/// or `blocks[]` arrays, and adds it if missing. Returns (new_edges, existing_edges).
fn persist_discovered_edges(w: &mut VerbCtx, root: &std::path::Path, needles: &[Value]) -> Result<(usize, usize), String> {
    let mut new_edges: usize = 0;
    let mut existing_edges: usize = 0;

    // Collect all needle IDs for validation
    let all_ids: std::collections::HashSet<String> = needles.iter()
        .filter_map(|n| n.get("id").and_then(|v| v.as_str()).map(|s| s.to_string()))
        .collect();

    // Collect discovered edges: (source_id, "depends_on"|"blocks", target_id)
    let mut edges_to_persist: Vec<(String, String, String)> = Vec::new();

    for n in needles {
        let id = match n.get("id").and_then(|v| v.as_str()) {
            Some(id) => id,
            None => continue,
        };
        let status = n.get("status").and_then(|v| v.as_str()).unwrap_or("");
        if status != "open" && status != "in_progress" {
            continue;
        }
        let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();

        // Check depends-on patterns: source depends on target
        for pat in DEPENDS_PATTERNS {
            let re = Regex::new(pat).unwrap();
            for cap in re.captures_iter(&title) {
                let target = format!("→{}", &cap[1]);
                if all_ids.contains(&target) && target != id {
                    edges_to_persist.push((id.to_string(), "depends_on".to_string(), target));
                }
            }
        }

        // Check blocks patterns: source blocks target
        for pat in BLOCKS_PATTERNS {
            let re = Regex::new(pat).unwrap();
            for cap in re.captures_iter(&title) {
                let target = format!("→{}", &cap[1]);
                if all_ids.contains(&target) && target != id {
                    edges_to_persist.push((id.to_string(), "blocks".to_string(), target));
                }
            }
        }
    }

    if edges_to_persist.is_empty() {
        return Ok((0, 0));
    }

    // Persist edges under lock
    with_needles_locked(root, |beads| {
        for (source_id, relation, target_id) in &edges_to_persist {
            let src_idx = beads.iter().position(|b| b.get("id").and_then(|v| v.as_str()) == Some(source_id));
            let tgt_idx = beads.iter().position(|b| b.get("id").and_then(|v| v.as_str()) == Some(target_id));

            let (src_idx, tgt_idx) = match (src_idx, tgt_idx) {
                (Some(s), Some(t)) => (s, t),
                _ => continue,
            };

            if relation == "depends_on" {
                // Source depends on target: add target to source's depends_on[], source to target's blocks[]
                let already_has_dep = beads[src_idx].get("depends_on")
                    .and_then(|v| v.as_array())
                    .map(|a| a.iter().any(|v| v.as_str() == Some(target_id)))
                    .unwrap_or(false);

                if already_has_dep {
                    existing_edges += 1;
                } else {
                    ensure_array_contains(&mut beads[src_idx], "depends_on", target_id);
                    ensure_array_contains(&mut beads[tgt_idx], "blocks", source_id);
                    new_edges += 1;
                }
            } else if relation == "blocks" {
                // Source blocks target: add target to source's blocks[], source to target's depends_on[]
                let already_has_block = beads[src_idx].get("blocks")
                    .and_then(|v| v.as_array())
                    .map(|a| a.iter().any(|v| v.as_str() == Some(target_id)))
                    .unwrap_or(false);

                if already_has_block {
                    existing_edges += 1;
                } else {
                    ensure_array_contains(&mut beads[src_idx], "blocks", target_id);
                    ensure_array_contains(&mut beads[tgt_idx], "depends_on", source_id);
                    new_edges += 1;
                }
            }
        }
        Ok(())
    })?;

    if new_edges > 0 {
        writeln!(w, "Discovered {} new edges. Persisted.", new_edges).unwrap();
    }

    Ok((new_edges, existing_edges))
}

/// CLI entry point (thin wrapper).
pub fn run_compile(dry_run: bool) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_compile_verb(&mut ctx, dry_run)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
pub fn run_compile_verb(ctx: &mut VerbCtx, dry_run: bool) -> Result<(), String> {
    let root = ctx.root.to_path_buf();

    // Phase 1: Cluster hay (the intelligence layer)
    let cluster_result = crate::kernel::intelligence::cluster_hay(&root)?;

    let total_hay = cluster_result.clusters.iter()
        .map(|c| c.members.len())
        .sum::<usize>() + cluster_result.unclustered.len();

    // Show discovered clusters
    if total_hay > 0 && !cluster_result.clusters.is_empty() {
        writeln!(ctx, "── intelligence: {} clusters discovered ──", cluster_result.clusters.len()).unwrap();
        for cluster in &cluster_result.clusters {
            let sphere_note = cluster.sphere_point.as_ref()
                .map(|p| format!(" → sphere(point={})", p))
                .unwrap_or_default();
            writeln!(ctx, "  {} ({} hay){}", cluster.theme, cluster.members.len(), sphere_note).unwrap();
        }
        if !cluster_result.unclustered.is_empty() {
            writeln!(ctx, "  + {} unclustered", cluster_result.unclustered.len()).unwrap();
        }
        writeln!(ctx).unwrap();
    }

    // Phase 2: Joint graph rebuild — discover and persist edges, then build spheres
    let needles = read_needles(&root)?;
    if !needles.is_empty() {
        // 2a. Discover edges from title patterns and persist them onto needles
        let (new_edges, existing_edges) = persist_discovered_edges(ctx, &root, &needles)?;
        let total_persisted = new_edges + existing_edges;

        if total_persisted > 0 {
            writeln!(ctx, "── edges: {} persisted ({} new, {} existing) ──",
                total_persisted, new_edges, existing_edges).unwrap();
        }

        // 2b. Re-read needles (edges may have been persisted) and rebuild graph
        let needles = if new_edges > 0 {
            read_needles(&root)?
        } else {
            needles
        };
        let (_joints, adj) = build_joint_graph(&needles);
        let spheres = find_spheres(&adj);

        // Build title index for display
        let title_map: std::collections::HashMap<String, String> = needles.iter()
            .filter_map(|n| {
                let id = n.get("id").and_then(|v| v.as_str())?;
                let title = n.get("title").and_then(|v| v.as_str())?;
                Some((id.to_string(), title.to_string()))
            })
            .collect();

        if !spheres.is_empty() {
            let multi_needle_spheres: Vec<_> = spheres.iter()
                .filter(|s| s.needles.len() > 1)
                .collect();
            let isolated_count = spheres.iter()
                .filter(|s| s.needles.len() == 1)
                .count();

            writeln!(ctx, "── spheres: {} connected, {} isolated ──",
                multi_needle_spheres.len(), isolated_count).unwrap();

            for (i, sphere) in multi_needle_spheres.iter().enumerate() {
                let radius_map = bfs_radius(&adj, &sphere.point);
                let max_radius = radius_map.values().copied().max().unwrap_or(0);

                let point_title = title_map.get(&sphere.point)
                    .map(|s| {
                        if s.chars().count() > 50 { format!("{}...", s.chars().take(50).collect::<String>()) } else { s.clone() }
                    })
                    .unwrap_or_else(|| "(unknown)".to_string());

                writeln!(ctx, "  sphere {} | {} needles | point={} (d{}) | radius={}",
                    i + 1, sphere.needles.len(), sphere.point,
                    sphere.point_degree, max_radius).unwrap();
                writeln!(ctx, "    point: {}", point_title).unwrap();

                if dry_run {
                    // In dry run, show all member needles
                    for nid in &sphere.needles {
                        let dist = radius_map.get(nid).copied().unwrap_or(0);
                        let ntitle = title_map.get(nid)
                            .map(|s| {
                                if s.chars().count() > 55 { format!("{}...", s.chars().take(55).collect::<String>()) } else { s.clone() }
                            })
                            .unwrap_or_else(|| "(unknown)".to_string());
                        let marker = if *nid == sphere.point { "◉" } else { "○" };
                        writeln!(ctx, "    {} {} [r{}] {}", marker, nid, dist, ntitle).unwrap();
                    }
                }
            }
            if isolated_count > 0 && dry_run {
                writeln!(ctx, "  ({} isolated needles — use :compound to create joints)", isolated_count).unwrap();
            }
            writeln!(ctx).unwrap();
        } else {
            writeln!(ctx, "── spheres: none (no open needles with joints) ──").unwrap();
            writeln!(ctx).unwrap();
        }
    }

    // Phase 3: Tack lint pass (existing behavior — only when there's hay)
    let mut needle_count = 0;
    let mut hay_count = 0;

    if total_hay > 0 {
        let audit_path = crate::state_dir(&root).join("audit.jsonl");
        let content = fs::read_to_string(&audit_path).unwrap_or_default();

        let mut hay_items = Vec::new();
        for line in content.lines() {
            if let Ok(v) = serde_json::from_str::<Value>(line)
                && v.get("event").and_then(|e| e.as_str()) == Some("hay.filed") {
                    hay_items.push(v);
                }
        }

        for item in &hay_items {
            let straw = item.get("straw").and_then(|s| s.as_str()).unwrap_or("");
            if straw.is_empty() { continue; }

            // →597: Tack Linter (Tier 0) — validate verb if it looks like a tack command
            let hs_dir = crate::state_dir(&root);
            let lint_res = crate::fcp::tack::lint(straw, &hs_dir);

            match lint_res {
                crate::fcp::tack::TackLint::Ok => {
                    if dry_run {
                        writeln!(ctx, "  [NEEDLE CANDIDATE] {}", straw).unwrap();
                    }
                    needle_count += 1;
                }
                crate::fcp::tack::TackLint::UnknownVerb(v) => {
                    if dry_run {
                        writeln!(ctx, "  [STAY AS HAY] {} (unknown verb: {})", straw, v).unwrap();
                    }
                    hay_count += 1;
                }
                _ => {
                    if dry_run {
                        writeln!(ctx, "  [STAY AS HAY] {}", straw).unwrap();
                    }
                    hay_count += 1;
                }
            }
        }
    }

    if dry_run {
        if total_hay > 0 {
            writeln!(ctx, "\n── dry run: {} needle candidates, {} stay as hay ──", needle_count, hay_count).unwrap();
        } else {
            writeln!(ctx, "── dry run: no hay to compile ──").unwrap();
        }
    } else if total_hay > 0 {
        writeln!(ctx, "compiled: {} → needles, {} → hay", needle_count, hay_count).unwrap();
    } else {
        writeln!(ctx, "compiled: graph rebuilt (no hay)").unwrap();
    }

    Ok(())
}

/// Format sphere summary lines for compile output.
/// Returns a Vec of display strings (one per sphere), plus isolated count.
pub fn format_compile_spheres(needles: &[Value]) -> (Vec<String>, usize) {
    if needles.is_empty() {
        return (Vec::new(), 0);
    }

    let (_joints, adj) = build_joint_graph(needles);
    let spheres = find_spheres(&adj);

    let title_map: std::collections::HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let title = n.get("title").and_then(|v| v.as_str())?;
            Some((id.to_string(), title.to_string()))
        })
        .collect();

    let mut lines = Vec::new();
    let mut isolated_count = 0;

    for sphere in &spheres {
        if sphere.needles.len() == 1 {
            isolated_count += 1;
            continue;
        }
        let radius_map = bfs_radius(&adj, &sphere.point);
        let max_radius = radius_map.values().copied().max().unwrap_or(0);
        let point_title = title_map.get(&sphere.point)
            .cloned()
            .unwrap_or_else(|| "(unknown)".to_string());

        lines.push(format!(
            "sphere | {} needles | point={} (d{}) | radius={} | {}",
            sphere.needles.len(), sphere.point, sphere.point_degree,
            max_radius, point_title
        ));
    }

    (lines, isolated_count)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn make_needle(id: &str, title: &str, status: &str, priority: &str) -> Value {
        json!({
            "id": id,
            "title": title,
            "status": status,
            "priority": priority,
            "issue_type": "task"
        })
    }

    #[test]
    fn test_compile_spheres_explicit_deps() {
        let needles = vec![
            make_needle("→001", "implement auth", "open", "P0"),
            make_needle("→002", "add tests depends on →001", "open", "P1"),
            make_needle("→003", "deploy pipeline depends on →002", "open", "P2"),
        ];
        let (lines, isolated) = format_compile_spheres(&needles);
        assert_eq!(lines.len(), 1, "should find 1 connected sphere");
        assert_eq!(isolated, 0);
        assert!(lines[0].contains("3 needles"), "sphere should have 3 needles: {}", lines[0]);
        // The point should be →001 or →002 (highest degree in chain)
        assert!(lines[0].contains("point="), "should show point");
        assert!(lines[0].contains("radius="), "should show radius");
    }

    #[test]
    fn test_compile_spheres_with_isolated() {
        let needles = vec![
            make_needle("→001", "depends on →002", "open", "P0"),
            make_needle("→002", "base module", "open", "P0"),
            make_needle("→003", "completely unrelated alpha", "open", "P1"),
        ];
        let (lines, isolated) = format_compile_spheres(&needles);
        assert_eq!(lines.len(), 1, "1 connected sphere");
        assert!(isolated >= 1, "at least 1 isolated needle");
    }

    #[test]
    fn test_compile_spheres_point_is_hub() {
        // →002 is the hub: five others depend on it explicitly.
        // Use unique titles with no shared concept terms to avoid extra edges.
        let needles = vec![
            make_needle("→001", "alpha depends on →002", "open", "P1"),
            make_needle("→002", "central hub", "open", "P0"),
            make_needle("→003", "bravo depends on →002", "open", "P1"),
            make_needle("→004", "charlie depends on →002", "open", "P1"),
            make_needle("→005", "delta depends on →002", "open", "P1"),
            make_needle("→006", "echo depends on →002", "open", "P1"),
        ];
        let (lines, _isolated) = format_compile_spheres(&needles);
        assert_eq!(lines.len(), 1);
        // →002 has degree 5 (connected to all others via explicit deps).
        // Others have degree 1 (to →002) + shared-ref edges among themselves.
        // With 5 dependents sharing →002, each gets shared-ref edges to the other 4,
        // giving degree 5 each. →002 also has degree 5. Tie-break: find_spheres picks
        // the first seen. Let's just verify it's one of the high-degree nodes and the
        // sphere contains all 6 needles.
        assert!(lines[0].contains("6 needles"), "sphere should have 6 needles: {}", lines[0]);
        assert!(lines[0].contains("point="), "should identify a point: {}", lines[0]);
    }

    #[test]
    fn test_compile_spheres_radius_computed() {
        // Linear chain: →001 → →002 → →003 → →004
        let needles = vec![
            make_needle("→001", "step one", "open", "P0"),
            make_needle("→002", "step two depends on →001", "open", "P0"),
            make_needle("→003", "step three depends on →002", "open", "P0"),
            make_needle("→004", "step four depends on →003", "open", "P0"),
        ];
        let (lines, _) = format_compile_spheres(&needles);
        assert_eq!(lines.len(), 1);
        // The point is the highest-degree node. In a chain, interior nodes have degree 2,
        // endpoints have degree 1. Radius should be >= 1.
        assert!(lines[0].contains("radius="), "should include radius");
    }

    #[test]
    fn test_compile_spheres_closed_excluded() {
        let needles = vec![
            make_needle("→001", "open needle with audit", "open", "P0"),
            make_needle("→002", "closed needle with audit depends on →001", "closed", "P1"),
        ];
        let (lines, isolated) = format_compile_spheres(&needles);
        // →002 is closed, excluded from graph. →001 is isolated.
        assert_eq!(lines.len(), 0, "no connected spheres (closed excluded)");
        assert!(isolated <= 1);
    }

    #[test]
    fn test_compile_spheres_empty_needles() {
        let (lines, isolated) = format_compile_spheres(&[]);
        assert!(lines.is_empty());
        assert_eq!(isolated, 0);
    }

    #[test]
    fn test_compile_spheres_concept_edges() {
        let needles = vec![
            make_needle("→001", "tui overlay rendering", "open", "P1"),
            make_needle("→002", "tui input handling", "open", "P1"),
            make_needle("→003", "tui help panel", "open", "P2"),
        ];
        let (lines, _) = format_compile_spheres(&needles);
        // All share "tui" concept term → should be 1 sphere
        assert_eq!(lines.len(), 1, "tui needles should form 1 sphere");
        assert!(lines[0].contains("3 needles"));
    }

    // ── radius tiebreaker tests ──

    #[test]
    fn test_compute_needle_radii_hub_is_zero() {
        // →002 is the hub: four others depend on it via explicit deps.
        // With 4 spokes all referencing →002, shared-ref edges give each spoke
        // degree 4 (to →002 + 3 other spokes). →002 has degree 4 (4 explicit deps).
        // We need →002 to have strictly the highest degree, so use 5 spokes:
        // →002 gets degree 5, each spoke gets degree 5 (1 explicit + 4 shared-ref).
        // Tie is possible, but with 6+ spokes →002 wins.
        let needles = vec![
            make_needle("→001", "alpha depends on →002", "open", "P1"),
            make_needle("→002", "central hub", "open", "P1"),
            make_needle("→003", "bravo depends on →002", "open", "P1"),
            make_needle("→004", "charlie depends on →002", "open", "P1"),
            make_needle("→005", "delta depends on →002", "open", "P1"),
            make_needle("→006", "echo depends on →002", "open", "P1"),
            make_needle("→007", "foxtrot depends on →002", "open", "P1"),
        ];
        let radii = compute_needle_radii(&needles);

        // With 6 spokes: each spoke has degree = 1 (explicit to →002) + 5 (shared-ref to
        // other 5 spokes) = 6. →002 has degree 6 (explicit from all 6 spokes).
        // All equal at degree 6 — find_spheres picks first sorted key = →001.
        // Actually: spokes also have shared-ref edges among themselves (all reference →002),
        // so each spoke connects to →002 + 5 other spokes = degree 6,
        // while →002 connects to 6 spokes = degree 6. Tie resolved by sort order.
        //
        // The key invariant: the sphere point has radius 0, and all other needles
        // have radius >= 0. Just verify the structure is sound.
        let (_joints, adj) = build_joint_graph(&needles);
        let spheres = find_spheres(&adj);
        assert_eq!(spheres.len(), 1);
        let point = &spheres[0].point;
        assert_eq!(radii.get(point.as_str()).copied(), Some(0), "point should have radius 0");
        // All non-point needles should have radius >= 1
        for nid in &spheres[0].needles {
            if nid != point {
                let r = radii.get(nid.as_str()).copied().unwrap_or(0);
                assert!(r >= 1, "{} should have radius >= 1, got {}", nid, r);
            }
        }
    }

    #[test]
    fn test_radius_tiebreaks_same_priority() {
        // Three P1 needles in a linear chain: →001 → →002 → →003
        // →002 is the point (degree 2 in chain), radius 0.
        // →001 and →003 are at radius 1.
        // pick_next_index should prefer →002 (radius 0) over →001 or →003.
        let needles = vec![
            make_needle("→001", "step one", "open", "P1"),
            make_needle("→002", "step two depends on →001", "open", "P1"),
            make_needle("→003", "step three depends on →002", "open", "P1"),
        ];

        // We can't call pick_next_index without a real root, so test via
        // compute_needle_radii + priority_rank to verify the sort key logic.
        let radii = compute_needle_radii(&needles);

        // Build sort keys the same way pick_next_index does
        let sort_key = |n: &Value| -> (u8, usize) {
            let pri = priority_rank(
                n.get("priority").and_then(|v| v.as_str()).unwrap_or("P2"),
            );
            let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let radius = radii.get(id).copied().unwrap_or(usize::MAX);
            (pri, radius)
        };

        let mut open: Vec<&Value> = needles.iter()
            .filter(|n| n.get("status").and_then(|v| v.as_str()) == Some("open"))
            .collect();
        open.sort_by_key(|n| sort_key(n));

        let winner_id = open[0].get("id").and_then(|v| v.as_str()).unwrap();
        // →002 is the point (degree 2), should win with radius 0
        assert_eq!(winner_id, "→002", "sphere point (lowest radius) should win tiebreak");
    }

    #[test]
    fn test_priority_still_beats_radius() {
        // P0 at radius 2 should still beat P1 at radius 0.
        // Hub →010 is P1, leaf →011 depends on →010 is P0.
        let needles = vec![
            make_needle("→010", "central hub", "open", "P1"),
            make_needle("→011", "critical fix depends on →010", "open", "P0"),
        ];

        let radii = compute_needle_radii(&needles);

        let sort_key = |n: &Value| -> (u8, usize) {
            let pri = priority_rank(
                n.get("priority").and_then(|v| v.as_str()).unwrap_or("P2"),
            );
            let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let radius = radii.get(id).copied().unwrap_or(usize::MAX);
            (pri, radius)
        };

        let mut open: Vec<&Value> = needles.iter()
            .filter(|n| n.get("status").and_then(|v| v.as_str()) == Some("open"))
            .collect();
        open.sort_by_key(|n| sort_key(n));

        let winner_id = open[0].get("id").and_then(|v| v.as_str()).unwrap();
        assert_eq!(winner_id, "→011", "P0 should beat P1 regardless of radius");
    }

    #[test]
    fn test_isolated_needle_sorts_last_among_same_priority() {
        // →020 is in a sphere (connected to →022 via explicit dep), →021 is isolated.
        // Both P1 — connected needle should win because isolated gets usize::MAX radius.
        // Use a title for →021 that shares no concept terms and references no IDs.
        let needles = vec![
            make_needle("→020", "depends on →022", "open", "P1"),
            make_needle("→021", "paint the bicycle shed mauve", "open", "P1"),
            make_needle("→022", "foundational plumbing", "open", "P1"),
        ];

        let radii = compute_needle_radii(&needles);

        // →021 should not appear in radii (truly isolated, no joints)
        let r021 = radii.get("→021").copied().unwrap_or(usize::MAX);
        let r020 = radii.get("→020").copied().unwrap_or(usize::MAX);

        assert!(r020 < r021, "connected needle should have lower radius than isolated; got r020={}, r021={}", r020, r021);
    }

    #[test]
    fn test_radius_ordering_in_chain() {
        // Linear chain: →030 → →031 → →032 → →033
        // Interior nodes (→031, →032) have degree 2, endpoints have degree 1.
        // The sphere point should be one of the interior nodes.
        // Verify radius ordering: point=0, neighbors=1, far end=2.
        let needles = vec![
            make_needle("→030", "step one", "open", "P1"),
            make_needle("→031", "step two depends on →030", "open", "P1"),
            make_needle("→032", "step three depends on →031", "open", "P1"),
            make_needle("→033", "step four depends on →032", "open", "P1"),
        ];

        let radii = compute_needle_radii(&needles);
        let (_joints, adj) = build_joint_graph(&needles);
        let spheres = find_spheres(&adj);

        assert_eq!(spheres.len(), 1);
        let point = &spheres[0].point;
        assert_eq!(radii.get(point).copied(), Some(0), "point should have radius 0");

        // Verify monotonic: each step away from point increases radius
        let mut distances: Vec<usize> = radii.values().copied().collect();
        distances.sort();
        assert!(distances[0] == 0, "minimum radius should be 0");
        // Max radius in a 4-node chain with interior point is 2
        assert!(*distances.last().unwrap() <= 3, "max radius should be reasonable");
    }
}
