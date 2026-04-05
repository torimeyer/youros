use std::fmt::Write;

use crate::kernel::verb_ctx::VerbCtx;
use crate::{find_project_root, read_needles};
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::time::{SystemTime, UNIX_EPOCH};

/// CLI entry point (thin wrapper).
pub fn run(target: &str, json_output: bool) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({ "target": target, "json": json_output });
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx, target, json_output)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for :show (→1157).
pub fn run_verb(ctx: &mut VerbCtx, target: &str, json_output: bool) -> Result<(), String> {
    // Route: needle ID (→NNN, bd-NNN, nd-NNN)
    if target.starts_with('→')
        || target.starts_with("bd-")
        || target.starts_with("nd-")
    {
        return show_needle(ctx, target, json_output);
    }

    // Route: :verb introspection (e.g. `ostk show :compile`)
    if target.starts_with(':') {
        return show_verb(ctx, target, json_output);
    }

    // Route: keywords
    match target {
        "needles" => show_needles(ctx, json_output),
        "hay" => show_hay(ctx, json_output),
        "threads" => show_threads(ctx, json_output),
        "status" => show_status(ctx, json_output),
        "clock" => show_clock(ctx, json_output),
        "verbs" => show_verbs(ctx, json_output),
        "agents" => show_agents(ctx, json_output),
        _ => Err(format!(
            "unknown target '{}'. Try: :verb, verbs, agents, →NNN, needles, hay, threads, status, clock",
            target
        )),
    }
}

/// Show a single needle by ID.
fn show_needle(w: &mut VerbCtx, id: &str, json_output: bool) -> Result<(), String> {
    let root = w.root.to_path_buf();
    let needles = read_needles(&root)?;

    // Normalize: accept bd-NNN and nd-NNN as aliases for →NNN
    let normalized = normalize_needle_id(id);

    let needle = needles
        .iter()
        .find(|n| {
            n.get("id")
                .and_then(|v| v.as_str())
                .map(|nid| nid == normalized || nid == id)
                .unwrap_or(false)
        })
        .ok_or_else(|| format!("needle '{}' not found", id))?;

    if json_output {
        let pretty = serde_json::to_string_pretty(needle).map_err(|e| e.to_string())?;
        writeln!(w, "{}", pretty).unwrap();
    } else {
        let nid = needle.get("id").and_then(|v| v.as_str()).unwrap_or("?");
        let title = needle.get("title").and_then(|v| v.as_str()).unwrap_or("?");
        let status = needle.get("status").and_then(|v| v.as_str()).unwrap_or("?");
        let priority = needle
            .get("priority")
            .and_then(|v| v.as_str())
            .unwrap_or("?");
        let created = needle
            .get("created_at")
            .and_then(|v| v.as_str())
            .unwrap_or("?");

        writeln!(w, "{} {}", nid, title).unwrap();
        writeln!(w, "  status:   {}", status).unwrap();
        writeln!(w, "  priority: {}", priority).unwrap();
        writeln!(w, "  created:  {}", created).unwrap();

        if let Some(assignee) = needle.get("assignee").and_then(|v| v.as_str()) {
            writeln!(w, "  assignee: {}", assignee).unwrap();
        }
        if let Some(reason) = needle.get("close_reason").and_then(|v| v.as_str()) {
            writeln!(w, "  reason:   {}", reason).unwrap();
        }
        if let Some(closed) = needle.get("closed_at").and_then(|v| v.as_str()) {
            writeln!(w, "  closed:   {}", closed).unwrap();
        }
        if let Some(commit) = needle.get("commit_ref").and_then(|v| v.as_str()) {
            writeln!(w, "  commit:   {}", commit).unwrap();
        }
    }

    Ok(())
}

/// Normalize bd-NNN / nd-NNN to →NNN.
fn normalize_needle_id(id: &str) -> String {
    if let Some(rest) = id.strip_prefix("bd-")
        && let Ok(n) = rest.parse::<u64>() {
            return format!("→{n:03}");
        }
    if let Some(rest) = id.strip_prefix("nd-")
        && let Ok(n) = rest.parse::<u64>() {
            return format!("→{n:03}");
        }
    id.to_string()
}

/// Show active needles (open + in_progress).
/// Canonical definition: active needles = open + in_progress.
fn show_needles(w: &mut VerbCtx, json_output: bool) -> Result<(), String> {
    let root = w.root.to_path_buf();
    let needles = read_needles(&root)?;

    let open: Vec<&Value> = needles
        .iter()
        .filter(|n| {
            let s = n.get("status").and_then(|v| v.as_str()).unwrap_or("");
            s == "open" || s == "in_progress"
        })
        .collect();

    if json_output {
        let pretty = serde_json::to_string_pretty(&open).map_err(|e| e.to_string())?;
        writeln!(w, "{}", pretty).unwrap();
    } else {
        if open.is_empty() {
            writeln!(w, "no open needles").unwrap();
            return Ok(());
        }
        for n in &open {
            let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("?");
            let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("?");
            let p = n.get("priority").and_then(|v| v.as_str()).unwrap_or("?");
            writeln!(w, "{} [{}] {}", id, p, title).unwrap();
        }
        writeln!(w, "{} open", open.len()).unwrap();
    }

    Ok(())
}

