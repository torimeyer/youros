use std::fmt::Write;

use crate::kernel::verb_ctx::VerbCtx;
use crate::{append_audit, find_project_root, now_iso, read_needles, with_needles_locked};
use regex::Regex;
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};

/// Concept terms used to discover implicit joints between needles.
/// Each term, when shared between two needles' titles, creates a potential connection.
/// Also used by the hay intelligence layer for concept-term clustering.
pub static CONCEPT_TERMS: &[&str] = &[
    // Core work
    "compile", "needle", "hay", "thread", "fleet",
    // Graph
    "sphere", "joint", "link", "activate", "radiate", "refine", "compound",
    // Infrastructure
    "kernel", "audit", "boot", "intelligence", "dispatch", "secret",
    "compress", "squash", "interstitial", "resource", "attribution",
    // UI/UX
    "tui", "console", "overlay", "autocomplete", "input", "help",
    // Execution
    "serve", "mcp", "shim", "agent", "spawn", "reap", "socket",
    "pty", "vte", "install",
    // Governance
    "governance", "approval", "policy", "humanfile", "tori",
    // Bench
    "bench", "test", "docker", "score", "model", "arm",
    "prompt", "cost", "token", "container", "build",
    // External
    "web", "discord", "deploy", "nudge",
    // Intelligence
    "embed", "cluster",
];

/// Dependency signal patterns (mirrors depends.rs).
pub static DEPENDS_PATTERNS: &[&str] = &[
    r"depends on →(\d+)",
    r"requires →(\d+)",
    r"blocked by →(\d+)",
    r"after →(\d+)",
];
pub static BLOCKS_PATTERNS: &[&str] = &[
    r"blocks →(\d+)",
    r"unblocks →(\d+)",
    r"enables →(\d+)",
];

/// A joint between two needles — an explicit or implicit connection.
#[derive(Clone, Debug)]
pub struct Joint {
    pub source: String,
    pub target: String,
    pub joint_type: JointType,
}

#[derive(Clone, Debug)]
pub enum JointType {
    /// Explicit "depends on →NNN" or "blocks →NNN" in title
    Explicit,
    /// Both needles mention the same concept term
    Concept(String),
    /// Both needles reference the same needle ID in their titles
    SharedRef(String),
}

/// A sphere: a connected component of needles linked by joints.
pub struct Sphere {
    pub needles: Vec<String>,
    pub point: String,
    pub point_degree: usize,
    pub joints: Vec<Joint>,
}

/// Build the full joint graph from all needles. Returns (joints, adjacency map).
pub fn build_joint_graph(needles: &[Value]) -> (Vec<Joint>, HashMap<String, HashSet<String>>) {
    let mut joints: Vec<Joint> = Vec::new();
    let mut adj: HashMap<String, HashSet<String>> = HashMap::new();

    let id_re = Regex::new(r"(→\d+|bd-\d+|nd-\d+)").unwrap();

    // Index: needle_id -> title (lowercase)
    let mut titles: HashMap<String, String> = HashMap::new();
    let open_ids: HashSet<String> = needles.iter()
        .filter_map(|n| {
            let status = n.get("status").and_then(|v| v.as_str()).unwrap_or("");
            if status == "open" || status == "in_progress" {
                n.get("id").and_then(|v| v.as_str()).map(|s| s.to_string())
            } else {
                None
            }
        })
        .collect();

    for n in needles {
        let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("");
        let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("");
        if !id.is_empty() && open_ids.contains(id) {
            titles.insert(id.to_string(), title.to_lowercase());
            adj.entry(id.to_string()).or_default();
        }
    }

    let ids: Vec<String> = titles.keys().cloned().collect();

    // 1a. Explicit dependency edges from title patterns
    for id in &ids {
        let title = &titles[id];
        for pat in DEPENDS_PATTERNS {
            let re = Regex::new(pat).unwrap();
            for cap in re.captures_iter(title) {
                let target = format!("→{}", &cap[1]);
                if open_ids.contains(&target) && target != *id {
                    joints.push(Joint { source: id.clone(), target: target.clone(), joint_type: JointType::Explicit });
                    adj.entry(id.clone()).or_default().insert(target.clone());
                    adj.entry(target).or_default().insert(id.clone());
                }
            }
        }
        for pat in BLOCKS_PATTERNS {
            let re = Regex::new(pat).unwrap();
            for cap in re.captures_iter(title) {
                let target = format!("→{}", &cap[1]);
                if open_ids.contains(&target) && target != *id {
                    joints.push(Joint { source: id.clone(), target: target.clone(), joint_type: JointType::Explicit });
                    adj.entry(id.clone()).or_default().insert(target.clone());
                    adj.entry(target).or_default().insert(id.clone());
                }
            }
        }
    }

    // 1b. Persisted dependency edges from depends_on[] and blocks[] fields
    for n in needles {
        let id = match n.get("id").and_then(|v| v.as_str()) {
            Some(id) if open_ids.contains(id) => id.to_string(),
            _ => continue,
        };

        if let Some(deps) = n.get("depends_on").and_then(|v| v.as_array()) {
            for dep in deps {
                if let Some(target) = dep.as_str() {
                    let target = target.to_string();
                    if open_ids.contains(&target) && target != id {
                        // Only add if not already connected (avoid duplicating title-discovered edges)
                        if !adj.get(&id).is_some_and(|s| s.contains(&target)) {
                            joints.push(Joint { source: id.clone(), target: target.clone(), joint_type: JointType::Explicit });
                            adj.entry(id.clone()).or_default().insert(target.clone());
                            adj.entry(target).or_default().insert(id.clone());
                        }
                    }
                }
            }
        }

        if let Some(blocks) = n.get("blocks").and_then(|v| v.as_array()) {
            for blk in blocks {
                if let Some(target) = blk.as_str() {
                    let target = target.to_string();
                    if open_ids.contains(&target) && target != id
                        && !adj.get(&id).is_some_and(|s| s.contains(&target))
                    {
                        joints.push(Joint { source: id.clone(), target: target.clone(), joint_type: JointType::Explicit });
                        adj.entry(id.clone()).or_default().insert(target.clone());
                        adj.entry(target).or_default().insert(id.clone());
                    }
                }
            }
        }
    }

    // 2. Shared reference edges: if two needles both mention →NNN
    let mut ref_map: HashMap<String, Vec<String>> = HashMap::new();
    for id in &ids {
        let title = &titles[id];
        for cap in id_re.captures_iter(title) {
            let mentioned = cap[1].to_string();
            if mentioned != *id {
                ref_map.entry(mentioned).or_default().push(id.clone());
            }
        }
    }
    for (ref_id, mentioners) in &ref_map {
        if mentioners.len() >= 2 && mentioners.len() <= 30 {
            for i in 0..mentioners.len() {
                for j in (i + 1)..mentioners.len() {
                    let a = &mentioners[i];
                    let b = &mentioners[j];
                    joints.push(Joint {
                        source: a.clone(),
                        target: b.clone(),
                        joint_type: JointType::SharedRef(ref_id.clone()),
                    });
                    adj.entry(a.clone()).or_default().insert(b.clone());
                    adj.entry(b.clone()).or_default().insert(a.clone());
                }
            }
        }
    }

    // 3. Concept edges: shared terms (only for small clusters to avoid noise)
    let mut concept_map: HashMap<String, Vec<String>> = HashMap::new();
    for id in &ids {
        let title = &titles[id];
        for term in CONCEPT_TERMS {
            if title.contains(term) {
                concept_map.entry(term.to_string()).or_default().push(id.clone());
            }
        }
    }
    for (term, members) in &concept_map {
        if members.len() >= 2 && members.len() <= 15 {
            for i in 0..members.len() {
                for j in (i + 1)..members.len() {
                    let a = &members[i];
                    let b = &members[j];
                    // Only add if not already connected (avoid duplicating explicit edges)
                    if !adj.get(a).is_some_and(|s| s.contains(b)) {
                        joints.push(Joint {
                            source: a.clone(),
                            target: b.clone(),
                            joint_type: JointType::Concept(term.clone()),
                        });
                        adj.entry(a.clone()).or_default().insert(b.clone());
                        adj.entry(b.clone()).or_default().insert(a.clone());
                    }
                }
            }
        }
    }

    (joints, adj)
}

