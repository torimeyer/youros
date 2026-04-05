//! Hay intelligence layer — the pile organizes itself.
//!
//! Clusters hay entries by semantic similarity (embeddings, when available)
//! or concept-term matching (always available). Discovers themes the human
//! didn't plan, surfaces them in `hay list`, and bridges to the Hoberman
//! sphere model so clusters show which sphere they'd join on compile.

use crate::commands::refine::{self, CONCEPT_TERMS};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{HashMap, HashSet};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// A single hay entry parsed from audit.jsonl.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HayEntry {
    pub straw: String,
    pub source: String,
    pub timestamp: String,
}

/// A cluster of related hay entries with a discovered theme.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HayCluster {
    /// Discovered theme name (most representative straw or concept term).
    pub theme: String,
    /// Member hay entries.
    pub members: Vec<HayEntry>,
    /// If this cluster maps to an existing needle sphere, the sphere's point needle ID.
    pub sphere_point: Option<String>,
}

/// Result of clustering hay.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ClusterResult {
    /// Clusters with 2+ members.
    pub clusters: Vec<HayCluster>,
    /// Hay entries that didn't cluster with anything.
    pub unclustered: Vec<HayEntry>,
}

// ---------------------------------------------------------------------------
// Read hay from audit
// ---------------------------------------------------------------------------

/// Extract all hay entries from audit.jsonl.
pub fn read_hay(root: &std::path::Path) -> Result<Vec<HayEntry>, String> {
    let audit_path = crate::state_dir(root).join("audit.jsonl");
    let content = std::fs::read_to_string(&audit_path).unwrap_or_default();

    let mut entries = Vec::new();
    for line in content.lines() {
        if let Ok(v) = serde_json::from_str::<Value>(line)
            && v.get("event").and_then(|e| e.as_str()) == Some("hay.filed") {
                let straw = v.get("straw").and_then(|s| s.as_str()).unwrap_or("").to_string();
                let source = v.get("source").and_then(|s| s.as_str()).unwrap_or("cli").to_string();
                let timestamp = v.get("timestamp").and_then(|s| s.as_str()).unwrap_or("").to_string();
                if !straw.is_empty() {
                    entries.push(HayEntry { straw, source, timestamp });
                }
            }
    }
    Ok(entries)
}

// ---------------------------------------------------------------------------
// Clustering: concept-term matching (always available, no dependencies)
// ---------------------------------------------------------------------------

/// Cluster hay by shared concept terms from the Hoberman sphere vocabulary.
/// This is the fallback when embeddings are unavailable — zero cost, instant.
fn cluster_by_concepts(entries: &[HayEntry]) -> ClusterResult {
    // Map each entry to its matching concept terms
    let mut entry_concepts: Vec<HashSet<String>> = Vec::new();
    for entry in entries {
        let lower = entry.straw.to_lowercase();
        let concepts: HashSet<String> = CONCEPT_TERMS.iter()
            .filter(|term| lower.contains(**term))
            .map(|s| s.to_string())
            .collect();
        entry_concepts.push(concepts);
    }

    // Build concept -> entry indices mapping
    let mut concept_members: HashMap<String, Vec<usize>> = HashMap::new();
    for (i, concepts) in entry_concepts.iter().enumerate() {
        for concept in concepts {
            concept_members.entry(concept.clone()).or_default().push(i);
        }
    }

    // Union-find for grouping entries that share concepts
    let n = entries.len();
    let mut parent: Vec<usize> = (0..n).collect();

    fn find(parent: &mut [usize], i: usize) -> usize {
        if parent[i] != i {
            parent[i] = find(parent, parent[i]);
        }
        parent[i]
    }

    fn union(parent: &mut [usize], a: usize, b: usize) {
        let ra = find(parent, a);
        let rb = find(parent, b);
        if ra != rb {
            parent[ra] = rb;
        }
    }

    // Union entries that share at least one concept term
    for members in concept_members.values() {
        if members.len() >= 2 && members.len() <= 20 {
            for i in 1..members.len() {
                union(&mut parent, members[0], members[i]);
            }
        }
    }

    // Collect groups
    let mut groups: HashMap<usize, Vec<usize>> = HashMap::new();
    for i in 0..n {
        let root = find(&mut parent, i);
        groups.entry(root).or_default().push(i);
    }

    let mut clusters = Vec::new();
    let mut unclustered = Vec::new();

    for (_root, indices) in groups {
        if indices.len() < 2 {
            unclustered.push(entries[indices[0]].clone());
        } else {
            // Theme: most common concept term across members
            let mut term_counts: HashMap<String, usize> = HashMap::new();
            for &i in &indices {
                for concept in &entry_concepts[i] {
                    *term_counts.entry(concept.clone()).or_default() += 1;
                }
            }
            let theme = term_counts.into_iter()
                .max_by_key(|(_, count)| *count)
                .map(|(term, _)| term.to_uppercase())
                .unwrap_or_else(|| "UNNAMED".to_string());

            let members: Vec<HayEntry> = indices.iter().map(|&i| entries[i].clone()).collect();
            clusters.push(HayCluster {
                theme,
                members,
                sphere_point: None,
            });
        }
    }

    // Sort clusters by size (largest first)
    clusters.sort_by(|a, b| b.members.len().cmp(&a.members.len()));

    ClusterResult { clusters, unclustered }
}