/// Show hay.filed events from audit.jsonl.
fn show_hay(w: &mut VerbCtx, json_output: bool) -> Result<(), String> {
    let root = w.root.to_path_buf();
    let events = read_audit_events(&root)?;

    let hay: Vec<&Value> = events
        .iter()
        .filter(|v| v.get("event").and_then(|e| e.as_str()) == Some("hay.filed"))
        .collect();

    if json_output {
        let pretty = serde_json::to_string_pretty(&hay).map_err(|e| e.to_string())?;
        writeln!(w, "{}", pretty).unwrap();
    } else {
        if hay.is_empty() {
            writeln!(w, "no hay").unwrap();
            return Ok(());
        }
        for h in &hay {
            let straw = h.get("straw").and_then(|s| s.as_str()).unwrap_or("?");
            let source = h.get("source").and_then(|s| s.as_str()).unwrap_or("?");
            let ts = h.get("timestamp").and_then(|s| s.as_str()).unwrap_or("?");
            writeln!(w, "~ {} ({}, {})", straw, source, ts).unwrap();
        }
        writeln!(w, "{} hay", hay.len()).unwrap();
    }

    Ok(())
}

/// Show threads (alias for `thread list`).
fn show_threads(w: &mut VerbCtx, json_output: bool) -> Result<(), String> {
    let root = w.root.to_path_buf();
    let events = read_audit_events(&root)?;

    let threads: Vec<&Value> = events
        .iter()
        .filter(|v| v.get("event").and_then(|e| e.as_str()) == Some("thread.created"))
        .collect();

    if json_output {
        let pretty = serde_json::to_string_pretty(&threads).map_err(|e| e.to_string())?;
        writeln!(w, "{}", pretty).unwrap();
    } else {
        if threads.is_empty() {
            writeln!(w, "no threads").unwrap();
            return Ok(());
        }
        for t in &threads {
            let name = t.get("name").and_then(|n| n.as_str()).unwrap_or("?");
            let needle_count = t
                .get("needles")
                .and_then(|n| n.as_array())
                .map(|a| a.len())
                .unwrap_or(0);
            writeln!(w, "{}: {} needles", name, needle_count).unwrap();
        }
        writeln!(w, "{} threads", threads.len()).unwrap();
    }

    Ok(())
}

/// Show status: counts of open needles by priority, hay count, thread count.
/// Reads from .ostk/index.json when available (fast path), falls back to scanning.
fn show_status(w: &mut VerbCtx, json_output: bool) -> Result<(), String> {
    let root = w.root.to_path_buf();

    // Fast path: read from index.json if it exists
    let index_path = crate::state_dir(&root).join("index.json");
    if index_path.exists()
        && let Ok(content) = fs::read_to_string(&index_path)
            && let Ok(index) = serde_json::from_str::<Value>(&content) {
                return show_status_from_index(w, &root, &index, json_output);
            }

    // Slow path: scan issues.jsonl + audit.jsonl
    show_status_from_scan(w, &root, json_output)
}