/// Find connected components (spheres) from adjacency map.
pub fn find_spheres(adj: &HashMap<String, HashSet<String>>) -> Vec<Sphere> {
    let mut visited: HashSet<String> = HashSet::new();
    let mut spheres: Vec<Sphere> = Vec::new();

    let mut all_ids: Vec<&String> = adj.keys().collect();
    all_ids.sort();

    for start in all_ids {
        if visited.contains(start) {
            continue;
        }
        let mut component: Vec<String> = Vec::new();
        let mut stack = vec![start.clone()];
        while let Some(node) = stack.pop() {
            if !visited.insert(node.clone()) {
                continue;
            }
            component.push(node.clone());
            if let Some(neighbors) = adj.get(&node) {
                for n in neighbors {
                    if !visited.contains(n) {
                        stack.push(n.clone());
                    }
                }
            }
        }

        // Find point (highest degree, ties broken by smallest id for determinism)
        let mut point = component[0].clone();
        let mut point_degree = 0;
        for nid in &component {
            let deg = adj.get(nid).map_or(0, |s| s.len());
            if deg > point_degree || (deg == point_degree && *nid < point) {
                point_degree = deg;
                point = nid.clone();
            }
        }

        component.sort();
        spheres.push(Sphere {
            needles: component,
            point,
            point_degree,
            joints: Vec::new(), // filled on demand
        });
    }

    spheres.sort_by(|a, b| b.needles.len().cmp(&a.needles.len()));
    spheres
}

/// Compute BFS radius from a point to all reachable needles.
pub fn bfs_radius(adj: &HashMap<String, HashSet<String>>, start: &str) -> HashMap<String, usize> {
    let mut distances: HashMap<String, usize> = HashMap::new();
    let mut queue = std::collections::VecDeque::new();
    distances.insert(start.to_string(), 0);
    queue.push_back(start.to_string());

    while let Some(current) = queue.pop_front() {
        let dist = distances[&current];
        if let Some(neighbors) = adj.get(&current) {
            for n in neighbors {
                if !distances.contains_key(n) {
                    distances.insert(n.clone(), dist + 1);
                    queue.push_back(n.clone());
                }
            }
        }
    }
    distances
}

// ---------------------------------------------------------------------------
// :refine — validate, link, assess needles
// ---------------------------------------------------------------------------