// ---------------------------------------------------------------------------
// Clustering: semantic (embeddings feature, when model available)
// ---------------------------------------------------------------------------

/// Cluster hay by embedding similarity using the leader algorithm.
/// Falls back to concept-term clustering if embeddings unavailable.
#[cfg(feature = "embeddings")]
fn cluster_by_embeddings(entries: &[HayEntry]) -> ClusterResult {
    use crate::squasher::dedup::tokenize_for_embedding;
    use crate::squasher::embeddings;

    if !embeddings::model_available() {
        return cluster_by_concepts(entries);
    }

    let threshold: f32 = std::env::var("OSTK_HAY_CLUSTER_THRESHOLD")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(0.75); // Lower than squasher's 0.85 — hay is fuzzier

    // Embed all entries
    let mut embeddings: Vec<Option<Vec<f32>>> = Vec::new();
    for entry in entries {
        let tokenized = tokenize_for_embedding(&entry.straw);
        match embeddings::embed(&tokenized) {
            Ok(emb) => embeddings.push(Some(emb)),
            Err(_) => embeddings.push(None),
        }
    }

    // Leader algorithm: greedy single-pass clustering
    // Each cluster: (representative_index, centroid_embedding, member_indices)
    let mut clusters_raw: Vec<(usize, Vec<f32>, Vec<usize>)> = Vec::new();

    for (i, emb_opt) in embeddings.iter().enumerate() {
        let emb = match emb_opt {
            Some(e) => e,
            None => {
                // No embedding — skip, will be handled by concept fallback
                continue;
            }
        };

        let mut best_sim: f32 = -1.0;
        let mut best_cluster: Option<usize> = None;

        for (ci, (_rep, centroid, _members)) in clusters_raw.iter().enumerate() {
            let sim = embeddings::cosine_similarity(emb, centroid);
            if sim > best_sim {
                best_sim = sim;
                best_cluster = Some(ci);
            }
        }

        if best_sim > threshold {
            if let Some(ci) = best_cluster {
                clusters_raw[ci].2.push(i);
            }
        } else {
            clusters_raw.push((i, emb.clone(), vec![i]));
        }
    }

    // Convert to HayClusters
    let mut clusters = Vec::new();
    let mut unclustered = Vec::new();

    for (rep_idx, _centroid, member_indices) in &clusters_raw {
        if member_indices.len() < 2 {
            unclustered.push(entries[member_indices[0]].clone());
        } else {
            // Theme: use the representative straw, uppercased and truncated
            let rep_straw = &entries[*rep_idx].straw;
            let theme = derive_theme(rep_straw, member_indices.iter().map(|&i| entries[i].straw.as_str()));
            let members: Vec<HayEntry> = member_indices.iter().map(|&i| entries[i].clone()).collect();
            clusters.push(HayCluster {
                theme,
                members,
                sphere_point: None,
            });
        }
    }

    // Also catch entries with no embeddings via concept fallback
    let unembedded_indices: Vec<usize> = embeddings.iter().enumerate()
        .filter(|(_, e)| e.is_none())
        .map(|(i, _)| i)
        .collect();
    if !unembedded_indices.is_empty() {
        let unembedded_entries: Vec<HayEntry> = unembedded_indices.iter()
            .map(|&i| entries[i].clone())
            .collect();
        let concept_result = cluster_by_concepts(&unembedded_entries);
        clusters.extend(concept_result.clusters);
        unclustered.extend(concept_result.unclustered);
    }

    clusters.sort_by(|a, b| b.members.len().cmp(&a.members.len()));
    ClusterResult { clusters, unclustered }
}