/// Render status from a pre-built index.json (fast path).
fn show_status_from_index(
    w: &mut VerbCtx,
    root: &std::path::Path,
    index: &Value,
    json_output: bool,
) -> Result<(), String> {
    let needles_val = index
        .get("needles")
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    let total = needles_val
        .get("total")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let open = needles_val
        .get("open")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let closed = needles_val
        .get("closed")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);

    let by_priority_val = needles_val
        .get("by_priority")
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    let by_priority: HashMap<String, usize> = if let Some(obj) = by_priority_val.as_object() {
        obj.iter()
            .map(|(k, v)| (k.clone(), v.as_u64().unwrap_or(0) as usize))
            .collect()
    } else {
        HashMap::new()
    };

    let hay = index
        .get("hay")
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    let hay_pending = hay.get("pending").and_then(|v| v.as_u64()).unwrap_or(0);
    let hay_compiled = hay
        .get("compiled")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);

    let thread_count = index.get("threads").and_then(|v| v.as_u64()).unwrap_or(0);

    let last_mutation = index
        .get("last_mutation")
        .and_then(|v| v.as_str())
        .unwrap_or("?");
    let last_compile = index.get("last_compile").and_then(|v| v.as_str());

    // Compute unclassified count from by_priority "?" key
    let unclassified = by_priority.get("?").copied().unwrap_or(0) as u64;

    // Compute nudges
    let last_compile_epoch = last_compile.and_then(parse_iso_epoch);
    let nudge_input = NudgeInput {
        hay_pending,
        open_needles: open,
        unclassified_needles: unclassified,
        thread_count,
        last_committed_epoch: last_committed_epoch(root),
        last_compile_epoch,
        now_epoch: current_epoch(),
    };
    let nudges = compute_nudges(&nudge_input);

    if json_output {
        let status = serde_json::json!({
            "needles": {
                "total": total,
                "open": open,
                "closed": closed,
                "by_priority": by_priority,
            },
            "hay": {
                "pending": hay_pending,
                "compiled": hay_compiled,
            },
            "threads": thread_count,
            "last_mutation": last_mutation,
            "last_compile": last_compile,
            "nudges": nudges,
        });
        let pretty = serde_json::to_string_pretty(&status).map_err(|e| e.to_string())?;
        writeln!(w, "{}", pretty).unwrap();
    } else {
        writeln!(w,
            "needles: {} total, {} open, {} closed",
            total, open, closed
        ).unwrap();
        let mut priorities: Vec<_> = by_priority.iter().collect();
        priorities.sort_by_key(|(p, _)| (*p).clone());
        for (p, count) in &priorities {
            writeln!(w, "  {}: {}", p, count).unwrap();
        }
        writeln!(w, "hay: {} pending, {} compiled", hay_pending, hay_compiled).unwrap();
        writeln!(w, "threads: {}", thread_count).unwrap();
        writeln!(w, "last mutation: {}", last_mutation).unwrap();
        if let Some(lc) = last_compile {
            writeln!(w, "last compile:  {}", lc).unwrap();
        }
        print_nudges(w, &nudges);
    }

    Ok(())
}

