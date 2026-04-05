//! Needle-in-a-haystack adversarial test suite (bd-197).
//!
//! Two runnable test categories:
//! 1. Shim fidelity — verify haystack shim produces byte-exact output vs real tools
//! 2. Compile quality — verify mutation pipeline maintains accurate index counts
//!
//! Run with: cargo test --test needle_tests

use std::fs;
use std::path::{Path, PathBuf};

// =========================================================================
// Helpers
// =========================================================================

/// Create a temp directory for needle tests. Each test gets a unique dir.
fn needle_test_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir()
        .join("haystack_needle_tests")
        .join(name);
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

/// Create a temp dir with .ostk/needles/ structure.
fn setup_haystack_project(name: &str) -> PathBuf {
    let dir = needle_test_dir(name);
    fs::create_dir_all(dir.join(".ostk/needles")).unwrap();
    dir
}

/// Find the haystack/ostk binary using multiple strategies:
/// 1. CARGO_BIN_EXE_ostk (set by `cargo test` for [[bin]] name = "ostk")
/// 2. CARGO_BIN_EXE_haystack (legacy, in case the binary is renamed back)
/// 3. target/debug or target/release relative to CARGO_MANIFEST_DIR
/// 4. `which ostk` or `which haystack` on PATH
#[allow(dead_code)]
fn find_haystack() -> Option<PathBuf> {
    // Strategy 1: cargo test sets CARGO_BIN_EXE_<name> automatically
    if let Some(p) = option_env!("CARGO_BIN_EXE_ostk") {
        let path = PathBuf::from(p);
        if path.exists() {
            return Some(path);
        }
    }

    // Strategy 2: legacy binary name
    if let Some(p) = option_env!("CARGO_BIN_EXE_haystack") {
        let path = PathBuf::from(p);
        if path.exists() {
            return Some(path);
        }
    }

    // Strategy 3: look in target/ relative to manifest dir
    if let Some(manifest) = option_env!("CARGO_MANIFEST_DIR") {
        let manifest = PathBuf::from(manifest);
        for profile in &["debug", "release"] {
            for name in &["ostk", "haystack"] {
                let candidate = manifest.join("target").join(profile).join(name);
                if candidate.exists() {
                    return Some(candidate);
                }
            }
        }
    }

    // Strategy 4: fall back to PATH lookup
    for name in &["ostk", "haystack"] {
        if let Ok(output) = std::process::Command::new("which").arg(name).output() {
            let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if !path.is_empty() {
                return Some(PathBuf::from(path));
            }
        }
    }

    None
}

/// Create a symlink to the haystack/ostk binary with a given tool name.
/// Returns None if the binary cannot be found (test should skip).
#[allow(dead_code)]
fn create_shim(dir: &Path, tool_name: &str) -> Option<PathBuf> {
    let binary = find_haystack()?;
    let symlink_path = dir.join(tool_name);
    std::os::unix::fs::symlink(&binary, &symlink_path).unwrap();
    Some(symlink_path)
}

// =========================================================================
// SHIM FIDELITY TESTS — REMOVED
// =========================================================================
//
// The shim dispatch path (ostk invoked as sed/head/tail via argv[0] symlink)
// was removed in →829. Commands now execute via `sh -c` to native binaries,
// with grammars handling output compression. These tests tested dead code
// and produced false failures. Removed 2026-03-26.
//
// Original: 22 tests comparing symlink-invoked ostk against /usr/bin/{tool}.

