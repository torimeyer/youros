use crate::{find_project_root, read_needles};
use regex::Regex;
use serde_json::Value;
use std::collections::{HashMap, HashSet, VecDeque};

// ---------------------------------------------------------------------------
// Dependency extraction
// ---------------------------------------------------------------------------

/// Signals that declare a dependency edge from *this* needle to *target*.
/// "this needle depends on target"
static DEPENDS_ON_PATTERNS: &[&str] = &[
    r"depends on (→\d+)",
    r"depends on →(\d+)",
    r"requires (→\d+)",
    r"requires →(\d+)",
    r"blocked by (→\d+)",
    r"blocked by →(\d+)",
    r"after (→\d+)",
    r"after →(\d+)",
];

/// Signals that declare this needle blocks a target.
/// "this needle blocks target"
static BLOCKS_PATTERNS: &[&str] = &[
    r"blocks (→\d+)",
    r"blocks →(\d+)",
    r"unblocks (→\d+)",
    r"unblocks →(\d+)",
    r"enables (→\d+)",
    r"enables →(\d+)",
];

/// Normalize a needle reference to canonical form "→NNN".
fn normalize_id(raw: &str) -> String {
    // Already canonical
    if raw.starts_with('→') {
        return raw.to_string();
    }
    // Strip leading zeros when re-formatting
    if let Some(digits) = raw.strip_prefix("nd-").or_else(|| raw.strip_prefix("bd-"))
        && let Ok(n) = digits.parse::<u64>() {
            return format!("→{n:03}");
        }
    raw.to_string()
}

/// Extract all needle IDs that appear as `→NNN` in text.
#[allow(dead_code)] // needle ID extraction helper, planned for depends --deep
fn extract_all_ids(text: &str) -> Vec<String> {
    let re = Regex::new(r"→(\d+)").unwrap();
    re.captures_iter(text)
        .map(|c| format!("→{}", &c[1]))
        .collect()
}

/// Parse dependency signals from a needle's title (and optionally body fields).
/// Returns (depends_on_ids, blocks_ids).
fn parse_deps(title: &str) -> (Vec<String>, Vec<String>) {
    let text = title.to_lowercase();
    let mut depends_on = Vec::new();
    let mut blocks = Vec::new();

    for pat in DEPENDS_ON_PATTERNS {
        // Match the pattern case-insensitively; extract the →NNN group
        let re = Regex::new(pat).unwrap();
        for cap in re.captures_iter(&text) {
            let raw = &cap[1];
            let id = if raw.starts_with('→') {
                raw.to_string()
            } else {
                format!("→{raw}")
            };
            depends_on.push(id);
        }
    }
    for pat in BLOCKS_PATTERNS {
        let re = Regex::new(pat).unwrap();
        for cap in re.captures_iter(&text) {
            let raw = &cap[1];
            let id = if raw.starts_with('→') {
                raw.to_string()
            } else {
                format!("→{raw}")
            };
            blocks.push(id);
        }
    }

    (depends_on, blocks)
}

// ---------------------------------------------------------------------------
// Dependency graph
// ---------------------------------------------------------------------------

struct DepGraph {
    /// Map from needle ID → needle title
    titles: HashMap<String, String>,
    /// "this depends on these" — forward edges
    depends_on: HashMap<String, Vec<String>>,
    /// "this is depended on by these" (inverse of depends_on)
    required_by: HashMap<String, Vec<String>>,
}

impl DepGraph {
    fn build(needles: &[Value]) -> Self {
        let mut titles: HashMap<String, String> = HashMap::new();
        let mut depends_on: HashMap<String, Vec<String>> = HashMap::new();
        let mut required_by: HashMap<String, Vec<String>> = HashMap::new();
        let mut explicit_blocks: HashMap<String, Vec<String>> = HashMap::new();

        // Collect all titles first
        for n in needles {
            let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
            if !id.is_empty() {
                titles.insert(id.clone(), title);
            }
        }

        // Parse dependency signals from titles
        for n in needles {
            let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("");
            if id.is_empty() {
                continue;
            }

            let (deps, blks) = parse_deps(title);

            for dep in deps {
                let dep = normalize_id(&dep);
                depends_on.entry(id.clone()).or_default().push(dep.clone());
                required_by.entry(dep).or_default().push(id.clone());
            }

            for blk in blks {
                let blk = normalize_id(&blk);
                explicit_blocks.entry(id.clone()).or_default().push(blk.clone());
            }
        }

        // Explicit "blocks →NNN" means: target depends on this needle.
        // So add reverse edges: target.depends_on += [this], this.required_by += [target]
        for (blocker, targets) in &explicit_blocks {
            for target in targets {
                depends_on.entry(target.clone()).or_default().push(blocker.clone());
                required_by.entry(blocker.clone()).or_default().push(target.clone());
            }
        }

        // Deduplicate
        for v in depends_on.values_mut() {
            v.sort();
            v.dedup();
        }
        for v in required_by.values_mut() {
            v.sort();
            v.dedup();
        }

        DepGraph { titles, depends_on, required_by }
    }

    fn title(&self, id: &str) -> &str {
        self.titles.get(id).map(|s| s.as_str()).unwrap_or("(unknown)")
    }

    fn deps_of(&self, id: &str) -> &[String] {
        self.depends_on.get(id).map(|v| v.as_slice()).unwrap_or(&[])
    }

    fn dependents_of(&self, id: &str) -> &[String] {
        self.required_by.get(id).map(|v| v.as_slice()).unwrap_or(&[])
    }

