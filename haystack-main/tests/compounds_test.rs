use serde_json::json;
use serde_json::Value;

// We test the public API from ostk::commands::compounds
use ostk::commands::compounds::{compute_compounds, priority_weight};

/// Helper: create a needle JSON value with the given fields.
fn make_needle(id: &str, title: &str, priority: &str, status: &str) -> Value {
    json!({
        "id": id,
        "title": title,
        "priority": priority,
        "status": status,
        "issue_type": "task"
    })
}

/// Helper: create a needle with an explicit blocks[] array.
fn make_needle_with_blocks(id: &str, title: &str, priority: &str, status: &str, blocks: &[&str]) -> Value {
    json!({
        "id": id,
        "title": title,
        "priority": priority,
        "status": status,
        "issue_type": "task",
        "blocks": blocks
    })
}

// ---------------------------------------------------------------
// Test 1: Empty pile — no needles, no crash
// ---------------------------------------------------------------

#[test]
fn compounds_empty_pile_no_output() {
    let needles: Vec<Value> = vec![];
    let results = compute_compounds(&needles);
    assert!(results.is_empty(), "empty needle pile should produce no results");
}

// ---------------------------------------------------------------
// Test 2: Single blocking needle — →1 blocks →2
// ---------------------------------------------------------------

#[test]
fn compounds_single_blocking_needle() {
    let needles = vec![
        make_needle_with_blocks("→001", "implement auth", "P1", "open", &["→002"]),
        make_needle("→002", "add login page", "P1", "open"),
    ];

    let results = compute_compounds(&needles);
    assert!(!results.is_empty(), "should have results");

    // →001 should be first since it blocks →002
    assert_eq!(results[0].id, "→001");
    assert_eq!(results[0].direct_fan_out, 1);
    assert_eq!(results[0].transitive_fan_out, 1);

    // →002 blocks nothing
    let r2 = results.iter().find(|r| r.id == "→002").unwrap();
    assert_eq!(r2.direct_fan_out, 0);
    assert_eq!(r2.transitive_fan_out, 0);
}

// ---------------------------------------------------------------
// Test 3: Transitive chain — →1 blocks →2 blocks →3
// ---------------------------------------------------------------

#[test]
fn compounds_transitive_chain() {
    let needles = vec![
        make_needle_with_blocks("→001", "foundation", "P1", "open", &["→002"]),
        make_needle_with_blocks("→002", "middleware", "P1", "open", &["→003"]),
        make_needle("→003", "frontend", "P1", "open"),
    ];

    let results = compute_compounds(&needles);

    // →001 transitively blocks →002 and →003 (fan-out = 2)
    let r1 = results.iter().find(|r| r.id == "→001").unwrap();
    assert_eq!(r1.transitive_fan_out, 2, "→001 should transitively block 2 needles");
    assert_eq!(r1.direct_fan_out, 1, "→001 directly blocks only →002");

    // →002 transitively blocks only →003 (fan-out = 1)
    let r2 = results.iter().find(|r| r.id == "→002").unwrap();
    assert_eq!(r2.transitive_fan_out, 1, "→002 should transitively block 1 needle");

    // →003 blocks nothing
    let r3 = results.iter().find(|r| r.id == "→003").unwrap();
    assert_eq!(r3.transitive_fan_out, 0);

    // →001 should be ranked first (highest transitive fan-out)
    assert_eq!(results[0].id, "→001");
}

// ---------------------------------------------------------------
// Test 4: Priority weighting — P0 with 1 block beats P2 with 2
// ---------------------------------------------------------------

#[test]
fn compounds_priority_weighting() {
    let needles = vec![
        // P0 needle blocks 1 other needle: weighted = 1 * 3 = 3
        make_needle_with_blocks("→001", "critical fix", "P0", "open", &["→003"]),
        // P2 needle blocks 2 other needles: weighted = 2 * 1 = 2
        make_needle_with_blocks("→002", "nice-to-have refactor", "P2", "open", &["→004", "→005"]),
        make_needle("→003", "depends on critical fix", "P1", "open"),
        make_needle("→004", "depends on refactor a", "P1", "open"),
        make_needle("→005", "depends on refactor b", "P1", "open"),
    ];

    let results = compute_compounds(&needles);

    let r1 = results.iter().find(|r| r.id == "→001").unwrap();
    let r2 = results.iter().find(|r| r.id == "→002").unwrap();

    // P0 * 1 transitive = 3, P2 * 2 transitive = 2
    assert_eq!(r1.weighted_score, 3, "P0 with 1 block: 1 * 3 = 3");
    assert_eq!(r2.weighted_score, 2, "P2 with 2 blocks: 2 * 1 = 2");

    // →001 should rank higher
    assert_eq!(results[0].id, "→001", "P0 needle should rank first due to priority weighting");
}

