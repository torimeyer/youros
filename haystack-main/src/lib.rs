

pub mod agentfile;
pub mod cli;
pub mod commands;
pub mod config;
pub mod cpu;
pub mod fcp;
pub mod fcp_screen;
pub mod fuse;
pub mod humanfile;
pub mod kernel;
pub mod language;
pub mod serve;
pub mod squasher;
pub mod strings;
pub mod util;

pub use serve::socket::BROADCAST;

// Re-export all utility functions and types at crate root so existing
// `crate::function_name` imports continue to work unchanged.
pub use util::{
    append_audit, find_project_root, next_needle_id, normalize_spec_path, now_iso,
    parse_frontmatter, parse_needle_ids, parse_spec_refs, post_mutation, read_audit_events,
    read_needles, resolve_remaps, state_dir, verify_os_integrity, with_needles_locked,
    write_needles, write_with_frontmatter, MutationEvent, STATE_DIR,
};

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};
    use std::collections::HashMap;
    use std::fs;
    use std::path::Path;
    use std::sync::Mutex;
    use tempfile::TempDir;

    // Also test the private helper via the util module
    use crate::util::time::days_to_ymd;

    /// Mutex to serialize tests that change the process-global CWD.
    static CWD_LOCK: Mutex<()> = Mutex::new(());

    /// Helper: create a temp dir with .ostk/ structure
    fn setup_project() -> TempDir {
        let tmp = TempDir::new().unwrap();
        fs::create_dir_all(tmp.path().join(".ostk/needles")).unwrap();
        tmp
    }

    // ---------------------------------------------------------------
    // find_project_root
    // ---------------------------------------------------------------

    #[test]
    fn find_project_root_finds_ostk_dir() {
        let tmp = setup_project();
        let child = tmp.path().join("a/b/c");
        fs::create_dir_all(&child).unwrap();

        // Temporarily change CWD
        let orig = std::env::current_dir().unwrap();
        std::env::set_current_dir(&child).unwrap();
        let result = find_project_root();
        std::env::set_current_dir(&orig).unwrap();

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), tmp.path().canonicalize().unwrap());
    }

    #[test]
    fn find_project_root_fails_without_ostk_dir() {
        let tmp = TempDir::new().unwrap();
        // No .ostk/ created
        let orig = std::env::current_dir().unwrap();
        std::env::set_current_dir(tmp.path()).unwrap();
        let result = find_project_root();
        std::env::set_current_dir(&orig).unwrap();

        assert!(result.is_err());
        assert!(result.unwrap_err().contains("no .ostk/"));
    }

    // ---------------------------------------------------------------
    // append_audit
    // ---------------------------------------------------------------

    #[test]
    fn append_audit_creates_file_and_writes_jsonl() {
        let tmp = setup_project();
        let event = json!({"type": "test", "msg": "hello"});

        append_audit(tmp.path(), &event).unwrap();

        let content = fs::read_to_string(tmp.path().join(".ostk/audit.jsonl")).unwrap();
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(lines.len(), 1);
        let parsed: Value = serde_json::from_str(lines[0]).unwrap();
        assert_eq!(parsed["type"], "test");
        assert_eq!(parsed["msg"], "hello");
    }

    #[test]
    fn append_audit_appends_multiple_lines() {
        let tmp = setup_project();

        append_audit(tmp.path(), &json!({"seq": 1})).unwrap();
        append_audit(tmp.path(), &json!({"seq": 2})).unwrap();
        append_audit(tmp.path(), &json!({"seq": 3})).unwrap();

        let content = fs::read_to_string(tmp.path().join(".ostk/audit.jsonl")).unwrap();
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(lines.len(), 3);
        for (i, line) in lines.iter().enumerate() {
            let v: Value = serde_json::from_str(line).unwrap();
            assert_eq!(v["seq"], (i + 1) as u64);
        }
    }

    // ---------------------------------------------------------------
    // parse_frontmatter
    // ---------------------------------------------------------------

    #[test]
    fn parse_frontmatter_with_valid_yaml() {
        let input = "---\ntitle: Hello\nstatus: draft\n---\nBody content here.";
        let (fm, body) = parse_frontmatter(input);

        assert_eq!(
            fm.get("title").and_then(|v| v.as_str()),
            Some("Hello")
        );
        assert_eq!(
            fm.get("status").and_then(|v| v.as_str()),
            Some("draft")
        );
        assert_eq!(body, "Body content here.");
    }

    #[test]
    fn parse_frontmatter_without_frontmatter() {
        let input = "Just some body text\nwith multiple lines.";
        let (fm, body) = parse_frontmatter(input);

        assert!(fm.is_empty());
        assert_eq!(body, input);
    }

    #[test]
    fn parse_frontmatter_empty_string() {
        let (fm, body) = parse_frontmatter("");
        assert!(fm.is_empty());
        assert_eq!(body, "");
    }

    #[test]
    fn parse_frontmatter_only_opening_fence() {
        let input = "---\ntitle: Hello\nNo closing fence";
        let (fm, body) = parse_frontmatter(input);
        // No closing ---, returns original content
        assert!(fm.is_empty());
        assert_eq!(body, input);
    }

    #[test]
    fn parse_frontmatter_empty_frontmatter() {
        // With no content between --- fences on the same line, the parser
        // doesn't find a closing fence (needs \n---), so it returns original.
        let input = "---\n---\nBody after empty frontmatter.";
        let (fm, body) = parse_frontmatter(input);
        assert!(fm.is_empty());
        assert_eq!(body, input);
    }

    #[test]
    fn parse_frontmatter_empty_yaml_block() {
        // A newline between fences creates a valid (empty) YAML block
        let input = "---\n\n---\nBody after empty yaml.";
        let (fm, body) = parse_frontmatter(input);
        assert!(fm.is_empty());
        assert_eq!(body, "Body after empty yaml.");
    }

    #[test]
    fn parse_frontmatter_multiline_body() {
        let input = "---\nkey: value\n---\nLine 1\nLine 2\nLine 3";
        let (fm, body) = parse_frontmatter(input);
        assert_eq!(fm.get("key").and_then(|v| v.as_str()), Some("value"));
        assert!(body.contains("Line 1"));
        assert!(body.contains("Line 3"));
    }

    // ---------------------------------------------------------------
    // write_with_frontmatter
    // ---------------------------------------------------------------

    #[test]
    fn write_with_frontmatter_roundtrip() {
        let tmp = TempDir::new().unwrap();
        let path = tmp.path().join("test.md");

        let mut fm = HashMap::new();
        fm.insert(
            "title".to_string(),
            serde_yaml::Value::String("Test Doc".to_string()),
        );
        fm.insert(
            "status".to_string(),
            serde_yaml::Value::String("draft".to_string()),
        );

        write_with_frontmatter(&path, &fm, "Body text here.\n").unwrap();

        let content = fs::read_to_string(&path).unwrap();
        assert!(content.starts_with("---\n"));
        assert!(content.contains("title: Test Doc"));
        assert!(content.contains("status: draft"));
        assert!(content.ends_with("Body text here.\n"));

        // Round-trip: parse it back
        let (parsed_fm, parsed_body) = parse_frontmatter(&content);
        assert_eq!(
            parsed_fm.get("title").and_then(|v| v.as_str()),
            Some("Test Doc")
        );
        assert_eq!(parsed_body.trim(), "Body text here.");
    }

    // ---------------------------------------------------------------
    // next_needle_id
    // ---------------------------------------------------------------

    #[test]
    fn next_needle_id_starts_at_one() {
        let tmp = setup_project();
        let id = next_needle_id(tmp.path()).unwrap();
        assert_eq!(id, "\u{2192}001");
    }

    #[test]
    fn next_needle_id_increments() {
        let tmp = setup_project();

        let id1 = next_needle_id(tmp.path()).unwrap();
        let id2 = next_needle_id(tmp.path()).unwrap();
        let id3 = next_needle_id(tmp.path()).unwrap();

        assert_eq!(id1, "\u{2192}001");
        assert_eq!(id2, "\u{2192}002");
        assert_eq!(id3, "\u{2192}003");
    }

    #[test]
    fn next_needle_id_resumes_from_existing_counter() {
        let tmp = setup_project();
        fs::write(tmp.path().join(".ostk/needles/counter"), "42").unwrap();

        let id = next_needle_id(tmp.path()).unwrap();
        assert_eq!(id, "\u{2192}043");
    }

    #[test]
    fn next_needle_id_handles_empty_counter_file() {
        let tmp = setup_project();
        fs::write(tmp.path().join(".ostk/needles/counter"), "").unwrap();

        let id = next_needle_id(tmp.path()).unwrap();
        assert_eq!(id, "\u{2192}001");
    }

    #[test]
    fn next_needle_id_handles_non_numeric_counter() {
        let tmp = setup_project();
        fs::write(tmp.path().join(".ostk/needles/counter"), "abc").unwrap();

        let id = next_needle_id(tmp.path()).unwrap();
        assert_eq!(id, "\u{2192}001");
    }

    #[test]
    fn next_needle_id_sequential_no_duplicates() {
        let tmp = setup_project();

        let mut ids: Vec<String> = Vec::new();
        for _ in 0..10 {
            ids.push(next_needle_id(tmp.path()).unwrap());
        }

        // All unique
        let mut sorted = ids.clone();
        sorted.sort();
        sorted.dedup();
        assert_eq!(sorted.len(), ids.len());

        // Counter should be at 10
        let counter = fs::read_to_string(tmp.path().join(".ostk/needles/counter")).unwrap();
        assert_eq!(counter, "10");
    }

    // ---------------------------------------------------------------
    // read_needles
    // ---------------------------------------------------------------

    #[test]
    fn read_needles_empty_project() {
        let tmp = setup_project();
        let needles = read_needles(tmp.path()).unwrap();
        assert!(needles.is_empty());
    }

    #[test]
    fn read_needles_from_needles_dir() {
        let tmp = setup_project();
        let content = "{\"id\":\"nd-001\",\"title\":\"First\",\"status\":\"open\"}\n{\"id\":\"nd-002\",\"title\":\"Second\",\"status\":\"closed\"}\n";
        fs::write(
            tmp.path().join(".ostk/needles/issues.jsonl"),
            content,
        )
        .unwrap();

        let needles = read_needles(tmp.path()).unwrap();
        assert_eq!(needles.len(), 2);
        assert_eq!(needles[0]["id"], "nd-001");
        assert_eq!(needles[1]["id"], "nd-002");
    }

    #[test]
    fn read_needles_skips_empty_lines() {
        let tmp = setup_project();
        let content = "{\"id\":\"nd-001\",\"status\":\"open\"}\n\n{\"id\":\"nd-002\",\"status\":\"open\"}\n\n";
        fs::write(
            tmp.path().join(".ostk/needles/issues.jsonl"),
            content,
        )
        .unwrap();

        let needles = read_needles(tmp.path()).unwrap();
        assert_eq!(needles.len(), 2);
    }

    #[test]
    fn read_needles_no_issues_file_returns_empty() {
        let tmp = TempDir::new().unwrap();
        fs::create_dir_all(tmp.path().join(".ostk")).unwrap();

        let needles = read_needles(tmp.path()).unwrap();
        assert!(needles.is_empty());
    }

    // ---------------------------------------------------------------
    // write_needles
    // ---------------------------------------------------------------

    #[test]
    fn write_needles_creates_file() {
        let tmp = setup_project();
        let needles = vec![
            json!({"id": "nd-001", "title": "First", "status": "open"}),
            json!({"id": "nd-002", "title": "Second", "status": "closed"}),
        ];

        write_needles(tmp.path(), &needles).unwrap();

        let content = fs::read_to_string(
            tmp.path().join(".ostk/needles/issues.jsonl"),
        )
        .unwrap();
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(lines.len(), 2);
    }

    #[test]
    fn write_needles_roundtrip() {
        let tmp = setup_project();
        let original = vec![
            json!({"id": "nd-001", "title": "Alpha", "status": "open", "priority": "P0"}),
            json!({"id": "nd-002", "title": "Beta", "status": "closed", "priority": "P1"}),
            json!({"id": "nd-003", "title": "Gamma", "status": "in_progress", "priority": "P2"}),
        ];

        write_needles(tmp.path(), &original).unwrap();
        let read_back = read_needles(tmp.path()).unwrap();

        assert_eq!(read_back.len(), original.len());
        for (a, b) in original.iter().zip(read_back.iter()) {
            assert_eq!(a["id"], b["id"]);
            assert_eq!(a["title"], b["title"]);
            assert_eq!(a["status"], b["status"]);
        }
    }

    #[test]
    fn write_needles_empty_vec() {
        let tmp = setup_project();
        write_needles(tmp.path(), &[]).unwrap();

        let content = fs::read_to_string(
            tmp.path().join(".ostk/needles/issues.jsonl"),
        )
        .unwrap();
        assert!(content.is_empty());
    }

    // ---------------------------------------------------------------
    // with_needles_locked
    // ---------------------------------------------------------------

    #[test]
    fn with_needles_locked_basic_operation() {
        let tmp = setup_project();
        let initial = vec![json!({"id": "nd-001", "status": "open"})];
        write_needles(tmp.path(), &initial).unwrap();

        let result = with_needles_locked(tmp.path(), |needles| {
            needles.push(json!({"id": "nd-002", "status": "open"}));
            Ok(needles.len())
        })
        .unwrap();

        assert_eq!(result, 2);

        let read_back = read_needles(tmp.path()).unwrap();
        assert_eq!(read_back.len(), 2);
        assert_eq!(read_back[1]["id"], "nd-002");
    }

    #[test]
    fn with_needles_locked_closure_error_propagated() {
        let tmp = setup_project();
        write_needles(tmp.path(), &[json!({"id": "nd-001"})]).unwrap();

        let result: Result<(), String> = with_needles_locked(tmp.path(), |_needles| {
            Err("intentional error".to_string())
        });

        assert!(result.is_err());
        assert_eq!(result.unwrap_err(), "intentional error");
    }

    #[test]
    fn with_needles_locked_modifies_existing() {
        let tmp = setup_project();
        write_needles(
            tmp.path(),
            &[
                json!({"id": "nd-001", "status": "open"}),
                json!({"id": "nd-002", "status": "open"}),
            ],
        )
        .unwrap();

        with_needles_locked(tmp.path(), |needles| {
            if let Some(n) = needles.get_mut(0) {
                n["status"] = json!("closed");
            }
            Ok(())
        })
        .unwrap();

        let read_back = read_needles(tmp.path()).unwrap();
        assert_eq!(read_back[0]["status"], "closed");
        assert_eq!(read_back[1]["status"], "open");
    }

    #[test]
    fn with_needles_locked_returns_value() {
        let tmp = setup_project();
        write_needles(
            tmp.path(),
            &[
                json!({"id": "nd-001", "status": "open"}),
                json!({"id": "nd-002", "status": "closed"}),
            ],
        )
        .unwrap();

        let open_count = with_needles_locked(tmp.path(), |needles| {
            let count = needles
                .iter()
                .filter(|n| n["status"] == "open")
                .count();
            Ok(count)
        })
        .unwrap();

        assert_eq!(open_count, 1);
    }

    // ---------------------------------------------------------------
    // post_mutation
    // ---------------------------------------------------------------

    #[test]
    fn post_mutation_creates_index_json() {
        let tmp = setup_project();
        write_needles(
            tmp.path(),
            &[json!({"id": "nd-001", "status": "open", "priority": "P0"})],
        )
        .unwrap();

        post_mutation(
            tmp.path(),
            &MutationEvent::NeedleAdded {
                id: "nd-001".into(),
                priority: "P0".into(),
            },
        )
        .unwrap();

        let index_path = tmp.path().join(".ostk/index.json");
        assert!(index_path.exists());

        let content = fs::read_to_string(&index_path).unwrap();
        let index: Value = serde_json::from_str(&content).unwrap();
        assert_eq!(index["needles"]["total"], 1);
        assert_eq!(index["needles"]["open"], 1);
        assert_eq!(index["needles"]["closed"], 0);
    }

    #[test]
    fn post_mutation_needle_closed_updates_counts() {
        let tmp = setup_project();
        write_needles(
            tmp.path(),
            &[
                json!({"id": "nd-001", "status": "closed", "priority": "P0"}),
                json!({"id": "nd-002", "status": "open", "priority": "P1"}),
            ],
        )
        .unwrap();

        post_mutation(
            tmp.path(),
            &MutationEvent::NeedleClosed {
                id: "nd-001".into(),
            },
        )
        .unwrap();

        let content =
            fs::read_to_string(tmp.path().join(".ostk/index.json")).unwrap();
        let index: Value = serde_json::from_str(&content).unwrap();
        assert_eq!(index["needles"]["total"], 2);
        assert_eq!(index["needles"]["open"], 1);
        assert_eq!(index["needles"]["closed"], 1);
    }

    #[test]
    fn post_mutation_hay_filed_increments_pending() {
        let tmp = setup_project();
        write_needles(tmp.path(), &[]).unwrap();

        post_mutation(tmp.path(), &MutationEvent::HayFiled).unwrap();
        post_mutation(tmp.path(), &MutationEvent::HayFiled).unwrap();

        let content =
            fs::read_to_string(tmp.path().join(".ostk/index.json")).unwrap();
        let index: Value = serde_json::from_str(&content).unwrap();
        assert_eq!(index["hay"]["pending"], 2);
        assert_eq!(index["hay"]["compiled"], 0);
    }

    #[test]
    fn post_mutation_hay_compiled_moves_pending_to_compiled() {
        let tmp = setup_project();
        write_needles(tmp.path(), &[]).unwrap();

        post_mutation(tmp.path(), &MutationEvent::HayFiled).unwrap();
        post_mutation(tmp.path(), &MutationEvent::HayFiled).unwrap();
        post_mutation(tmp.path(), &MutationEvent::HayFiled).unwrap();
        post_mutation(
            tmp.path(),
            &MutationEvent::HayCompiled { as_needle: true },
        )
        .unwrap();

        let content =
            fs::read_to_string(tmp.path().join(".ostk/index.json")).unwrap();
        let index: Value = serde_json::from_str(&content).unwrap();
        assert_eq!(index["hay"]["pending"], 2);
        assert_eq!(index["hay"]["compiled"], 1);
    }

    #[test]
    fn post_mutation_thread_created_increments_count() {
        let tmp = setup_project();
        write_needles(tmp.path(), &[]).unwrap();

        post_mutation(tmp.path(), &MutationEvent::ThreadCreated).unwrap();
        post_mutation(tmp.path(), &MutationEvent::ThreadCreated).unwrap();

        let content =
            fs::read_to_string(tmp.path().join(".ostk/index.json")).unwrap();
        let index: Value = serde_json::from_str(&content).unwrap();
        assert_eq!(index["threads"], 2);
    }

    #[test]
    fn post_mutation_spec_promoted_preserves_counts() {
        let tmp = setup_project();
        write_needles(tmp.path(), &[]).unwrap();

        post_mutation(tmp.path(), &MutationEvent::HayFiled).unwrap();
        post_mutation(tmp.path(), &MutationEvent::ThreadCreated).unwrap();

        post_mutation(tmp.path(), &MutationEvent::SpecPromoted).unwrap();

        let content =
            fs::read_to_string(tmp.path().join(".ostk/index.json")).unwrap();
        let index: Value = serde_json::from_str(&content).unwrap();
        assert_eq!(index["hay"]["pending"], 1);
        assert_eq!(index["threads"], 1);
    }

    #[test]
    fn post_mutation_in_progress_counted_as_open() {
        let tmp = setup_project();
        write_needles(
            tmp.path(),
            &[
                json!({"id": "nd-001", "status": "in_progress", "priority": "P0"}),
                json!({"id": "nd-002", "status": "open", "priority": "P1"}),
                json!({"id": "nd-003", "status": "closed"}),
            ],
        )
        .unwrap();

        post_mutation(
            tmp.path(),
            &MutationEvent::NeedleAdded {
                id: "nd-001".into(),
                priority: "P0".into(),
            },
        )
        .unwrap();

        let content =
            fs::read_to_string(tmp.path().join(".ostk/index.json")).unwrap();
        let index: Value = serde_json::from_str(&content).unwrap();
        assert_eq!(index["needles"]["open"], 2);
        assert_eq!(index["needles"]["closed"], 1);
    }

    #[test]
    fn post_mutation_by_priority_breakdown() {
        let tmp = setup_project();
        write_needles(
            tmp.path(),
            &[
                json!({"id": "nd-001", "status": "open", "priority": "P0"}),
                json!({"id": "nd-002", "status": "open", "priority": "P0"}),
                json!({"id": "nd-003", "status": "open", "priority": "P1"}),
                json!({"id": "nd-004", "status": "closed", "priority": "P0"}),
            ],
        )
        .unwrap();

        post_mutation(
            tmp.path(),
            &MutationEvent::NeedleAdded {
                id: "nd-001".into(),
                priority: "P0".into(),
            },
        )
        .unwrap();

        let content =
            fs::read_to_string(tmp.path().join(".ostk/index.json")).unwrap();
        let index: Value = serde_json::from_str(&content).unwrap();
        assert_eq!(index["needles"]["by_priority"]["P0"], 2);
        assert_eq!(index["needles"]["by_priority"]["P1"], 1);
    }

    #[test]
    fn post_mutation_last_mutation_is_iso_timestamp() {
        let tmp = setup_project();
        write_needles(tmp.path(), &[]).unwrap();

        post_mutation(tmp.path(), &MutationEvent::HayFiled).unwrap();

        let content =
            fs::read_to_string(tmp.path().join(".ostk/index.json")).unwrap();
        let index: Value = serde_json::from_str(&content).unwrap();
        let ts = index["last_mutation"].as_str().unwrap();
        assert!(ts.contains('T'));
        assert!(ts.ends_with('Z'));
        assert_eq!(ts.len(), 20);
    }

    // ---------------------------------------------------------------
    // now_iso
    // ---------------------------------------------------------------

    #[test]
    fn now_iso_returns_valid_iso8601() {
        let ts = now_iso();
        assert_eq!(ts.len(), 20);
        assert!(ts.contains('T'));
        assert!(ts.ends_with('Z'));
        assert!(ts.starts_with("20"));
        assert_eq!(&ts[4..5], "-");
        assert_eq!(&ts[7..8], "-");
        assert_eq!(&ts[13..14], ":");
        assert_eq!(&ts[16..17], ":");
    }

    // ---------------------------------------------------------------
    // days_to_ymd
    // ---------------------------------------------------------------

    #[test]
    fn days_to_ymd_unix_epoch() {
        let (y, m, d) = days_to_ymd(0);
        assert_eq!((y, m, d), (1970, 1, 1));
    }

    #[test]
    fn days_to_ymd_known_dates() {
        // 2024-01-01 = day 19723
        let (y, m, d) = days_to_ymd(19723);
        assert_eq!((y, m, d), (2024, 1, 1));

        // 2000-03-01 = day 11017
        let (y, m, d) = days_to_ymd(11017);
        assert_eq!((y, m, d), (2000, 3, 1));
    }

    #[test]
    fn days_to_ymd_leap_year() {
        // 2024 is a leap year. 2024-02-29 = day 19723 + 59
        let (y, m, d) = days_to_ymd(19723 + 59);
        assert_eq!((y, m, d), (2024, 2, 29));
    }

    // ---------------------------------------------------------------
    // normalize_spec_path
    // ---------------------------------------------------------------

    #[test]
    fn normalize_spec_path_absolute_under_root() {
        let root = Path::new("/projects/ostk");
        let result = normalize_spec_path(root, "/projects/ostk/docs/spec/foo.md");
        assert_eq!(result, "docs/spec/foo.md");
    }

    #[test]
    fn normalize_spec_path_relative_simple() {
        let _lock = CWD_LOCK.lock().unwrap();
        let tmp = TempDir::new().unwrap();
        let root = tmp.path().canonicalize().unwrap();
        let orig = std::env::current_dir().unwrap();
        std::env::set_current_dir(&root).unwrap();

        let result = normalize_spec_path(&root, "docs/spec/bar.md");
        std::env::set_current_dir(&orig).unwrap();

        assert_eq!(result, "docs/spec/bar.md");
    }

    #[test]
    fn normalize_spec_path_strips_dot_slash() {
        let _lock = CWD_LOCK.lock().unwrap();
        let tmp = TempDir::new().unwrap();
        let root = tmp.path().canonicalize().unwrap();
        let orig = std::env::current_dir().unwrap();
        std::env::set_current_dir(&root).unwrap();

        let result = normalize_spec_path(&root, "./docs/spec/baz.md");
        std::env::set_current_dir(&orig).unwrap();

        assert_eq!(result, "docs/spec/baz.md");
    }

    #[test]
    fn normalize_spec_path_parent_dir() {
        let _lock = CWD_LOCK.lock().unwrap();
        let tmp = TempDir::new().unwrap();
        let root = tmp.path().canonicalize().unwrap();
        let child = root.join("sub");
        fs::create_dir_all(&child).unwrap();

        let orig = std::env::current_dir().unwrap();
        std::env::set_current_dir(&child).unwrap();

        let result = normalize_spec_path(&root, "../sub/file.md");
        std::env::set_current_dir(&orig).unwrap();

        assert_eq!(result, "sub/file.md");
    }

    #[test]
    fn normalize_spec_path_outside_root_returns_cleaned() {
        let _lock = CWD_LOCK.lock().unwrap();
        let root = Path::new("/projects/ostk");
        let result = normalize_spec_path(root, "/completely/different/path.md");
        assert!(result.contains("path.md"));
    }

    #[test]
    fn normalize_spec_path_double_dot_collapse() {
        let _lock = CWD_LOCK.lock().unwrap();
        let tmp = TempDir::new().unwrap();
        let root = tmp.path().canonicalize().unwrap();
        let deep = root.join("a/b/c");
        fs::create_dir_all(&deep).unwrap();

        let orig = std::env::current_dir().unwrap();
        std::env::set_current_dir(&deep).unwrap();

        let result = normalize_spec_path(&root, "../../b/file.md");
        std::env::set_current_dir(&orig).unwrap();

        assert_eq!(result, "a/b/file.md");
    }
}
