//! `:compounds` — "What, if completed, unblocks the most other work?"
//!
//! Pure graph traversal over the dependency graph stored on needles.
//! Builds adjacency from `blocks[]` edges and title-parsed dependency signals,
//! computes transitive fan-out (BFS downstream count), weights by priority,
//! and prints the top 10 highest-compounding needles.

use crate::{find_project_root, read_needles};
use regex::Regex;
use serde_json::Value;
use std::collections::{HashMap, HashSet, VecDeque};

/// Dependency signals parsed from needle titles (mirrors depends.rs).
static BLOCKS_TITLE_PATTERNS: &[&str] = &[
    r"blocks →(\d+)",
    r"unblocks →(\d+)",
    r"enables →(\d+)",
];

/// "depends on" signals — the reverse: if A depends on B, then B blocks A.
static DEPENDS_ON_TITLE_PATTERNS: &[&str] = &[
    r"depends on →(\d+)",
    r"requires →(\d+)",
    r"blocked by →(\d+)",
    r"after →(\d+)",
];

/// A needle entry with the fields we care about for compounding analysis.
#[derive(Debug, Clone)]
pub struct CompoundNeedle {
    pub id: String,
    pub title: String,
    pub priority: String,
    pub status: String,
}

/// Result for a single needle's compounding score.
#[derive(Debug, Clone)]
pub struct CompoundResult {
    pub id: String,
    pub title: String,
    pub priority: String,
    pub direct_fan_out: usize,
    pub transitive_fan_out: usize,
    pub weighted_score: usize,
}

/// Build the blocks graph from raw JSON needle values.
///
/// This is the full implementation that reads both structured `blocks[]` fields
/// and title-parsed dependency signals.
pub fn build_blocks_graph_from_values(needles: &[Value]) -> (Vec<CompoundNeedle>, HashMap<String, HashSet<String>>) {
    let mut compound_needles: Vec<CompoundNeedle> = Vec::new();
    let mut graph: HashMap<String, HashSet<String>> = HashMap::new();

    // First pass: collect all open needles
    let mut open_ids: HashSet<String> = HashSet::new();
    for n in needles {
        let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("");
        let status = n.get("status").and_then(|v| v.as_str()).unwrap_or("");
        let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("");
        let priority = n.get("priority").and_then(|v| v.as_str()).unwrap_or("P1");

        if id.is_empty() {
            continue;
        }

        if status == "open" || status == "in_progress" {
            open_ids.insert(id.to_string());
            compound_needles.push(CompoundNeedle {
                id: id.to_string(),
                title: title.to_string(),
                priority: priority.to_string(),
                status: status.to_string(),
            });
            graph.entry(id.to_string()).or_default();
        }
    }

    // Second pass: build edges
    for n in needles {
        let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("");
        let status = n.get("status").and_then(|v| v.as_str()).unwrap_or("");
        let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("");

        if id.is_empty() || (status != "open" && status != "in_progress") {
            continue;
        }

        // 1. Structured `blocks[]` field
        if let Some(blocks_arr) = n.get("blocks").and_then(|v| v.as_array()) {
            for target in blocks_arr {
                if let Some(target_id) = target.as_str() {
                    let normalized = normalize_id(target_id);
                    if open_ids.contains(&normalized) && normalized != id {
                        graph.entry(id.to_string()).or_default().insert(normalized);
                    }
                }
            }
        }

        // 2. Title patterns: "blocks →NNN"
        let title_lower = title.to_lowercase();
        for pat in BLOCKS_TITLE_PATTERNS {
            let re = Regex::new(pat).unwrap();
            for cap in re.captures_iter(&title_lower) {
                let target = format!("→{}", &cap[1]);
                if open_ids.contains(&target) && target != id {
                    graph.entry(id.to_string()).or_default().insert(target);
                }
            }
        }

        // 3. Reverse of "depends on": if this needle depends on B, then B blocks this
        for pat in DEPENDS_ON_TITLE_PATTERNS {
            let re = Regex::new(pat).unwrap();
            for cap in re.captures_iter(&title_lower) {
                let blocker = format!("→{}", &cap[1]);
                if open_ids.contains(&blocker) && blocker != id {
                    graph.entry(blocker.clone()).or_default().insert(id.to_string());
                }
            }
        }

        // 4. Structured `depends_on[]` field (reverse edge)
        if let Some(deps_arr) = n.get("depends_on").and_then(|v| v.as_array()) {
            for dep in deps_arr {
                if let Some(dep_id) = dep.as_str() {
                    let normalized = normalize_id(dep_id);
                    if open_ids.contains(&normalized) && normalized != id {
                        graph.entry(normalized).or_default().insert(id.to_string());
                    }
                }
            }
        }
    }

    (compound_needles, graph)
}