/// Scan issues.jsonl + audit.jsonl to compute status (slow path / fallback).
fn show_status_from_scan(w: &mut VerbCtx, root: &std::path::Path, json_output: bool) -> Result<(), String> {
    let needles = read_needles(root)?;
    let events = read_audit_events(root)?;

    // Active needles by priority (open + in_progress = canonical definition)
    let open: Vec<&Value> = needles
        .iter()
        .filter(|n| {
            let s = n.get("status").and_then(|v| v.as_str()).unwrap_or("");
            s == "open" || s == "in_progress"
        })
        .collect();

    let mut by_priority: HashMap<String, usize> = HashMap::new();
    for n in &open {
        let p = n
            .get("priority")
            .and_then(|v| v.as_str())
            .unwrap_or("?")
            .to_string();
        *by_priority.entry(p).or_insert(0) += 1;
    }

    // Hay count (uncompiled — hay.filed minus hay.compiled)
    let hay_filed: usize = events
        .iter()
        .filter(|v| v.get("event").and_then(|e| e.as_str()) == Some("hay.filed"))
        .count();

    let hay_compiled: usize = events
        .iter()
        .filter(|v| v.get("event").and_then(|e| e.as_str()) == Some("hay.compiled"))
        .count();

    let hay_pending = hay_filed.saturating_sub(hay_compiled);

    // Thread count
    let thread_count: usize = events
        .iter()
        .filter(|v| v.get("event").and_then(|e| e.as_str()) == Some("thread.created"))
        .count();

    // Total needles / closed
    let total = needles.len();
    let closed: usize = needles
        .iter()
        .filter(|n| n.get("status").and_then(|v| v.as_str()) == Some("closed"))
        .count();

    // Compute nudges
    let unclassified = count_unclassified(&needles);
    let last_commit_epoch = events
        .iter()
        .rev()
        .filter(|v| {
            v.get("event")
                .and_then(|e| e.as_str())
                .map(|e| e.contains("committed"))
                .unwrap_or(false)
        })
        .find_map(|v| {
            v.get("timestamp")
                .and_then(|t| t.as_str())
                .and_then(parse_iso_epoch)
        });

    let nudge_input = NudgeInput {
        hay_pending: hay_pending as u64,
        open_needles: open.len() as u64,
        unclassified_needles: unclassified,
        thread_count: thread_count as u64,
        last_committed_epoch: last_commit_epoch,
        last_compile_epoch: None, // No index.json in scan path
        now_epoch: current_epoch(),
    };
    let nudges = compute_nudges(&nudge_input);

    if json_output {
        let status = serde_json::json!({
            "needles": {
                "total": total,
                "open": open.len(),
                "closed": closed,
                "by_priority": by_priority,
            },
            "hay": {
                "filed": hay_filed,
                "compiled": hay_compiled,
                "pending": hay_pending,
            },
            "threads": thread_count,
            "nudges": nudges,
        });
        let pretty = serde_json::to_string_pretty(&status).map_err(|e| e.to_string())?;
        writeln!(w, "{}", pretty).unwrap();
    } else {
        writeln!(w,
            "needles: {} total, {} open, {} closed",
            total,
            open.len(),
            closed
        ).unwrap();
        let mut priorities: Vec<_> = by_priority.iter().collect();
        priorities.sort_by_key(|(p, _)| (*p).clone());
        for (p, count) in &priorities {
            writeln!(w, "  {}: {}", p, count).unwrap();
        }
        writeln!(w,
            "hay: {} pending ({} filed, {} compiled)",
            hay_pending, hay_filed, hay_compiled
        ).unwrap();
        writeln!(w, "threads: {}", thread_count).unwrap();
        print_nudges(w, &nudges);
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Nudge heuristics — intelligent suggestions based on current state
// ---------------------------------------------------------------------------

/// Inputs for nudge computation, decoupled from data source.
struct NudgeInput {
    hay_pending: u64,
    open_needles: u64,
    unclassified_needles: u64, // priority "?" count among open
    thread_count: u64,
    last_committed_epoch: Option<u64>,
    last_compile_epoch: Option<u64>,
    now_epoch: u64,
}

/// Compute nudges from current state. Returns `.?`-prefixed suggestion strings.
fn compute_nudges(input: &NudgeInput) -> Vec<String> {
    let mut nudges = Vec::new();

    // 1. Hay pile growing: pending hay > 5
    if input.hay_pending > 5 {
        nudges.push(format!(
            ".? {} hay pending \u{2014} consider `ostk compile`",
            input.hay_pending
        ));
    }

    // 2. Unclassified needles: priority "?" > 10
    if input.unclassified_needles > 10 {
        nudges.push(format!(
            ".? {} needles have no priority \u{2014} consider triage",
            input.unclassified_needles
        ));
    }

    // 3. Stale work: no committed event in last 2 hours (7200 seconds)
    if let Some(last_commit_epoch) = input.last_committed_epoch {
        if input.now_epoch.saturating_sub(last_commit_epoch) > 7200 {
            nudges.push(".? no commits in 2h \u{2014} consider `ostk commit`".to_string());
        }
    } else {
        // No committed events at all — also suggest
        nudges.push(".? no commits in 2h \u{2014} consider `ostk commit`".to_string());
    }

    // 4. No threads: open needles > 20 but threads = 0
    if input.open_needles > 20 && input.thread_count == 0 {
        nudges.push(format!(
            ".? {} open needles but no threads \u{2014} consider threading",
            input.open_needles
        ));
    }

    // 5. Coverage gap: last_compile older than 1 hour (3600 seconds)
    if let Some(last_compile_epoch) = input.last_compile_epoch {
        if input.now_epoch.saturating_sub(last_compile_epoch) > 3600 {
            nudges.push(".? last compile over 1h ago \u{2014} consider `ostk compile`".to_string());
        }
    } else {
        // Never compiled — nudge if there are open needles
        if input.open_needles > 0 {
            nudges.push(".? never compiled \u{2014} consider `ostk compile`".to_string());
        }
    }

    nudges
}

/// Get current epoch seconds.
fn current_epoch() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

/// Find the most recent "committed" event timestamp from audit events.
fn last_committed_epoch(root: &std::path::Path) -> Option<u64> {
    let events = read_audit_events(root).ok()?;
    events
        .iter()
        .rev()
        .filter(|v| {
            v.get("event")
                .and_then(|e| e.as_str())
                .map(|e| e.contains("committed"))
                .unwrap_or(false)
        })
        .find_map(|v| {
            v.get("timestamp")
                .and_then(|t| t.as_str())
                .and_then(parse_iso_epoch)
        })
}

/// Count active needles with priority "?" from needle data.
/// Uses canonical definition: active = open + in_progress.
fn count_unclassified(needles: &[Value]) -> u64 {
    needles
        .iter()
        .filter(|n| {
            let s = n.get("status").and_then(|v| v.as_str()).unwrap_or("");
            (s == "open" || s == "in_progress")
                && n.get("priority").and_then(|v| v.as_str()) == Some("?")
        })
        .count() as u64
}

/// Print nudges section (human-readable).
fn print_nudges(w: &mut VerbCtx, nudges: &[String]) {
    if nudges.is_empty() {
        return;
    }
    writeln!(w).unwrap();
    writeln!(w, "Nudges:").unwrap();
    for nudge in nudges {
        writeln!(w, "  {}", nudge).unwrap();
    }
}

/// Show a single verb's details from .language.
fn show_verb(w: &mut VerbCtx, verb: &str, json_output: bool) -> Result<(), String> {
    let root = w.root.to_path_buf();
    let entries = language::parse_language_file(&root)?;

    // verb arg comes in with colon prefix (e.g. ":boot"), strip it to match LanguageEntry.verb
    let needle = verb.trim_start_matches(':');
    let entry = entries
        .iter()
        .find(|e| e.verb == needle)
        .ok_or_else(|| format!("verb '{}' not found in .language", verb))?;

    if json_output {
        let j = serde_json::json!({
            "verb": format!(":{}", entry.verb),
            "tier": entry.tier,
            "layer": entry.layer,
            "momentum": entry.momentum,
            "resolution": entry.resolution,
            "signature": entry.signature,
            "doc": entry.doc,
        });
        let pretty = serde_json::to_string_pretty(&j).map_err(|e| e.to_string())?;
        writeln!(w, "{}", pretty).unwrap();
    } else {
        writeln!(w, ":{:<13} {}", entry.verb, entry.doc).unwrap();
        writeln!(w, "  tier:       {}", entry.tier).unwrap();
        writeln!(w, "  layer:      {}", entry.layer).unwrap();
        writeln!(w, "  momentum:   {}", entry.momentum).unwrap();
        writeln!(w, "  resolution: {}", entry.resolution).unwrap();
        writeln!(w, "  signature:  {}", entry.signature).unwrap();
    }
    Ok(())
}

/// Show all verbs from .language.
fn show_verbs(w: &mut VerbCtx, json_output: bool) -> Result<(), String> {
    let root = w.root.to_path_buf();
    let entries = language::parse_language_file(&root)?;

    // Separate user-invocable verbs from infrastructure (tier 0)
    let user_entries: Vec<_> = entries.iter().filter(|e| !e.is_infrastructure()).collect();
    let infra_entries: Vec<_> = entries.iter().filter(|e| e.is_infrastructure()).collect();

    if json_output {
        let arr: Vec<serde_json::Value> = entries
            .iter()
            .map(|e| {
                serde_json::json!({
                    "verb": format!(":{}", e.verb),
                    "tier": e.tier,
                    "layer": e.layer,
                    "momentum": e.momentum,
                    "resolution": e.resolution,
                    "doc": e.doc,
                })
            })
            .collect();
        let pretty = serde_json::to_string_pretty(&arr).map_err(|e| e.to_string())?;
        writeln!(w, "{}", pretty).unwrap();
    } else {
        for entry in &user_entries {
            writeln!(w,
                ":{:<13} T{} {:>4.2}  {}",
                entry.verb, entry.tier, entry.momentum, entry.doc
            ).unwrap();
        }
        if !infra_entries.is_empty() {
            writeln!(w).unwrap();
            let devices: Vec<_> = infra_entries.iter().filter(|e| e.is_device()).collect();
            let services: Vec<_> = infra_entries.iter().filter(|e| e.is_service()).collect();
            if !devices.is_empty() {
                let alive = devices.iter().filter(|e| e.is_alive()).count();
                writeln!(w, "devices: {}/{} alive", alive, devices.len()).unwrap();
            }
            if !services.is_empty() {
                let alive = services.iter().filter(|e| e.is_alive()).count();
                writeln!(w, "services: {}/{} alive", alive, services.len()).unwrap();
            }
        }
        writeln!(w, "{} verbs", user_entries.len()).unwrap();
    }
    Ok(())
}

/// Show all Agentfiles with first-line comment.
fn show_agents(w: &mut VerbCtx, json_output: bool) -> Result<(), String> {
    let root = w.root.to_path_buf();
    let state_dir = std::env::var("OSTK_STATE_DIR").unwrap_or_else(|_| ".ostk".to_string());
    let agents_dir = root.join(&state_dir).join("agents");

    if !agents_dir.exists() {
        if json_output {
            writeln!(w, "[]").unwrap();
        } else {
            writeln!(w, "no agents directory").unwrap();
        }
        return Ok(());
    }

    let mut agent_files: Vec<_> = fs::read_dir(&agents_dir)
        .map_err(|e| format!("failed to read agents/: {e}"))?
        .filter_map(|e| e.ok())
        .filter(|e| {
            e.path()
                .extension()
                .map(|ext| ext == "Agentfile")
                .unwrap_or(false)
        })
        .collect();
    agent_files.sort_by_key(|e| e.file_name());

    if json_output {
        let arr: Vec<serde_json::Value> = agent_files
            .iter()
            .map(|e| {
                let name = e
                    .path()
                    .file_stem()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_default();
                let first_line = fs::read_to_string(e.path())
                    .ok()
                    .and_then(|c| c.lines().next().map(String::from))
                    .unwrap_or_default();
                serde_json::json!({ "name": name, "first_line": first_line })
            })
            .collect();
        let pretty = serde_json::to_string_pretty(&arr).map_err(|e| e.to_string())?;
        writeln!(w, "{}", pretty).unwrap();
    } else {
        if agent_files.is_empty() {
            writeln!(w, "no Agentfiles").unwrap();
            return Ok(());
        }
        for entry in &agent_files {
            let name = entry
                .path()
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();
            let first_line = fs::read_to_string(entry.path())
                .ok()
                .and_then(|c| c.lines().next().map(String::from))
                .unwrap_or_default();
            writeln!(w, "{:<30} {}", name, first_line).unwrap();
        }
        writeln!(w, "{} agents", agent_files.len()).unwrap();
    }
    Ok(())
}

use crate::language;

/// Show clock: first/last audit timestamps and elapsed.
fn show_clock(w: &mut VerbCtx, json_output: bool) -> Result<(), String> {
    let root = w.root.to_path_buf();
    let events = read_audit_events(&root)?;

    if events.is_empty() {
        if json_output {
            writeln!(w, "{{}}").unwrap();
        } else {
            writeln!(w, "no audit events").unwrap();
        }
        return Ok(());
    }

    let first_ts = events
        .first()
        .and_then(|v| v.get("timestamp").and_then(|t| t.as_str()))
        .unwrap_or("?");

    let last_ts = events
        .last()
        .and_then(|v| v.get("timestamp").and_then(|t| t.as_str()))
        .unwrap_or("?");

    let elapsed = compute_elapsed(first_ts, last_ts);

    if json_output {
        let clock = serde_json::json!({
            "first": first_ts,
            "last": last_ts,
            "elapsed": elapsed,
            "events": events.len(),
        });
        let pretty = serde_json::to_string_pretty(&clock).map_err(|e| e.to_string())?;
        writeln!(w, "{}", pretty).unwrap();
    } else {
        writeln!(w, "first: {}", first_ts).unwrap();
        writeln!(w, "last:  {}", last_ts).unwrap();
        writeln!(w, "elapsed: {}", elapsed).unwrap();
        writeln!(w, "events: {}", events.len()).unwrap();
    }

    Ok(())
}

/// Parse ISO 8601 timestamp to epoch seconds (basic parser, no TZ offset).
fn parse_iso_epoch(ts: &str) -> Option<u64> {
    // Expected: 2026-03-08T12:34:56Z
    let ts = ts.trim_end_matches('Z');
    let (date, time) = ts.split_once('T')?;
    let parts: Vec<&str> = date.split('-').collect();
    if parts.len() != 3 {
        return None;
    }
    let year: u64 = parts[0].parse().ok()?;
    let month: u64 = parts[1].parse().ok()?;
    let day: u64 = parts[2].parse().ok()?;

    let time_parts: Vec<&str> = time.split(':').collect();
    if time_parts.len() != 3 {
        return None;
    }
    let hours: u64 = time_parts[0].parse().ok()?;
    let minutes: u64 = time_parts[1].parse().ok()?;
    let seconds: u64 = time_parts[2].parse().ok()?;

    // Days from epoch using the same algorithm as lib.rs (inverse of days_to_ymd)
    let days = ymd_to_days(year, month, day)?;
    Some(days * 86400 + hours * 3600 + minutes * 60 + seconds)
}

/// Convert year/month/day to days since Unix epoch.
fn ymd_to_days(year: u64, month: u64, day: u64) -> Option<u64> {
    let y = if month <= 2 { year - 1 } else { year };
    let m = if month <= 2 { month + 9 } else { month - 3 };
    let era = y / 400;
    let yoe = y - era * 400;
    let doy = (153 * m + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe;
    // Subtract the Unix epoch offset (719468 days from year 0 to 1970-01-01)
    days.checked_sub(719468)
}

/// Compute human-readable elapsed time between two ISO timestamps.
fn compute_elapsed(first: &str, last: &str) -> String {
    let Some(start) = parse_iso_epoch(first) else {
        return "?".to_string();
    };
    let Some(end) = parse_iso_epoch(last) else {
        return "?".to_string();
    };
    if end < start {
        return "?".to_string();
    }
    let diff = end - start;
    let days = diff / 86400;
    let hours = (diff % 86400) / 3600;
    let minutes = (diff % 3600) / 60;

    if days > 0 {
        format!("{}d {}h {}m", days, hours, minutes)
    } else if hours > 0 {
        format!("{}h {}m", hours, minutes)
    } else {
        format!("{}m", minutes)
    }
}

/// Read all audit events from .ostk/audit.jsonl.
fn read_audit_events(root: &std::path::Path) -> Result<Vec<Value>, String> {
    let path = crate::state_dir(root).join("audit.jsonl");
    if !path.exists() {
        return Ok(vec![]);
    }
    let content =
        fs::read_to_string(&path).map_err(|e| format!("failed to read audit.jsonl: {e}"))?;
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::language::LanguageEntry;

    #[test]
    fn test_normalize_needle_id_arrow() {
        assert_eq!(normalize_needle_id("→406"), "→406");
    }

    #[test]
    fn test_normalize_needle_id_bd() {
        assert_eq!(normalize_needle_id("bd-406"), "→406");
        assert_eq!(normalize_needle_id("bd-1"), "→001");
    }

    #[test]
    fn test_normalize_needle_id_nd() {
        assert_eq!(normalize_needle_id("nd-42"), "→042");
    }

    #[test]
    fn test_parse_iso_epoch() {
        let ts = "2026-03-08T12:00:00Z";
        let epoch = parse_iso_epoch(ts).unwrap();
        // Just verify it's a reasonable value (March 2026)
        assert!(epoch > 1_770_000_000);
    }

    #[test]
    fn test_compute_elapsed_same() {
        let ts = "2026-03-08T12:00:00Z";
        assert_eq!(compute_elapsed(ts, ts), "0m");
    }

    #[test]
    fn test_compute_elapsed_hours() {
        let start = "2026-03-08T10:00:00Z";
        let end = "2026-03-08T13:30:00Z";
        assert_eq!(compute_elapsed(start, end), "3h 30m");
    }

    #[test]
    fn test_compute_elapsed_days() {
        let start = "2026-03-06T10:00:00Z";
        let end = "2026-03-08T13:30:00Z";
        assert_eq!(compute_elapsed(start, end), "2d 3h 30m");
    }

    #[test]
    fn test_compute_elapsed_invalid() {
        assert_eq!(compute_elapsed("garbage", "2026-03-08T12:00:00Z"), "?");
    }

    // ---------------------------------------------------------------
    // Nudge heuristics
    // ---------------------------------------------------------------

    /// Helper: base NudgeInput with no nudge triggers.
    fn quiet_input() -> NudgeInput {
        NudgeInput {
            hay_pending: 0,
            open_needles: 5,
            unclassified_needles: 0,
            thread_count: 1,
            last_committed_epoch: Some(current_epoch()), // just committed
            last_compile_epoch: Some(current_epoch()),   // just compiled
            now_epoch: current_epoch(),
        }
    }

    #[test]
    fn nudge_no_suggestions_when_everything_healthy() {
        let nudges = compute_nudges(&quiet_input());
        assert!(nudges.is_empty(), "expected no nudges, got: {:?}", nudges);
    }

    #[test]
    fn nudge_hay_pile_growing() {
        let mut input = quiet_input();
        input.hay_pending = 6; // threshold is > 5
        let nudges = compute_nudges(&input);
        assert!(nudges.iter().any(|n| n.contains("hay pending")));
        assert!(nudges.iter().any(|n| n.contains("ostk compile")));

        // At threshold (5) should NOT trigger
        input.hay_pending = 5;
        let nudges = compute_nudges(&input);
        assert!(!nudges.iter().any(|n| n.contains("hay pending")));
    }

    #[test]
    fn nudge_unclassified_needles() {
        let mut input = quiet_input();
        input.unclassified_needles = 11; // threshold is > 10
        let nudges = compute_nudges(&input);
        assert!(nudges.iter().any(|n| n.contains("no priority")));
        assert!(nudges.iter().any(|n| n.contains("triage")));

        // At threshold (10) should NOT trigger
        input.unclassified_needles = 10;
        let nudges = compute_nudges(&input);
        assert!(!nudges.iter().any(|n| n.contains("no priority")));
    }

    #[test]
    fn nudge_stale_work_no_commits() {
        let mut input = quiet_input();
        input.last_committed_epoch = None; // no commits ever
        let nudges = compute_nudges(&input);
        assert!(nudges.iter().any(|n| n.contains("no commits in 2h")));
    }

    #[test]
    fn nudge_stale_work_old_commit() {
        let mut input = quiet_input();
        let now = current_epoch();
        input.now_epoch = now;
        input.last_committed_epoch = Some(now - 7201); // > 2 hours ago
        let nudges = compute_nudges(&input);
        assert!(nudges.iter().any(|n| n.contains("no commits in 2h")));
    }

    #[test]
    fn nudge_stale_work_recent_commit() {
        let mut input = quiet_input();
        let now = current_epoch();
        input.now_epoch = now;
        input.last_committed_epoch = Some(now - 3600); // 1 hour ago, within threshold
        let nudges = compute_nudges(&input);
        assert!(!nudges.iter().any(|n| n.contains("no commits in 2h")));
    }

    #[test]
    fn nudge_no_threads_with_many_open() {
        let mut input = quiet_input();
        input.open_needles = 21; // threshold is > 20
        input.thread_count = 0;
        let nudges = compute_nudges(&input);
        assert!(nudges.iter().any(|n| n.contains("no threads")));
        assert!(nudges.iter().any(|n| n.contains("threading")));

        // At threshold (20) should NOT trigger
        input.open_needles = 20;
        let nudges = compute_nudges(&input);
        assert!(!nudges.iter().any(|n| n.contains("no threads")));

        // With threads > 0, should NOT trigger even with many open
        input.open_needles = 100;
        input.thread_count = 1;
        let nudges = compute_nudges(&input);
        assert!(!nudges.iter().any(|n| n.contains("no threads")));
    }

    #[test]
    fn nudge_coverage_gap_stale_compile() {
        let mut input = quiet_input();
        let now = current_epoch();
        input.now_epoch = now;
        input.last_compile_epoch = Some(now - 3601); // > 1 hour ago
        let nudges = compute_nudges(&input);
        assert!(nudges.iter().any(|n| n.contains("last compile over 1h")));
    }

    #[test]
    fn nudge_coverage_gap_never_compiled() {
        let mut input = quiet_input();
        input.last_compile_epoch = None;
        input.open_needles = 5; // has open needles
        let nudges = compute_nudges(&input);
        assert!(nudges.iter().any(|n| n.contains("never compiled")));
    }

    #[test]
    fn nudge_coverage_gap_never_compiled_no_needles() {
        let mut input = quiet_input();
        input.last_compile_epoch = None;
        input.open_needles = 0; // no open needles — no point suggesting compile
        let nudges = compute_nudges(&input);
        assert!(!nudges.iter().any(|n| n.contains("never compiled")));
    }

    #[test]
    fn nudge_coverage_gap_recent_compile() {
        let mut input = quiet_input();
        let now = current_epoch();
        input.now_epoch = now;
        input.last_compile_epoch = Some(now - 1800); // 30 min ago, within threshold
        let nudges = compute_nudges(&input);
        assert!(!nudges.iter().any(|n| n.contains("compile over 1h")));
    }

    #[test]
    fn nudge_all_prefix_with_dot_question() {
        let mut input = quiet_input();
        input.hay_pending = 10;
        input.unclassified_needles = 20;
        input.last_committed_epoch = None;
        input.open_needles = 30;
        input.thread_count = 0;
        input.last_compile_epoch = None;
        let nudges = compute_nudges(&input);
        assert!(!nudges.is_empty());
        for nudge in &nudges {
            assert!(
                nudge.starts_with(".?"),
                "nudge should start with .? prefix: {}",
                nudge
            );
        }
    }

    // ---------------------------------------------------------------
    // .language / verb introspection
    // ---------------------------------------------------------------

    #[test]
    fn test_parse_language_entries_format() {
        // Verify LanguageEntry struct via the shared parser
        let entry = LanguageEntry {
            verb: "compile".to_string(),
            tier: 1,
            layer: "user".to_string(),
            last_gen: 0,
            half_life: 500,
            momentum: 0.75,
            resolution: "ostk compile".to_string(),
            signature: "(hay) \u{2192} (needles)".to_string(),
            doc: "triage thinking into work".to_string(),
            spec: String::new(),
        };
        assert_eq!(entry.verb, "compile");
        assert_eq!(entry.resolution, "ostk compile");
        assert_eq!(entry.doc, "triage thinking into work");
    }

    #[test]
    fn test_show_verb_introspection() {
        // Integration test: parse the real .language file if available
        let root = crate::find_project_root();
        if root.is_err() {
            return; // skip if not in a project
        }
        let root = root.unwrap();
        let entries = language::parse_language_file(&root);
        if entries.is_err() {
            return; // skip if .language doesn't exist
        }
        let entries = entries.unwrap();
        // boot should always be present (verb stored without colon prefix)
        let boot = entries.iter().find(|e| e.verb == "boot");
        assert!(boot.is_some(), "boot should exist in .language");
        let boot = boot.unwrap();
        // →1157: boot is now internalized — resolution is "internal"
        assert!(
            boot.resolution == "internal" || boot.resolution == "ostk boot",
            "boot resolution should be 'internal' or 'ostk boot', got '{}'",
            boot.resolution
        );
    }

    #[test]
    fn nudge_count_unclassified_helper() {
        let needles = vec![
            serde_json::json!({"status": "open", "priority": "?"}),
            serde_json::json!({"status": "open", "priority": "P1"}),
            serde_json::json!({"status": "open", "priority": "?"}),
            serde_json::json!({"status": "closed", "priority": "?"}), // closed — not counted
        ];
        assert_eq!(count_unclassified(&needles), 2);
    }
}