/*
mod shim_fidelity {
    use super::*;
    use std::process::Command;

    /// Create a shim or skip the test if the binary is not found.
    macro_rules! shim_or_skip {
        ($dir:expr, $tool:expr) => {
            match create_shim($dir, $tool) {
                Some(p) => p,
                None => {
                    eprintln!("SKIP: haystack/ostk binary not found, skipping shim test");
                    return;
                }
            }
        };
    }

    /// Run a command and capture (stdout, exit_code).
    fn run_tool(tool: &str, args: &[&str], env: &[(&str, &str)]) -> (String, i32) {
        let mut cmd = Command::new(tool);
        cmd.args(args);
        for (k, v) in env {
            cmd.env(k, v);
        }
        let output = cmd.output().unwrap_or_else(|_| panic!("failed to run {tool}"));
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let code = output.status.code().unwrap_or(-1);
        (stdout, code)
    }

    /// Compare shim output against real tool. Panics on mismatch.
    fn assert_shim_matches(
        test_name: &str,
        shim_path: &Path,
        real_tool: &str,
        args: &[&str],
    ) {
        let real_path = format!("/usr/bin/{real_tool}");
        if !PathBuf::from(&real_path).exists() {
            eprintln!("SKIP {test_name}: {real_path} not found");
            return;
        }

        let (real_out, real_code) = run_tool(&real_path, args, &[]);
        let (shim_out, shim_code) = run_tool(
            shim_path.to_str().unwrap(),
            args,
            &[("HAYSTACK_QUIET", "1")],
        );

        assert_eq!(
            shim_code, real_code,
            "{test_name}: exit code mismatch (real={real_code}, shim={shim_code})"
        );
        assert_eq!(
            shim_out, real_out,
            "{test_name}: output mismatch\n--- real ({} bytes) ---\n{real_out}\n--- shim ({} bytes) ---\n{shim_out}",
            real_out.len(),
            shim_out.len()
        );
    }

    // ---- cat tests ----

    #[test]
    fn cat_plain_file() {
        let dir = needle_test_dir("shim_cat_plain");
        let test_file = dir.join("test.txt");
        fs::write(&test_file, "line1\nline2\nline3\n").unwrap();
        let shim = shim_or_skip!(&dir, "cat");
        assert_shim_matches("cat_plain", &shim, "cat", &[test_file.to_str().unwrap()]);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn cat_with_line_numbers() {
        let dir = needle_test_dir("shim_cat_n");
        let test_file = dir.join("test.txt");
        fs::write(&test_file, "alpha\nbeta\ngamma\n").unwrap();
        let shim = shim_or_skip!(&dir, "cat");
        assert_shim_matches(
            "cat_n",
            &shim,
            "cat",
            &["-n", test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn cat_empty_file() {
        let dir = needle_test_dir("shim_cat_empty");
        let test_file = dir.join("empty.txt");
        fs::write(&test_file, "").unwrap();
        let shim = shim_or_skip!(&dir, "cat");
        assert_shim_matches(
            "cat_empty",
            &shim,
            "cat",
            &[test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn cat_special_chars() {
        let dir = needle_test_dir("shim_cat_special");
        let test_file = dir.join("special.txt");
        fs::write(
            &test_file,
            "tabs\there\n  spaces  \n!@#$%^&*()\nunicode: \u{2603}\n",
        )
        .unwrap();
        let shim = shim_or_skip!(&dir, "cat");
        assert_shim_matches(
            "cat_special",
            &shim,
            "cat",
            &[test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn cat_multiple_files() {
        let dir = needle_test_dir("shim_cat_multi");
        let f1 = dir.join("a.txt");
        let f2 = dir.join("b.txt");
        fs::write(&f1, "file a\n").unwrap();
        fs::write(&f2, "file b\n").unwrap();
        let shim = shim_or_skip!(&dir, "cat");
        assert_shim_matches(
            "cat_multi",
            &shim,
            "cat",
            &[f1.to_str().unwrap(), f2.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    // ---- head tests ----

    #[test]
    fn head_default() {
        let dir = needle_test_dir("shim_head_default");
        let test_file = dir.join("test.txt");
        let content: String = (1..=15).map(|i| format!("line{i}\n")).collect();
        fs::write(&test_file, &content).unwrap();
        let shim = shim_or_skip!(&dir, "head");
        assert_shim_matches(
            "head_default",
            &shim,
            "head",
            &[test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn head_custom_count() {
        let dir = needle_test_dir("shim_head_3");
        let test_file = dir.join("test.txt");
        let content: String = (1..=10).map(|i| format!("line{i}\n")).collect();
        fs::write(&test_file, &content).unwrap();
        let shim = shim_or_skip!(&dir, "head");
        assert_shim_matches(
            "head_3",
            &shim,
            "head",
            &["-3", test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn head_single_line() {
        let dir = needle_test_dir("shim_head_1");
        let test_file = dir.join("test.txt");
        let content: String = (1..=10).map(|i| format!("line{i}\n")).collect();
        fs::write(&test_file, &content).unwrap();
        let shim = shim_or_skip!(&dir, "head");
        assert_shim_matches(
            "head_1",
            &shim,
            "head",
            &["-1", test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn head_empty_file() {
        let dir = needle_test_dir("shim_head_empty");
        let test_file = dir.join("empty.txt");
        fs::write(&test_file, "").unwrap();
        let shim = shim_or_skip!(&dir, "head");
        assert_shim_matches(
            "head_empty",
            &shim,
            "head",
            &[test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn head_byte_count() {
        let dir = needle_test_dir("shim_head_c");
        let test_file = dir.join("test.txt");
        fs::write(&test_file, "abcdefghijklmnop\n").unwrap();
        let shim = shim_or_skip!(&dir, "head");
        assert_shim_matches(
            "head_c",
            &shim,
            "head",
            &["-c", "5", test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    // ---- tail tests ----

    #[test]
    fn tail_default() {
        let dir = needle_test_dir("shim_tail_default");
        let test_file = dir.join("test.txt");
        let content: String = (1..=15).map(|i| format!("line{i}\n")).collect();
        fs::write(&test_file, &content).unwrap();
        let shim = shim_or_skip!(&dir, "tail");
        assert_shim_matches(
            "tail_default",
            &shim,
            "tail",
            &[test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn tail_custom_count() {
        let dir = needle_test_dir("shim_tail_3");
        let test_file = dir.join("test.txt");
        let content: String = (1..=10).map(|i| format!("line{i}\n")).collect();
        fs::write(&test_file, &content).unwrap();
        let shim = shim_or_skip!(&dir, "tail");
        assert_shim_matches(
            "tail_3",
            &shim,
            "tail",
            &["-3", test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn tail_single_line() {
        let dir = needle_test_dir("shim_tail_1");
        let test_file = dir.join("test.txt");
        let content: String = (1..=10).map(|i| format!("line{i}\n")).collect();
        fs::write(&test_file, &content).unwrap();
        let shim = shim_or_skip!(&dir, "tail");
        assert_shim_matches(
            "tail_1",
            &shim,
            "tail",
            &["-1", test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn tail_empty_file() {
        let dir = needle_test_dir("shim_tail_empty");
        let test_file = dir.join("empty.txt");
        fs::write(&test_file, "").unwrap();
        let shim = shim_or_skip!(&dir, "tail");
        assert_shim_matches(
            "tail_empty",
            &shim,
            "tail",
            &[test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn tail_from_offset() {
        let dir = needle_test_dir("shim_tail_offset");
        let test_file = dir.join("test.txt");
        let content: String = (1..=10).map(|i| format!("line{i}\n")).collect();
        fs::write(&test_file, &content).unwrap();
        let shim = shim_or_skip!(&dir, "tail");
        assert_shim_matches(
            "tail_offset",
            &shim,
            "tail",
            &["-n", "+5", test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    // ---- sed tests ----

    #[test]
    fn sed_substitution_on_file() {
        let dir = needle_test_dir("shim_sed_sub");
        let test_file = dir.join("test.txt");
        fs::write(&test_file, "hello foo world\nfoo bar foo\n").unwrap();
        let shim = shim_or_skip!(&dir, "sed");
        assert_shim_matches(
            "sed_sub",
            &shim,
            "sed",
            &["s/foo/bar/", test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn sed_global_substitution() {
        let dir = needle_test_dir("shim_sed_global");
        let test_file = dir.join("test.txt");
        fs::write(&test_file, "aaa bbb aaa\nccc aaa ddd\n").unwrap();
        let shim = shim_or_skip!(&dir, "sed");
        assert_shim_matches(
            "sed_global",
            &shim,
            "sed",
            &["s/aaa/ZZZ/g", test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn sed_no_match() {
        let dir = needle_test_dir("shim_sed_nomatch");
        let test_file = dir.join("test.txt");
        fs::write(&test_file, "hello world\n").unwrap();
        let shim = shim_or_skip!(&dir, "sed");
        assert_shim_matches(
            "sed_nomatch",
            &shim,
            "sed",
            &["s/xyz/abc/", test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn sed_delete_pattern() {
        let dir = needle_test_dir("shim_sed_delete");
        let test_file = dir.join("test.txt");
        fs::write(&test_file, "keep\ndelete_me\nkeep\n").unwrap();
        let shim = shim_or_skip!(&dir, "sed");
        assert_shim_matches(
            "sed_delete",
            &shim,
            "sed",
            &["/delete_me/d", test_file.to_str().unwrap()],
        );
        let _ = fs::remove_dir_all(&dir);
    }
}
*/