/// CLI entry point (thin wrapper).
pub fn run_refine(ids: &[String]) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_refine_verb(&mut ctx, ids)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
/// Passes over specified needles: checks validity, discovers joints,
/// reports sphere membership and radius from point.
pub fn run_refine_verb(ctx: &mut VerbCtx, ids: &[String]) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let needles = read_needles(&root)?;

    if ids.is_empty() {
        return Err("usage: ostk work refine →NNN [→NNN ...]".to_string());
    }

    // Normalize input IDs
    let target_ids: Vec<String> = ids.iter().map(|id| normalize_needle_id(id)).collect();

    // Build title index
    let title_map: HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let title = n.get("title").and_then(|v| v.as_str())?;
            Some((id.to_string(), title.to_string()))
        })
        .collect();

    let status_map: HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let status = n.get("status").and_then(|v| v.as_str())?;
            Some((id.to_string(), status.to_string()))
        })
        .collect();

    let priority_map: HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let priority = n.get("priority").and_then(|v| v.as_str())?;
            Some((id.to_string(), priority.to_string()))
        })
        .collect();

    // Validate targets exist
    for tid in &target_ids {
        if !title_map.contains_key(tid) {
            return Err(format!("needle '{}' not found", tid));
        }
    }

    // Build joint graph
    let (_joints, adj) = build_joint_graph(&needles);
    let spheres = find_spheres(&adj);

    // Map needle -> sphere index
    let mut needle_sphere: HashMap<String, usize> = HashMap::new();
    for (i, sphere) in spheres.iter().enumerate() {
        for nid in &sphere.needles {
            needle_sphere.insert(nid.clone(), i);
        }
    }

    writeln!(ctx, "── refine {} needle(s) ──", target_ids.len()).unwrap();
    writeln!(ctx).unwrap();

    for tid in &target_ids {
        let title = title_map.get(tid).map(|s| s.as_str()).unwrap_or("(unknown)");
        let status = status_map.get(tid).map(|s| s.as_str()).unwrap_or("?");
        let priority = priority_map.get(tid).map(|s| s.as_str()).unwrap_or("?");
        let degree = adj.get(tid).map_or(0, |s| s.len());

        writeln!(ctx, "{} [{}|{}] {}", tid, priority, status, title).unwrap();

        // Sphere membership
        if let Some(&si) = needle_sphere.get(tid) {
            let sphere = &spheres[si];
            let radius_map = bfs_radius(&adj, &sphere.point);
            let radius = radius_map.get(tid).copied().unwrap_or(0);
            writeln!(ctx, "  sphere: {} ({} needles, point={})", si + 1, sphere.needles.len(), sphere.point).unwrap();
            writeln!(ctx, "  radius from point: {}", radius).unwrap();
            writeln!(ctx, "  degree: {} joints", degree).unwrap();

            // Show direct connections
            if let Some(neighbors) = adj.get(tid) {
                let mut sorted_neighbors: Vec<&String> = neighbors.iter().collect();
                sorted_neighbors.sort();
                if !sorted_neighbors.is_empty() {
                    writeln!(ctx, "  joints:").unwrap();
                    for n in sorted_neighbors.iter().take(10) {
                        let ntitle = title_map.get(*n).map(|s| s.as_str()).unwrap_or("(unknown)");
                        let short = crate::util::safe_truncate(ntitle, 60);
                        writeln!(ctx, "    ↔ {} {}", n, short).unwrap();
                    }
                    if sorted_neighbors.len() > 10 {
                        writeln!(ctx, "    ... +{} more", sorted_neighbors.len() - 10).unwrap();
                    }
                }
            }
        } else {
            writeln!(ctx, "  sphere: ISOLATED (no joints)").unwrap();
            writeln!(ctx, "  degree: 0").unwrap();

            // Suggest potential connections based on title concepts
            let title_lower = title.to_lowercase();
            let mut suggestions: Vec<(String, String)> = Vec::new();
            for term in CONCEPT_TERMS {
                if title_lower.contains(term) {
                    // Find other needles with this term
                    for (other_id, other_title) in &title_map {
                        if other_id != tid
                            && other_title.to_lowercase().contains(term)
                            && status_map.get(other_id).map(|s| s.as_str()) == Some("open")
                        {
                            suggestions.push((other_id.clone(), term.to_string()));
                        }
                    }
                }
            }
            suggestions.sort();
            suggestions.dedup_by(|a, b| a.0 == b.0);
            if !suggestions.is_empty() {
                writeln!(ctx, "  potential joints (via :compound):").unwrap();
                for (sid, term) in suggestions.iter().take(5) {
                    let stitle = title_map.get(sid).map(|s| s.as_str()).unwrap_or("?");
                    let short = crate::util::safe_truncate(stitle, 50);
                    writeln!(ctx, "    ? {} [{}] {}", sid, term, short).unwrap();
                }
            }
        }

        // Validity check
        if status == "closed" {
            writeln!(ctx, "  ⚠ CLOSED — consider removing from active work").unwrap();
        }
        if priority == "?" {
            writeln!(ctx, "  ⚠ UNPRIORITIZED — needs P0/P1/P2 assignment").unwrap();
        }

        writeln!(ctx).unwrap();
    }

    // Audit the refine event
    append_audit(&root, &json!({
        "event": "needle.refined",
        "needles": target_ids,
        "timestamp": now_iso()
    }))?;

    Ok(())
}

// ---------------------------------------------------------------------------
// :compound — add intent / connection to a needle
// ---------------------------------------------------------------------------