/// Derive a theme name from a cluster's members.
/// Finds the longest common prefix or most frequent significant word.
#[allow(dead_code)] // called by cluster_by_embeddings; embedding path not yet active
fn derive_theme<'a>(representative: &str, members: impl Iterator<Item = &'a str>) -> String {
    // Collect all words across members
    let mut word_counts: HashMap<String, usize> = HashMap::new();
    let stop_words: HashSet<&str> = ["the", "a", "an", "and", "or", "but", "in", "on", "at",
        "to", "for", "of", "with", "by", "from", "is", "it", "that", "this",
        "fix", "add", "the", "some", "about"].iter().copied().collect();

    for straw in members {
        for word in straw.split_whitespace() {
            let w = word.to_lowercase().trim_matches(|c: char| !c.is_alphanumeric()).to_string();
            if w.len() > 2 && !stop_words.contains(w.as_str()) {
                *word_counts.entry(w).or_default() += 1;
            }
        }
    }

    // Pick the most frequent non-stop word
    word_counts.into_iter()
        .max_by_key(|(_, count)| *count)
        .map(|(word, _)| word.to_uppercase())
        .unwrap_or_else(|| {
            // Fallback: first significant word from representative
            representative.split_whitespace()
                .find(|w| w.len() > 2)
                .unwrap_or("UNNAMED")
                .to_uppercase()
        })
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Cluster all hay entries. Uses embeddings when available, concept-terms otherwise.
pub fn cluster_hay(root: &std::path::Path) -> Result<ClusterResult, String> {
    let entries = read_hay(root)?;
    if entries.is_empty() {
        return Ok(ClusterResult { clusters: Vec::new(), unclustered: Vec::new() });
    }

    #[cfg(feature = "embeddings")]
    let mut result = cluster_by_embeddings(&entries);

    #[cfg(not(feature = "embeddings"))]
    let mut result = cluster_by_concepts(&entries);

    // Bridge: cross-reference clusters with existing needle spheres
    bridge_to_spheres(root, &mut result)?;

    Ok(result)
}

/// Cross-reference hay clusters with the Hoberman sphere model.
/// For each cluster, check if its concept terms overlap with a sphere's needles.
/// If so, annotate the cluster with the sphere's point needle.
fn bridge_to_spheres(root: &std::path::Path, result: &mut ClusterResult) -> Result<(), String> {
    let needles = crate::read_needles(root)?;
    if needles.is_empty() {
        return Ok(());
    }

    let (_joints, adj) = refine::build_joint_graph(&needles);
    let spheres = refine::find_spheres(&adj);

    if spheres.is_empty() {
        return Ok(());
    }

    // Build sphere concept profiles: sphere_index -> set of concept terms in its needles
    let title_map: HashMap<String, String> = needles.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let title = n.get("title").and_then(|v| v.as_str())?;
            Some((id.to_string(), title.to_lowercase()))
        })
        .collect();

    let mut sphere_concepts: Vec<HashSet<String>> = Vec::new();
    for sphere in &spheres {
        let mut concepts = HashSet::new();
        for nid in &sphere.needles {
            if let Some(title) = title_map.get(nid) {
                for term in CONCEPT_TERMS {
                    if title.contains(*term) {
                        concepts.insert(term.to_string());
                    }
                }
            }
        }
        sphere_concepts.push(concepts);
    }

    // For each cluster, find the best-matching sphere by concept overlap
    for cluster in &mut result.clusters {
        let mut cluster_concepts = HashSet::new();
        for member in &cluster.members {
            let lower = member.straw.to_lowercase();
            for term in CONCEPT_TERMS {
                if lower.contains(*term) {
                    cluster_concepts.insert(term.to_string());
                }
            }
        }

        if cluster_concepts.is_empty() {
            continue;
        }

        let mut best_score: f64 = 0.0;
        let mut best_sphere: Option<usize> = None;

        for (si, sc) in sphere_concepts.iter().enumerate() {
            if sc.is_empty() {
                continue;
            }
            let overlap = cluster_concepts.intersection(sc).count();
            if overlap == 0 {
                continue;
            }
            // Jaccard similarity: overlap / union. Normalizes for sphere size
            // so a 3-needle sphere matching 3/3 beats a 126-needle sphere matching 3/50.
            let union = cluster_concepts.union(sc).count();
            let score = overlap as f64 / union as f64;
            if score > best_score {
                best_score = score;
                best_sphere = Some(si);
            }
        }

        if best_score > 0.0
            && let Some(si) = best_sphere {
                cluster.sphere_point = Some(spheres[si].point.clone());
            }
    }

    Ok(())
}

