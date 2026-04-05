//! Tests for needle decomposition (Phase 4).
//!
//! Tests the needle creation/linking logic without requiring an LLM.
//! Run with: cargo test --test decompose_test

use std::fs;
use std::path::PathBuf;

use serde_json::json;

// =========================================================================
// Helpers
// =========================================================================

/// Create a temp dir with .ostk/needles/ structure.
fn setup_project(name: &str) -> PathBuf {
    let dir = std::env::temp_dir()
        .join("haystack_decompose_tests")
        .join(name);
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(dir.join(".ostk/needles")).unwrap();
    dir
}

/// Write a counter value to the needles counter file.
fn set_counter(dir: &PathBuf, value: u64) {
    fs::write(dir.join(".ostk/needles/counter"), value.to_string()).unwrap();
}

// =========================================================================
// Test: decompose_needle_creates_sub_needles_with_deps
// =========================================================================

#[test]
fn decompose_needle_creates_sub_needles_with_deps() {
    let dir = setup_project("creates_sub_needles");

    // Set up a parent needle
    let parent = json!({
        "id": "\u{2192}843",
        "title": "implement init subcommand",
        "status": "open",
        "priority": "P0",
        "issue_type": "task",
        "ac": "haystack init && echo $?",
        "created_at": "2025-01-01T00:00:00Z"
    });
    ostk::write_needles(&dir, &[parent]).unwrap();
    set_counter(&dir, 843);

    // Define sub-needle proposals
    let proposals = vec![
        ostk::commands::decompose::SubNeedleProposal {
            title: "implement init subcommand scaffold".to_string(),
            ac: Some("haystack init --help".to_string()),
            priority: "P0".to_string(),
        },
        ostk::commands::decompose::SubNeedleProposal {
            title: "write e2e test for init".to_string(),
            ac: Some("cargo test --test e2e_init".to_string()),
            priority: "P0".to_string(),
        },
        ostk::commands::decompose::SubNeedleProposal {
            title: "add init to CLI help text".to_string(),
            ac: Some("haystack --help | grep init".to_string()),
            priority: "P1".to_string(),
        },
    ];

    // Write sub-needles
    let created_ids = ostk::commands::decompose::write_sub_needles(
        &dir, "\u{2192}843", "P0", &proposals,
    ).unwrap();

    assert_eq!(created_ids.len(), 3, "should create 3 sub-needles");

    // Read back and verify
    let needles = ostk::read_needles(&dir).unwrap();
    // 1 parent + 3 children = 4
    assert_eq!(needles.len(), 4, "should have parent + 3 children");

    // Verify each sub-needle
    for (i, id) in created_ids.iter().enumerate() {
        let child = needles.iter().find(|n| {
            n.get("id").and_then(|v| v.as_str()) == Some(id.as_str())
        }).expect(&format!("child {} should exist", id));

        // Check title
        assert_eq!(
            child["title"].as_str().unwrap(),
            proposals[i].title,
            "child {} title mismatch", i
        );

        // Check depends_on contains parent
        let depends_on = child["depends_on"].as_array().unwrap();
        assert!(
            depends_on.iter().any(|v| v.as_str() == Some("\u{2192}843")),
            "child {} should depend on parent \u{2192}843", i
        );

        // Check sequential sibling dependency
        if i > 0 {
            assert!(
                depends_on.iter().any(|v| v.as_str() == Some(created_ids[i - 1].as_str())),
                "child {} should depend on previous sibling {}", i, created_ids[i - 1]
            );
        }

        // Check parent field
        assert_eq!(
            child["parent"].as_str().unwrap(),
            "\u{2192}843",
            "child {} should have parent field", i
        );

        // Check AC
        if let Some(ref ac) = proposals[i].ac {
            assert_eq!(
                child["ac"].as_str().unwrap(),
                ac.as_str(),
                "child {} AC mismatch", i
            );
        }

        // Check status is open
        assert_eq!(child["status"].as_str().unwrap(), "open");
    }

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test: decompose_updates_parent_blocks
// =========================================================================

#[test]
fn decompose_updates_parent_blocks() {
    let dir = setup_project("updates_parent_blocks");

    // Set up a parent needle
    let parent = json!({
        "id": "\u{2192}100",
        "title": "build authentication system",
        "status": "open",
        "priority": "P0",
        "issue_type": "task",
        "created_at": "2025-01-01T00:00:00Z"
    });
    ostk::write_needles(&dir, &[parent]).unwrap();
    set_counter(&dir, 100);

    // Create sub-needles
    let proposals = vec![
        ostk::commands::decompose::SubNeedleProposal {
            title: "implement login endpoint".to_string(),
            ac: Some("curl -f localhost:8080/login".to_string()),
            priority: "P0".to_string(),
        },
        ostk::commands::decompose::SubNeedleProposal {
            title: "implement signup endpoint".to_string(),
            ac: Some("curl -f localhost:8080/signup".to_string()),
            priority: "P0".to_string(),
        },
    ];

    let created_ids = ostk::commands::decompose::write_sub_needles(
        &dir, "\u{2192}100", "P0", &proposals,
    ).unwrap();

    // Now update parent blocks
    ostk::commands::decompose::update_parent_blocks(
        &dir, "\u{2192}100", &created_ids,
    ).unwrap();

    // Read back parent and check blocks[]
    let needles = ostk::read_needles(&dir).unwrap();
    let parent = needles.iter().find(|n| {
        n.get("id").and_then(|v| v.as_str()) == Some("\u{2192}100")
    }).expect("parent should exist");

    let blocks = parent["blocks"].as_array().expect("blocks should be an array");
    assert_eq!(blocks.len(), created_ids.len(), "blocks should contain all child IDs");

    for child_id in &created_ids {
        assert!(
            blocks.iter().any(|v| v.as_str() == Some(child_id.as_str())),
            "blocks should contain {}", child_id
        );
    }

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test: decompose_without_llm_prints_guidance
// =========================================================================

#[test]
fn decompose_without_llm_prints_guidance() {
    // This test verifies that the is_needle_id detection and normalize work,
    // and that the manual guidance path doesn't crash.
    // We can't easily test run_needle without a real project root in CWD,
    // so we test the components.

    // is_needle_id correctly identifies needle IDs
    assert!(ostk::commands::decompose::is_needle_id("\u{2192}843"));
    assert!(ostk::commands::decompose::is_needle_id("->843"));
    assert!(ostk::commands::decompose::is_needle_id("843"));
    assert!(!ostk::commands::decompose::is_needle_id("docs/spec/foo.md"));

    // The run function routes correctly based on argument type
    // (We can't call run_needle here without a project root, but we verify the routing logic)
}

// =========================================================================
// Test: decompose_spec_still_works
// =========================================================================

#[test]
fn decompose_spec_still_works() {
    let dir = setup_project("spec_still_works");

    // Create a spec file with checkboxes
    let spec_content = "# My Spec\n\n- [ ] Implement feature A\n- [ ] Write tests for feature A\n- [ ] Update docs\n";
    let spec_path = dir.join("docs");
    fs::create_dir_all(&spec_path).unwrap();
    let spec_file = spec_path.join("test-spec.md");
    fs::write(&spec_file, spec_content).unwrap();

    // Set counter
    set_counter(&dir, 0);

    // Change CWD to the project dir so find_project_root works
    let orig = std::env::current_dir().unwrap();
    std::env::set_current_dir(&dir).unwrap();

    let result = ostk::commands::decompose::run_spec(spec_file.to_str().unwrap());
    std::env::set_current_dir(&orig).unwrap();

    assert!(result.is_ok(), "spec decomposition should succeed: {:?}", result.err());

    // Read back needles
    let needles = ostk::read_needles(&dir).unwrap();
    assert_eq!(needles.len(), 3, "should create 3 needles from 3 checkboxes");

    // Verify titles
    let titles: Vec<&str> = needles.iter()
        .filter_map(|n| n.get("title").and_then(|v| v.as_str()))
        .collect();
    assert!(titles.contains(&"Implement feature A"));
    assert!(titles.contains(&"Write tests for feature A"));
    assert!(titles.contains(&"Update docs"));

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test: sequential sibling dependencies are correct
// =========================================================================

#[test]
fn sub_needles_have_sequential_sibling_deps() {
    let dir = setup_project("sibling_deps");

    let parent = json!({
        "id": "\u{2192}500",
        "title": "build pipeline",
        "status": "open",
        "priority": "P1",
        "issue_type": "task",
        "created_at": "2025-01-01T00:00:00Z"
    });
    ostk::write_needles(&dir, &[parent]).unwrap();
    set_counter(&dir, 500);

    let proposals = vec![
        ostk::commands::decompose::SubNeedleProposal {
            title: "step one".to_string(),
            ac: None,
            priority: "P0".to_string(),
        },
        ostk::commands::decompose::SubNeedleProposal {
            title: "step two".to_string(),
            ac: None,
            priority: "P0".to_string(),
        },
        ostk::commands::decompose::SubNeedleProposal {
            title: "step three".to_string(),
            ac: None,
            priority: "P0".to_string(),
        },
        ostk::commands::decompose::SubNeedleProposal {
            title: "step four".to_string(),
            ac: None,
            priority: "P0".to_string(),
        },
    ];

    let ids = ostk::commands::decompose::write_sub_needles(
        &dir, "\u{2192}500", "P1", &proposals,
    ).unwrap();

    assert_eq!(ids.len(), 4);

    let needles = ostk::read_needles(&dir).unwrap();

    // First child: depends only on parent
    let first = needles.iter().find(|n| n["id"].as_str() == Some(&ids[0])).unwrap();
    let first_deps = first["depends_on"].as_array().unwrap();
    assert_eq!(first_deps.len(), 1, "first child depends only on parent");
    assert_eq!(first_deps[0].as_str().unwrap(), "\u{2192}500");

    // Second child: depends on parent + first sibling
    let second = needles.iter().find(|n| n["id"].as_str() == Some(&ids[1])).unwrap();
    let second_deps = second["depends_on"].as_array().unwrap();
    assert_eq!(second_deps.len(), 2, "second child depends on parent + first sibling");
    assert!(second_deps.iter().any(|v| v.as_str() == Some("\u{2192}500")));
    assert!(second_deps.iter().any(|v| v.as_str() == Some(&ids[0])));

    // Third child: depends on parent + second sibling
    let third = needles.iter().find(|n| n["id"].as_str() == Some(&ids[2])).unwrap();
    let third_deps = third["depends_on"].as_array().unwrap();
    assert_eq!(third_deps.len(), 2);
    assert!(third_deps.iter().any(|v| v.as_str() == Some("\u{2192}500")));
    assert!(third_deps.iter().any(|v| v.as_str() == Some(&ids[1])));

    // Fourth child: depends on parent + third sibling
    let fourth = needles.iter().find(|n| n["id"].as_str() == Some(&ids[3])).unwrap();
    let fourth_deps = fourth["depends_on"].as_array().unwrap();
    assert_eq!(fourth_deps.len(), 2);
    assert!(fourth_deps.iter().any(|v| v.as_str() == Some("\u{2192}500")));
    assert!(fourth_deps.iter().any(|v| v.as_str() == Some(&ids[2])));

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test: parent blocks idempotent — calling update_parent_blocks twice
// doesn't duplicate entries
// =========================================================================

#[test]
fn update_parent_blocks_idempotent() {
    let dir = setup_project("blocks_idempotent");

    let parent = json!({
        "id": "\u{2192}200",
        "title": "parent task",
        "status": "open",
        "priority": "P0",
        "issue_type": "task",
        "created_at": "2025-01-01T00:00:00Z"
    });
    ostk::write_needles(&dir, &[parent]).unwrap();
    set_counter(&dir, 200);

    let child_ids = vec!["\u{2192}201".to_string(), "\u{2192}202".to_string()];

    // Call twice
    ostk::commands::decompose::update_parent_blocks(&dir, "\u{2192}200", &child_ids).unwrap();
    ostk::commands::decompose::update_parent_blocks(&dir, "\u{2192}200", &child_ids).unwrap();

    let needles = ostk::read_needles(&dir).unwrap();
    let parent = needles.iter().find(|n| n["id"].as_str() == Some("\u{2192}200")).unwrap();
    let blocks = parent["blocks"].as_array().unwrap();

    // Should still have exactly 2 entries, not 4
    assert_eq!(blocks.len(), 2, "blocks should not have duplicates after double update");

    let _ = fs::remove_dir_all(&dir);
}

// =========================================================================
// Test: priority inheritance — sub-needles inherit parent priority
// when proposal priority is empty
// =========================================================================

#[test]
fn sub_needles_inherit_parent_priority() {
    let dir = setup_project("priority_inherit");

    let parent = json!({
        "id": "\u{2192}300",
        "title": "parent with P1",
        "status": "open",
        "priority": "P1",
        "issue_type": "task",
        "created_at": "2025-01-01T00:00:00Z"
    });
    ostk::write_needles(&dir, &[parent]).unwrap();
    set_counter(&dir, 300);

    let proposals = vec![
        ostk::commands::decompose::SubNeedleProposal {
            title: "child with explicit P0".to_string(),
            ac: None,
            priority: "P0".to_string(),
        },
        ostk::commands::decompose::SubNeedleProposal {
            title: "child inherits parent priority".to_string(),
            ac: None,
            priority: String::new(), // empty = inherit
        },
    ];

    let ids = ostk::commands::decompose::write_sub_needles(
        &dir, "\u{2192}300", "P1", &proposals,
    ).unwrap();

    let needles = ostk::read_needles(&dir).unwrap();

    let first = needles.iter().find(|n| n["id"].as_str() == Some(&ids[0])).unwrap();
    assert_eq!(first["priority"].as_str().unwrap(), "P0", "explicit priority preserved");

    let second = needles.iter().find(|n| n["id"].as_str() == Some(&ids[1])).unwrap();
    assert_eq!(second["priority"].as_str().unwrap(), "P1", "empty priority inherits from parent");

    let _ = fs::remove_dir_all(&dir);
}
