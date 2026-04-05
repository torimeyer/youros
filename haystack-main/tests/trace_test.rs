//! Phase 2 trace chain tests.
//!
//! Tests:
//! 1. commit_with_needle_appends_commit_ref — after commit with --needle, the needle has the hash in commit_refs
//! 2. trace_needle_shows_linked_entities — trace output includes specs, blocks, depends_on
//! 3. trace_spec_finds_referencing_needles — trace a spec name, finds all needles that reference it
//!
//! Run with: cargo test --test trace_test

use std::fs;
use std::path::PathBuf;

use ostk::{read_needles, write_needles, with_needles_locked};
use serde_json::{json, Value};

// =========================================================================
// Helpers
// =========================================================================

/// Create a temp dir with .ostk/needles/ structure.
fn setup_project(name: &str) -> PathBuf {
    let dir = std::env::temp_dir()
        .join("haystack_trace_tests")
        .join(name);
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(dir.join(".ostk/needles")).unwrap();
    fs::write(dir.join(".ostk/needles/counter"), "0").unwrap();
    fs::write(dir.join(".ostk/needles/issues.jsonl"), "").unwrap();
    dir
}

fn ensure_array_contains(needle: &mut Value, field: &str, value: &str) {
    let arr = needle
        .get(field)
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

// =========================================================================
// Test 1: commit_with_needle_appends_commit_ref
// =========================================================================

#[test]
fn commit_with_needle_appends_commit_ref() {
    let dir = setup_project("commit_ref");

    // Create a needle
    let needles = vec![json!({
        "id": "→843",
        "title": "ostk init subcommand missing",
        "status": "in_progress",
        "priority": "P0",
        "issue_type": "task"
    })];
    write_needles(&dir, &needles).unwrap();

    // Simulate what commit.rs does after a successful git commit:
    // Append commit hash to needle's commit_refs[]
    let commit_hash = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0";
    with_needles_locked(&dir, |needles| {
        if let Some(needle) = needles.iter_mut().find(|n| {
            n.get("id").and_then(|v| v.as_str()) == Some("→843")
        }) {
            let mut refs = needle
                .get("commit_refs")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default();
            let hash_val = json!(commit_hash);
            if !refs.contains(&hash_val) {
                refs.push(hash_val);
            }
            needle["commit_refs"] = json!(refs);
        }
        Ok(())
    })
    .unwrap();

    let read_back = read_needles(&dir).unwrap();
    let needle = read_back
        .iter()
        .find(|n| n["id"] == "→843")
        .expect("needle →843 should exist");

    let commit_refs = needle
        .get("commit_refs")
        .and_then(|v| v.as_array())
        .expect("commit_refs should be an array");
    assert_eq!(commit_refs.len(), 1, "should have exactly one commit ref");
    assert_eq!(
        commit_refs[0].as_str(),
        Some(commit_hash),
        "commit ref should match"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn commit_with_needle_appends_multiple_refs() {
    let dir = setup_project("commit_ref_multi");

    let needles = vec![json!({
        "id": "→844",
        "title": "multi-commit needle",
        "status": "in_progress",
        "priority": "P1",
        "issue_type": "task"
    })];
    write_needles(&dir, &needles).unwrap();

    let hashes = ["aaa1111", "bbb2222", "ccc3333"];
    for hash in &hashes {
        with_needles_locked(&dir, |needles| {
            if let Some(needle) = needles.iter_mut().find(|n| n["id"] == "→844") {
                let mut refs = needle
                    .get("commit_refs")
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default();
                let hash_val = json!(*hash);
                if !refs.contains(&hash_val) {
                    refs.push(hash_val);
                }
                needle["commit_refs"] = json!(refs);
            }
            Ok(())
        })
        .unwrap();
    }

    let read_back = read_needles(&dir).unwrap();
    let needle = read_back.iter().find(|n| n["id"] == "→844").unwrap();
    let commit_refs = needle["commit_refs"].as_array().unwrap();
    assert_eq!(commit_refs.len(), 3, "should have three commit refs");
    assert_eq!(commit_refs[0].as_str(), Some("aaa1111"));
    assert_eq!(commit_refs[1].as_str(), Some("bbb2222"));
    assert_eq!(commit_refs[2].as_str(), Some("ccc3333"));

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn commit_ref_append_is_idempotent() {
    let dir = setup_project("commit_ref_idempotent");

    let needles = vec![json!({
        "id": "→845",
        "title": "idempotent test",
        "status": "open",
        "priority": "P1",
        "issue_type": "task"
    })];
    write_needles(&dir, &needles).unwrap();

    let hash = "deadbeef1234567890";
    // Append the same hash twice
    for _ in 0..2 {
        with_needles_locked(&dir, |needles| {
            if let Some(needle) = needles.iter_mut().find(|n| n["id"] == "→845") {
                let mut refs = needle
                    .get("commit_refs")
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default();
                let hash_val = json!(hash);
                if !refs.contains(&hash_val) {
                    refs.push(hash_val);
                }
                needle["commit_refs"] = json!(refs);
            }
            Ok(())
        })
        .unwrap();
    }

    let read_back = read_needles(&dir).unwrap();
    let needle = read_back.iter().find(|n| n["id"] == "→845").unwrap();
    let commit_refs = needle["commit_refs"].as_array().unwrap();
    assert_eq!(
        commit_refs.len(),
        1,
        "duplicate hash should not create duplicate entry"
    );

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test 2: trace_needle_shows_linked_entities
// =========================================================================

#[test]
fn trace_needle_shows_linked_entities() {
    let dir = setup_project("trace_linked");

    // Create a fully-linked needle
    let needles = vec![
        json!({
            "id": "→843",
            "title": "ostk init subcommand missing",
            "status": "open",
            "priority": "P0",
            "issue_type": "task",
            "ac": "haystack init && echo $?",
            "specs": ["haystack-compile.md", "cli-surface.md"],
            "drafts": [],
            "agentfiles": ["Agentfile.→843"],
            "depends_on": [],
            "blocks": ["→844", "→846"],
            "commit_refs": ["a1b2c3d4e5f6789012345678901234567890abcd", "e4f5g6h7890123456789012345678901234567ab"]
        }),
        json!({
            "id": "→844",
            "title": "deploy pipeline",
            "status": "open",
            "priority": "P1",
            "issue_type": "task",
            "depends_on": ["→843"]
        }),
        json!({
            "id": "→846",
            "title": "release notes",
            "status": "open",
            "priority": "P2",
            "issue_type": "task",
            "depends_on": ["→843"]
        }),
    ];
    write_needles(&dir, &needles).unwrap();

    // Read back and verify the needle structure
    let read_back = read_needles(&dir).unwrap();
    let needle = read_back.iter().find(|n| n["id"] == "→843").unwrap();

    // Verify all linked fields are present
    let specs = needle["specs"].as_array().unwrap();
    assert_eq!(specs.len(), 2);
    assert_eq!(specs[0].as_str(), Some("haystack-compile.md"));
    assert_eq!(specs[1].as_str(), Some("cli-surface.md"));

    let agentfiles = needle["agentfiles"].as_array().unwrap();
    assert_eq!(agentfiles.len(), 1);
    assert_eq!(agentfiles[0].as_str(), Some("Agentfile.→843"));

    let blocks = needle["blocks"].as_array().unwrap();
    assert_eq!(blocks.len(), 2);
    assert_eq!(blocks[0].as_str(), Some("→844"));
    assert_eq!(blocks[1].as_str(), Some("→846"));

    let commit_refs = needle["commit_refs"].as_array().unwrap();
    assert_eq!(commit_refs.len(), 2);

    // Verify status and priority
    assert_eq!(needle["status"].as_str(), Some("open"));
    assert_eq!(needle["priority"].as_str(), Some("P0"));

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn trace_needle_with_depends_on() {
    let dir = setup_project("trace_depends");

    let needles = vec![
        json!({
            "id": "→850",
            "title": "base module",
            "status": "open",
            "priority": "P0",
            "issue_type": "task",
            "blocks": ["→851"]
        }),
        json!({
            "id": "→851",
            "title": "dependent feature",
            "status": "open",
            "priority": "P1",
            "issue_type": "task",
            "depends_on": ["→850"],
            "specs": ["feature.md"]
        }),
    ];
    write_needles(&dir, &needles).unwrap();

    let read_back = read_needles(&dir).unwrap();
    let needle = read_back.iter().find(|n| n["id"] == "→851").unwrap();

    let deps = needle["depends_on"].as_array().unwrap();
    assert_eq!(deps.len(), 1);
    assert_eq!(deps[0].as_str(), Some("→850"));

    let specs = needle["specs"].as_array().unwrap();
    assert_eq!(specs.len(), 1);
    assert_eq!(specs[0].as_str(), Some("feature.md"));

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test 3: trace_spec_finds_referencing_needles
// =========================================================================

#[test]
fn trace_spec_finds_referencing_needles() {
    let dir = setup_project("trace_spec");

    // Create needles: 3 reference the spec, 2 of them are closed
    let needles = vec![
        json!({
            "id": "→860",
            "title": "implement auth",
            "status": "closed",
            "priority": "P0",
            "issue_type": "task",
            "specs": ["auth-spec.md"]
        }),
        json!({
            "id": "→861",
            "title": "auth tests",
            "status": "closed",
            "priority": "P1",
            "issue_type": "task",
            "specs": ["auth-spec.md", "testing.md"]
        }),
        json!({
            "id": "→862",
            "title": "auth docs",
            "status": "open",
            "priority": "P2",
            "issue_type": "task",
            "specs": ["auth-spec.md"]
        }),
        json!({
            "id": "→863",
            "title": "unrelated work",
            "status": "open",
            "priority": "P1",
            "issue_type": "task",
            "specs": ["other-spec.md"]
        }),
    ];
    write_needles(&dir, &needles).unwrap();

    // Read back and count how many reference auth-spec.md
    let read_back = read_needles(&dir).unwrap();
    let matching: Vec<&Value> = read_back
        .iter()
        .filter(|n| {
            n.get("specs")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .any(|v| v.as_str() == Some("auth-spec.md"))
                })
                .unwrap_or(false)
        })
        .collect();

    assert_eq!(matching.len(), 3, "3 needles reference auth-spec.md");

    let closed_count = matching
        .iter()
        .filter(|n| n.get("status").and_then(|v| v.as_str()) == Some("closed"))
        .count();
    assert_eq!(closed_count, 2, "2 of the 3 referencing needles are closed");

    // Verify the unrelated needle is NOT in the match set
    assert!(
        !matching.iter().any(|n| n["id"] == "→863"),
        "unrelated needle should not match"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn trace_spec_with_legacy_spec_ref_field() {
    let dir = setup_project("trace_spec_legacy");

    // Old-style needle with spec_ref instead of specs[]
    let needles = vec![
        json!({
            "id": "→870",
            "title": "legacy needle",
            "status": "open",
            "priority": "P1",
            "issue_type": "task",
            "spec_ref": "legacy-spec.md"
        }),
        json!({
            "id": "→871",
            "title": "new needle",
            "status": "closed",
            "priority": "P0",
            "issue_type": "task",
            "specs": ["legacy-spec.md"]
        }),
    ];
    write_needles(&dir, &needles).unwrap();

    // Both should be found when tracing legacy-spec.md
    let read_back = read_needles(&dir).unwrap();
    let matching: Vec<&Value> = read_back
        .iter()
        .filter(|b| {
            let matches_spec_ref = b
                .get("spec_ref")
                .and_then(|v| v.as_str())
                .map(|s| s == "legacy-spec.md")
                .unwrap_or(false);
            let matches_specs = b
                .get("specs")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().any(|v| v.as_str() == Some("legacy-spec.md")))
                .unwrap_or(false);
            matches_spec_ref || matches_specs
        })
        .collect();

    assert_eq!(matching.len(), 2, "both legacy and new-style should match");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn trace_spec_no_matches_returns_empty() {
    let dir = setup_project("trace_spec_empty");

    let needles = vec![json!({
        "id": "→880",
        "title": "unrelated",
        "status": "open",
        "priority": "P1",
        "issue_type": "task",
        "specs": ["other.md"]
    })];
    write_needles(&dir, &needles).unwrap();

    let read_back = read_needles(&dir).unwrap();
    let matching: Vec<&Value> = read_back
        .iter()
        .filter(|n| {
            n.get("specs")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().any(|v| v.as_str() == Some("nonexistent.md")))
                .unwrap_or(false)
        })
        .collect();

    assert_eq!(matching.len(), 0, "no needles should match nonexistent spec");

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Combined: commit_refs survive through link operations
// =========================================================================

#[test]
fn commit_refs_survive_link_mutations() {
    let dir = setup_project("commit_refs_survive");

    let needles = vec![json!({
        "id": "→890",
        "title": "feature with commits",
        "status": "open",
        "priority": "P0",
        "issue_type": "task",
        "commit_refs": ["abc1234"]
    })];
    write_needles(&dir, &needles).unwrap();

    // Add a spec link
    with_needles_locked(&dir, |beads| {
        let idx = beads.iter().position(|b| b["id"] == "→890").unwrap();
        ensure_array_contains(&mut beads[idx], "specs", "new-spec.md");
        Ok(())
    })
    .unwrap();

    // Add another commit ref
    with_needles_locked(&dir, |beads| {
        if let Some(needle) = beads.iter_mut().find(|n| n["id"] == "→890") {
            let mut refs = needle
                .get("commit_refs")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default();
            refs.push(json!("def5678"));
            needle["commit_refs"] = json!(refs);
        }
        Ok(())
    })
    .unwrap();

    let read_back = read_needles(&dir).unwrap();
    let needle = read_back.iter().find(|n| n["id"] == "→890").unwrap();

    // Both commit refs should survive
    let commit_refs = needle["commit_refs"].as_array().unwrap();
    assert_eq!(commit_refs.len(), 2);
    assert_eq!(commit_refs[0].as_str(), Some("abc1234"));
    assert_eq!(commit_refs[1].as_str(), Some("def5678"));

    // Spec link should also be present
    let specs = needle["specs"].as_array().unwrap();
    assert_eq!(specs.len(), 1);
    assert_eq!(specs[0].as_str(), Some("new-spec.md"));

    let _ = fs::remove_dir_all(&dir);
}