/// Display clustered hay to stdout.
pub fn display_clusters(result: &ClusterResult) {
    if result.clusters.is_empty() && result.unclustered.is_empty() {
        println!("no hay filed");
        return;
    }

    if !result.clusters.is_empty() {
        println!("── clusters (discovered) ──");
        println!();
        for cluster in &result.clusters {
            let sphere_note = cluster.sphere_point.as_ref()
                .map(|p| format!(" → would join sphere (point={})", p))
                .unwrap_or_default();
            println!("  {} ({} hay){}", cluster.theme, cluster.members.len(), sphere_note);
            for member in &cluster.members {
                println!("    ~ {}", member.straw);
            }
            println!();
        }
    }

    if !result.unclustered.is_empty() {
        println!("── unclustered ({} hay) ──", result.unclustered.len());
        for entry in &result.unclustered {
            println!("    ~ {}", entry.straw);
        }
        println!();
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn make_hay(straw: &str) -> HayEntry {
        HayEntry {
            straw: straw.to_string(),
            source: "test".to_string(),
            timestamp: "2026-03-15T00:00:00Z".to_string(),
        }
    }

    #[test]
    fn test_cluster_by_concepts_groups_shared_terms() {
        let entries = vec![
            make_hay("tui overlay rendering"),
            make_hay("tui input handling"),
            make_hay("tui help panel"),
            make_hay("something about auth completely different"),
        ];
        let result = cluster_by_concepts(&entries);
        // The three tui entries should cluster together
        assert_eq!(result.clusters.len(), 1);
        assert_eq!(result.clusters[0].members.len(), 3);
        assert_eq!(result.clusters[0].theme, "TUI");
        assert_eq!(result.unclustered.len(), 1);
    }

    #[test]
    fn test_cluster_by_concepts_multiple_clusters() {
        let entries = vec![
            make_hay("tui overlay rendering"),
            make_hay("tui input handling"),
            make_hay("kernel audit trail"),
            make_hay("kernel boot sequence"),
        ];
        let result = cluster_by_concepts(&entries);
        assert_eq!(result.clusters.len(), 2);
    }

    #[test]
    fn test_cluster_by_concepts_no_matches() {
        let entries = vec![
            make_hay("completely random thought"),
            make_hay("another unrelated idea"),
        ];
        let result = cluster_by_concepts(&entries);
        assert!(result.clusters.is_empty());
        assert_eq!(result.unclustered.len(), 2);
    }

    #[test]
    fn test_cluster_by_concepts_empty() {
        let result = cluster_by_concepts(&[]);
        assert!(result.clusters.is_empty());
        assert!(result.unclustered.is_empty());
    }

    #[test]
    fn test_cluster_single_entry_not_clustered() {
        let entries = vec![make_hay("tui thing")];
        let result = cluster_by_concepts(&entries);
        // Single entry can't form a cluster
        assert!(result.clusters.is_empty());
        assert_eq!(result.unclustered.len(), 1);
    }

    #[test]
    fn test_cluster_shared_multiple_concepts() {
        // Entries sharing different concepts should still cluster if connected
        let entries = vec![
            make_hay("tui agent dispatch"),
            make_hay("agent fleet spawn"),
            make_hay("tui console overlay"),
        ];
        let result = cluster_by_concepts(&entries);
        // "tui" links 0↔2, "agent" links 0↔1 → all connected → 1 cluster
        assert_eq!(result.clusters.len(), 1);
        assert_eq!(result.clusters[0].members.len(), 3);
    }

    #[test]
    fn test_derive_theme_picks_frequent_word() {
        let members = vec!["file safety guard", "file overwrite protection", "file exists check"];
        let theme = derive_theme("file safety guard", members.into_iter());
        assert_eq!(theme, "FILE");
    }

    #[test]
    fn test_read_hay_empty_audit() {
        let tmp = std::env::temp_dir().join("test_hay_empty");
        let _ = std::fs::create_dir_all(tmp.join(".ostk"));
        std::fs::write(tmp.join(".ostk/audit.jsonl"), "").unwrap();
        let result = read_hay(&tmp).unwrap();
        assert!(result.is_empty());
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_read_hay_parses_entries() {
        let tmp = std::env::temp_dir().join("test_hay_parse");
        let _ = std::fs::create_dir_all(tmp.join(".ostk"));
        let audit = r#"{"event":"hay.filed","straw":"test idea","source":"cli","timestamp":"2026-03-15T00:00:00Z"}
{"event":"other.event","data":"ignored"}
{"event":"hay.filed","straw":"another thought","source":"cli","timestamp":"2026-03-15T01:00:00Z"}"#;
        std::fs::write(tmp.join(".ostk/audit.jsonl"), audit).unwrap();
        let result = read_hay(&tmp).unwrap();
        assert_eq!(result.len(), 2);
        assert_eq!(result[0].straw, "test idea");
        assert_eq!(result[1].straw, "another thought");
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