/// Normalize a needle ID to canonical "→NNN" form.
fn normalize_id(raw: &str) -> String {
    if raw.starts_with('→') {
        return raw.to_string();
    }
    if let Some(digits) = raw.strip_prefix("nd-").or_else(|| raw.strip_prefix("bd-"))
        && let Ok(n) = digits.parse::<u64>()
    {
        return format!("→{n:03}");
    }
    // Bare number
    if raw.chars().all(|c| c.is_ascii_digit())
        && let Ok(n) = raw.parse::<u64>()
    {
        return format!("→{n:03}");
    }
    raw.to_string()
}

/// BFS from `start` through the blocks graph, counting all transitively blocked needles.
pub fn transitive_fan_out(graph: &HashMap<String, HashSet<String>>, start: &str) -> usize {
    let mut visited: HashSet<String> = HashSet::new();
    let mut queue: VecDeque<String> = VecDeque::new();
    queue.push_back(start.to_string());

    while let Some(current) = queue.pop_front() {
        if let Some(blocked) = graph.get(&current) {
            for target in blocked {
                if visited.insert(target.clone()) {
                    queue.push_back(target.clone());
                }
            }
        }
    }

    // Don't count the start node itself
    visited.remove(start);
    visited.len()
}

/// Priority weight multiplier: P0 = 3x, P1 = 2x, P2 = 1x.
pub fn priority_weight(priority: &str) -> usize {
    match priority {
        "P0" => 3,
        "P1" => 2,
        "P2" => 1,
        _ => 1,
    }
}

/// Compute compound results for all open needles.
pub fn compute_compounds(needles: &[Value]) -> Vec<CompoundResult> {
    let (compound_needles, graph) = build_blocks_graph_from_values(needles);

    let mut results: Vec<CompoundResult> = Vec::new();

    for cn in &compound_needles {
        let direct = graph.get(&cn.id).map_or(0, |s| s.len());
        let transitive = transitive_fan_out(&graph, &cn.id);
        let weight = priority_weight(&cn.priority);
        let weighted_score = transitive * weight;

        results.push(CompoundResult {
            id: cn.id.clone(),
            title: cn.title.clone(),
            priority: cn.priority.clone(),
            direct_fan_out: direct,
            transitive_fan_out: transitive,
            weighted_score,
        });
    }

    // Sort by weighted score descending, then by transitive fan-out, then by ID for stability
    results.sort_by(|a, b| {
        b.weighted_score.cmp(&a.weighted_score)
            .then_with(|| b.transitive_fan_out.cmp(&a.transitive_fan_out))
            .then_with(|| a.id.cmp(&b.id))
    });

    results
}

/// Format and print the compounds report.
pub fn print_compounds(results: &[CompoundResult]) {
    // Filter to only needles that actually block something
    let blocking: Vec<&CompoundResult> = results.iter()
        .filter(|r| r.transitive_fan_out > 0)
        .take(10)
        .collect();

    println!(":compounds \u{2014} highest compounding work");
    println!();

    if blocking.is_empty() {
        println!("  (no blocking dependencies found)");
        return;
    }

    for r in &blocking {
        let short_title = crate::util::safe_truncate(&r.title, 40);
        println!(
            "  {:<6} {:<43} [{}]  blocks {} (transitive: {})",
            r.id, short_title, r.priority, r.direct_fan_out, r.transitive_fan_out
        );
    }
}

/// CLI entry point: `ostk compounds` or `:compounds` tack verb.
pub fn run() -> Result<(), String> {
    let root = find_project_root()?;
    let needles = read_needles(&root)?;
    let results = compute_compounds(&needles);
    print_compounds(&results);
    Ok(())
}
