//! Integration test for kernel/hotpr.rs conflict tiers.
//!
//! Tests the happy path: Tier 1 auto-merge when edits are non-overlapping
//! and sufficiently distant (>3 lines apart).

use ostk::kernel::hotpr::{try_auto_merge, MergeResult, PROXIMITY_LINES};

#[test]
fn test_hotpr_tier1_auto_merge() {
    // Base content: a simple file with well-separated sections
    let base_content = r#"fn header() {
    println!("header");
}

fn section_a() {
    println!("section A");
}

fn spacer1() {}
fn spacer2() {}
fn spacer3() {}

fn section_b() {
    println!("section B");
}

fn footer() {
    println!("footer");
}
"#;

    // Current content: another writer edited section_b (lines far from section_a)
    let current_content = r#"fn header() {
    println!("header");
}

fn section_a() {
    println!("section A");
}

fn spacer1() {}
fn spacer2() {}
fn spacer3() {}

fn section_b() {
    println!("section B - MODIFIED");
}

fn footer() {
    println!("footer");
}
"#;

    // Agent's intended edit: change section_a (non-overlapping)
    let agent_old_str = r#"fn section_a() {
    println!("section A");
}"#;

    let agent_new_str = r#"fn section_a() {
    println!("section A - AGENT EDIT");
}"#;

    // Attempt merge: previous_content, current_content, old_str, new_str, current_gen, other_writer
    let result = try_auto_merge(
        base_content,
        current_content,
        agent_old_str,
        agent_new_str,
        101,
        "test_writer",
    );

    // Should be Tier 1: auto-merged
    match result {
        MergeResult::AutoMerged {
            merged_content,
            other_writer,
            from_gen,
            to_gen,
        } => {
            assert_eq!(other_writer, "test_writer");
            assert_eq!(from_gen, 101);
            assert_eq!(to_gen, 102); // Incremented after merge
            
            // Merged content should contain both edits
            assert!(merged_content.contains("section A - AGENT EDIT"));
            assert!(merged_content.contains("section B - MODIFIED"));
            
            println!("✓ Tier 1 auto-merge succeeded");
            println!("  Merged content length: {} bytes", merged_content.len());
            println!("  Generation: {} -> {}", from_gen, to_gen);
        }
        MergeResult::AssistedMerge { .. } => {
            panic!("Expected AutoMerged (Tier 1), got AssistedMerge (Tier 2)");
        }
        MergeResult::Conflict { .. } => {
            panic!("Expected AutoMerged (Tier 1), got Conflict (Tier 3)");
        }
    }
}

#[test]
fn test_hotpr_proximity_threshold() {
    // Verify that the proximity constant is set correctly
    assert_eq!(PROXIMITY_LINES, 3, "PROXIMITY_LINES should be 3");
}
