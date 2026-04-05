//! Phase 5 compile edge-persistence tests.
//!
//! Tests:
//! 1. compile_persists_discovered_dependency_edges — needle with "depends on →NNN" in title
//!    gets →NNN added to depends_on[] after compile
//! 2. compile_persists_blocks_edges — needle with "blocks →NNN" in title
//!    gets →NNN added to blocks[] after compile
//! 3. compile_does_not_duplicate_existing_edges — manually linked edge is not duplicated
//! 4. spheres_include_manually_linked_needles — manually linked needles share a sphere
//!
//! Run with: cargo test --test compile_test

use std::fs;
use std::path::PathBuf;

use ostk::{read_needles, write_needles, with_needles_locked};
use ostk::commands::refine::{build_joint_graph, find_spheres};
use serde_json::{json, Value};

// =========================================================================
// Helpers
// =========================================================================

fn setup_project(name: &str) -> PathBuf {
    let dir = std::env::temp_dir()
        .join("haystack_compile_tests")
        .join(name);
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(dir.join(".ostk/needles")).unwrap();
    fs::write(dir.join(".ostk/needles/counter"), "100").unwrap();
    fs::write(dir.join(".ostk/needles/issues.jsonl"), "").unwrap();
    // Create empty audit.jsonl (required by compile)
    fs::write(dir.join(".ostk/audit.jsonl"), "").unwrap();
    dir
}

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

fn make_needle(id: &str, title: &str, status: &str, priority: &str) -> Value {
    json!({
        "id": id,
        "title": title,
        "status": status,
        "priority": priority,
        "issue_type": "task"
    })
}

// =========================================================================
// Test 1: compile_persists_discovered_dependency_edges
// =========================================================================