// =========================================================================
// COMPILE QUALITY TESTS
// =========================================================================
//
// Verify that post_mutation maintains accurate index.json counts
// across all mutation event types and edge cases.

mod compile_quality {
    use super::*;
    use ostk::{
        post_mutation, read_needles, write_needles, MutationEvent,
    };
    use serde_json::{json, Value};

    /// Read index.json from a project root.
    fn read_index(root: &std::path::Path) -> Value {
        let path = root.join(".ostk/index.json");
        let content = fs::read_to_string(&path).expect("index.json should exist");
        serde_json::from_str(&content).expect("index.json should be valid JSON")
    }

    // ---- Needle lifecycle ----

    #[test]
    fn single_needle_filed() {
        let dir = setup_haystack_project("cq_single_needle");
        write_needles(
            &dir,
            &[json!({"id": "nd-001", "status": "open", "priority": "P0"})],
        )
        .unwrap();

        post_mutation(
            &dir,
            &MutationEvent::NeedleAdded {
                id: "nd-001".into(),
                priority: "P0".into(),
            },
        )
        .unwrap();

        let index = read_index(&dir);
        assert_eq!(index["needles"]["total"], 1);
        assert_eq!(index["needles"]["open"], 1);
        assert_eq!(index["needles"]["closed"], 0);
        assert_eq!(index["needles"]["by_priority"]["P0"], 1);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn multiple_needles_with_priorities() {
        let dir = setup_haystack_project("cq_multi_needle");
        write_needles(
            &dir,
            &[
                json!({"id": "nd-001", "status": "open", "priority": "P0"}),
                json!({"id": "nd-002", "status": "open", "priority": "P0"}),
                json!({"id": "nd-003", "status": "open", "priority": "P1"}),
                json!({"id": "nd-004", "status": "open", "priority": "P2"}),
            ],
        )
        .unwrap();

        post_mutation(
            &dir,
            &MutationEvent::NeedleAdded {
                id: "nd-004".into(),
                priority: "P2".into(),
            },
        )
        .unwrap();

        let index = read_index(&dir);
        assert_eq!(index["needles"]["total"], 4);
        assert_eq!(index["needles"]["open"], 4);
        assert_eq!(index["needles"]["closed"], 0);
        assert_eq!(index["needles"]["by_priority"]["P0"], 2);
        assert_eq!(index["needles"]["by_priority"]["P1"], 1);
        assert_eq!(index["needles"]["by_priority"]["P2"], 1);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn needle_close_updates_counts() {
        let dir = setup_haystack_project("cq_needle_close");
        write_needles(
            &dir,
            &[
                json!({"id": "nd-001", "status": "open", "priority": "P0"}),
                json!({"id": "nd-002", "status": "closed", "priority": "P0"}),
                json!({"id": "nd-003", "status": "open", "priority": "P1"}),
            ],
        )
        .unwrap();

        post_mutation(
            &dir,
            &MutationEvent::NeedleClosed {
                id: "nd-002".into(),
            },
        )
        .unwrap();

        let index = read_index(&dir);
        assert_eq!(index["needles"]["total"], 3);
        assert_eq!(index["needles"]["open"], 2);
        assert_eq!(index["needles"]["closed"], 1);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn in_progress_counted_as_open() {
        let dir = setup_haystack_project("cq_in_progress");
        write_needles(
            &dir,
            &[
                json!({"id": "nd-001", "status": "in_progress", "priority": "P0"}),
                json!({"id": "nd-002", "status": "open", "priority": "P1"}),
                json!({"id": "nd-003", "status": "closed", "priority": "P0"}),
            ],
        )
        .unwrap();

        post_mutation(
            &dir,
            &MutationEvent::NeedleAdded {
                id: "nd-001".into(),
                priority: "P0".into(),
            },
        )
        .unwrap();

        let index = read_index(&dir);
        assert_eq!(index["needles"]["open"], 2, "in_progress counts as open");
        assert_eq!(index["needles"]["closed"], 1);
        let _ = fs::remove_dir_all(&dir);
    }

    // ---- Hay lifecycle ----

    #[test]
    fn hay_filed_increments_pending() {
        let dir = setup_haystack_project("cq_hay_filed");
        write_needles(&dir, &[]).unwrap();

        post_mutation(&dir, &MutationEvent::HayFiled).unwrap();
        post_mutation(&dir, &MutationEvent::HayFiled).unwrap();
        post_mutation(&dir, &MutationEvent::HayFiled).unwrap();

        let index = read_index(&dir);
        assert_eq!(index["hay"]["pending"], 3);
        assert_eq!(index["hay"]["compiled"], 0);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn hay_compiled_as_needle() {
        let dir = setup_haystack_project("cq_hay_compiled_needle");
        write_needles(&dir, &[]).unwrap();

        post_mutation(&dir, &MutationEvent::HayFiled).unwrap();
        post_mutation(&dir, &MutationEvent::HayFiled).unwrap();
        post_mutation(
            &dir,
            &MutationEvent::HayCompiled { as_needle: true },
        )
        .unwrap();

        let index = read_index(&dir);
        assert_eq!(index["hay"]["pending"], 1);
        assert_eq!(index["hay"]["compiled"], 1);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn hay_compiled_not_as_needle() {
        let dir = setup_haystack_project("cq_hay_compiled_no_needle");
        write_needles(&dir, &[]).unwrap();

        post_mutation(&dir, &MutationEvent::HayFiled).unwrap();
        post_mutation(
            &dir,
            &MutationEvent::HayCompiled { as_needle: false },
        )
        .unwrap();

        let index = read_index(&dir);
        assert_eq!(index["hay"]["pending"], 0);
        assert_eq!(index["hay"]["compiled"], 1);
        let _ = fs::remove_dir_all(&dir);
    }

    // ---- Thread tracking ----

    #[test]
    fn thread_created_increments() {
        let dir = setup_haystack_project("cq_thread");
        write_needles(&dir, &[]).unwrap();

        post_mutation(&dir, &MutationEvent::ThreadCreated).unwrap();
        post_mutation(&dir, &MutationEvent::ThreadCreated).unwrap();
        post_mutation(&dir, &MutationEvent::ThreadCreated).unwrap();

        let index = read_index(&dir);
        assert_eq!(index["threads"], 3);
        let _ = fs::remove_dir_all(&dir);
    }

    // ---- Mixed workload ----

    #[test]
    fn interleaved_mutations_consistent() {
        let dir = setup_haystack_project("cq_mixed");

        // Start with 2 needles
        write_needles(
            &dir,
            &[
                json!({"id": "nd-001", "status": "open", "priority": "P0"}),
                json!({"id": "nd-002", "status": "open", "priority": "P1"}),
            ],
        )
        .unwrap();
        post_mutation(
            &dir,
            &MutationEvent::NeedleAdded {
                id: "nd-002".into(),
                priority: "P1".into(),
            },
        )
        .unwrap();

        // File some hay
        post_mutation(&dir, &MutationEvent::HayFiled).unwrap();
        post_mutation(&dir, &MutationEvent::HayFiled).unwrap();

        // Create a thread
        post_mutation(&dir, &MutationEvent::ThreadCreated).unwrap();

        // Compile one hay
        post_mutation(
            &dir,
            &MutationEvent::HayCompiled { as_needle: true },
        )
        .unwrap();

        // Close a needle (update the file first)
        let mut needles = read_needles(&dir).unwrap();
        needles[0]["status"] = json!("closed");
        write_needles(&dir, &needles).unwrap();
        post_mutation(
            &dir,
            &MutationEvent::NeedleClosed {
                id: "nd-001".into(),
            },
        )
        .unwrap();

        // Verify everything is consistent
        let index = read_index(&dir);
        assert_eq!(index["needles"]["total"], 2);
        assert_eq!(index["needles"]["open"], 1);
        assert_eq!(index["needles"]["closed"], 1);
        assert_eq!(index["hay"]["pending"], 1);
        assert_eq!(index["hay"]["compiled"], 1);
        assert_eq!(index["threads"], 1);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn spec_promoted_preserves_all_counts() {
        let dir = setup_haystack_project("cq_promote");
        write_needles(
            &dir,
            &[json!({"id": "nd-001", "status": "open", "priority": "P0"})],
        )
        .unwrap();

        // Set up some state first
        post_mutation(
            &dir,
            &MutationEvent::NeedleAdded {
                id: "nd-001".into(),
                priority: "P0".into(),
            },
        )
        .unwrap();
        post_mutation(&dir, &MutationEvent::HayFiled).unwrap();
        post_mutation(&dir, &MutationEvent::ThreadCreated).unwrap();

        // Now promote a spec — should not change needle/hay/thread counts
        post_mutation(&dir, &MutationEvent::SpecPromoted).unwrap();

        let index = read_index(&dir);
        assert_eq!(index["needles"]["total"], 1);
        assert_eq!(index["needles"]["open"], 1);
        assert_eq!(index["hay"]["pending"], 1);
        assert_eq!(index["threads"], 1);
        let _ = fs::remove_dir_all(&dir);
    }

    // ---- Edge cases ----

    #[test]
    fn empty_project_no_panic() {
        let dir = setup_haystack_project("cq_empty");
        write_needles(&dir, &[]).unwrap();

        post_mutation(&dir, &MutationEvent::HayFiled).unwrap();

        let index = read_index(&dir);
        assert_eq!(index["needles"]["total"], 0);
        assert_eq!(index["needles"]["open"], 0);
        assert_eq!(index["needles"]["closed"], 0);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn missing_priority_defaults_to_p1_counting() {
        let dir = setup_haystack_project("cq_missing_priority");
        // Needle without explicit priority
        write_needles(
            &dir,
            &[json!({"id": "nd-001", "status": "open"})],
        )
        .unwrap();

        post_mutation(
            &dir,
            &MutationEvent::NeedleAdded {
                id: "nd-001".into(),
                priority: "P1".into(),
            },
        )
        .unwrap();

        let index = read_index(&dir);
        assert_eq!(index["needles"]["total"], 1);
        assert_eq!(index["needles"]["open"], 1);
        // Missing priority defaults to P1 in the counting logic
        assert_eq!(index["needles"]["by_priority"]["P1"], 1);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn unknown_status_not_counted() {
        let dir = setup_haystack_project("cq_unknown_status");
        write_needles(
            &dir,
            &[
                json!({"id": "nd-001", "status": "open", "priority": "P0"}),
                json!({"id": "nd-002", "status": "weird_status", "priority": "P1"}),
            ],
        )
        .unwrap();

        post_mutation(
            &dir,
            &MutationEvent::NeedleAdded {
                id: "nd-001".into(),
                priority: "P0".into(),
            },
        )
        .unwrap();

        let index = read_index(&dir);
        assert_eq!(index["needles"]["total"], 2);
        assert_eq!(index["needles"]["open"], 1, "unknown status not counted as open");
        assert_eq!(index["needles"]["closed"], 0, "unknown status not counted as closed");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn rapid_mutations_stay_consistent() {
        let dir = setup_haystack_project("cq_rapid");
        write_needles(&dir, &[]).unwrap();

        // 50 rapid hay files
        for _ in 0..50 {
            post_mutation(&dir, &MutationEvent::HayFiled).unwrap();
        }

        let index = read_index(&dir);
        assert_eq!(index["hay"]["pending"], 50);
        assert_eq!(index["hay"]["compiled"], 0);

        // Compile 20 of them
        for _ in 0..20 {
            post_mutation(
                &dir,
                &MutationEvent::HayCompiled { as_needle: false },
            )
            .unwrap();
        }

        let index = read_index(&dir);
        assert_eq!(index["hay"]["pending"], 30);
        assert_eq!(index["hay"]["compiled"], 20);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn index_has_last_mutation_timestamp() {
        let dir = setup_haystack_project("cq_timestamp");
        write_needles(&dir, &[]).unwrap();

        post_mutation(&dir, &MutationEvent::HayFiled).unwrap();

        let index = read_index(&dir);
        let ts = index["last_mutation"].as_str().unwrap();
        assert!(ts.contains('T'), "timestamp should be ISO format");
        assert!(ts.ends_with('Z'), "timestamp should end with Z");
        assert_eq!(ts.len(), 20, "timestamp should be 20 chars");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn index_consistency_after_every_mutation() {
        let dir = setup_haystack_project("cq_every_step");

        // Build up state step by step, verify after EVERY mutation
        let mut needles = vec![
            json!({"id": "nd-001", "status": "open", "priority": "P0"}),
        ];
        write_needles(&dir, &needles).unwrap();
        post_mutation(
            &dir,
            &MutationEvent::NeedleAdded {
                id: "nd-001".into(),
                priority: "P0".into(),
            },
        )
        .unwrap();
        let idx = read_index(&dir);
        assert_eq!(idx["needles"]["total"], 1, "step 1");
        assert_eq!(idx["needles"]["open"], 1, "step 1");

        // Add second needle
        needles.push(json!({"id": "nd-002", "status": "open", "priority": "P1"}));
        write_needles(&dir, &needles).unwrap();
        post_mutation(
            &dir,
            &MutationEvent::NeedleAdded {
                id: "nd-002".into(),
                priority: "P1".into(),
            },
        )
        .unwrap();
        let idx = read_index(&dir);
        assert_eq!(idx["needles"]["total"], 2, "step 2");
        assert_eq!(idx["needles"]["open"], 2, "step 2");

        // File hay
        post_mutation(&dir, &MutationEvent::HayFiled).unwrap();
        let idx = read_index(&dir);
        assert_eq!(idx["hay"]["pending"], 1, "step 3");

        // Close first needle
        needles[0]["status"] = json!("closed");
        write_needles(&dir, &needles).unwrap();
        post_mutation(
            &dir,
            &MutationEvent::NeedleClosed {
                id: "nd-001".into(),
            },
        )
        .unwrap();
        let idx = read_index(&dir);
        assert_eq!(idx["needles"]["open"], 1, "step 4");
        assert_eq!(idx["needles"]["closed"], 1, "step 4");
        assert_eq!(idx["hay"]["pending"], 1, "step 4 - hay preserved");

        // Compile hay
        post_mutation(
            &dir,
            &MutationEvent::HayCompiled { as_needle: true },
        )
        .unwrap();
        let idx = read_index(&dir);
        assert_eq!(idx["hay"]["pending"], 0, "step 5");
        assert_eq!(idx["hay"]["compiled"], 1, "step 5");

        let _ = fs::remove_dir_all(&dir);
    }
}

// =========================================================================
// SQUASHER FIDELITY TESTS
// =========================================================================
//
// Test the output compression pipeline (VTE strip + dedup) for correctness.
// This is part of shim fidelity — the squasher must not lose signal.

mod squasher_fidelity {
    use ostk::squasher;
    use ostk::squasher::vte_strip::{strip_ansi, VteStripper};

    #[test]
    fn plain_text_roundtrip() {
        let input = "hello world\nfoo bar\nbaz";
        let (output, stats) = squasher::compress(input);
        assert_eq!(output, input, "plain text should pass through unchanged");
        assert!(stats.is_empty(), "no compression for non-repeated lines");
    }

    #[test]
    fn ansi_stripped_preserves_content() {
        let input = "\x1b[31merror: something failed\x1b[0m\n\x1b[32mok: test passed\x1b[0m";
        let (output, _) = squasher::compress(input);
        assert!(output.contains("error: something failed"));
        assert!(output.contains("ok: test passed"));
        assert!(!output.contains("\x1b["), "no ANSI sequences should remain");
    }

    #[test]
    fn dedup_compiles_repeated_patterns() {
        let lines: Vec<String> = (1..=20)
            .map(|i| format!("   Compiling crate-{i} v1.{i}.0"))
            .collect();
        let input = lines.join("\n");
        let (output, stats) = squasher::compress(&input);

        // Should collapse 20 Compiling lines into fewer
        let output_line_count = output.lines().count();
        assert!(
            output_line_count < 20,
            "dedup should collapse 20 similar lines, got {output_line_count}"
        );
        assert!(!stats.is_empty(), "stats should show compression");
    }

    #[test]
    fn dedup_preserves_dissimilar_lines() {
        let input = "error: mismatched types\nwarning: unused variable\nnote: see docs\nhelp: add #[derive]";
        let (output, stats) = squasher::compress(input);
        assert_eq!(
            output.lines().count(),
            4,
            "dissimilar lines should not be merged"
        );
        assert!(stats.is_empty(), "no compression for dissimilar lines");
    }

    #[test]
    fn mixed_ansi_and_dedup() {
        let mut lines: Vec<String> = Vec::new();
        for i in 1..=5 {
            lines.push(format!("\x1b[32m   Compiling\x1b[0m pkg-{i} v0.{i}.0"));
        }
        lines.push("\x1b[31merror[E0308]\x1b[0m: mismatched types".to_string());
        let input = lines.join("\n");
        let (output, _) = squasher::compress(&input);

        // ANSI stripped
        assert!(!output.contains("\x1b["));
        // Error line preserved
        assert!(output.contains("error[E0308]: mismatched types"));
        // Compiling lines collapsed
        assert!(output.lines().count() < 6);
    }

    #[test]
    fn vte_strip_cursor_movement_detection() {
        let input = b"\x1b[2Asome text\x1b[K";
        let meta = VteStripper::strip(input);
        assert!(meta.has_cursor_movement, "should detect cursor up");
        assert!(meta.has_erase, "should detect erase");
        assert_eq!(meta.clean_text, "some text");
    }

    #[test]
    fn vte_strip_color_detection() {
        let input = b"\x1b[1;31mFATAL\x1b[0m \x1b[33mwarning\x1b[0m";
        let meta = VteStripper::strip(input);
        assert_eq!(meta.clean_text, "FATAL warning");
        assert!(
            meta.colors
                .contains(&ostk::squasher::vte_strip::AnsiColor::BrightRed)
        );
        assert!(
            meta.colors
                .contains(&ostk::squasher::vte_strip::AnsiColor::Yellow)
        );
    }

    #[test]
    fn strip_ansi_multiline_preserves_structure() {
        let input = "\x1b[32mline1\x1b[0m\n\x1b[31mline2\x1b[0m\nplain line 3";
        let result = strip_ansi(input);
        let lines: Vec<&str> = result.lines().collect();
        assert_eq!(lines.len(), 3);
        assert_eq!(lines[0], "line1");
        assert_eq!(lines[1], "line2");
        assert_eq!(lines[2], "plain line 3");
    }

    #[test]
    fn cargo_build_real_world_output() {
        // Simulate realistic cargo build output with ANSI
        let input = [
            "\x1b[32m   Compiling\x1b[0m serde v1.0.195",
            "\x1b[32m   Compiling\x1b[0m tokio v1.35.0",
            "\x1b[32m   Compiling\x1b[0m regex v1.10.0",
            "\x1b[32m   Compiling\x1b[0m clap v4.4.12",
            "\x1b[32m   Compiling\x1b[0m haystack v0.1.0",
            "\x1b[32m    Finished\x1b[0m `dev` profile [unoptimized + debuginfo] target(s) in 5.42s",
        ]
        .join("\n");

        let (output, _) = squasher::compress(&input);

        // All ANSI gone
        assert!(!output.contains("\x1b["));
        // Finished line preserved (it breaks the Compiling streak)
        assert!(output.contains("Finished"));
        // Compiling lines should be collapsed
        assert!(
            output.lines().count() < 6,
            "should compress Compiling lines, got:\n{output}"
        );
    }
}

// =========================================================================
// HOT PR FIDELITY TESTS
// =========================================================================
//
// Adversarial tests for the conflict resolution engine.
// These test edge cases not covered by the standard hotpr tests.

mod hotpr_adversarial {
    use ostk::kernel::hotpr::*;

    #[test]
    fn identical_edits_by_two_agents() {
        // Both agents make the exact same edit. Should auto-merge (no conflict).
        let prev = "line1\nline2\nline3\n";
        let curr = "LINE1\nline2\nline3\n"; // agent-1 changed line1

        // agent-2 also wants to change line1 to the same thing
        let result = try_auto_merge(prev, curr, "line1", "LINE1", 2, "agent-1");

        match result {
            // The old_str "line1" is gone, but "LINE1" exists. The edit is a no-op
            // (old matches new in effect). Should not panic.
            MergeResult::AutoMerged { .. }
            | MergeResult::AssistedMerge { .. }
            | MergeResult::Conflict { .. } => {
                // Any non-panic result is acceptable for this edge case
            }
        }
    }

    #[test]
    fn empty_old_str() {
        let prev = "line1\nline2\n";
        let curr = "line1\nLINE2\n";
        let result = try_auto_merge(prev, curr, "", "new text", 2, "agent-1");

        match result {
            // Empty old_str can't be found in previous content -> Conflict
            MergeResult::Conflict { .. } => {}
            other => panic!("empty old_str should conflict, got: {other:?}"),
        }
    }

    #[test]
    fn old_str_appears_multiple_times() {
        let prev = "x\nx\nx\n";
        let curr = "Y\nx\nx\n";
        // old_str "x" appears multiple times in previous content
        let result = try_auto_merge(prev, curr, "x", "Z", 2, "agent-1");

        // Should still produce some result without panicking
        match result {
            MergeResult::AutoMerged { merged_content, .. } => {
                // If auto-merged, only one "x" should become "Z"
                assert!(
                    merged_content.contains('Z'),
                    "merged should contain the edit"
                );
            }
            MergeResult::AssistedMerge { .. } | MergeResult::Conflict { .. } => {
                // Also acceptable
            }
        }
    }

    #[test]
    fn single_line_file() {
        let prev = "single";
        let curr = "SINGLE";
        let result = try_auto_merge(prev, curr, "single", "OTHER", 2, "agent-1");

        match result {
            MergeResult::AssistedMerge {
                suggested_old_str,
                suggested_new_str,
                ..
            } => {
                assert_eq!(suggested_old_str, "SINGLE");
                assert_eq!(suggested_new_str, "OTHER");
            }
            MergeResult::Conflict { .. } => {
                // Also acceptable for overlapping single-line edits
            }
            other => panic!("single-line overlap should be Tier 2 or 3, got: {other:?}"),
        }
    }

    #[test]
    fn very_large_file_far_apart() {
        // 1000 lines, edits at line 1 and line 999
        let lines: Vec<String> = (1..=1000).map(|i| format!("line{i}")).collect();
        let prev = lines.join("\n") + "\n";

        let mut curr_lines = lines.clone();
        curr_lines[0] = "CHANGED1".to_string();
        let curr = curr_lines.join("\n") + "\n";

        let result = try_auto_merge(
            &prev,
            &curr,
            "line999",
            "CHANGED999",
            2,
            "agent-1",
        );

        match result {
            MergeResult::AutoMerged { merged_content, .. } => {
                assert!(merged_content.contains("CHANGED1"));
                assert!(merged_content.contains("CHANGED999"));
            }
            other => panic!("far-apart edits in large file should auto-merge, got: {other:?}"),
        }
    }

    #[test]
    fn format_conflict_unknown_range() {
        let result = MergeResult::Conflict {
            current_content: "content".to_string(),
            current_gen: 1,
            conflict_writer: "test".to_string(),
            changed_lines: 0..0,
            agent_old_str: "old".to_string(),
            agent_new_str: "new".to_string(),
        };
        let msg = format_conflict("file.rs", &result);
        assert!(msg.contains("[conflict]"));
        assert!(msg.contains("unknown"), "0..0 range should show 'unknown'");
    }

    #[test]
    fn ranges_within_proximity_edge_cases() {
        // Adjacent ranges
        assert!(ranges_within_proximity(&(1..2), &(2..3), 0));
        // Same range
        assert!(ranges_within_proximity(&(5..10), &(5..10), 0));
        // Zero-width ranges
        assert!(ranges_within_proximity(&(5..5), &(5..5), 0));
    }
}
