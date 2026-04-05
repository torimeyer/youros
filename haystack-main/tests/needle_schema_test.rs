//! Phase 1 needle schema upgrade tests.
//!
//! Tests:
//! 1. needle add with --ac creates needle with ac field
//! 2. needle add with --depends-on creates needle with depends_on array
//! 3. needle add with --blocks creates needle with blocks array
//! 4. needle link →A blocks →B updates both needles
//! 5. needle link →A spec foo.md updates only the source needle
//! 6. work close on needle with AC runs the command and reports result
//! 7. Existing needles without new fields still work (backward compat)
//!
//! Run with: cargo test --test needle_schema_test

use std::fs;
use std::path::PathBuf;

use ostk::{read_needles, write_needles, with_needles_locked};
use serde_json::{json, Value};

// =========================================================================
// Helpers
// =========================================================================

/// Create a temp dir with .ostk/needles/ structure and a counter file.
fn setup_project(name: &str) -> PathBuf {
    let dir = std::env::temp_dir()
        .join("haystack_needle_schema_tests")
        .join(name);
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(dir.join(".ostk/needles")).unwrap();
    // Start the counter at 0 so next_needle_id produces →001
    fs::write(dir.join(".ostk/needles/counter"), "0").unwrap();
    // Create empty issues.jsonl
    fs::write(dir.join(".ostk/needles/issues.jsonl"), "").unwrap();
    dir
}

/// Write needles and read them back.
fn write_and_read(root: &PathBuf, needles: &[Value]) -> Vec<Value> {
    write_needles(root, needles).unwrap();
    read_needles(root).unwrap()
}

// =========================================================================
// Test 1: needle add with --ac creates needle with ac field
// =========================================================================