/// CLI entry point (thin wrapper).
pub fn run_compound(id: &str, intent: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_compound_verb(&mut ctx, id, intent)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
/// Adds intent to a needle — a new connection, requirement, or context.
/// This makes the needle denser and potentially links it into a sphere.
pub fn run_compound_verb(ctx: &mut VerbCtx, id: &str, intent: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let needle_id = normalize_needle_id(id);

    // Verify needle exists
    let needles = read_needles(&root)?;
    let existing = needles.iter().find(|n| {
        n.get("id").and_then(|v| v.as_str()) == Some(&needle_id)
    });
    if existing.is_none() {
        return Err(format!("needle '{}' not found", needle_id));
    }

    // Extract any needle references from the intent text
    let id_re = Regex::new(r"(→\d+|bd-\d+|nd-\d+)").unwrap();
    let refs: Vec<String> = id_re.captures_iter(intent)
        .map(|c| c[1].to_string())
        .filter(|r| *r != needle_id)
        .collect();

    // Append the intent to the needle's title (compounding it)
    with_needles_locked(&root, |all_needles| {
        for n in all_needles.iter_mut() {
            if n.get("id").and_then(|v| v.as_str()) == Some(&needle_id) {
                let current_title = n.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
                // Append compound intent with separator
                let new_title = format!("{} ⊕ {}", current_title, intent);
                n["title"] = json!(new_title);
                break;
            }
        }
        Ok(())
    })?;

    // Audit the compound event
    append_audit(&root, &json!({
        "event": "needle.compounded",
        "id": needle_id,
        "intent": intent,
        "refs": refs,
        "timestamp": now_iso()
    }))?;

    writeln!(ctx, "⊕ {} compounded: {}", needle_id, intent).unwrap();
    if !refs.is_empty() {
        writeln!(ctx, "  new joints: {}", refs.join(", ")).unwrap();
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// :radiate — expand from point, show frontier for delegation
// ---------------------------------------------------------------------------

/// CLI entry point (thin wrapper).
pub fn run_radiate(id: Option<&str>) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_radiate_verb(&mut ctx, id)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
/// If no ID given, finds the highest-degree point across all spheres.
/// Shows the expansion frontier: which needles are at radius 1 from the point,
/// what work they need, and generates delegation-ready output.
pub fn run_radiate_verb(ctx: &mut VerbCtx, id: Option<&str>) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let needles = read_needles(&root)?;

    // Build graph
    let (_joints, adj) = build_joint_graph(&needles);
    let spheres = find_spheres(&adj);

    if spheres.is_empty() {
        writeln!(ctx, "no spheres found — run :compile to discover connections").unwrap();
        return Ok(());
    }

    // Title + priority index
    let title_map: HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let title = n.get("title").and_then(|v| v.as_str())?;
            Some((id.to_string(), title.to_string()))
        })
        .collect();

    let priority_map: HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let priority = n.get("priority").and_then(|v| v.as_str())?;
            Some((id.to_string(), priority.to_string()))
        })
        .collect();

    let status_map: HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let status = n.get("status").and_then(|v| v.as_str())?;
            Some((id.to_string(), status.to_string()))
        })
        .collect();

    // Determine the expansion point
    let point = if let Some(given_id) = id {
        normalize_needle_id(given_id)
    } else {
        // Find the highest-degree point across all spheres (largest sphere wins ties)
        spheres[0].point.clone()
    };

    if !title_map.contains_key(&point) {
        return Err(format!("needle '{}' not found", point));
    }

    let point_title = title_map.get(&point).map(|s| s.as_str()).unwrap_or("?");
    let point_priority = priority_map.get(&point).map(|s| s.as_str()).unwrap_or("?");
    let point_degree = adj.get(&point).map_or(0, |s| s.len());

    // BFS from point
    let radius_map = bfs_radius(&adj, &point);
    let max_radius = radius_map.values().copied().max().unwrap_or(0);

    writeln!(ctx, "── radiate from {} [{}|d{}] ──", point, point_priority, point_degree).unwrap();
    writeln!(ctx, "  {}", point_title).unwrap();
    writeln!(ctx, "  max radius: {}", max_radius).unwrap();
    writeln!(ctx).unwrap();

    // Group by radius
    let mut by_radius: HashMap<usize, Vec<String>> = HashMap::new();
    for (nid, &dist) in &radius_map {
        by_radius.entry(dist).or_default().push(nid.clone());
    }

    // Show each ring
    for r in 0..=max_radius.min(5) {
        let ring = by_radius.get(&r).cloned().unwrap_or_default();
        let mut open_ring: Vec<&String> = ring.iter()
            .filter(|nid| status_map.get(*nid).map(|s| s.as_str()) == Some("open"))
            .collect();
        open_ring.sort();

        if r == 0 {
            writeln!(ctx, "◉ POINT (radius 0): {}", point).unwrap();
        } else {
            writeln!(ctx, "○ RING {} ({} needles, {} open):", r, ring.len(), open_ring.len()).unwrap();
            for nid in open_ring.iter().take(8) {
                let ntitle = title_map.get(*nid).map(|s| s.as_str()).unwrap_or("?");
                let short = crate::util::safe_truncate(ntitle, 65);
                let np = priority_map.get(*nid).map(|s| s.as_str()).unwrap_or("?");
                writeln!(ctx, "    {} [{}] {}", nid, np, short).unwrap();
            }
            if open_ring.len() > 8 {
                writeln!(ctx, "    ... +{} more open", open_ring.len() - 8).unwrap();
            }
        }
    }

    if max_radius > 5 {
        let remaining: usize = (6..=max_radius)
            .map(|r| by_radius.get(&r).map_or(0, |v| v.len()))
            .sum();
        writeln!(ctx).unwrap();
        writeln!(ctx, "  ... +{} needles at radius 6-{}", remaining, max_radius).unwrap();
    }

    // Summary: delegation targets = open needles at radius 1
    writeln!(ctx).unwrap();
    let frontier: Vec<&String> = by_radius.get(&1).map_or(Vec::new(), |ring| {
        ring.iter()
            .filter(|nid| status_map.get(*nid).map(|s| s.as_str()) == Some("open"))
            .collect()
    });

    if !frontier.is_empty() {
        writeln!(ctx, "── delegation frontier ({} targets) ──", frontier.len()).unwrap();
        for nid in &frontier {
            let ntitle = title_map.get(*nid).map(|s| s.as_str()).unwrap_or("?");
            writeln!(ctx, "  :spawn {} → {}", nid, ntitle).unwrap();
        }
    }

    // Audit
    append_audit(&root, &json!({
        "event": "needle.radiated",
        "point": point,
        "max_radius": max_radius,
        "frontier_size": frontier.len(),
        "timestamp": now_iso()
    }))?;

    Ok(())
}

// ---------------------------------------------------------------------------
// :sphere — show sphere digest for a needle
// ---------------------------------------------------------------------------