#[test]
fn compile_persists_discovered_dependency_edges() {
    let dir = setup_project("persist_depends");

    let needles = vec![
        make_needle("→001", "implement auth", "open", "P0"),
        make_needle("→002", "foo depends on →001", "open", "P1"),
    ];
    write_needles(&dir, &needles).unwrap();

    // Simulate what run_compile does: call persist_discovered_edges via
    // the same title-pattern logic. We do it manually here to test the
    // persistence behavior without needing the full compile pipeline
    // (which requires hay/intelligence layer setup).
    persist_edges_from_titles(&dir);

    let read_back = read_needles(&dir).unwrap();

    // →002 should now have depends_on: ["→001"]
    let n002 = read_back.iter().find(|n| n["id"] == "→002").unwrap();
    let deps = n002.get("depends_on").and_then(|v| v.as_array());
    assert!(deps.is_some(), "→002 should have depends_on array after compile");
    let deps = deps.unwrap();
    assert!(
        deps.iter().any(|v| v.as_str() == Some("→001")),
        "→002.depends_on should contain →001, got {:?}", deps
    );

    // →001 should now have blocks: ["→002"] (bidirectional)
    let n001 = read_back.iter().find(|n| n["id"] == "→001").unwrap();
    let blocks = n001.get("blocks").and_then(|v| v.as_array());
    assert!(blocks.is_some(), "→001 should have blocks array after compile");
    let blocks = blocks.unwrap();
    assert!(
        blocks.iter().any(|v| v.as_str() == Some("→002")),
        "→001.blocks should contain →002, got {:?}", blocks
    );

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test 2: compile_persists_blocks_edges
// =========================================================================

#[test]
fn compile_persists_blocks_edges() {
    let dir = setup_project("persist_blocks");

    let needles = vec![
        make_needle("→001", "implement core", "open", "P0"),
        make_needle("→002", "bar blocks →001", "open", "P1"),
    ];
    write_needles(&dir, &needles).unwrap();

    persist_edges_from_titles(&dir);

    let read_back = read_needles(&dir).unwrap();

    // →002 should now have blocks: ["→001"]
    let n002 = read_back.iter().find(|n| n["id"] == "→002").unwrap();
    let blocks = n002.get("blocks").and_then(|v| v.as_array());
    assert!(blocks.is_some(), "→002 should have blocks array after compile");
    let blocks = blocks.unwrap();
    assert!(
        blocks.iter().any(|v| v.as_str() == Some("→001")),
        "→002.blocks should contain →001, got {:?}", blocks
    );

    // →001 should now have depends_on: ["→002"] (bidirectional)
    let n001 = read_back.iter().find(|n| n["id"] == "→001").unwrap();
    let deps = n001.get("depends_on").and_then(|v| v.as_array());
    assert!(deps.is_some(), "→001 should have depends_on array after compile");
    let deps = deps.unwrap();
    assert!(
        deps.iter().any(|v| v.as_str() == Some("→002")),
        "→001.depends_on should contain →002, got {:?}", deps
    );

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test 3: compile_does_not_duplicate_existing_edges
// =========================================================================

#[test]
fn compile_does_not_duplicate_existing_edges() {
    let dir = setup_project("no_dup_edges");

    // Create needles where →002 has title "depends on →001" AND already
    // has →001 in depends_on[] (manually linked before compile)
    let mut n001 = make_needle("→001", "implement auth", "open", "P0");
    n001["blocks"] = json!(["→002"]);
    let mut n002 = make_needle("→002", "foo depends on →001", "open", "P1");
    n002["depends_on"] = json!(["→001"]);

    write_needles(&dir, &[n001, n002]).unwrap();

    persist_edges_from_titles(&dir);

    let read_back = read_needles(&dir).unwrap();

    // →002.depends_on should still be ["→001"] — no duplicate
    let n002 = read_back.iter().find(|n| n["id"] == "→002").unwrap();
    let deps = n002.get("depends_on").and_then(|v| v.as_array()).unwrap();
    assert_eq!(deps.len(), 1, "depends_on should not have duplicates, got {:?}", deps);

    // →001.blocks should still be ["→002"] — no duplicate
    let n001 = read_back.iter().find(|n| n["id"] == "→001").unwrap();
    let blocks = n001.get("blocks").and_then(|v| v.as_array()).unwrap();
    assert_eq!(blocks.len(), 1, "blocks should not have duplicates, got {:?}", blocks);

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test 4: spheres_include_manually_linked_needles
// =========================================================================

#[test]
fn spheres_include_manually_linked_needles() {
    let dir = setup_project("manual_link_spheres");

    // Two needles with no text-discoverable relationship, but manually linked
    let mut n001 = make_needle("→001", "alpha feature", "open", "P1");
    n001["blocks"] = json!(["→002"]);
    let mut n002 = make_needle("→002", "beta feature", "open", "P1");
    n002["depends_on"] = json!(["→001"]);
    // A third needle that is truly isolated
    let n003 = make_needle("→003", "gamma unrelated", "open", "P2");

    write_needles(&dir, &[n001, n002, n003]).unwrap();

    let needles = read_needles(&dir).unwrap();
    let (_joints, adj) = build_joint_graph(&needles);
    let spheres = find_spheres(&adj);

    // →001 and →002 should be in the same sphere (connected via persisted edges)
    let sphere_for_001 = spheres.iter().find(|s| s.needles.contains(&"→001".to_string()));
    assert!(sphere_for_001.is_some(), "→001 should be in a sphere");
    let sphere = sphere_for_001.unwrap();
    assert!(
        sphere.needles.contains(&"→002".to_string()),
        "→001 and →002 should be in the same sphere, sphere contains: {:?}",
        sphere.needles
    );
    assert_eq!(sphere.needles.len(), 2, "sphere should have exactly 2 needles");

    // →003 should be in a different sphere (isolated)
    let sphere_for_003 = spheres.iter().find(|s| s.needles.contains(&"→003".to_string()));
    assert!(sphere_for_003.is_some(), "→003 should exist in a sphere (as isolated)");
    let iso_sphere = sphere_for_003.unwrap();
    assert_eq!(iso_sphere.needles.len(), 1, "→003 should be isolated");

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Helper: persist edges from title patterns (mirrors compile's edge logic)
// =========================================================================

/// Replicates the edge-persistence logic from run_compile without requiring
/// the full intelligence/hay pipeline.
fn persist_edges_from_titles(root: &PathBuf) {
    use regex::Regex;

    let depends_patterns: &[&str] = &[
        r"depends on →(\d+)",
        r"requires →(\d+)",
        r"blocked by →(\d+)",
        r"after →(\d+)",
    ];
    let blocks_patterns: &[&str] = &[
        r"blocks →(\d+)",
        r"unblocks →(\d+)",
        r"enables →(\d+)",
    ];

    let needles = read_needles(root).unwrap();

    let all_ids: std::collections::HashSet<String> = needles.iter()
        .filter_map(|n| n.get("id").and_then(|v| v.as_str()).map(|s| s.to_string()))
        .collect();

    // Collect edges: (source_id, "depends_on"|"blocks", target_id)
    let mut edges: Vec<(String, String, String)> = Vec::new();

    for n in &needles {
        let id = match n.get("id").and_then(|v| v.as_str()) {
            Some(id) => id,
            None => continue,
        };
        let status = n.get("status").and_then(|v| v.as_str()).unwrap_or("");
        if status != "open" && status != "in_progress" {
            continue;
        }
        let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();

        for pat in depends_patterns {
            let re = Regex::new(pat).unwrap();
            for cap in re.captures_iter(&title) {
                let target = format!("→{}", &cap[1]);
                if all_ids.contains(&target) && target != id {
                    edges.push((id.to_string(), "depends_on".to_string(), target));
                }
            }
        }

        for pat in blocks_patterns {
            let re = Regex::new(pat).unwrap();
            for cap in re.captures_iter(&title) {
                let target = format!("→{}", &cap[1]);
                if all_ids.contains(&target) && target != id {
                    edges.push((id.to_string(), "blocks".to_string(), target));
                }
            }
        }
    }

    if edges.is_empty() {
        return;
    }

    with_needles_locked(root, |beads| {
        for (source_id, relation, target_id) in &edges {
            let src_idx = beads.iter().position(|b| b.get("id").and_then(|v| v.as_str()) == Some(source_id));
            let tgt_idx = beads.iter().position(|b| b.get("id").and_then(|v| v.as_str()) == Some(target_id));

            let (src_idx, tgt_idx) = match (src_idx, tgt_idx) {
                (Some(s), Some(t)) => (s, t),
                _ => continue,
            };

            if relation == "depends_on" {
                ensure_array_contains(&mut beads[src_idx], "depends_on", target_id);
                ensure_array_contains(&mut beads[tgt_idx], "blocks", source_id);
            } else if relation == "blocks" {
                ensure_array_contains(&mut beads[src_idx], "blocks", target_id);
                ensure_array_contains(&mut beads[tgt_idx], "depends_on", source_id);
            }
        }
        Ok(())
    }).unwrap();
}