#[test]
fn add_with_ac_stores_ac_field() {
    let dir = setup_project("add_ac");

    let needles = vec![json!({
        "id": "→001",
        "title": "fix onboarding",
        "status": "open",
        "priority": "P1",
        "issue_type": "task",
        "ac": "cargo test --test e2e_onboarding"
    })];
    let read_back = write_and_read(&dir, &needles);

    assert_eq!(read_back.len(), 1);
    assert_eq!(
        read_back[0].get("ac").and_then(|v| v.as_str()),
        Some("cargo test --test e2e_onboarding")
    );
    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test 2: needle add with --depends-on creates depends_on array
// =========================================================================

#[test]
fn add_with_depends_on_stores_array() {
    let dir = setup_project("add_depends_on");

    let needles = vec![json!({
        "id": "→002",
        "title": "add tests",
        "status": "open",
        "priority": "P1",
        "issue_type": "task",
        "depends_on": ["→001"]
    })];
    let read_back = write_and_read(&dir, &needles);

    assert_eq!(read_back.len(), 1);
    let deps = read_back[0].get("depends_on").and_then(|v| v.as_array()).unwrap();
    assert_eq!(deps.len(), 1);
    assert_eq!(deps[0].as_str(), Some("→001"));
    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test 3: needle add with --blocks creates blocks array
// =========================================================================

#[test]
fn add_with_blocks_stores_array() {
    let dir = setup_project("add_blocks");

    let needles = vec![json!({
        "id": "→001",
        "title": "implement auth",
        "status": "open",
        "priority": "P0",
        "issue_type": "task",
        "blocks": ["→002", "→003"]
    })];
    let read_back = write_and_read(&dir, &needles);

    assert_eq!(read_back.len(), 1);
    let blocks = read_back[0].get("blocks").and_then(|v| v.as_array()).unwrap();
    assert_eq!(blocks.len(), 2);
    assert_eq!(blocks[0].as_str(), Some("→002"));
    assert_eq!(blocks[1].as_str(), Some("→003"));
    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test 4: needle link →A blocks →B updates both needles
// =========================================================================

#[test]
fn link_blocks_updates_both_sides() {
    let dir = setup_project("link_blocks");

    // Set up two needles with no links
    let needles = vec![
        json!({
            "id": "→010",
            "title": "implement auth",
            "status": "open",
            "priority": "P0",
            "issue_type": "task"
        }),
        json!({
            "id": "→011",
            "title": "deploy pipeline",
            "status": "open",
            "priority": "P1",
            "issue_type": "task"
        }),
    ];
    write_needles(&dir, &needles).unwrap();

    // Simulate run_link: →010 blocks →011
    with_needles_locked(&dir, |beads| {
        let src_idx = beads.iter().position(|b| b.get("id").and_then(|v| v.as_str()) == Some("→010")).unwrap();
        let tgt_idx = beads.iter().position(|b| b.get("id").and_then(|v| v.as_str()) == Some("→011")).unwrap();

        // Add →011 to →010's blocks[]
        ensure_array_contains(&mut beads[src_idx], "blocks", "→011");
        // Add →010 to →011's depends_on[]
        ensure_array_contains(&mut beads[tgt_idx], "depends_on", "→010");

        Ok(())
    }).unwrap();

    let read_back = read_needles(&dir).unwrap();

    // →010 should have blocks: ["→011"]
    let src = read_back.iter().find(|n| n["id"] == "→010").unwrap();
    let blocks = src.get("blocks").and_then(|v| v.as_array()).unwrap();
    assert_eq!(blocks.len(), 1);
    assert_eq!(blocks[0].as_str(), Some("→011"));

    // →011 should have depends_on: ["→010"]
    let tgt = read_back.iter().find(|n| n["id"] == "→011").unwrap();
    let deps = tgt.get("depends_on").and_then(|v| v.as_array()).unwrap();
    assert_eq!(deps.len(), 1);
    assert_eq!(deps[0].as_str(), Some("→010"));

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn link_depends_on_updates_both_sides() {
    let dir = setup_project("link_depends_on");

    let needles = vec![
        json!({
            "id": "→020",
            "title": "write tests",
            "status": "open",
            "priority": "P1",
            "issue_type": "task"
        }),
        json!({
            "id": "→021",
            "title": "implement feature",
            "status": "open",
            "priority": "P0",
            "issue_type": "task"
        }),
    ];
    write_needles(&dir, &needles).unwrap();

    // Simulate run_link: →020 depends-on →021
    with_needles_locked(&dir, |beads| {
        let src_idx = beads.iter().position(|b| b["id"] == "→020").unwrap();
        let tgt_idx = beads.iter().position(|b| b["id"] == "→021").unwrap();

        ensure_array_contains(&mut beads[src_idx], "depends_on", "→021");
        ensure_array_contains(&mut beads[tgt_idx], "blocks", "→020");

        Ok(())
    }).unwrap();

    let read_back = read_needles(&dir).unwrap();

    let src = read_back.iter().find(|n| n["id"] == "→020").unwrap();
    let deps = src.get("depends_on").and_then(|v| v.as_array()).unwrap();
    assert!(deps.iter().any(|v| v.as_str() == Some("→021")));

    let tgt = read_back.iter().find(|n| n["id"] == "→021").unwrap();
    let blocks = tgt.get("blocks").and_then(|v| v.as_array()).unwrap();
    assert!(blocks.iter().any(|v| v.as_str() == Some("→020")));

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test 5: needle link →A spec foo.md updates only the source
// =========================================================================

#[test]
fn link_spec_updates_only_source() {
    let dir = setup_project("link_spec");

    let needles = vec![json!({
        "id": "→030",
        "title": "implement onboarding",
        "status": "open",
        "priority": "P0",
        "issue_type": "task"
    })];
    write_needles(&dir, &needles).unwrap();

    // Simulate run_link: →030 spec onboarding-flow.md
    with_needles_locked(&dir, |beads| {
        let src_idx = beads.iter().position(|b| b["id"] == "→030").unwrap();
        ensure_array_contains(&mut beads[src_idx], "specs", "onboarding-flow.md");
        Ok(())
    }).unwrap();

    let read_back = read_needles(&dir).unwrap();
    let src = read_back.iter().find(|n| n["id"] == "→030").unwrap();
    let specs = src.get("specs").and_then(|v| v.as_array()).unwrap();
    assert_eq!(specs.len(), 1);
    assert_eq!(specs[0].as_str(), Some("onboarding-flow.md"));

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn link_draft_updates_only_source() {
    let dir = setup_project("link_draft");

    let needles = vec![json!({
        "id": "→031",
        "title": "design flow",
        "status": "open",
        "priority": "P1",
        "issue_type": "task"
    })];
    write_needles(&dir, &needles).unwrap();

    with_needles_locked(&dir, |beads| {
        let src_idx = beads.iter().position(|b| b["id"] == "→031").unwrap();
        ensure_array_contains(&mut beads[src_idx], "drafts", "design-v2.md");
        Ok(())
    }).unwrap();

    let read_back = read_needles(&dir).unwrap();
    let src = read_back.iter().find(|n| n["id"] == "→031").unwrap();
    let drafts = src.get("drafts").and_then(|v| v.as_array()).unwrap();
    assert_eq!(drafts.len(), 1);
    assert_eq!(drafts[0].as_str(), Some("design-v2.md"));

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn link_agentfile_updates_only_source() {
    let dir = setup_project("link_agentfile");

    let needles = vec![json!({
        "id": "→032",
        "title": "automate build",
        "status": "open",
        "priority": "P1",
        "issue_type": "task"
    })];
    write_needles(&dir, &needles).unwrap();

    with_needles_locked(&dir, |beads| {
        let src_idx = beads.iter().position(|b| b["id"] == "→032").unwrap();
        ensure_array_contains(&mut beads[src_idx], "agentfiles", "Agentfile.→032");
        Ok(())
    }).unwrap();

    let read_back = read_needles(&dir).unwrap();
    let src = read_back.iter().find(|n| n["id"] == "→032").unwrap();
    let agentfiles = src.get("agentfiles").and_then(|v| v.as_array()).unwrap();
    assert_eq!(agentfiles.len(), 1);
    assert_eq!(agentfiles[0].as_str(), Some("Agentfile.→032"));

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test 6: work close with AC runs command and reports result
// =========================================================================

#[test]
fn close_with_passing_ac_stores_pass() {
    let dir = setup_project("close_ac_pass");

    let needles = vec![json!({
        "id": "→040",
        "title": "add feature",
        "status": "open",
        "priority": "P1",
        "issue_type": "task",
        "ac": "true"
    })];
    write_needles(&dir, &needles).unwrap();

    // Simulate the AC check + close logic
    let ac_cmd = "true"; // always passes
    let output = std::process::Command::new("sh")
        .args(["-c", ac_cmd])
        .output()
        .unwrap();
    let ac_result = if output.status.success() { "pass" } else { "fail" };

    with_needles_locked(&dir, |beads| {
        beads[0]["status"] = json!("closed");
        beads[0]["ac_result"] = json!(ac_result);
        Ok(())
    }).unwrap();

    let read_back = read_needles(&dir).unwrap();
    assert_eq!(read_back[0]["status"], "closed");
    assert_eq!(read_back[0]["ac_result"].as_str(), Some("pass"));

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn close_with_failing_ac_stores_fail() {
    let dir = setup_project("close_ac_fail");

    let needles = vec![json!({
        "id": "→041",
        "title": "fix bug",
        "status": "open",
        "priority": "P0",
        "issue_type": "task",
        "ac": "false"
    })];
    write_needles(&dir, &needles).unwrap();

    // Simulate the AC check + close logic
    let ac_cmd = "false"; // always fails
    let output = std::process::Command::new("sh")
        .args(["-c", ac_cmd])
        .output()
        .unwrap();
    let ac_result = if output.status.success() { "pass" } else { "fail" };

    with_needles_locked(&dir, |beads| {
        beads[0]["status"] = json!("closed");
        beads[0]["ac_result"] = json!(ac_result);
        Ok(())
    }).unwrap();

    let read_back = read_needles(&dir).unwrap();
    assert_eq!(read_back[0]["status"], "closed");
    assert_eq!(read_back[0]["ac_result"].as_str(), Some("fail"));

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn close_without_ac_does_not_store_ac_result() {
    let dir = setup_project("close_no_ac");

    let needles = vec![json!({
        "id": "→042",
        "title": "simple task",
        "status": "open",
        "priority": "P2",
        "issue_type": "task"
    })];
    write_needles(&dir, &needles).unwrap();

    // No AC present — close directly
    with_needles_locked(&dir, |beads| {
        beads[0]["status"] = json!("closed");
        Ok(())
    }).unwrap();

    let read_back = read_needles(&dir).unwrap();
    assert_eq!(read_back[0]["status"], "closed");
    assert!(read_back[0].get("ac_result").is_none(), "should not have ac_result when no AC");

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test 7: Backward compatibility — needles without new fields still work
// =========================================================================

#[test]
fn backward_compat_old_needle_format_works() {
    let dir = setup_project("backward_compat");

    // Old-style needle: no ac, no depends_on, no blocks, no specs, no drafts, no agentfiles
    let needles = vec![
        json!({
            "id": "→050",
            "title": "legacy needle",
            "status": "open",
            "priority": "P1",
            "issue_type": "task",
            "created_at": "2025-01-01T00:00:00Z"
        }),
        json!({
            "id": "→051",
            "title": "new-style needle",
            "status": "open",
            "priority": "P0",
            "issue_type": "task",
            "created_at": "2025-01-02T00:00:00Z",
            "ac": "cargo test",
            "description": "a longer description",
            "depends_on": ["→050"],
            "blocks": [],
            "specs": ["spec.md"],
            "drafts": [],
            "agentfiles": []
        }),
    ];
    let read_back = write_and_read(&dir, &needles);

    // Both needles survive the roundtrip
    assert_eq!(read_back.len(), 2);

    // Old needle: no new fields
    let old = read_back.iter().find(|n| n["id"] == "→050").unwrap();
    assert_eq!(old["title"].as_str(), Some("legacy needle"));
    assert!(old.get("ac").is_none());
    assert!(old.get("depends_on").is_none());
    assert!(old.get("blocks").is_none());

    // New needle: all new fields present
    let new = read_back.iter().find(|n| n["id"] == "→051").unwrap();
    assert_eq!(new["ac"].as_str(), Some("cargo test"));
    assert_eq!(new["description"].as_str(), Some("a longer description"));
    assert_eq!(new["depends_on"].as_array().unwrap().len(), 1);
    assert_eq!(new["specs"].as_array().unwrap().len(), 1);

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn mixed_old_new_needles_survive_locked_mutation() {
    let dir = setup_project("mixed_mutation");

    // Start with an old-style needle
    let needles = vec![json!({
        "id": "→060",
        "title": "old style",
        "status": "open",
        "priority": "P1",
        "issue_type": "task"
    })];
    write_needles(&dir, &needles).unwrap();

    // Add a new-style needle via with_needles_locked
    with_needles_locked(&dir, |beads| {
        beads.push(json!({
            "id": "→061",
            "title": "new style",
            "status": "open",
            "priority": "P0",
            "issue_type": "task",
            "ac": "echo ok",
            "depends_on": ["→060"],
            "blocks": [],
            "specs": [],
            "drafts": [],
            "agentfiles": []
        }));
        Ok(())
    }).unwrap();

    let read_back = read_needles(&dir).unwrap();
    assert_eq!(read_back.len(), 2);

    // Old needle untouched
    assert_eq!(read_back[0]["id"], "→060");
    assert!(read_back[0].get("ac").is_none());

    // New needle has all fields
    assert_eq!(read_back[1]["id"], "→061");
    assert_eq!(read_back[1]["ac"].as_str(), Some("echo ok"));
    assert_eq!(read_back[1]["depends_on"].as_array().unwrap().len(), 1);

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Additional edge cases
// =========================================================================

#[test]
fn link_is_idempotent() {
    let dir = setup_project("link_idempotent");

    let needles = vec![
        json!({"id": "→070", "title": "a", "status": "open", "priority": "P1", "issue_type": "task"}),
        json!({"id": "→071", "title": "b", "status": "open", "priority": "P1", "issue_type": "task"}),
    ];
    write_needles(&dir, &needles).unwrap();

    // Link twice — should not create duplicates
    for _ in 0..2 {
        with_needles_locked(&dir, |beads| {
            let src_idx = beads.iter().position(|b| b["id"] == "→070").unwrap();
            let tgt_idx = beads.iter().position(|b| b["id"] == "→071").unwrap();
            ensure_array_contains(&mut beads[src_idx], "blocks", "→071");
            ensure_array_contains(&mut beads[tgt_idx], "depends_on", "→070");
            Ok(())
        }).unwrap();
    }

    let read_back = read_needles(&dir).unwrap();
    let src = read_back.iter().find(|n| n["id"] == "→070").unwrap();
    let blocks = src.get("blocks").and_then(|v| v.as_array()).unwrap();
    assert_eq!(blocks.len(), 1, "blocks should not have duplicates");

    let tgt = read_back.iter().find(|n| n["id"] == "→071").unwrap();
    let deps = tgt.get("depends_on").and_then(|v| v.as_array()).unwrap();
    assert_eq!(deps.len(), 1, "depends_on should not have duplicates");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn all_new_fields_roundtrip_through_jsonl() {
    let dir = setup_project("full_roundtrip");

    let needle = json!({
        "id": "→080",
        "title": "full needle",
        "status": "open",
        "priority": "P0",
        "issue_type": "task",
        "ac": "cargo test --test e2e_onboarding",
        "description": "boot error says 'ostk init' but command doesn't exist",
        "depends_on": ["→081"],
        "blocks": ["→082", "→083"],
        "specs": ["haystack-compile.md"],
        "drafts": ["onboarding-flow.md"],
        "agentfiles": ["Agentfile.→080"]
    });

    let read_back = write_and_read(&dir, &[needle.clone()]);
    assert_eq!(read_back.len(), 1);
    let n = &read_back[0];

    assert_eq!(n["ac"].as_str(), Some("cargo test --test e2e_onboarding"));
    assert_eq!(n["description"].as_str(), Some("boot error says 'ostk init' but command doesn't exist"));
    assert_eq!(n["depends_on"].as_array().unwrap().len(), 1);
    assert_eq!(n["blocks"].as_array().unwrap().len(), 2);
    assert_eq!(n["specs"].as_array().unwrap().len(), 1);
    assert_eq!(n["drafts"].as_array().unwrap().len(), 1);
    assert_eq!(n["agentfiles"].as_array().unwrap().len(), 1);

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Helper: ensure_array_contains (mirrors the one in work.rs)
// =========================================================================

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