/// CLI entry point (thin wrapper).
pub fn run_sphere(id: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_sphere_verb(&mut ctx, id)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
/// Shows the compact sphere digest: members, joints, hay clusters.
/// This is the "index" an agent needs to reason about dependencies.
pub fn run_sphere_verb(ctx: &mut VerbCtx, id: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let needles = read_needles(&root)?;
    let needle_id = normalize_needle_id(id);

    // Verify needle exists
    let title_map: HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let title = n.get("title").and_then(|v| v.as_str())?;
            Some((id.to_string(), title.to_string()))
        })
        .collect();

    if !title_map.contains_key(&needle_id) {
        return Err(format!("needle '{}' not found", needle_id));
    }

    let status_map: HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let status = n.get("status").and_then(|v| v.as_str())?;
            Some((id.to_string(), status.to_string()))
        })
        .collect();

    let priority_map: HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let priority = n.get("priority").and_then(|v| v.as_str())?;
            Some((id.to_string(), priority.to_string()))
        })
        .collect();

    // Build graph
    let (all_joints, adj) = build_joint_graph(&needles);
    let spheres = find_spheres(&adj);

    // Find which sphere this needle belongs to
    let mut found_sphere: Option<&Sphere> = None;
    let mut sphere_idx: usize = 0;
    for (i, sphere) in spheres.iter().enumerate() {
        if sphere.needles.contains(&needle_id) {
            found_sphere = Some(sphere);
            sphere_idx = i + 1;
            break;
        }
    }

    let sphere = match found_sphere {
        Some(s) => s,
        None => {
            writeln!(ctx, "{} — ISOLATED (no joints)", needle_id).unwrap();
            writeln!(ctx, "  {}", title_map.get(&needle_id).unwrap_or(&"?".to_string())).unwrap();
            writeln!(ctx).unwrap();
            writeln!(ctx, "  Use :link to connect, or :near to find related needles.").unwrap();
            return Ok(());
        }
    };

    let joint_count = sphere.needles.iter()
        .flat_map(|nid| adj.get(nid).map(|s| s.len()).into_iter())
        .sum::<usize>() / 2; // Each edge counted twice in undirected graph

    writeln!(ctx, "SPHERE {} (point: {}, {} members, {} joints)",
        sphere_idx, sphere.point, sphere.needles.len(), joint_count).unwrap();

    // List members with status
    for nid in &sphere.needles {
        let title = title_map.get(nid).map(|s| s.as_str()).unwrap_or("?");
        let status = status_map.get(nid).map(|s| s.as_str()).unwrap_or("?");
        let priority = priority_map.get(nid).map(|s| s.as_str()).unwrap_or("?");
        let short = crate::util::safe_truncate(title, 60);
        let marker = if *nid == needle_id { "▸" } else { " " };
        writeln!(ctx, "  {}{} [{}|{}] {}", marker, nid, priority, status, short).unwrap();
    }

    // Show explicit joints within this sphere
    let sphere_set: HashSet<&String> = sphere.needles.iter().collect();
    let explicit_joints: Vec<&Joint> = all_joints.iter()
        .filter(|j| {
            sphere_set.contains(&j.source) && sphere_set.contains(&j.target)
                && matches!(j.joint_type, JointType::Explicit)
        })
        .collect();

    if !explicit_joints.is_empty() {
        writeln!(ctx).unwrap();
        writeln!(ctx, "  JOINTS:").unwrap();
        let mut seen: HashSet<(String, String)> = HashSet::new();
        for j in &explicit_joints {
            let key = if j.source < j.target {
                (j.source.clone(), j.target.clone())
            } else {
                (j.target.clone(), j.source.clone())
            };
            if seen.insert(key) {
                writeln!(ctx, "    {} ↔ {} [explicit]", j.source, j.target).unwrap();
            }
        }
    }

    // Show concept joints (summarized)
    let concept_joints: Vec<&Joint> = all_joints.iter()
        .filter(|j| {
            sphere_set.contains(&j.source) && sphere_set.contains(&j.target)
                && matches!(j.joint_type, JointType::Concept(_))
        })
        .collect();

    if !concept_joints.is_empty() {
        let mut concept_counts: HashMap<String, usize> = HashMap::new();
        for j in &concept_joints {
            if let JointType::Concept(ref term) = j.joint_type {
                *concept_counts.entry(term.clone()).or_insert(0) += 1;
            }
        }
        let mut concepts: Vec<(String, usize)> = concept_counts.into_iter().collect();
        concepts.sort_by(|a, b| b.1.cmp(&a.1));
        let concept_list: Vec<String> = concepts.iter().map(|(t, c)| format!("{}({})", t, c)).collect();
        writeln!(ctx, "  CONCEPTS: {}", concept_list.join(", ")).unwrap();
    }

    // Bridge to hay clusters
    let hay_entries = crate::kernel::intelligence::read_hay(&root).unwrap_or_default();
    if !hay_entries.is_empty() {
        let cluster_result = crate::kernel::intelligence::cluster_hay(&root);
        let bridged: Vec<&crate::kernel::intelligence::HayCluster> = match &cluster_result {
            Ok(cr) => cr.clusters.iter()
                .filter(|c| c.sphere_point.as_ref() == Some(&sphere.point))
                .collect(),
            Err(_) => Vec::new(),
        };

        if !bridged.is_empty() {
            writeln!(ctx).unwrap();
            let total_straws: usize = bridged.iter().map(|c| c.members.len()).sum();
            writeln!(ctx, "  HAY ({} straws in {} clusters):", total_straws, bridged.len()).unwrap();
            for cluster in &bridged {
                for entry in cluster.members.iter().take(3) {
                    let short = crate::util::safe_truncate(&entry.straw, 70);
                    writeln!(ctx, "    \"{}\"", short).unwrap();
                }
                if cluster.members.len() > 3 {
                    writeln!(ctx, "    ... +{} more", cluster.members.len() - 3).unwrap();
                }
            }
        }
    }

    writeln!(ctx).unwrap();

    // Audit
    append_audit(&root, &json!({
        "event": "needle.sphere",
        "needle": needle_id,
        "sphere_idx": sphere_idx,
        "sphere_point": sphere.point,
        "sphere_size": sphere.needles.len(),
        "timestamp": now_iso()
    }))?;

    Ok(())
}

// ---------------------------------------------------------------------------
// :near — find needles and hay by concept
// ---------------------------------------------------------------------------