    /// Compute the longest chain *downstream* from `id` (fan-out depth).
    #[allow(dead_code)] // graph traversal for compounds/depends feature
    fn longest_downstream_chain(&self, id: &str) -> usize {
        let mut visited = HashSet::new();
        self.dfs_depth(id, &mut visited, true)
    }

    #[allow(dead_code)] // graph traversal for compounds/depends feature
    fn dfs_depth(&self, id: &str, visited: &mut HashSet<String>, downstream: bool) -> usize {
        if !visited.insert(id.to_string()) {
            return 0; // cycle guard
        }
        let children: &[String] = if downstream {
            self.dependents_of(id)
        } else {
            self.deps_of(id)
        };
        let max_depth = children
            .iter()
            .map(|c| self.dfs_depth(c, visited, downstream))
            .max()
            .unwrap_or(0);
        visited.remove(id);
        1 + max_depth
    }

    /// Critical path: longest chain across all needles (downstream direction).
    /// Returns the chain as a Vec of IDs from root to leaf.
    fn critical_path(&self) -> Vec<String> {
        let all_ids: Vec<String> = self.titles.keys().cloned().collect();
        let mut best: Vec<String> = Vec::new();

        for start in &all_ids {
            let chain = self.longest_chain_from(start);
            if chain.len() > best.len() {
                best = chain;
            }
        }
        best
    }

    fn longest_chain_from(&self, start: &str) -> Vec<String> {
        // BFS/DFS to find longest downstream path
        let mut best: Vec<String> = Vec::new();
        let mut stack: Vec<(String, Vec<String>)> = vec![(start.to_string(), vec![start.to_string()])];
        let mut seen_cycles: HashSet<(String, String)> = HashSet::new();

        while let Some((current, path)) = stack.pop() {
            let deps = self.dependents_of(&current);
            if deps.is_empty() {
                if path.len() > best.len() {
                    best = path;
                }
                continue;
            }
            for dep in deps {
                let edge = (current.clone(), dep.clone());
                if seen_cycles.contains(&edge) {
                    continue;
                }
                seen_cycles.insert(edge);
                if !path.contains(dep) {
                    let mut new_path = path.clone();
                    new_path.push(dep.clone());
                    stack.push((dep.clone(), new_path));
                }
            }
        }
        best
    }
}

// ---------------------------------------------------------------------------
// Public commands
// ---------------------------------------------------------------------------

/// `ostk needle depends →NNN`
pub fn run(id: Option<&str>, critical_path: bool) -> Result<(), String> {
    if critical_path {
        run_critical_path()
    } else if let Some(needle_id) = id {
        run_depends(needle_id)
    } else {
        Err("usage: ostk work depends →NNN [--critical-path]".to_string())
    }
}

pub fn run_depends(id: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let needles = read_needles(&root)?;
    let graph = DepGraph::build(&needles);

    // Normalize the target ID
    let target = if id.starts_with('→') {
        id.to_string()
    } else if id.starts_with("nd-") || id.starts_with("bd-") {
        normalize_id(id)
    } else {
        // Allow bare numbers: "576" → "→576"
        if id.chars().all(|c| c.is_ascii_digit()) {
            format!("→{}", id.parse::<u64>().unwrap_or(0))
        } else {
            id.to_string()
        }
    };

    if !graph.titles.contains_key(&target) {
        return Err(format!("needle '{}' not found", target));
    }

    let title = graph.title(&target);
    println!("{} {}", target, title);

    // fan-out (what this needle blocks / enables downstream)
    let dependents = graph.dependents_of(&target);
    println!();
    println!("blocks (fan-out):");
    if dependents.is_empty() {
        println!("  (none)");
    } else {
        for dep in dependents {
            println!("  {} {}", dep, graph.title(dep));
        }
    }

    // depends on (what this needle needs)
    let deps = graph.deps_of(&target);
    println!();
    println!("depends on:");
    if deps.is_empty() {
        println!("  (none — Tier 1 kernel)");
    } else {
        for dep in deps {
            println!("  {} {}", dep, graph.title(dep));
        }
    }

    // compounding score = total transitive fan-out count
    let score = count_transitive_dependents(&graph, &target);
    println!();
    println!("compounding score: {} (fan-out count)", score);

    Ok(())
}

/// Count all unique transitive dependents (fan-out) of a needle.
fn count_transitive_dependents(graph: &DepGraph, id: &str) -> usize {
    let mut visited: HashSet<String> = HashSet::new();
    let mut queue: VecDeque<String> = VecDeque::new();
    queue.push_back(id.to_string());

    while let Some(current) = queue.pop_front() {
        for dep in graph.dependents_of(&current) {
            if visited.insert(dep.clone()) {
                queue.push_back(dep.clone());
            }
        }
    }
    visited.len()
}

/// `ostk needle depends --critical-path`
pub fn run_critical_path() -> Result<(), String> {
    let root = find_project_root()?;
    let needles = read_needles(&root)?;
    let graph = DepGraph::build(&needles);

    let path = graph.critical_path();
    if path.is_empty() {
        println!("no dependency edges found — add 'depends on →NNN' to needle titles");
        return Ok(());
    }

    println!("critical path ({} steps):", path.len());
    println!();
    for (i, id) in path.iter().enumerate() {
        let connector = if i == 0 {
            "  "
        } else {
            "  → "
        };
        println!("{}{} {}", connector, id, graph.title(id));
    }

    Ok(())
}