// ---------------------------------------------------------------
// Test 5: Closed needles don't appear in output
// ---------------------------------------------------------------

#[test]
fn compounds_ignores_closed_needles() {
    let needles = vec![
        make_needle_with_blocks("→001", "closed blocker", "P0", "closed", &["→002"]),
        make_needle("→002", "open target", "P1", "open"),
        make_needle_with_blocks("→003", "open blocker", "P1", "open", &["→002"]),
    ];

    let results = compute_compounds(&needles);

    // →001 is closed — should not appear
    assert!(
        results.iter().all(|r| r.id != "→001"),
        "closed needles must not appear in results"
    );

    // →003 blocks →002 (both open)
    let r3 = results.iter().find(|r| r.id == "→003").unwrap();
    assert_eq!(r3.transitive_fan_out, 1);

    // →002 should not count →001 as a blocker since →001 is closed
    // (→002 should have 0 transitive fan-out)
    let r2 = results.iter().find(|r| r.id == "→002").unwrap();
    assert_eq!(r2.transitive_fan_out, 0);
}

// ---------------------------------------------------------------
// Additional tests
// ---------------------------------------------------------------

#[test]
fn compounds_title_pattern_blocks() {
    // Test that "blocks →NNN" in title text is recognized
    let needles = vec![
        make_needle("→001", "core API blocks →002", "P1", "open"),
        make_needle("→002", "frontend depends on API", "P1", "open"),
    ];

    let results = compute_compounds(&needles);
    let r1 = results.iter().find(|r| r.id == "→001").unwrap();
    assert_eq!(r1.direct_fan_out, 1, "title 'blocks →002' should create an edge");
    assert_eq!(r1.transitive_fan_out, 1);
}

#[test]
fn compounds_title_pattern_depends_on() {
    // Test that "depends on →NNN" creates a reverse edge
    let needles = vec![
        make_needle("→001", "core library", "P1", "open"),
        make_needle("→002", "feature depends on →001", "P1", "open"),
    ];

    let results = compute_compounds(&needles);
    let r1 = results.iter().find(|r| r.id == "→001").unwrap();
    assert_eq!(r1.transitive_fan_out, 1, "→001 should block →002 via depends-on reverse edge");
}

#[test]
fn compounds_priority_weight_values() {
    assert_eq!(priority_weight("P0"), 3);
    assert_eq!(priority_weight("P1"), 2);
    assert_eq!(priority_weight("P2"), 1);
    assert_eq!(priority_weight(""), 1);
    assert_eq!(priority_weight("P3"), 1);
}

#[test]
fn compounds_diamond_graph() {
    // Diamond: →001 blocks →002 and →003, both block →004
    // →001 transitive fan-out = 3 (→002, →003, →004)
    let needles = vec![
        make_needle_with_blocks("→001", "root", "P1", "open", &["→002", "→003"]),
        make_needle_with_blocks("→002", "left path", "P1", "open", &["→004"]),
        make_needle_with_blocks("→003", "right path", "P1", "open", &["→004"]),
        make_needle("→004", "convergence point", "P1", "open"),
    ];

    let results = compute_compounds(&needles);
    let r1 = results.iter().find(|r| r.id == "→001").unwrap();
    assert_eq!(r1.transitive_fan_out, 3, "→001 should transitively block →002, →003, →004");
    assert_eq!(r1.direct_fan_out, 2, "→001 directly blocks →002 and →003");
}

#[test]
fn compounds_cycle_does_not_hang() {
    // →001 blocks →002, →002 blocks →001 — cycle should not cause infinite loop
    let needles = vec![
        make_needle_with_blocks("→001", "cyclic a", "P1", "open", &["→002"]),
        make_needle_with_blocks("→002", "cyclic b", "P1", "open", &["→001"]),
    ];

    let results = compute_compounds(&needles);
    // Should complete without hanging; both have transitive fan-out of 1
    let r1 = results.iter().find(|r| r.id == "→001").unwrap();
    let r2 = results.iter().find(|r| r.id == "→002").unwrap();
    assert_eq!(r1.transitive_fan_out, 1);
    assert_eq!(r2.transitive_fan_out, 1);
}

#[test]
fn compounds_in_progress_counted_as_open() {
    let needles = vec![
        make_needle_with_blocks("→001", "in progress blocker", "P0", "in_progress", &["→002"]),
        make_needle("→002", "waiting", "P1", "open"),
    ];

    let results = compute_compounds(&needles);
    assert_eq!(results.len(), 2, "in_progress needles should be included");
    let r1 = results.iter().find(|r| r.id == "→001").unwrap();
    assert_eq!(r1.transitive_fan_out, 1);
}