/// CLI entry point (thin wrapper).
pub fn run_near(query: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_near_verb(&mut ctx, query)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
/// Finds needles and hay matching a concept term or keyword.
/// Returns the neighborhood grouped by sphere.
pub fn run_near_verb(ctx: &mut VerbCtx, query: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let needles = read_needles(&root)?;
    let query_lower = query.to_lowercase();

    // Find needles matching the query in their title
    let matching: Vec<&Value> = needles.iter()
        .filter(|n| {
            let status = n.get("status").and_then(|v| v.as_str()).unwrap_or("");
            let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("");
            (status == "open" || status == "in_progress") && title.to_lowercase().contains(&query_lower)
        })
        .collect();

    if matching.is_empty() {
        writeln!(ctx, "no open needles matching \"{}\"", query).unwrap();
    } else {
        // Build graph to show sphere context
        let (_joints, adj) = build_joint_graph(&needles);
        let spheres = find_spheres(&adj);

        // Map needle -> sphere
        let mut needle_sphere: HashMap<String, usize> = HashMap::new();
        for (i, sphere) in spheres.iter().enumerate() {
            for nid in &sphere.needles {
                needle_sphere.insert(nid.clone(), i);
            }
        }

        writeln!(ctx, "── {} open needles near \"{}\" ──", matching.len(), query).unwrap();
        writeln!(ctx).unwrap();

        // Group by sphere
        let mut by_sphere: HashMap<usize, Vec<&Value>> = HashMap::new();
        let mut no_sphere: Vec<&Value> = Vec::new();
        for n in &matching {
            let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("");
            if let Some(&si) = needle_sphere.get(id) {
                by_sphere.entry(si).or_default().push(n);
            } else {
                no_sphere.push(n);
            }
        }

        for (si, group) in &by_sphere {
            let sphere = &spheres[*si];
            writeln!(ctx, "  SPHERE {} (point: {}, {} members):", si + 1, sphere.point, sphere.needles.len()).unwrap();
            for n in group {
                let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("?");
                let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("?");
                let priority = n.get("priority").and_then(|v| v.as_str()).unwrap_or("?");
                let short = crate::util::safe_truncate(title, 60);
                writeln!(ctx, "    {} [{}] {}", id, priority, short).unwrap();
            }
            writeln!(ctx).unwrap();
        }

        if !no_sphere.is_empty() {
            writeln!(ctx, "  ISOLATED:").unwrap();
            for n in &no_sphere {
                let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("?");
                let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("?");
                let short = crate::util::safe_truncate(title, 60);
                writeln!(ctx, "    {} {}", id, short).unwrap();
            }
            writeln!(ctx).unwrap();
        }
    }

    // Also search hay
    let hay_entries = crate::kernel::intelligence::read_hay(&root).unwrap_or_default();
    let matching_hay: Vec<&crate::kernel::intelligence::HayEntry> = hay_entries.iter()
        .filter(|h| h.straw.to_lowercase().contains(&query_lower))
        .collect();

    if !matching_hay.is_empty() {
        writeln!(ctx, "── {} hay straws near \"{}\" ──", matching_hay.len(), query).unwrap();
        for h in matching_hay.iter().take(10) {
            let short = crate::util::safe_truncate(&h.straw, 70);
            writeln!(ctx, "  \"{}\" ({})", short, h.source).unwrap();
        }
        if matching_hay.len() > 10 {
            writeln!(ctx, "  ... +{} more", matching_hay.len() - 10).unwrap();
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// :link — explicitly create a typed joint between two needles
// ---------------------------------------------------------------------------

/// CLI entry point (thin wrapper).
pub fn run_link(source_id: &str, target_id: &str, link_type: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_link_verb(&mut ctx, source_id, target_id, link_type)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
/// Creates an explicit dependency edge. Bidirectional: depends_on / blocks.
/// Types: depends_on, blocks, after, enables, requires
pub fn run_link_verb(ctx: &mut VerbCtx, source_id: &str, target_id: &str, link_type: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let source = normalize_needle_id(source_id);
    let target = normalize_needle_id(target_id);

    if source == target {
        return Err("cannot link a needle to itself".to_string());
    }

    // Validate link type
    let (forward_field, reverse_field) = match link_type {
        "depends_on" | "requires" | "after" | "blocked_by" => ("depends_on", "blocks"),
        "blocks" | "enables" | "unblocks" => ("blocks", "depends_on"),
        _ => return Err(format!("unknown link type '{}'. Valid: depends_on, blocks, after, enables, requires", link_type)),
    };

    with_needles_locked(&root, |all_needles| {
        // Verify both exist
        let source_exists = all_needles.iter().any(|n| n.get("id").and_then(|v| v.as_str()) == Some(&source));
        let target_exists = all_needles.iter().any(|n| n.get("id").and_then(|v| v.as_str()) == Some(&target));

        if !source_exists { return Err(format!("needle '{}' not found", source)); }
        if !target_exists { return Err(format!("needle '{}' not found", target)); }

        // Add forward edge: source.forward_field += target
        for n in all_needles.iter_mut() {
            if n.get("id").and_then(|v| v.as_str()) == Some(&source) {
                let arr = n.get(forward_field)
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default();
                let target_val = serde_json::Value::String(target.clone());
                if !arr.contains(&target_val) {
                    let mut new_arr = arr;
                    new_arr.push(target_val);
                    n[forward_field] = serde_json::Value::Array(new_arr);
                }
            }
        }

        // Add reverse edge: target.reverse_field += source
        for n in all_needles.iter_mut() {
            if n.get("id").and_then(|v| v.as_str()) == Some(&target) {
                let arr = n.get(reverse_field)
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default();
                let source_val = serde_json::Value::String(source.clone());
                if !arr.contains(&source_val) {
                    let mut new_arr = arr;
                    new_arr.push(source_val);
                    n[reverse_field] = serde_json::Value::Array(new_arr);
                }
            }
        }

        Ok(())
    })?;

    writeln!(ctx, "⊕ {} {} {} → linked", source, link_type, target).unwrap();

    // Audit
    append_audit(&root, &json!({
        "event": "needle.linked",
        "source": source,
        "target": target,
        "link_type": link_type,
        "timestamp": now_iso()
    }))?;

    Ok(())
}

// ---------------------------------------------------------------------------
// :unlink — remove a joint between two needles
// ---------------------------------------------------------------------------

/// CLI entry point (thin wrapper).
pub fn run_unlink(source_id: &str, target_id: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_unlink_verb(&mut ctx, source_id, target_id)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
/// Removes all dependency edges between two needles.
pub fn run_unlink_verb(ctx: &mut VerbCtx, source_id: &str, target_id: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let source = normalize_needle_id(source_id);
    let target = normalize_needle_id(target_id);

    with_needles_locked(&root, |all_needles| {
        // Remove target from source's depends_on and blocks
        for n in all_needles.iter_mut() {
            let nid = n.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
            if nid == source || nid == target {
                let other = if nid == source { &target } else { &source };
                for field in &["depends_on", "blocks"] {
                    if let Some(arr) = n.get(*field).and_then(|v| v.as_array()).cloned() {
                        let other_val = serde_json::Value::String(other.clone());
                        let new_arr: Vec<Value> = arr.into_iter().filter(|v| v != &other_val).collect();
                        n[*field] = serde_json::Value::Array(new_arr);
                    }
                }
            }
        }
        Ok(())
    })?;

    writeln!(ctx, "⊖ {} ↔ {} — unlinked", source, target).unwrap();

    append_audit(&root, &json!({
        "event": "needle.unlinked",
        "source": source,
        "target": target,
        "timestamp": now_iso()
    }))?;

    Ok(())
}

// ---------------------------------------------------------------------------
// :activate — full context briefing for working a needle
// ---------------------------------------------------------------------------

/// CLI entry point (thin wrapper).
pub fn run_activate(id: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_activate_verb(&mut ctx, id)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
/// Pre-flight briefing: sphere digest + hay + frontier + suggested actions.
/// This is what an agent needs before starting work on a needle.
pub fn run_activate_verb(ctx: &mut VerbCtx, id: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let needles = read_needles(&root)?;
    let needle_id = normalize_needle_id(id);

    let needle = needles.iter().find(|n| n.get("id").and_then(|v| v.as_str()) == Some(&needle_id));
    let needle = match needle {
        Some(n) => n,
        None => return Err(format!("needle '{}' not found", needle_id)),
    };

    let title = needle.get("title").and_then(|v| v.as_str()).unwrap_or("?");
    let status = needle.get("status").and_then(|v| v.as_str()).unwrap_or("?");
    let priority = needle.get("priority").and_then(|v| v.as_str()).unwrap_or("?");

    writeln!(ctx, "═══ ACTIVATE {} [{}|{}] ═══", needle_id, priority, status).unwrap();
    writeln!(ctx, "  {}", title).unwrap();
    writeln!(ctx).unwrap();

    // Show depends_on (blockers)
    let deps: Vec<String> = needle.get("depends_on")
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default();

    let blocks: Vec<String> = needle.get("blocks")
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default();

    // Title map for lookups
    let title_map: HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let title = n.get("title").and_then(|v| v.as_str())?;
            Some((id.to_string(), title.to_string()))
        })
        .collect();

    let status_map: HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let status = n.get("status").and_then(|v| v.as_str())?;
            Some((id.to_string(), status.to_string()))
        })
        .collect();

    if !deps.is_empty() {
        writeln!(ctx, "  BLOCKED BY:").unwrap();
        let mut all_resolved = true;
        for dep in &deps {
            let dtitle = title_map.get(dep).map(|s| s.as_str()).unwrap_or("?");
            let dstatus = status_map.get(dep).map(|s| s.as_str()).unwrap_or("?");
            let short = crate::util::safe_truncate(dtitle, 55);
            let resolved = dstatus == "closed";
            if !resolved { all_resolved = false; }
            let mark = if resolved { "✓" } else { "✗" };
            writeln!(ctx, "    {} {} [{}] {}", mark, dep, dstatus, short).unwrap();
        }
        if all_resolved {
            writeln!(ctx, "    → all blockers resolved ✓").unwrap();
        } else {
            writeln!(ctx, "    → ⚠ unresolved blockers — may not be ready").unwrap();
        }
        writeln!(ctx).unwrap();
    }

    if !blocks.is_empty() {
        writeln!(ctx, "  UNBLOCKS:").unwrap();
        for blk in &blocks {
            let btitle = title_map.get(blk).map(|s| s.as_str()).unwrap_or("?");
            let short = crate::util::safe_truncate(btitle, 55);
            writeln!(ctx, "    → {} {}", blk, short).unwrap();
        }
        writeln!(ctx).unwrap();
    }

    // Show sphere context (compact)
    let (_joints, adj) = build_joint_graph(&needles);
    let spheres = find_spheres(&adj);
    let mut found_sphere: Option<&Sphere> = None;
    for sphere in &spheres {
        if sphere.needles.contains(&needle_id) {
            found_sphere = Some(sphere);
            break;
        }
    }

    if let Some(sphere) = found_sphere {
        let radius_map = bfs_radius(&adj, &sphere.point);
        let my_radius = radius_map.get(&needle_id).copied().unwrap_or(0);
        let total_joints = sphere.needles.iter()
            .flat_map(|nid| adj.get(nid).map(|s| s.len()).into_iter())
            .sum::<usize>() / 2;

        writeln!(ctx, "  SPHERE: point={}, {} members, {} joints, my radius={}",
            sphere.point, sphere.needles.len(), total_joints, my_radius).unwrap();

        // Show immediate neighbors only
        if let Some(neighbors) = adj.get(&needle_id) {
            let mut sorted: Vec<&String> = neighbors.iter().collect();
            sorted.sort();
            writeln!(ctx, "  NEIGHBORS ({}):", sorted.len()).unwrap();
            for n in sorted.iter().take(8) {
                let ntitle = title_map.get(*n).map(|s| s.as_str()).unwrap_or("?");
                let nstatus = status_map.get(*n).map(|s| s.as_str()).unwrap_or("?");
                let short = crate::util::safe_truncate(ntitle, 55);
                writeln!(ctx, "    {} [{}] {}", n, nstatus, short).unwrap();
            }
            if sorted.len() > 8 {
                writeln!(ctx, "    ... +{} more", sorted.len() - 8).unwrap();
            }
        }
        writeln!(ctx).unwrap();
    }

    // Bridge to hay
    let hay_entries = crate::kernel::intelligence::read_hay(&root).unwrap_or_default();
    if !hay_entries.is_empty() {
        // Search hay for straws mentioning this needle or related concepts
        let title_lower = title.to_lowercase();
        let relevant_concepts: Vec<&str> = CONCEPT_TERMS.iter()
            .filter(|term| title_lower.contains(**term))
            .copied()
            .collect();

        let matching_hay: Vec<&crate::kernel::intelligence::HayEntry> = hay_entries.iter()
            .filter(|h| {
                let straw_lower = h.straw.to_lowercase();
                straw_lower.contains(&needle_id.to_lowercase())
                    || relevant_concepts.iter().any(|c| straw_lower.contains(c))
            })
            .collect();

        if !matching_hay.is_empty() {
            writeln!(ctx, "  HAY ({} related straws):", matching_hay.len()).unwrap();
            for h in matching_hay.iter().take(5) {
                let short = crate::util::safe_truncate(&h.straw, 70);
                writeln!(ctx, "    \"{}\"", short).unwrap();
            }
            if matching_hay.len() > 5 {
                writeln!(ctx, "    ... +{} more", matching_hay.len() - 5).unwrap();
            }
            writeln!(ctx).unwrap();
        }
    }

    writeln!(ctx, "═══ ready ═══").unwrap();

    // Audit
    append_audit(&root, &json!({
        "event": "needle.activated",
        "needle": needle_id,
        "timestamp": now_iso()
    }))?;

    Ok(())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn normalize_needle_id(id: &str) -> String {
    // Already canonical forms — keep as-is
    if id.starts_with('→') || id.starts_with("bd-") || id.starts_with("nd-") {
        return id.to_string();
    }
    // Bare number → arrow form
    if id.chars().all(|c| c.is_ascii_digit())
        && let Ok(n) = id.parse::<u64>() {
            return format!("→{n:03}");
        }
    id.to_string()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

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
    fn test_build_joint_graph_explicit_deps() {
        let needles = vec![
            make_needle("→001", "implement auth", "open", "P0"),
            make_needle("→002", "add tests depends on →001", "open", "P1"),
        ];
        let (joints, adj) = build_joint_graph(&needles);
        assert!(!joints.is_empty());
        assert!(adj.get("→002").unwrap().contains("→001"));
        assert!(adj.get("→001").unwrap().contains("→002"));
    }

    #[test]
    fn test_build_joint_graph_shared_refs() {
        let needles = vec![
            make_needle("→001", "core module", "open", "P0"),
            make_needle("→002", "uses →001 for auth", "open", "P1"),
            make_needle("→003", "also uses →001 for tests", "open", "P1"),
        ];
        let (_joints, adj) = build_joint_graph(&needles);
        // →002 and →003 both reference →001, creating a shared-ref edge
        assert!(adj.get("→002").unwrap().contains("→003"));
    }

    #[test]
    fn test_build_joint_graph_concept_edges() {
        let needles = vec![
            make_needle("→001", "tui overlay rendering", "open", "P1"),
            make_needle("→002", "tui input handling", "open", "P1"),
        ];
        let (_joints, adj) = build_joint_graph(&needles);
        // Both mention "tui" — concept edge
        assert!(adj.get("→001").unwrap().contains("→002"));
    }

    #[test]
    fn test_find_spheres_single_component() {
        let needles = vec![
            make_needle("→001", "depends on →002", "open", "P0"),
            make_needle("→002", "base module", "open", "P0"),
        ];
        let (_joints, adj) = build_joint_graph(&needles);
        let spheres = find_spheres(&adj);
        assert_eq!(spheres.len(), 1);
        assert_eq!(spheres[0].needles.len(), 2);
    }

    #[test]
    fn test_find_spheres_disconnected() {
        let needles = vec![
            make_needle("→001", "alpha feature", "open", "P0"),
            make_needle("→002", "beta feature", "open", "P0"),
        ];
        let (_joints, adj) = build_joint_graph(&needles);
        let spheres = find_spheres(&adj);
        // No shared concepts, no deps — should be 2 separate spheres (or isolates)
        assert!(spheres.len() >= 2);
    }

    #[test]
    fn test_bfs_radius_linear() {
        let mut adj: HashMap<String, HashSet<String>> = HashMap::new();
        adj.entry("A".into()).or_default().insert("B".into());
        adj.entry("B".into()).or_default().insert("A".into());
        adj.entry("B".into()).or_default().insert("C".into());
        adj.entry("C".into()).or_default().insert("B".into());

        let distances = bfs_radius(&adj, "A");
        assert_eq!(distances["A"], 0);
        assert_eq!(distances["B"], 1);
        assert_eq!(distances["C"], 2);
    }

    #[test]
    fn test_normalize_needle_id() {
        assert_eq!(normalize_needle_id("→540"), "→540");
        assert_eq!(normalize_needle_id("bd-025"), "bd-025");
        assert_eq!(normalize_needle_id("42"), "→042");
        assert_eq!(normalize_needle_id("540"), "→540");
    }

    #[test]
    fn test_closed_needles_excluded() {
        let needles = vec![
            make_needle("→001", "open needle with audit", "open", "P0"),
            make_needle("→002", "closed needle with audit", "closed", "P1"),
        ];
        let (_joints, adj) = build_joint_graph(&needles);
        // Closed needle should not appear in the graph
        assert!(!adj.contains_key("→002"));
    }

    #[test]
    fn test_point_is_highest_degree() {
        // →002 is the hub: its title references four others via "blocks →NNN",
        // giving it explicit edges to all of them. The others' titles are plain
        // (no →NNN references), so no shared-ref clique forms between them.
        let needles = vec![
            make_needle("→001", "alpha subtask", "open", "P1"),
            make_needle("→002", "central hub blocks →001 blocks →003 blocks →004 blocks →005", "open", "P0"),
            make_needle("→003", "bravo subtask", "open", "P1"),
            make_needle("→004", "charlie subtask", "open", "P1"),
            make_needle("→005", "delta subtask", "open", "P1"),
        ];
        let (_joints, adj) = build_joint_graph(&needles);
        let spheres = find_spheres(&adj);
        assert_eq!(spheres.len(), 1);
        // →002 has degree 4 (connected to all others), others have degree 1
        // →002 should be the point
        assert_eq!(spheres[0].point, "→002");
    }
}
